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
    supervised: bool,
}
impl Drop for ChildGuard {
    fn drop(&mut self) {
        if self.reaped {
            return;
        }
        if self.supervised {
            // EOF asks the guardian to kill and reap its worker before releasing
            // the transaction lock. Waiting here orders rollback after cleanup.
            drop(self.child.stdin.take());
            let _ = self.child.wait();
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
    let mut command = Command::new(program);
    command.args(args).stdin(Stdio::null());
    run_helper(command, deadline, false)
}

fn helper_lock(path: &Path, create: bool) -> Result<Option<std::fs::File>, Failure> {
    use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
    let unsafe_lock = || Failure::new(Status::Denied, "unsafe_helper_lock");
    // Refuse redirected parent directories as well as the final lock name.
    let mut component_path = std::path::PathBuf::new();
    for component in path.components() {
        component_path.push(component);
        match std::fs::symlink_metadata(&component_path) {
            Ok(meta) if meta.file_type().is_symlink() => return Err(unsafe_lock()),
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(io_failure(error)),
        }
    }
    let valid = |meta: &std::fs::Metadata| {
        meta.is_file() && meta.nlink() == 1 && meta.uid() == rustix::process::geteuid().as_raw()
    };
    match std::fs::symlink_metadata(path) {
        Ok(meta) if !valid(&meta) => return Err(unsafe_lock()),
        Ok(_) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => return Err(io_failure(error)),
    }
    let file = match std::fs::OpenOptions::new()
        .read(true)
        .write(create)
        .create(create)
        .mode(0o600)
        .custom_flags((OFlags::NOFOLLOW | OFlags::NONBLOCK).bits() as i32)
        .open(path)
    {
        Ok(file) => file,
        Err(error) if !create && error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(io_failure(error)),
    };
    if !valid(&file.metadata().map_err(io_failure)?) {
        return Err(unsafe_lock());
    }
    Ok(Some(file))
}

/// Serialize recovery with a surviving guardian before touching live resources.
pub(crate) fn helper_wait(lock_path: &Path, deadline: Instant) -> Result<(), Failure> {
    use rustix::fs::{FlockOperation, flock};
    let Some(file) = helper_lock(lock_path, false)? else {
        return Ok(());
    };
    loop {
        match flock(&file, FlockOperation::NonBlockingLockExclusive) {
            Ok(()) => return Ok(()),
            Err(rustix::io::Errno::WOULDBLOCK | rustix::io::Errno::INTR) => pause(deadline)?,
            Err(error) => return Err(errno_failure(error)),
        }
    }
}

/// Keep the helper's lifetime inside the durable transaction, even after SIGKILL.
pub(crate) fn helper_with_lock(
    program: &str,
    args: &[&str],
    deadline: Instant,
    lock_path: &Path,
) -> Result<HelperOutput, Failure> {
    let lock_timeout = remaining(deadline)?.as_secs_f64().to_string();
    let _validated_lock = helper_lock(lock_path, true)?;
    let mut command = Command::new("/bin/sh");
    command
        .args([
            "-c",
            r#"
umask 077
[ ! -L "$1" ] && [ -f "$1" ] || exit 126
exec 4<>"$1" || exit 126
flock -x -w "$2" 4 || exit 126
shift 2
printf 'ready\n'
IFS= read -r start || exit 125
[ "$start" = go ] || exit 125
worker=
watcher=
cleanup() {
    trap '' TERM
    [ -z "$worker" ] || kill -KILL -- -"$worker" 2>/dev/null
    [ -z "$worker" ] || wait "$worker" 2>/dev/null
    [ -z "$watcher" ] || kill "$watcher" 2>/dev/null
    [ -z "$watcher" ] || wait "$watcher" 2>/dev/null
}
trap 'cleanup; exit 125' TERM
exec 3<&0
setsid "$@" 3<&- 4>&- </dev/null &
worker=$!
( IFS= read -r lease <&3; kill -TERM "$$" ) 4>&- </dev/null >/dev/null 2>&1 &
watcher=$!
wait "$worker"
result=$?
cleanup
exit "$result"
"#,
            "emaki-helper",
        ])
        .arg(lock_path)
        .arg(lock_timeout)
        .arg(program)
        .args(args)
        .stdin(Stdio::piped());
    run_helper(command, deadline, true)
}

fn run_helper(
    mut command: Command,
    deadline: Instant,
    supervised: bool,
) -> Result<HelperOutput, Failure> {
    remaining(deadline)?;
    let child = command
        .env("LC_ALL", "C")
        .env("SYSTEMD_COLORS", "0")
        .env("SYSTEMD_PAGER", "")
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
        supervised,
    };
    let mut stdout = child.child.stdout.take().expect("piped stdout");
    let mut stderr = child.child.stderr.take().expect("piped stderr");
    nonblocking(&stdout)?;
    nonblocking(&stderr)?;
    if supervised {
        // Do not grant permission to start until the guardian holds the lock.
        // If we die before this handshake, EOF makes it exit without any work.
        let mut ready = Vec::new();
        loop {
            if drain(&mut stdout, &mut ready, deadline)? {
                return Err(Failure::new(Status::Error, "helper_guard_failed"));
            }
            if ready == b"ready\n" {
                break;
            }
            if ready.len() >= 6 {
                return Err(Failure::new(Status::Error, "helper_guard_failed"));
            }
            pause(deadline)?;
        }
        child
            .child
            .stdin
            .as_mut()
            .expect("lease stdin")
            .write_all(b"go\n")
            .map_err(io_failure)?;
    }
    let (mut out, mut err) = (Vec::new(), Vec::new());
    loop {
        let out_closed = drain(&mut stdout, &mut out, deadline)?;
        let err_closed = drain(&mut stderr, &mut err, deadline)?;
        if out_closed
            && err_closed
            && let Some(status) = child.child.try_wait().map_err(io_failure)?
        {
            child.reaped = true;
            if supervised && status.code() == Some(127) {
                return Err(Failure::new(Status::Error, "helper_missing"));
            }
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
#[path = "../../../tests/socket_dir.rs"]
mod socket_dir;

#[cfg(test)]
mod tests {
    use super::*;

    struct SocketDir(std::path::PathBuf);
    impl SocketDir {
        fn new(_name: &str) -> Self {
            Self(super::socket_dir::short_socket_dir())
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
    #[ignore = "subprocess entry point for the killed caller test"]
    fn supervised_helper_caller() {
        let root = std::env::var("EMAKI_HELPER_TEST_ROOT").unwrap();
        let lock = Path::new(&root).join("helper.lock");
        let _ = helper_with_lock(
            "/bin/sh",
            &[
                "-c",
                r#"
(sleep 1; printf late >"$1/late") &
printf started >"$1/started"
wait
"#,
                "helper",
                &root,
            ],
            Instant::now() + Duration::from_secs(10),
            &lock,
        );
    }

    #[test]
    fn killed_caller_stops_helper_descendants_before_recovery() {
        let dir = SocketDir::new("helper-lifetime");
        let mut caller = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "transport::tests::supervised_helper_caller",
                "--ignored",
            ])
            .env("EMAKI_HELPER_TEST_ROOT", &dir.0)
            .stdout(Stdio::null())
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        while !dir.0.join("started").exists() {
            assert!(
                caller.try_wait().unwrap().is_none(),
                "caller exited before helper started"
            );
            assert!(Instant::now() < deadline, "helper did not start");
            std::thread::sleep(Duration::from_millis(5));
        }
        caller.kill().unwrap();
        caller.wait().unwrap();
        helper_wait(&dir.0.join("helper.lock"), deadline).unwrap();
        // Recovery can now replace live files without a stale worker writing later.
        std::thread::sleep(Duration::from_millis(1200));
        assert!(!dir.0.join("late").exists());
    }

    #[test]
    fn supervised_timeout_reaps_worker_and_preserves_output() {
        let dir = SocketDir::new("helper-timeout");
        let lock = dir.0.join("helper.lock");
        let result = helper_with_lock(
            "/bin/sh",
            &["-c", "printf out; printf err >&2; exit 7"],
            Instant::now() + Duration::from_secs(2),
            &lock,
        )
        .unwrap();
        assert!(!result.success);
        assert_eq!(result.stdout, b"out");
        assert_eq!(result.stderr, b"err");
        let result = helper_with_lock(
            "/bin/sh",
            &["-c", "sleep 20 & wait"],
            Instant::now() + Duration::from_millis(100),
            &lock,
        );
        assert_eq!(result.err().unwrap().status, Status::Timeout);
        helper_wait(&lock, Instant::now() + Duration::from_secs(1)).unwrap();
    }

    #[test]
    fn guardian_lock_wait_is_bounded_and_cannot_start_expired_work() {
        use rustix::fs::{FlockOperation, flock};
        let dir = SocketDir::new("helper-lock");
        let lock = dir.0.join("helper.lock");
        let held = std::fs::File::create(&lock).unwrap();
        flock(&held, FlockOperation::LockExclusive).unwrap();
        let start = Instant::now();
        let result = helper_with_lock(
            "/bin/sh",
            &[
                "-c",
                "touch \"$1/started\"",
                "helper",
                dir.0.to_str().unwrap(),
            ],
            start + Duration::from_millis(100),
            &lock,
        );
        assert!(result.is_err());
        assert!(start.elapsed() < Duration::from_secs(1));
        drop(held);
        helper_wait(&lock, Instant::now() + Duration::from_secs(1)).unwrap();
        assert!(!dir.0.join("started").exists());
    }

    #[test]
    fn helper_locks_refuse_redirection_and_nonregular_files() {
        use std::os::unix::fs::{PermissionsExt, symlink};
        let dir = SocketDir::new("helper-lock-safety");
        let sentinel = dir.0.join("personal");
        std::fs::write(&sentinel, b"personal content").unwrap();
        let redirected = dir.0.join("redirected.lock");
        symlink(&sentinel, &redirected).unwrap();
        let linked = dir.0.join("linked.lock");
        std::fs::hard_link(&sentinel, &linked).unwrap();
        let directory = dir.0.join("directory.lock");
        std::fs::create_dir(&directory).unwrap();
        let fifo = dir.0.join("fifo.lock");
        rustix::fs::mknodat(
            rustix::fs::CWD,
            &fifo,
            rustix::fs::FileType::Fifo,
            rustix::fs::Mode::RUSR | rustix::fs::Mode::WUSR,
            0,
        )
        .unwrap();
        let redirected_parent = dir.0.join("redirected-parent");
        symlink(&dir.0, &redirected_parent).unwrap();
        for path in [
            redirected,
            linked,
            directory,
            fifo,
            redirected_parent.join("another.lock"),
        ] {
            let start = Instant::now();
            assert!(
                helper_with_lock("/bin/true", &[], start + Duration::from_secs(1), &path).is_err()
            );
            assert!(helper_wait(&path, start + Duration::from_secs(1)).is_err());
            assert!(start.elapsed() < Duration::from_secs(1));
            assert_eq!(std::fs::read(&sentinel).unwrap(), b"personal content");
        }
        let ordinary = dir.0.join("ordinary.lock");
        helper_with_lock(
            "/bin/true",
            &[],
            Instant::now() + Duration::from_secs(1),
            &ordinary,
        )
        .unwrap();
        assert_eq!(
            std::fs::metadata(&ordinary).unwrap().permissions().mode() & 0o777,
            0o600
        );
        std::fs::write(&ordinary, b"preserve lock contents").unwrap();
        helper_with_lock(
            "/bin/true",
            &[],
            Instant::now() + Duration::from_secs(1),
            &ordinary,
        )
        .unwrap();
        assert_eq!(std::fs::read(&ordinary).unwrap(), b"preserve lock contents");
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
