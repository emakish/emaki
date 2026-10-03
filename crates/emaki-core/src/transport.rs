//! Bounded, nonblocking local IPC and helper processes. No temporary files.
use crate::state::{Failure, Status};
use rustix::fs::{OFlags, fcntl_getfl, fcntl_setfl};
use rustix::net::{self, AddressFamily, SocketAddrUnix, SocketFlags, SocketType};
use rustix::process::{Pid, Signal, kill_process_group};
use std::io::{Read, Write};
use std::os::fd::AsFd;
use std::os::unix::{net::UnixStream, process::CommandExt};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

pub(crate) const MAX_REPLY: usize = 2 * 1024 * 1024;

pub(crate) fn io_failure(error: std::io::Error) -> Failure {
    use std::io::ErrorKind::*;
    match error.kind() {
        PermissionDenied => Failure::new(Status::Denied, "access_denied"),
        NotFound | ConnectionRefused => Failure::new(Status::Absent, "endpoint_missing"),
        TimedOut => Failure::new(Status::Timeout, "deadline_exceeded"),
        UnexpectedEof | ConnectionReset | ConnectionAborted | BrokenPipe => {
            Failure::new(Status::Incomplete, "connection_closed")
        }
        _ => Failure::new(Status::Error, "io_error"),
    }
}

fn errno_failure(error: rustix::io::Errno) -> Failure {
    io_failure(error.into())
}

pub(crate) fn remaining(deadline: Instant) -> Result<Duration, Failure> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|d| !d.is_zero())
        .ok_or(Failure::new(Status::Timeout, "deadline_exceeded"))
}

fn pause(deadline: Instant) -> Result<(), Failure> {
    std::thread::sleep(remaining(deadline)?.min(Duration::from_millis(2)));
    Ok(())
}

pub(crate) fn connect(path: &Path, deadline: Instant) -> Result<UnixStream, Failure> {
    let address = SocketAddrUnix::new(path)
        .map_err(|_| Failure::new(Status::Error, "invalid_socket_path"))?;
    let fd = net::socket_with(
        AddressFamily::UNIX,
        SocketType::STREAM,
        SocketFlags::CLOEXEC | SocketFlags::NONBLOCK,
        None,
    )
    .map_err(errno_failure)?;
    loop {
        remaining(deadline)?;
        match net::connect(&fd, &address) {
            Ok(()) => return Ok(UnixStream::from(fd)),
            // A full Unix listen queue returns EAGAIN without starting a connect.
            Err(rustix::io::Errno::AGAIN | rustix::io::Errno::INTR) => pause(deadline)?,
            Err(rustix::io::Errno::INPROGRESS) => {
                use rustix::event::{PollFd, PollFlags, Timespec, poll};
                loop {
                    let time: Timespec = remaining(deadline)?
                        .try_into()
                        .map_err(|_| Failure::new(Status::Error, "invalid_timeout"))?;
                    match poll(&mut [PollFd::new(&fd, PollFlags::OUT)], Some(&time)) {
                        Ok(0) => return Err(Failure::new(Status::Timeout, "deadline_exceeded")),
                        Ok(_) => {
                            net::sockopt::socket_error(&fd)
                                .map_err(errno_failure)?
                                .map_err(errno_failure)?;
                            return Ok(UnixStream::from(fd));
                        }
                        Err(rustix::io::Errno::INTR) => continue,
                        Err(e) => return Err(errno_failure(e)),
                    }
                }
            }
            Err(e) => return Err(errno_failure(e)),
        }
    }
}

/// Niri's newline-delimited protocol; one request at a time, no event stream.
pub(crate) fn exchange(
    stream: &mut UnixStream,
    request: &[u8],
    deadline: Instant,
) -> Result<Vec<u8>, Failure> {
    send(stream, request, deadline)?;
    read_reply(stream, deadline)
}

pub(crate) fn send(
    stream: &mut UnixStream,
    request: &[u8],
    deadline: Instant,
) -> Result<(), Failure> {
    let mut rest = request;
    while !rest.is_empty() {
        remaining(deadline)?;
        match stream.write(rest) {
            Ok(0) => return Err(Failure::new(Status::Incomplete, "connection_closed")),
            Ok(n) => rest = &rest[n..],
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => pause(deadline)?,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(io_failure(e)),
        }
    }
    Ok(())
}

pub(crate) fn read_reply(stream: &mut UnixStream, deadline: Instant) -> Result<Vec<u8>, Failure> {
    let mut bytes = Vec::new();
    let mut buffer = [0; 8192];
    loop {
        remaining(deadline)?;
        match stream.read(&mut buffer) {
            Ok(0) => return Err(Failure::new(Status::Incomplete, "connection_closed")),
            Ok(n) => {
                if bytes.len() + n > MAX_REPLY {
                    return Err(Failure::new(Status::Incomplete, "response_too_large"));
                }
                bytes.extend_from_slice(&buffer[..n]);
                if let Some(end) = bytes.iter().position(|b| *b == b'\n') {
                    if bytes[end + 1..].iter().any(|b| !b.is_ascii_whitespace()) {
                        return Err(Failure::new(Status::Incomplete, "unexpected_extra_reply"));
                    }
                    bytes.truncate(end);
                    return Ok(bytes);
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => pause(deadline)?,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(io_failure(e)),
        }
    }
}

struct ChildGuard {
    child: Child,
    reaped: bool,
}
impl Drop for ChildGuard {
    fn drop(&mut self) {
        if self.reaped {
            return;
        }
        // Helpers get their own group. Also stop descendants holding pipe FDs.
        if let Some(pid) = Pid::from_raw(self.child.id() as i32) {
            let _ = kill_process_group(pid, Signal::KILL);
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

pub(crate) struct HelperOutput {
    pub success: bool,
    pub stdout: Vec<u8>,
    pub stderr: Vec<u8>,
}

fn nonblocking(fd: &impl AsFd) -> Result<(), Failure> {
    let flags = fcntl_getfl(fd).map_err(errno_failure)?;
    fcntl_setfl(fd, flags | OFlags::NONBLOCK).map_err(errno_failure)
}

fn drain(reader: &mut impl Read, bytes: &mut Vec<u8>, deadline: Instant) -> Result<bool, Failure> {
    let mut buffer = [0; 8192];
    loop {
        remaining(deadline)?;
        match reader.read(&mut buffer) {
            Ok(0) => return Ok(true),
            Ok(n) => {
                if bytes.len() + n > MAX_REPLY {
                    return Err(Failure::new(Status::Incomplete, "response_too_large"));
                }
                bytes.extend_from_slice(&buffer[..n]);
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => return Ok(false),
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(io_failure(e)),
        }
    }
}

pub(crate) fn helper(
    program: &str,
    args: &[&str],
    deadline: Instant,
) -> Result<HelperOutput, Failure> {
    remaining(deadline)?;
    let child = Command::new(program)
        .args(args)
        .env("LC_ALL", "C")
        .env("SYSTEMD_COLORS", "0")
        .env("SYSTEMD_PAGER", "")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()
        .map_err(|e| {
            if e.kind() == std::io::ErrorKind::NotFound {
                Failure::new(Status::Error, "helper_missing")
            } else {
                io_failure(e)
            }
        })?;
    let mut child = ChildGuard {
        child,
        reaped: false,
    };
    let mut stdout = child.child.stdout.take().expect("piped stdout");
    let mut stderr = child.child.stderr.take().expect("piped stderr");
    nonblocking(&stdout)?;
    nonblocking(&stderr)?;
    let (mut out, mut err) = (Vec::new(), Vec::new());
    loop {
        let out_closed = drain(&mut stdout, &mut out, deadline)?;
        let err_closed = drain(&mut stderr, &mut err, deadline)?;
        if out_closed
            && err_closed
            && let Some(status) = child.child.try_wait().map_err(io_failure)?
        {
            child.reaped = true;
            return Ok(HelperOutput {
                success: status.success(),
                stdout: out,
                stderr: err,
            });
        }
        pause(deadline)?;
    }
}

/// Match only known categories; never emit stderr, which can contain secrets.
pub(crate) fn helper_failure(stderr: &[u8]) -> Failure {
    let message = String::from_utf8_lossy(stderr).to_ascii_lowercase();
    let contains = |patterns: &[&str]| patterns.iter().any(|p| message.contains(p));
    if contains(&[
        "accessdenied",
        "access denied",
        "permission denied",
        "operation not permitted",
        "authentication failed",
    ]) {
        Failure::new(Status::Denied, "access_denied")
    } else if contains(&["timed out", "timeout", "noreply", "no reply"]) {
        Failure::new(Status::Timeout, "source_timeout")
    } else if contains(&[
        "namehasnoowner",
        "serviceunknown",
        "no such file",
        "connection refused",
        "not provided by any",
        "nosuchunit",
        "not loaded",
    ]) {
        Failure::new(Status::Absent, "endpoint_missing")
    } else if contains(&[
        "disconnected",
        "connection reset",
        "connection terminated",
        "broken pipe",
        "unexpected eof",
    ]) {
        Failure::new(Status::Incomplete, "connection_closed")
    } else {
        Failure::new(Status::Error, "helper_failed")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct SocketDir(std::path::PathBuf);
    impl SocketDir {
        fn new(name: &str) -> Self {
            let path = Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../../.cache/tmp")
                .join(format!("transport-{}-{name}", std::process::id()));
            std::fs::create_dir_all(&path).unwrap();
            Self(path.canonicalize().unwrap())
        }
    }
    impl Drop for SocketDir {
        fn drop(&mut self) {
            std::fs::remove_dir_all(&self.0).unwrap();
        }
    }

    #[test]
    fn full_unix_listen_queue_cannot_block_connect() {
        let dir = SocketDir::new("queue");
        let path = dir.0.join("niri.sock");
        let address = SocketAddrUnix::new(&path).unwrap();
        let listener = net::socket_with(
            AddressFamily::UNIX,
            SocketType::STREAM,
            SocketFlags::CLOEXEC | SocketFlags::NONBLOCK,
            None,
        )
        .unwrap();
        net::bind(&listener, &address).unwrap();
        net::listen(&listener, 0).unwrap();
        let _first = connect(&path, Instant::now() + Duration::from_millis(100)).unwrap();
        let start = Instant::now();
        let error = connect(&path, start + Duration::from_millis(50)).unwrap_err();
        assert_eq!(error.status, Status::Timeout);
        assert!(start.elapsed() < Duration::from_millis(500));
    }

    #[test]
    fn socket_file_permission_denial_is_reported() {
        use std::os::unix::fs::PermissionsExt;
        if rustix::process::geteuid().is_root() {
            // Root bypasses mode bits; permission_errnos test still checks both errno mappings.
            eprintln!("chmod denial requires unprivileged execution; errno tests cover root CI");
            return;
        }
        let dir = SocketDir::new("denied");
        let path = dir.0.join("niri.sock");
        let _listener = std::os::unix::net::UnixListener::bind(&path).unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o000)).unwrap();
        let error = connect(&path, Instant::now() + Duration::from_millis(100)).unwrap_err();
        assert_eq!(error.status, Status::Denied);
    }

    #[test]
    fn permission_errnos_are_denied_not_absent() {
        // Deterministic even in root CI, where chmod-based denial cannot work.
        for errno in [rustix::io::Errno::ACCESS, rustix::io::Errno::PERM] {
            let failure = errno_failure(errno);
            assert_eq!(failure.status, Status::Denied);
            assert_eq!(failure.reason, "access_denied");
        }
    }

    #[test]
    fn dribbling_socket_does_not_extend_deadline() {
        let (mut client, mut server) = UnixStream::pair().unwrap();
        client.set_nonblocking(true).unwrap();
        let writer = std::thread::spawn(move || {
            let mut request = [0; 2];
            server.read_exact(&mut request).unwrap();
            for _ in 0..100 {
                if server.write_all(b" ").is_err() {
                    break;
                }
                std::thread::sleep(Duration::from_millis(5));
            }
        });
        let start = Instant::now();
        let error = exchange(&mut client, b"x\n", start + Duration::from_millis(50)).unwrap_err();
        assert_eq!(error.status, Status::Timeout);
        assert!(start.elapsed() < Duration::from_millis(500));
        drop(client);
        writer.join().unwrap();
    }

    #[test]
    fn inherited_pipes_cannot_keep_helper_alive_past_deadline() {
        let start = Instant::now();
        let result = helper(
            "sh",
            &["-c", "sleep 20 & exit 0"],
            start + Duration::from_millis(70),
        );
        assert_eq!(result.err().unwrap().status, Status::Timeout);
        assert!(start.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn closed_socket_is_incomplete() {
        let (mut client, server) = UnixStream::pair().unwrap();
        client.set_nonblocking(true).unwrap();
        drop(server);
        let error = exchange(
            &mut client,
            b"x\n",
            Instant::now() + Duration::from_millis(50),
        )
        .unwrap_err();
        assert_eq!(error.status, Status::Incomplete);
    }
}
