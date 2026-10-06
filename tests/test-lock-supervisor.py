#!/usr/bin/env python3
"""Isolated real sockets/processes; never connects to Wayland, PAM or a session bus."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import reaper
reaper.guard()  # nothing this test starts outlives it

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
ROOT.joinpath('.cache').mkdir(exist_ok=True)
SANDBOX = os.environ.get('EMAKI_TEST_SANDBOX') == '1'
loader = importlib.machinery.SourceFileLoader('emaki_lock', str(ROOT / 'scripts/emaki-lock'))
spec = importlib.util.spec_from_loader(loader.name, loader)
lock = importlib.util.module_from_spec(spec)
loader.exec_module(lock)
# Restricted authoring environments may forbid bind(2); still run the socketpair
# state tests, and retain the real process/listener suite for VM/host acceptance.
subprocess.run([sys.executable, str(ROOT / 'tests/test-lock-supervisor-unit.py')], check=True)
try:
    with socket.socket(socket.AF_UNIX) as probe:
        probe_path = Path(tempfile.gettempdir()) / ('emaki-lock-probe-' + str(os.getpid()))
        probe.bind(str(probe_path))
    probe_path.unlink()
except PermissionError:
    if not SANDBOX:
        raise SystemExit('FAIL real Unix-listener integration: bind(2) denied; EMAKI_TEST_SANDBOX=1 is required to opt out explicitly')
    print('BLOCKED real Unix-listener integration (explicit EMAKI_TEST_SANDBOX=1); rerun without opt-in outside sandbox')
    raise SystemExit(0)

FAKE = '''#!/usr/bin/env python3
import json,os,socket,sys,time,select
from pathlib import Path
r=Path(os.environ['LOCK_FIXTURE'])
name=Path(sys.argv[0]).name
with (r/'calls').open('a') as f:f.write(name+' '+str(os.getpid())+'\\n')
if name=='hyprlock':
 if (r/'fallback-fail').exists():sys.exit(1)
 if (r/'fallback-success').exists():sys.exit(0)
 if not (r/'fallback-silent').exists():
  # Noise/outgoing requests must never be interpreted as lock confirmation.
  print('noise onLockLocked called',flush=True)
  time.sleep(.08)
  print('DEBUG from core ]: onLockLocked called',file=sys.stderr,flush=True)
 while True:time.sleep(.1)
mode=(r/'mode').read_text()
if mode=='die-before':sys.exit(1)
s=socket.socket(socket.AF_UNIX);s.connect(os.environ['EMAKI_LOCK_SOCKET'])
def send(**v):s.sendall((json.dumps(dict(version=1,**v))+'\\n').encode())
send(event='state',secure=False,poured=False,phase='pour')
if mode=='late':time.sleep(.22)
if mode=='unconfirmed':
 while True:
  send(event='state',secure=True,poured=False,phase='locked');time.sleep(.05)
send(event='state',secure=True,poured=True,phase='locked')
if mode=='die-after':time.sleep(.12);sys.exit(1)
if mode=='normal':
 time.sleep(.12)
 send(event='state',secure=True,poured=True,phase='finished')
 send(event='authenticated-exit');sys.exit(0)
generation=0
while True:
 if select.select([s],[],[],0)[0]:
  data=s.recv(4096)
  if not data:
   if mode=='orphan':
    while True:time.sleep(.1)
   sys.exit(1)
  for line in data.splitlines():generation=max(generation,json.loads(line).get('generation',0))
 if mode!='hang':send(event='state',secure=True,poured=True,phase='locked',sleepGeneration=generation)
 time.sleep(.05)
'''


def until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('deadline')


# Use /tmp explicitly: inheriting CI's TMPDIR makes the longest AF_UNIX
# pathname 110 bytes, exceeding Linux's 107-byte pathname limit.
with tempfile.TemporaryDirectory(prefix='emaki-lock-', dir='/tmp') as value:
    base = Path(value)
    commands = base / 'bin'; commands.mkdir()
    for name in ('qs', 'hyprlock'):
        path = commands / name; path.write_text(FAKE); path.chmod(0o700)
    os.environ['PATH'] = str(commands) + ':' + os.environ['PATH']
    os.environ.pop('WAYLAND_DISPLAY', None)
    os.environ.pop('NIRI_SOCKET', None)
    os.environ.pop('DBUS_SESSION_BUS_ADDRESS', None)
    for mode in ('late', 'unconfirmed', 'die-before', 'die-after', 'hang', 'normal'):
        directory = base / mode; directory.mkdir(mode=0o700)
        (directory / 'mode').write_text(mode)
        os.environ['LOCK_FIXTURE'] = str(directory)
        supervisor = lock.Supervisor(directory, start_seconds=.6, heartbeat_seconds=.35)
        thread = threading.Thread(target=supervisor.run)
        thread.start()
        try:
            until(lambda: supervisor.child is not None)
            assert (directory / 'control.sock').stat().st_mode & 0o777 == 0o600
            if mode == 'late':
                # Public peers cannot forge state or authenticated-exit even at the same uid.
                with lock.connect(directory) as attacker:
                    attacker.sendall(lock.wire(dict(event='state', secure=True, poured=True, phase='locked')))
                    assert json.loads(attacker.recv(4096))['state'] == 'invalid_request'
                assert lock.client(directory, 'status')['state'] != 'locked'
                begin = time.monotonic()
                result = lock.client(directory, 'wait')
                assert time.monotonic() - begin > .12 and result['secure'] and result['poured']
                # A second owner loses flock and cannot spawn a second locker.
                other = lock.Supervisor(directory)
                other.run()
                assert (directory / 'calls').read_text().count('qs ') == 1
            elif mode == 'normal':
                until(lambda: supervisor.finished)
                assert 'hyprlock' not in (directory / 'calls').read_text()
            else:
                if mode in ('die-after', 'hang'):
                    until(lambda: supervisor.secure)
                until(lambda: supervisor.backend == 'hyprlock' and supervisor.secure)
                result = lock.client(directory, 'wait')
                assert result['state'] == 'locked' and result['backend'] == 'hyprlock'
                assert (directory / 'calls').read_text().count('hyprlock ') == 1
                assert (directory / 'calls').read_text().count('qs ') == 1
            print('PASS', mode)
        finally:
            supervisor.finished = True
            thread.join(timeout=2)
            assert not thread.is_alive()
            supervisor.retire(kill=True)
    # A fallback which lives but supplies no compositor ack must never pass --wait.
    directory = base / 'silent'; directory.mkdir(mode=0o700)
    (directory / 'mode').write_text('die-before')
    (directory / 'fallback-silent').touch()
    os.environ['LOCK_FIXTURE'] = str(directory)
    supervisor = lock.Supervisor(directory, start_seconds=.3)
    thread = threading.Thread(target=supervisor.run); thread.start()
    try:
        until(lambda: supervisor.backend == 'hyprlock' and supervisor.child is not None)
        result = lock.client(directory, 'wait')
        assert result['state'] == 'lock_failed' and not result['secure']
        print('PASS alive fallback without acknowledgement fails closed')
    finally:
        supervisor.finished = True; thread.join(timeout=2); supervisor.retire(kill=True)
    directory = base / 'exit-0'; directory.mkdir(mode=0o700)
    (directory / 'mode').write_text('die-before')
    (directory / 'fallback-success').touch()
    os.environ['LOCK_FIXTURE'] = str(directory)
    supervisor = lock.Supervisor(directory, start_seconds=.3)
    thread = threading.Thread(target=supervisor.run); thread.start()
    try:
        until(lambda: supervisor.finished)
        thread.join(timeout=2)
        assert not thread.is_alive() and supervisor.phase == 'refused'
        calls = (directory / 'calls').read_text()
        assert calls.count('qs ') == calls.count('hyprlock ') == 1
        time.sleep(.2)
        assert (directory / 'calls').read_text() == calls
        print('PASS fallback exit 0 without marker is terminal and cannot retry')
    finally:
        supervisor.finished = True; thread.join(timeout=2); supervisor.retire(kill=True)
    # Both lockers crash: new cycles after each backoff step, then terminal failure.
    directory = base / 'exit-1'; directory.mkdir(mode=0o700)
    (directory / 'mode').write_text('die-before')
    (directory / 'fallback-fail').touch()
    os.environ['LOCK_FIXTURE'] = str(directory)
    supervisor = lock.Supervisor(directory, start_seconds=.3)
    with patch.object(lock, 'RETRY_BACKOFF', (.2, .2, .2)):
        thread = threading.Thread(target=supervisor.run); thread.start()
        try:
            until(lambda: supervisor.retry_at is not None)
            assert not supervisor.finished and lock.client(directory, 'status')['state'] == 'recovering'
            calls = (directory / 'calls').read_text()
            assert calls.count('qs ') == calls.count('hyprlock ') == 1
            until(lambda: supervisor.finished, timeout=6)
            thread.join(timeout=2)
            assert not thread.is_alive() and supervisor.phase == 'failed'
            calls = (directory / 'calls').read_text()
            assert calls.count('qs ') == calls.count('hyprlock ') == 1 + len(lock.RETRY_BACKOFF), calls
            time.sleep(.3)
            assert (directory / 'calls').read_text() == calls
            print('PASS crashed lockers are retried through the backoff steps, then stop')
        finally:
            supervisor.finished = True; thread.join(timeout=2); supervisor.retire(kill=True)
    # An exception in an event handler must not unlink the socket or orphan the child.
    directory = base / 'exception'; directory.mkdir(mode=0o700)
    (directory / 'mode').write_text('late'); os.environ['LOCK_FIXTURE'] = str(directory)
    supervisor = lock.Supervisor(directory, start_seconds=.6, heartbeat_seconds=.35)
    original = supervisor.read_connection
    injected = []
    def failing_once(conn):
        if not injected:
            injected.append(True)
            raise RuntimeError('private exception text must not be logged')
        return original(conn)
    supervisor.read_connection = failing_once
    thread = threading.Thread(target=supervisor.run); thread.start()
    try:
        until(lambda: supervisor.secure)
        assert injected and lock.client(directory, 'wait')['state'] == 'locked'
        assert (directory / 'calls').read_text().count('qs ') == 1
        print('PASS unexpected handler exception retains ownership and supervision')
    finally:
        supervisor.finished = True; thread.join(timeout=2); supervisor.retire(kill=True)
    # Kill only the supervisor we started. Its living fake locker is retained,
    # then the next owner identifies and reaps that exact PID before replacement.
    for termination in (signal.SIGTERM, signal.SIGKILL):
        runtime = base / ('runtime-' + str(termination)); runtime.mkdir(mode=0o700)
        os.environ['XDG_RUNTIME_DIR'] = str(runtime)
        directory = lock.runtime_directory()
        (directory / 'mode').write_text('orphan'); os.environ['LOCK_FIXTURE'] = str(directory)
        owners = []
        try:
            first = subprocess.Popen([sys.executable, str(ROOT / 'scripts/emaki-lock'), '--supervise'], stderr=subprocess.PIPE)
            owners.append(first)
            until(lambda: lock.client(directory, 'status')['state'] == 'locked')
            old = json.loads((directory / 'child.json').read_text())
            first.send_signal(termination)
            first.wait(timeout=2)
            log = first.stderr.read().decode()
            if termination == signal.SIGTERM:
                assert first.returncode == 0 and 'no fallback started in the stopping scope' in log
                assert (directory / 'calls').read_text().count('hyprlock ') == 0
            assert lock.process_identity(old['pid']) == old['identity']
            second = subprocess.Popen([sys.executable, str(ROOT / 'scripts/emaki-lock'), '--supervise'])
            owners.append(second)
            until(lambda: lock.client(directory, 'status').get('backend') == 'hyprlock' and lock.client(directory, 'status')['state'] == 'locked')
            assert lock.process_identity(old['pid']) is None
            assert (directory / 'calls').read_text().count('hyprlock ') == 1
            print('PASS supervisor', termination.name, 'exits promptly; next request retires the exact orphan before one fallback')
        finally:
            for owner in owners:
                if owner.poll() is None:
                    owner.kill(); owner.wait(timeout=2)
                if owner.stderr:
                    owner.stderr.close()
            # The record belongs to the fake process tree this test just created.
            lock.Supervisor(directory).recover_orphan()
    # A fake user-manager failure must still produce one working standalone
    # supervisor. No real systemd executable or session bus is contacted.
    failed_scope = commands / 'systemd-run'
    failed_scope.write_text('#!' + sys.executable + '\nraise SystemExit(1)\n')
    failed_scope.chmod(0o700)
    runtime = base / 'runtime-scope-failure'; runtime.mkdir(mode=0o700)
    os.environ['XDG_RUNTIME_DIR'] = str(runtime)
    directory = lock.runtime_directory()
    (directory / 'mode').write_text('orphan'); os.environ['LOCK_FIXTURE'] = str(directory)
    owners = []
    real_popen = subprocess.Popen
    def owned_popen(*args, **kwargs):
        # Files cannot fill a pipe or wait for EOF from a still-running owner.
        # Override DEVNULL only in this fixture; production stays detached.
        with (directory / f'launcher-{len(owners)}.stderr').open('wb') as stderr:
            kwargs['stderr'] = stderr
            process = real_popen(*args, **kwargs)
        owners.append(process)
        return process
    try:
        with patch.object(lock.subprocess, 'Popen', owned_popen):
            result = lock.client(directory, 'wait')
        calls = (directory / 'calls').read_text() if (directory / 'calls').exists() else ''
        diagnostics = dict(result=result, calls=calls, socket_path=str(directory / 'control.sock'), launchers=[
            dict(argv=owner.args, returncode=owner.poll(),
                 stderr=(directory / f'launcher-{index}.stderr').read_text(errors='replace'))
            for index, owner in enumerate(owners)])
        assert result['state'] == 'locked' and result['secure'] and result['poured'], diagnostics
        assert len(owners) == 2 and owners[0].returncode == 1, diagnostics
        assert calls.count('qs ') == 1, diagnostics
        print('PASS failed fake systemd user manager falls back to one confirmed setsid locker')
    finally:
        for owner in owners:
            if owner.poll() is None:
                owner.terminate(); owner.wait(timeout=2)
        lock.Supervisor(directory).recover_orphan()
    assert lock.LOCKED_EVENT.fullmatch(b'onLockLocked called')
    assert lock.LOCKED_EVENT.fullmatch(b'DEBUG from core ]: onLockLocked called')
    for invalid in (b'locked', b'noise onLockLocked called', b'[ 100.0] ext_session_lock_v1@21.locked()'):
        assert not lock.LOCKED_EVENT.fullmatch(invalid)
    print('PASS strict fallback acknowledgement parser; no public unlock')
print('ALL GREEN')
