#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile the packaged supervisor and exercise only owned mock shell processes."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--patch', type=Path, default=ROOT / 'packaging/niri-emaki/0010-trusted-authentication-prompt.patch')
parser.add_argument('--regressions-only', action='store_true')
args = parser.parse_args()
PATCH = args.patch


def source():
    text = PATCH.read_text().split('+++ b/src/trusted_client.rs\n', 1)[1]
    text = text.split('\ndiff --git ', 1)[0]
    return '\n'.join(line[1:] for line in text.splitlines() if line.startswith('+'))


def between(text, start, end):
    return text[text.index(start):text.index(end)]



def regressions():
    """Exercise production timing and worker failures without host session IPC."""
    original = source()
    heartbeat = between(original, 'struct Heartbeat {', 'struct Running {')
    with tempfile.TemporaryDirectory(prefix='emaki-probe-policy-') as directory:
        base = Path(directory)
        probe = base / 'trusted-qs'
        probe.write_text('#!/bin/sh\nexit 0\n')
        probe.chmod(0o700)
        malicious = base / 'qs'
        malicious.write_text('#!/bin/sh\nexit 1\n')
        malicious.chmod(0o700)
        # Delay the actual pre_exec handoff, and fault the first owned child's
        # wait persistently. These hooks leave production scheduling untouched.
        heartbeat = heartbeat.replace('probe.try_wait()', 'probe.test_try_wait()')
        code = r"""
use std::{collections::BTreeMap, ffi::OsString, io, process::{Child, Command, Stdio},
    os::unix::process::CommandExt, sync::{Arc, atomic::{AtomicBool, AtomicU32, Ordering}},
    time::{Duration, Instant}};
static WAIT_ERROR: AtomicBool = AtomicBool::new(false);
static BAD_PID: AtomicU32 = AtomicU32::new(0);
static SLOW_SPAWN: AtomicBool = AtomicBool::new(false);
trait TestWait { fn test_try_wait(&mut self) -> io::Result<Option<std::process::ExitStatus>>; }
impl TestWait for Child {
    fn test_try_wait(&mut self) -> io::Result<Option<std::process::ExitStatus>> {
        if WAIT_ERROR.load(Ordering::Relaxed) {
            let _ = BAD_PID.compare_exchange(0, self.id(), Ordering::Relaxed, Ordering::Relaxed);
            if BAD_PID.load(Ordering::Relaxed) == self.id() {
                return Err(io::Error::from_raw_os_error(10));
            }
        }
        self.try_wait()
    }
}
mod utils { pub mod signals { pub fn unblock_all() -> std::io::Result<()> { Ok(()) } } }
fn restore_nofile_rlimit() {
    if SLOW_SPAWN.load(Ordering::Relaxed) {
        std::thread::sleep(Duration::from_millis(300));
    }
}
""" + heartbeat + r"""
fn tick(h: &mut Heartbeat, now: Instant, env: &BTreeMap<OsString, OsString>) {
    h.last_tick = now; // Advance the policy clock without fabricating a suspend.
    assert!(h.tick(now, std::process::id(), env));
}
fn complete(h: &mut Heartbeat, now: Instant, env: &BTreeMap<OsString, OsString>) {
    let until = Instant::now() + Duration::from_secs(2);
    while Instant::now() < until {
        tick(h, now, env);
        if h.probe.is_none() { return; }
        std::thread::sleep(Duration::from_millis(10));
    }
    panic!("probe slot remained occupied after completion or wait failure");
}
fn main() {
    let case = std::env::args().nth(1).unwrap();
    let env = std::env::vars_os().collect();
    let now = Instant::now();
    let mut h = Heartbeat::new(now);
    match case.as_str() {
        "timing" => {
            assert_eq!(h.next_probe.duration_since(now), Duration::from_secs(15));
            assert_eq!(h.deadline.duration_since(now), Duration::from_secs(30));
            let round = now + Duration::from_secs(15);
            tick(&mut h, round, &env);
            assert_eq!(h.next_probe.duration_since(round), Duration::from_secs(15));
            complete(&mut h, round, &env);
            assert_eq!(h.deadline.duration_since(round), Duration::from_secs(45));
        }
        "trusted-path" => {
            let round = now + Duration::from_secs(15);
            tick(&mut h, round, &env);
            complete(&mut h, round, &env);
            assert!(h.deadline > now + Duration::from_secs(30), "snapshot PATH probe was used");
        }
        "worker-spawn" => {
            SLOW_SPAWN.store(true, Ordering::Relaxed);
            let started = Instant::now();
            tick(&mut h, now + Duration::from_secs(15), &env);
            assert!(started.elapsed() < Duration::from_millis(150), "spawn blocked dispatch");
            complete(&mut h, now + Duration::from_secs(15), &env);
        }
        "wait-error" => {
            WAIT_ERROR.store(true, Ordering::Relaxed);
            let round = now + Duration::from_secs(15);
            tick(&mut h, round, &env);
            complete(&mut h, round, &env);
            let bad = BAD_PID.load(Ordering::Relaxed);
            assert_ne!(bad, 0);
            let proc = format!("/proc/{bad}");
            let until = Instant::now() + Duration::from_secs(2);
            while std::path::Path::new(&proc).exists() && Instant::now() < until {
                std::thread::sleep(Duration::from_millis(10));
            }
            assert!(!std::path::Path::new(&proc).exists(), "failed child was not reaped");
            // Keep this a policy test of retry after failure, not expiry.
            h.deadline = now + Duration::from_secs(90);
            let round = now + Duration::from_secs(30);
            tick(&mut h, round, &env);
            complete(&mut h, round, &env);
            assert_eq!(h.deadline, round + Duration::from_secs(45));
        }
        _ => panic!("unknown regression"),
    }
}
"""
        path = base / 'policy.rs'
        path.write_text(code)
        env = dict(os.environ, NIRI_EMAKI_QS_PROGRAM=str(probe))
        # Compile the production heartbeat itself: an absent, empty or relative
        # package identity must fail before any compositor can be started.
        for value in (None, '', 'qs', './qs'):
            invalid = env.copy()
            invalid.pop('NIRI_EMAKI_QS_PROGRAM', None)
            if value is not None:
                invalid['NIRI_EMAKI_QS_PROGRAM'] = value
            result = subprocess.run(
                ['rustc', '--edition=2021', '-A', 'warnings', str(path),
                 '-o', str(base / 'invalid-policy')],
                env=invalid, capture_output=True, text=True, timeout=30)
            assert result.returncode != 0, f'invalid probe identity compiled: {value!r}'
            assert 'NIRI_EMAKI_QS_PROGRAM' in result.stderr, result.stderr
        print('PASS build rejects missing, empty and relative probe identities', flush=True)
        subprocess.run(['rustc', '--edition=2021', '-A', 'warnings', str(path), '-o', str(base / 'policy')], env=env, check=True)
        failures = []
        for case in ('timing', 'trusted-path', 'worker-spawn', 'wait-error'):
            result = subprocess.run([str(base / 'policy'), case], env=dict(env, PATH=str(base)), timeout=5)
            print(('PASS ' if result.returncode == 0 else 'FAIL ') + case, flush=True)
            if result.returncode:
                failures.append(case)
        qml = (ROOT / 'shell/shell.qml').read_text()
        import re
        ping = re.search(r'target: "system"\s+function ping\(\): string\s*{\s*return "pong";\s*}', qml)
        trivial = ping and '"call", "system", "ping"' in heartbeat
        print(('PASS ' if trivial else 'FAIL ') + 'constant system.ping', flush=True)
        if not trivial:
            failures.append('constant system.ping')
        if failures:
            raise SystemExit('Failed regressions: ' + ', '.join(failures))


regressions()
if args.regressions_only:
    raise SystemExit(0)


with tempfile.TemporaryDirectory(prefix='emaki-shell-heartbeat-') as directory:
    base = Path(directory)
    # The transport is private test files: no graphical session or host IPC.
    (base / 'shell.py').write_text('''import os, pathlib, time
p = pathlib.Path(os.environ['TEST_DIR'])
while True:
    request = p / ('request-' + str(os.getpid()))
    if request.exists():
        token = request.read_text()
        time.sleep(.2)
        (p / ('reply-' + str(os.getpid()))).write_text(token)
    time.sleep(.01)
''')
    probe = base / 'qs'
    probe.write_text('''#!/usr/bin/env python3
import os, pathlib, sys, time
assert sys.argv[1:3] == ['ipc', '--pid']
assert sys.argv[4:] == ['call', 'system', 'ping']
assert 'WAYLAND_SOCKET' not in os.environ
p = pathlib.Path(os.environ['TEST_DIR'])
token = str(os.getpid())
(p / ('request-' + sys.argv[3])).write_text(token)
reply = p / ('reply-' + sys.argv[3])
while not reply.exists() or reply.read_text() != token:
    time.sleep(.01)
''')
    probe.chmod(0o700)
    original = source()
    # Compile production policy verbatim, replacing only compositor insertion and
    # platform helpers. This also runs the old supervisor in a before-fix checkout.
    heartbeat = (between(original, 'struct Heartbeat {', 'struct Running {')
                 if 'struct Heartbeat {' in original else '')
    running = between(original, 'struct Running {', 'struct Supervisor {')
    # FX19b made spawn take &mut self (the login handoff is consumed only after a successful spawn).
    spawn_marker = next(marker for marker in ('    fn spawn(&mut self, state: &mut State)',
                                              '    fn spawn(&self, state: &mut State)') if marker in original)
    supervisor = between(original, 'struct Supervisor {', spawn_marker)
    constructor = ('heartbeat: Heartbeat::new(Instant::now()), heartbeat_failed: false,'
                   if heartbeat else '')
    # FX19b: the supervisor holds the login handoff until the first successful spawn; the heartbeat has none.
    supervisor_extra = ' login_handoff: None,' if 'login_handoff' in supervisor else ''
    code = r'''
use std::{collections::BTreeMap, ffi::OsString, io, net::Shutdown,
    os::unix::{net::UnixStream, process::CommandExt}, process::{Child, Command, Stdio},
    sync::{Arc, Mutex, atomic::{AtomicBool, Ordering}}, time::{Duration, Instant}};
macro_rules! warn { ($($arg:tt)*) => {{ eprintln!($($arg)*); }} }
macro_rules! info { ($($arg:tt)*) => {{ eprintln!($($arg)*); }} }
mod libc {
    pub type pid_t = i32;
    pub const SIGTERM: i32 = 15;
    pub const SIGSTOP: i32 = 19;
    unsafe extern "C" { pub fn kill(pid: pid_t, signal: i32) -> i32; }
}
mod utils { pub mod signals { pub fn unblock_all() -> std::io::Result<()> { Ok(()) } } }
fn restore_nofile_rlimit() {}
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
            .envs(&self.environment).spawn()?;
        let (revoke, _peer) = UnixStream::pair()?;
        Ok(Running { child, revoke, connection: None, started_at: Instant::now(),
            terminating_at: None, CONSTRUCTOR })
    }
}
fn start_recovery(_: BTreeMap<OsString, OsString>) -> io::Result<()> { Ok(()) }
'''.replace('CONSTRUCTOR', constructor)
    code += between(original, 'fn restart_delay(', '#[cfg(test)]')
    code += r'''
fn main() {
    let dir = std::env::var("TEST_DIR").unwrap();
    let environment = std::env::vars_os().collect();
    let mut supervisor = Supervisor { handle: SupervisorHandle::default(), stopped: false,
        executable: format!("{dir}/shell.py").into(), arguments: vec![], environment,
        running: None, failures: 0, retry_at: Instant::now(),SUPERVISOR_EXTRA };
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
    GAP_TEST
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
'''.replace('SUPERVISOR_EXTRA', supervisor_extra).replace('GAP_TEST', r'''
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
''' if heartbeat else '')
    path = base / 'supervisor.rs'
    path.write_text(code)
    subprocess.run(['rustc', '--edition=2021', '-A', 'warnings', str(path), '-o', str(base / 'test')], env=dict(os.environ, NIRI_EMAKI_QS_PROGRAM=str(probe)), check=True)
    env = dict(os.environ, TEST_DIR=str(base), PATH=str(base) + os.pathsep + os.environ['PATH'],
               WAYLAND_SOCKET='never-inherit-this')
    subprocess.run([str(base / 'test')], env=env, check=True, timeout=45)
