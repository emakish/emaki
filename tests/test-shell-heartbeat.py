#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile the packaged supervisor and exercise only owned mock shell processes.

The heartbeat speaks Quickshell's own IPC itself: mock shells here keep a Quickshell 0.3.1
instance directory (held instance.lock, by-pid link, ipc.sock) under a private runtime
directory and answer the probe's `system.ping` request."""
import argparse
import fcntl
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import threading
import time

# Socket paths must fit Quickshell's 106-byte limit, as under /run/user/<uid>; a test runner's
# TMPDIR can be deep (the public export runs this under its own checkout).
SHORT_TMP = '/tmp' if os.path.isdir('/tmp') else None

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--patch', type=Path, default=ROOT / 'packaging/niri-emaki/0010-trusted-authentication-prompt.patch')
parser.add_argument('--regressions-only', action='store_true')
args = parser.parse_args()
PATCH = args.patch

# `qs ipc --pid N call system ping` sends exactly this (recorded from Quickshell 0.3.1):
# IpcCommand index 3 (StringCallCommand), QString "system", QString "ping", empty QVector.
REQUEST = bytes.fromhex('030000000c00730079007300740065006d0000000800700069006e006700000000')
# The shell's reply: StringCallResponse index 5 (Completed), isVoid false, QString "pong".
PONG = bytes.fromhex('0500000000080070006f006e0067')
INSTANCE_ID = 'c2xq1lmc'

# The libc items the heartbeat uses, for a compile without the crate (Linux, glibc, 64-bit).
LIBC = r'''
#[allow(non_camel_case_types, dead_code)]
mod libc {
    pub type c_int = i32;
    pub type c_short = i16;
    pub type c_char = i8;
    pub type pid_t = i32;
    pub type socklen_t = u32;
    pub const SIGTERM: c_int = 15;
    pub const SIGSTOP: c_int = 19;
    pub const EINTR: c_int = 4;
    pub const ECHILD: c_int = 10;
    pub const EAGAIN: c_int = 11;
    pub const WNOHANG: c_int = 1;
    pub const F_GETLK: c_int = 5;
    pub const F_WRLCK: c_int = 1;
    pub const F_UNLCK: c_int = 2;
    pub const SEEK_SET: c_int = 0;
    pub const AF_UNIX: c_int = 1;
    pub const SOCK_STREAM: c_int = 1;
    pub const SOCK_NONBLOCK: c_int = 0o4000;
    pub const SOCK_CLOEXEC: c_int = 0o2000000;
    pub const POLLIN: c_short = 1;
    pub const POLLOUT: c_short = 4;
    #[repr(C)]
    pub struct flock { pub l_type: c_short, pub l_whence: c_short, pub l_start: i64,
        pub l_len: i64, pub l_pid: pid_t }
    #[repr(C)]
    pub struct sockaddr { pub sa_family: u16, pub sa_data: [c_char; 14] }
    #[repr(C)]
    pub struct sockaddr_un { pub sun_family: u16, pub sun_path: [c_char; 108] }
    #[repr(C)]
    pub struct pollfd { pub fd: c_int, pub events: c_short, pub revents: c_short }
    unsafe extern "C" {
        pub fn kill(pid: pid_t, signal: c_int) -> c_int;
        pub fn getuid() -> u32;
        pub fn fcntl(fd: c_int, command: c_int, ...) -> c_int;
        pub fn socket(domain: c_int, kind: c_int, protocol: c_int) -> c_int;
        pub fn connect(fd: c_int, address: *const sockaddr, length: socklen_t) -> c_int;
        pub fn poll(fds: *mut pollfd, count: u64, timeout: c_int) -> c_int;
        pub fn waitpid(pid: pid_t, status: *mut c_int, options: c_int) -> pid_t;
    }
}
'''


def source():
    text = PATCH.read_text().split('+++ b/src/trusted_client.rs\n', 1)[1]
    text = text.split('\ndiff --git ', 1)[0]
    return '\n'.join(line[1:] for line in text.splitlines() if line.startswith('+'))


def between(text, start, end):
    return text[text.index(start):text.index(end)]


class FakeInstance:
    """A Quickshell instance directory whose lock this process holds; a thread answers."""

    def __init__(self, base, pid, delay=0.0, answer=True):
        self.runtime = base / f'run-{pid}'
        instance = self.runtime / 'quickshell/by-id' / INSTANCE_ID
        instance.mkdir(parents=True)
        (self.runtime / 'quickshell/by-pid').mkdir()
        (self.runtime / 'quickshell/by-pid' / str(pid)).symlink_to(instance)
        name = INSTANCE_ID.encode('utf-16-be')
        (instance / 'instance.lock').write_bytes(len(name).to_bytes(4, 'big') + name)
        self.lock = open(instance / 'instance.lock', 'r+b')
        fcntl.lockf(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(instance / 'ipc.sock'))
        self.server.listen(8)
        self.requests, self.held = [], []
        self.delay, self.answer = delay, answer
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            data = b''
            while len(data) < len(REQUEST):
                chunk = client.recv(64)
                if not chunk:
                    break
                data += chunk
            self.requests.append(data)
            if not self.answer:
                self.held.append(client)  # A hung main thread: accepted, never answered.
                continue
            time.sleep(self.delay)
            client.sendall(PONG)
            client.close()


def regressions():
    """Exercise production timing and the probe transport without host session IPC."""
    original = source()
    heartbeat = between(original, 'struct Heartbeat {', 'struct Running {')
    with tempfile.TemporaryDirectory(prefix='emaki-probe-policy-', dir=SHORT_TMP) as directory:
        base = Path(directory)
        code = r"""
use std::{collections::BTreeMap, ffi::OsString, io, os::fd::{AsRawFd, FromRawFd, OwnedFd},
    os::unix::net::UnixStream, sync::{Arc, atomic::{AtomicBool, Ordering}},
    time::{Duration, Instant}};
""" + LIBC + heartbeat + r"""
fn tick(h: &mut Heartbeat, now: Instant, pid: u32, env: &BTreeMap<OsString, OsString>) {
    h.last_tick = now; // Advance the policy clock without fabricating a suspend.
    assert!(h.tick(now, pid, env));
}
fn complete(h: &mut Heartbeat, now: Instant, pid: u32, env: &BTreeMap<OsString, OsString>) {
    let until = Instant::now() + Duration::from_secs(3);
    while Instant::now() < until {
        tick(h, now, pid, env);
        if h.probe.is_none() { return; }
        // The probe is a thread of this process: no child exists while it runs.
        let mut status = 0;
        let child = unsafe { libc::waitpid(-1, &mut status, libc::WNOHANG) };
        assert!(child == -1 && io::Error::last_os_error().raw_os_error() == Some(libc::ECHILD),
            "the probe started a process");
        std::thread::sleep(Duration::from_millis(10));
    }
    panic!("probe slot remained occupied after the reply");
}
fn main() {
    let case = std::env::args().nth(1).unwrap();
    let pid: u32 = std::env::args().nth(2).unwrap().parse().unwrap();
    let env = std::env::vars_os().collect();
    let now = Instant::now();
    let mut h = Heartbeat::new(now);
    match case.as_str() {
        "timing" => {
            assert_eq!(h.next_probe.duration_since(now), Duration::from_secs(15));
            assert_eq!(h.deadline.duration_since(now), Duration::from_secs(30));
            let round = now + Duration::from_secs(15);
            tick(&mut h, round, pid, &env);
            assert_eq!(h.next_probe.duration_since(round), Duration::from_secs(15));
            complete(&mut h, round, pid, &env);
            assert_eq!(h.deadline.duration_since(round), Duration::from_secs(45));
        }
        "no-process" => {
            let started = Instant::now();
            let round = now + Duration::from_secs(15);
            tick(&mut h, round, pid, &env);
            assert!(started.elapsed() < Duration::from_millis(150), "the probe blocked dispatch");
            complete(&mut h, round, pid, &env);
            assert!(started.elapsed() >= Duration::from_millis(300), "the delayed reply was not awaited");
            assert_eq!(h.deadline, round + Duration::from_secs(45));
        }
        "silent" => {
            let round = now + Duration::from_secs(15);
            tick(&mut h, round, pid, &env);
            std::thread::sleep(Duration::from_millis(500));
            tick(&mut h, round + Duration::from_secs(1), pid, &env);
            assert!(h.probe.is_some(), "a shell that never answers passed the probe");
            let expiry = now + Duration::from_secs(30);
            h.last_tick = expiry;
            assert!(!h.tick(expiry, pid, &env), "the deadline did not expire");
        }
        "request" => {
            let hex: String = SHELL_PING.iter().map(|byte| format!("{byte:02x}")).collect();
            println!("{hex}");
        }
        _ => panic!("unknown regression"),
    }
}
"""
        path = base / 'policy.rs'
        path.write_text(code)
        subprocess.run(['rustc', '--edition=2021', '-A', 'warnings', str(path), '-o', str(base / 'policy')], check=True)
        failures = []
        for number, (case, delay, answer) in enumerate((('timing', 0.0, True), ('no-process', 0.4, True),
                                                        ('silent', 0.0, False))):
            pid = 4_100_000 + number
            fake = FakeInstance(base, pid, delay, answer)
            env = {k: v for k, v in os.environ.items() if k not in ('WAYLAND_SOCKET', 'WAYLAND_DISPLAY')}
            env['XDG_RUNTIME_DIR'] = str(fake.runtime)
            result = subprocess.run([str(base / 'policy'), case, str(pid)], env=env, timeout=10)
            ok = result.returncode == 0 and fake.requests and all(r == REQUEST for r in fake.requests)
            print(('PASS ' if ok else 'FAIL ') + case, flush=True)
            if not ok:
                failures.append(case)
            fake.server.close()
            fake.lock.close()
        sent = subprocess.run([str(base / 'policy'), 'request', '0'], capture_output=True, text=True, check=True)
        qml = (ROOT / 'shell/shell.qml').read_text()
        ping = re.search(r'target: "system"\s+function ping\(\): string\s*{\s*return "pong";\s*}', qml)
        trivial = ping and bytes.fromhex(sent.stdout.strip()) == REQUEST
        print(('PASS ' if trivial else 'FAIL ') + 'constant system.ping', flush=True)
        if not trivial:
            failures.append('constant system.ping')
        if failures:
            raise SystemExit('Failed regressions: ' + ', '.join(failures))


regressions()
if args.regressions_only:
    raise SystemExit(0)


with tempfile.TemporaryDirectory(prefix='emaki-shell-heartbeat-', dir=SHORT_TMP) as directory:
    base = Path(directory)
    # A mock shell with a Quickshell instance directory under the private runtime
    # directory; it answers each request after a delay. No graphical session or host IPC.
    (base / 'shell.py').write_text('''import fcntl, os, pathlib, socket, time
pid = os.getpid()
run = pathlib.Path(os.environ['XDG_RUNTIME_DIR']) / 'quickshell'
instance = run / 'by-id' / ('mock' + str(pid))
instance.mkdir(parents=True)
(run / 'by-pid').mkdir(exist_ok=True)
(run / 'by-pid' / str(pid)).symlink_to(instance)
name = instance.name.encode('utf-16-be')
(instance / 'instance.lock').write_bytes(len(name).to_bytes(4, 'big') + name)
lock = open(instance / 'instance.lock', 'r+b')
fcntl.lockf(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
assert 'WAYLAND_SOCKET' not in os.environ
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(str(instance / 'ipc.sock'))
server.listen(8)
while True:
    client, _ = server.accept()
    data = b''
    while len(data) < 33:
        chunk = client.recv(64)
        if not chunk:
            break
        data += chunk
    assert data == bytes.fromhex('%s'), data
    time.sleep(.2)
    client.sendall(bytes.fromhex('%s'))
    client.close()
''' % (REQUEST.hex(), PONG.hex()))
    original = source()
    # Compile production policy verbatim, replacing only compositor insertion and
    # platform helpers.
    heartbeat = between(original, 'struct Heartbeat {', 'struct Running {')
    running = between(original, 'struct Running {', 'struct Supervisor {')
    supervisor = between(original, 'struct Supervisor {', '    fn spawn(&mut self, state: &mut State)')
    code = r'''
use std::{collections::BTreeMap, ffi::OsString, io, net::Shutdown,
    os::fd::{AsRawFd, FromRawFd, OwnedFd}, os::unix::net::UnixStream, process::{Child, Command},
    sync::{Arc, Mutex, atomic::{AtomicBool, Ordering}}, time::{Duration, Instant}};
macro_rules! warn { ($($arg:tt)*) => {{ eprintln!($($arg)*); }} }
macro_rules! info { ($($arg:tt)*) => {{ eprintln!($($arg)*); }} }
''' + LIBC + r'''
struct TrustedConnection { revoked: AtomicBool }
#[derive(Default)]
struct SupervisorHandle { restart: AtomicBool, running: AtomicBool,
    connection: Mutex<Option<Arc<TrustedConnection>>> }
struct State;
#[allow(dead_code)]
enum TimeoutAction { ToDuration(Duration), ToInstant(Instant) }
'''
    code += between(original, 'const POLL_INTERVAL:', '/// Control the captured')
    # Keep the existing real-child regression quick; the separate policy tests
    # above assert the unscaled production intervals and deadlines.
    heartbeat = heartbeat.replace('from_secs(15)', 'from_secs(1)').replace('from_secs(45)', 'from_secs(3)')
    code += heartbeat + running + supervisor
    code += r'''
    fn spawn(&self, _state: &mut State) -> io::Result<Running> {
        let child = Command::new("python3").arg(self.executable.clone())
            .envs(&self.environment).env_remove("WAYLAND_SOCKET").spawn()?;
        let (revoke, _peer) = UnixStream::pair()?;
        Ok(Running { child, revoke, connection: None, started_at: Instant::now(),
            terminating_at: None, heartbeat: Heartbeat::new(Instant::now()), heartbeat_failed: false })
    }
}
fn start_recovery(_: BTreeMap<OsString, OsString>) -> io::Result<()> { Ok(()) }
'''
    code += between(original, 'fn restart_delay(', '#[cfg(test)]')
    code += r'''
fn main() {
    let dir = std::env::var("TEST_DIR").unwrap();
    let environment = std::env::vars_os().collect();
    let mut supervisor = Supervisor { handle: SupervisorHandle::default(), stopped: false,
        executable: format!("{dir}/shell.py").into(), arguments: vec![], environment,
        login_handoff: None, running: None, failures: 0, retry_at: Instant::now() };
    let mut state = State;
    supervisor.tick(&mut state);
    let pid = supervisor.running.as_ref().unwrap().child.id();
    // A real delayed reply must be accepted over repeated heartbeat rounds.
    let until = Instant::now() + Duration::from_secs(12);
    while Instant::now() < until {
        supervisor.tick(&mut state);
        assert_eq!(supervisor.running.as_ref().unwrap().child.id(), pid);
        std::thread::sleep(Duration::from_millis(50));
    }
    println!("PASS slow live shell survives repeated IPC probes");
    {
        let running = supervisor.running.as_mut().unwrap();
        // A resume/scheduler gap is not evidence of a hung child. Force a
        // previously overdue probe while the supervisor was itself absent.
        let now = Instant::now();
        running.heartbeat.last_tick = now - Duration::from_secs(120);
        running.heartbeat.deadline = now - Duration::from_secs(60);
    }
    supervisor.tick(&mut state);
    assert!(supervisor.running.as_ref().unwrap().terminating_at.is_none());
    assert_eq!(supervisor.running.as_ref().unwrap().child.id(), pid);
    let until = Instant::now() + Duration::from_secs(1);
    while Instant::now() < until {
        supervisor.tick(&mut state);
        assert!(supervisor.running.as_ref().unwrap().terminating_at.is_none());
        std::thread::sleep(Duration::from_millis(50));
    }
    assert_eq!(supervisor.failures, 0);
    println!("PASS supervisor/suspend gap grants a fresh observation window");
    assert_eq!(unsafe { libc::kill(pid as i32, libc::SIGSTOP) }, 0);
    let until = Instant::now() + Duration::from_secs(18);
    while Instant::now() < until {
        supervisor.tick(&mut state);
        if supervisor.running.as_ref().is_some_and(|r| r.child.id() != pid) {
            assert_eq!(supervisor.failures, 1, "hang must consume a bounded retry");
            println!("PASS stopped child retired and fresh shell started with bounded retry");
            return;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    panic!("frozen shell was not restarted");
}
'''
    path = base / 'supervisor.rs'
    path.write_text(code)
    subprocess.run(['rustc', '--edition=2021', '-A', 'warnings', str(path), '-o', str(base / 'test')], check=True)
    runtime = base / 'run'
    runtime.mkdir(mode=0o700)
    env = dict(os.environ, TEST_DIR=str(base), XDG_RUNTIME_DIR=str(runtime),
               WAYLAND_SOCKET='never-inherit-this')
    subprocess.run([str(base / 'test')], env=env, check=True, timeout=45)
