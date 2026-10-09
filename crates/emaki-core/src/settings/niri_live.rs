// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
//! Confirm managed configuration loads through the compositor event stream.
use super::{Result, err};
use crate::transport;
use niri_ipc::{Action, Event, Reply, Request, Response};
use std::io::Read;
use std::os::unix::net::UnixStream;
use std::path::Path;
use std::time::{Duration, Instant};

pub(super) fn load(path: &Path, timeout: Duration) -> Result<()> {
    let socket = std::env::var_os("NIRI_SOCKET").ok_or_else(|| err("niri_socket_missing"))?;
    load_at(Path::new(&socket), path, timeout)
}

fn failure(failure: crate::state::Failure) -> super::Error {
    err(failure.reason)
}

fn request(request: &Request) -> Result<Vec<u8>> {
    let mut bytes = serde_json::to_vec(request).map_err(|_| err("niri_request_invalid"))?;
    bytes.push(b'\n');
    Ok(bytes)
}

fn handled(bytes: &[u8]) -> Result<()> {
    match serde_json::from_slice::<Reply>(bytes) {
        Ok(Ok(Response::Handled)) => Ok(()),
        Ok(Err(_)) => Err(err("niri_request_rejected")),
        _ => Err(err("niri_reply_invalid")),
    }
}

/// Preserve coalesced frames, while bounding each frame and the entire operation.
fn frame(stream: &mut UnixStream, pending: &mut Vec<u8>, deadline: Instant) -> Result<Vec<u8>> {
    let mut buffer = [0; 8192];
    loop {
        transport::remaining(deadline).map_err(failure)?;
        if let Some(end) = pending.iter().position(|byte| *byte == b'\n') {
            if end > transport::MAX_REPLY {
                return Err(err("response_too_large"));
            }
            let line = pending.drain(..=end).collect::<Vec<_>>();
            return Ok(line);
        }
        if pending.len() > transport::MAX_REPLY {
            return Err(err("response_too_large"));
        }
        match stream.read(&mut buffer) {
            Ok(0) => return Err(err("connection_closed")),
            Ok(n) => pending.extend_from_slice(&buffer[..n]),
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                let left = transport::remaining(deadline).map_err(failure)?;
                std::thread::sleep(left.min(Duration::from_millis(2)));
            }
            Err(error) => return Err(failure(transport::io_failure(error))),
        }
    }
}

fn config_event(stream: &mut UnixStream, pending: &mut Vec<u8>, deadline: Instant) -> Result<bool> {
    loop {
        let bytes = frame(stream, pending, deadline)?;
        match serde_json::from_slice::<Event>(&bytes) {
            Ok(Event::ConfigLoaded { failed }) => return Ok(failed),
            Ok(_) => (),
            Err(_) => return Err(err("niri_event_invalid")),
        }
    }
}

fn load_at(socket: &Path, path: &Path, timeout: Duration) -> Result<()> {
    if !path.is_absolute() {
        return Err(err("niri_config_path_invalid"));
    }
    let path = path
        .to_str()
        .ok_or_else(|| err("niri_config_path_invalid"))?;
    let deadline = Instant::now()
        .checked_add(timeout)
        .ok_or_else(|| err("invalid_timeout"))?;
    let mut events = transport::connect(socket, deadline).map_err(failure)?;
    transport::send(&mut events, &request(&Request::EventStream)?, deadline).map_err(failure)?;
    let mut pending = Vec::new();
    handled(&frame(&mut events, &mut pending, deadline)?)?;
    // This event describes the preceding load, including a preceding failed load.
    // It must never acknowledge the action we are about to send.
    config_event(&mut events, &mut pending, deadline)?;
    let mut actions = transport::connect(socket, deadline).map_err(failure)?;
    let action = request(&Request::Action(Action::LoadConfigFile {
        path: Some(path.to_owned()),
    }))?;
    let reply = transport::exchange(&mut actions, &action, deadline).map_err(failure)?;
    handled(&reply)?;
    if config_event(&mut events, &mut pending, deadline)? {
        Err(err("niri_config_load_failed"))
    } else {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader, Write};
    use std::os::unix::net::UnixListener;
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    struct Socket(PathBuf);
    impl Socket {
        fn new() -> Self {
            static NEXT: AtomicU64 = AtomicU64::new(0);
            Self(PathBuf::from(format!(
                "/tmp/emaki-niri-load-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            )))
        }
    }
    impl Drop for Socket {
        fn drop(&mut self) {
            let _ = std::fs::remove_file(&self.0);
        }
    }
    fn read_request(stream: &UnixStream) -> Request {
        stream
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        let mut line = String::new();
        BufReader::new(stream).read_line(&mut line).unwrap();
        serde_json::from_str(&line).unwrap()
    }
    fn reload(event: Option<bool>) -> Result<()> {
        let socket = Socket::new();
        let listener = UnixListener::bind(&socket.0).unwrap();
        let server = std::thread::spawn(move || {
            let (mut events, _) = listener.accept().unwrap();
            assert!(matches!(read_request(&events), Request::EventStream));
            // Coalesced initial frames also exercise preservation of buffered data.
            events.write_all(b"{\"Ok\":\"Handled\"}\n{\"OverviewOpenedOrClosed\":{\"is_open\":false}}\n{\"ConfigLoaded\":{\"failed\":true}}\n").unwrap();
            let (mut actions, _) = listener.accept().unwrap();
            assert!(
                matches!(read_request(&actions), Request::Action(Action::LoadConfigFile { path: Some(path) }) if path == "/tmp/managed.kdl")
            );
            actions.write_all(b"{\"Ok\":\"Handled\"}\n").unwrap();
            if let Some(failed) = event {
                let mut bytes = serde_json::to_vec(&Event::ConfigLoaded { failed }).unwrap();
                bytes.push(b'\n');
                events.write_all(&bytes).unwrap();
            } else {
                std::thread::sleep(Duration::from_millis(300));
            }
        });
        let result = load_at(
            &socket.0,
            Path::new("/tmp/managed.kdl"),
            Duration::from_millis(200),
        );
        server.join().unwrap();
        result
    }
    #[test]
    fn acknowledges_success_after_initial_failed_snapshot() {
        reload(Some(false)).unwrap();
    }
    #[test]
    fn reports_failed_reload() {
        assert_eq!(
            reload(Some(true)).unwrap_err().reason,
            "niri_config_load_failed"
        );
    }
    #[test]
    fn initial_snapshot_does_not_acknowledge_reload() {
        assert_eq!(reload(None).unwrap_err().reason, "deadline_exceeded");
    }
    #[test]
    fn missing_socket_and_relative_config_are_rejected() {
        let socket = Socket::new();
        assert_eq!(
            load_at(&socket.0, Path::new("relative.kdl"), Duration::from_secs(1))
                .unwrap_err()
                .reason,
            "niri_config_path_invalid"
        );
        assert_eq!(
            load_at(
                &socket.0,
                Path::new("/tmp/managed.kdl"),
                Duration::from_secs(1)
            )
            .unwrap_err()
            .reason,
            "endpoint_missing"
        );
    }
    #[test]
    fn oversized_frames_are_rejected() {
        let (mut reader, _writer) = UnixStream::pair().unwrap();
        let mut pending = vec![b'x'; transport::MAX_REPLY + 1];
        pending.push(b'\n');
        let result = frame(
            &mut reader,
            &mut pending,
            Instant::now() + Duration::from_secs(2),
        );
        assert_eq!(result.unwrap_err().reason, "response_too_large");
    }
    #[test]
    fn buffered_frames_still_obey_deadline() {
        let (mut reader, _writer) = UnixStream::pair().unwrap();
        let mut pending = b"{}\n{}\n".to_vec();
        assert_eq!(
            frame(&mut reader, &mut pending, Instant::now())
                .unwrap_err()
                .reason,
            "deadline_exceeded"
        );
    }
}
