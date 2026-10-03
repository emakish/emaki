//! Nonblocking newline framing, preserving coalesced ACK + event packets.
use crate::state::{Failure, Status};
use crate::transport::{MAX_REPLY, io_failure, remaining};
use rustix::event::{PollFd, PollFlags, Timespec, poll};
use std::io::Read;
use std::os::unix::net::UnixStream;
use std::time::{Duration, Instant};

pub(super) struct Lines {
    pub stream: UnixStream,
    pending: Vec<u8>,
    started: Option<Instant>,
    timeout: Duration,
}
impl Lines {
    pub fn new(stream: UnixStream, timeout: Duration) -> Self {
        Self {
            stream,
            pending: Vec::new(),
            started: None,
            timeout,
        }
    }

    /// None is an idle deadline, not a broken connection. A partial frame has
    /// its own deadline which cannot be prolonged by dribbling bytes.
    pub fn next(&mut self, deadline: Instant) -> Result<Option<Vec<u8>>, Failure> {
        loop {
            if let Some(end) = self.pending.iter().position(|b| *b == b'\n') {
                if end > MAX_REPLY {
                    return Err(Failure::new(Status::Incomplete, "response_too_large"));
                }
                let rest = self.pending.split_off(end + 1);
                let mut line = std::mem::replace(&mut self.pending, rest);
                line.truncate(end);
                self.started = (!self.pending.is_empty()).then(Instant::now);
                return Ok(Some(line));
            }
            if self.pending.len() > MAX_REPLY {
                return Err(Failure::new(Status::Incomplete, "response_too_large"));
            }
            let now = Instant::now();
            if self.started.is_some_and(|t| now >= t + self.timeout) {
                return Err(Failure::new(Status::Incomplete, "partial_line_timeout"));
            }
            if now >= deadline {
                return Ok(None);
            }
            let until = self
                .started
                .map_or(deadline, |t| deadline.min(t + self.timeout));
            let duration: Timespec = remaining(until)?
                .try_into()
                .map_err(|_| Failure::new(Status::Error, "invalid_timeout"))?;
            match poll(
                &mut [PollFd::new(&self.stream, PollFlags::IN)],
                Some(&duration),
            ) {
                Ok(0) => continue,
                Ok(_) => {}
                Err(rustix::io::Errno::INTR) => continue,
                Err(e) => return Err(io_failure(e.into())),
            }
            let mut buffer = [0; 8192];
            match self.stream.read(&mut buffer) {
                Ok(0) => {
                    return Err(Failure::new(
                        Status::Incomplete,
                        if self.pending.is_empty() {
                            "connection_closed"
                        } else {
                            "partial_line_closed"
                        },
                    ));
                }
                Ok(n) => {
                    self.started.get_or_insert_with(Instant::now);
                    self.pending.extend_from_slice(&buffer[..n]);
                }
                Err(e)
                    if matches!(
                        e.kind(),
                        std::io::ErrorKind::WouldBlock | std::io::ErrorKind::Interrupted
                    ) => {}
                Err(e) => return Err(io_failure(e)),
            }
        }
    }
}
