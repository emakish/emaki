#!/usr/bin/env python3
"""Supervisor state tests using socketpairs; no listeners, sessions or real lockers."""
import importlib.machinery
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import select
import socket
import signal
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
import reaper
reaper.guard()  # nothing this test starts outlives it

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parent.parent
loader = importlib.machinery.SourceFileLoader('lock_supervisor', str(ROOT / 'scripts/emaki-lock'))
spec = importlib.util.spec_from_loader(loader.name, loader)
lock = importlib.util.module_from_spec(spec); loader.exec_module(lock)


class Child:
    pid = 123
    returncode = None
    stdout = None
    killed = False
    def poll(self): return self.returncode
    def kill(self): self.killed = True; self.returncode = -9
    def wait(self): return self.returncode


def detached_stderr(directory):
    """Exercise the real launch and SIGTERM log with no listener or session access."""
    directory = directory / 'stderr-owner'; directory.mkdir()
    fixture = '''
import importlib.machinery, importlib.util, sys, time
from pathlib import Path
loader = importlib.machinery.SourceFileLoader('lock_fixture', sys.argv[1])
spec = importlib.util.spec_from_loader(loader.name, loader)
lock = importlib.util.module_from_spec(spec); loader.exec_module(lock)
directory = Path(sys.argv[2])
class Server:
 def bind(self, path): Path(path).touch()
 def listen(self, count): (directory / 'ready').touch()
 def setblocking(self, value): pass
 def close(self): pass
class Selector:
 def register(self, *args): pass
 def select(self, timeout): time.sleep(.01); return []
lock.socket.socket = lambda *args: Server()
lock.selectors.DefaultSelector = Selector
lock.Supervisor.spawn = lambda self, fallback=False: None
lock.Supervisor.recover_orphan = lambda self: False
lock.Supervisor.tick = lambda self: None
lock.runtime_directory = lambda: directory
sys.argv = [sys.argv[1], '--supervise']
raise SystemExit(lock.main())
'''
    popen = subprocess.Popen
    def fixture_spawn(argv, **kwargs):
        return popen([sys.executable, '-B', '-c', fixture, str(ROOT / 'scripts/emaki-lock'), str(directory)], **kwargs)
    read_fd, write_fd = os.pipe()
    saved_stderr = os.dup(2)
    child = None
    try:
        sys.stderr.flush()
        os.dup2(write_fd, 2)
        os.close(write_fd)
        try:
            with patch.object(lock.subprocess, 'Popen', fixture_spawn):
                child, scoped = lock.launch_supervisor(directory, scoped=False)
                assert not scoped
        finally:
            sys.stderr.flush()
            os.dup2(saved_stderr, 2)
            os.close(saved_stderr)
        deadline = time.monotonic() + 3
        while not (directory / 'ready').exists() and time.monotonic() < deadline:
            assert child.poll() is None
            time.sleep(.01)
        assert (directory / 'ready').exists()
        assert child.poll() is None
        assert select.select([read_fd], [], [], .5)[0]
        message = os.read(read_fd, 4096)
        assert b'using setsid fallback without cgroup isolation' in message
        assert select.select([read_fd], [], [], .5)[0], 'supervisor retained caller stderr'
        assert os.read(read_fd, 4096) == b'', 'caller stderr did not reach EOF'
        os.close(read_fd); read_fd = None
        child.send_signal(signal.SIGTERM)
        assert child.wait(timeout=3) == 0, 'SIGTERM logging must not fail after the caller closes its pipe'
    finally:
        if read_fd is not None:
            os.close(read_fd)
        if child and child.poll() is None:
            child.kill(); child.wait()
    print('PASS supervisor releases caller stderr before lock ends; SIGTERM log survives a closed caller pipe')


def run():
    with tempfile.TemporaryDirectory(prefix='lock-unit-', dir=ROOT / '.cache') as value:
        directory = Path(value)
        detached_stderr(directory)
        marker = directory / 'gate-executed'
        for release in (False, True):
            read_fd, write_fd = os.pipe()
            child = subprocess.Popen([sys.executable, '-c', lock.CHILD_GATE, str(read_fd),
                                      sys.executable, '-c', 'from pathlib import Path; Path(' + repr(str(marker)) + ').touch()'],
                                     pass_fds=(read_fd,))
            os.close(read_fd)
            if release:
                os.write(write_fd, b'1')
            os.close(write_fd)
            assert child.wait(timeout=3) == (0 if release else 1)
            assert marker.exists() is release
        marker.unlink()
        print('PASS launch gate refuses EOF and execs only after recorded ownership is released')
        supervisor = lock.Supervisor(directory)
        supervisor.child = Child()
        a, b = socket.socketpair()
        supervisor.connections[a] = dict(trusted=True, buffer=b'', wait=False)
        supervisor.child_socket = a
        supervisor.handle(a, dict(version=1, event='state', secure=True, poured=False, phase='locked'))
        assert supervisor.snapshot()['state'] != 'locked'
        supervisor.handle(a, dict(version=1, event='state', secure=True, poured=True, phase='locked'))
        assert supervisor.snapshot()['state'] == 'locked'
        supervisor.handle(a, dict(version=1, event='authenticated-exit'))
        assert not supervisor.authenticated_exit
        supervisor.handle(a, dict(version=1, event='state', secure=True, poured=True, phase='finished'))
        assert supervisor.snapshot()['state'] != 'locked'
        supervisor.handle(a, dict(version=1, event='authenticated-exit'))
        assert supervisor.authenticated_exit
        supervisor.child.returncode = 0
        supervisor.tick()
        assert supervisor.finished
        b.close()
        print('PASS pour completion, no early exit intent, authenticated normal exit')
        s = lock.Supervisor(directory); s.child = Child(); s.child.returncode = 0
        s.phase = 'finished'; s.authenticated_exit = True
        s.sleep_generation = 2; s.prepared_generation = 1
        restarts = []; s.spawn = lambda fallback=False: restarts.append(fallback)
        s.tick()
        assert restarts == [False] and not s.finished and s.child is None
        print('PASS new wait at authenticated exit starts a new locker after reap')
        for condition in ('death-before', 'death-after', 'hang', 'unconfirmed'):
            s = lock.Supervisor(directory, start_seconds=.1, heartbeat_seconds=.1)
            s.child = Child(); child = s.child
            s.started = s.last_heartbeat = s.readiness_since = time.monotonic() - 1
            if condition == 'death-before': child.returncode = 1
            elif condition == 'death-after': child.returncode = 1; s.secure = s.poured = True; s.phase = 'locked'
            elif condition == 'hang': s.secure = s.poured = True; s.phase = 'locked'
            else: s.secure = True; s.phase = 'locked'; s.last_heartbeat = time.monotonic()
            replacements = []
            s.spawn = lambda fallback=False: replacements.append(fallback)
            s.tick()
            assert replacements == [True] and s.child is None
            assert child.killed == (condition in ('hang', 'unconfirmed'))
            print('PASS', condition, 'reaps old child before fallback')
        for frozen in (True, False):
            s = lock.Supervisor(directory, start_seconds=1, heartbeat_seconds=1)
            s.child = Child(); child = s.child
            s.secure = s.poured = s.established = True; s.phase = 'locked'
            before = time.monotonic() - 15
            s.started = s.readiness_since = before - 5
            s.last_heartbeat = before - .2
            if frozen:
                s.last_tick = before  # supervisor and child frozen together for 15 s (system sleep)
            replacements = []
            s.spawn = lambda fallback=False: replacements.append(fallback)
            s.tick()
            assert (replacements, child.killed) == (([], False) if frozen else ([True], True)), frozen
            if frozen:
                # Shifted by the stall, not reset to now: the heartbeat keeps its age from before it.
                assert .1 < time.monotonic() - s.last_heartbeat < .5, time.monotonic() - s.last_heartbeat
        print('PASS a lock screen frozen with its supervisor in sleep is kept; a real hang is replaced')
        s = lock.Supervisor(directory, start_seconds=1, heartbeat_seconds=1)
        s.child = Child(); s.secure = s.poured = s.established = True; s.phase = 'locked'
        s.last_tick = time.monotonic() - 15
        s.last_heartbeat = s.last_tick - .2
        assert not s.snapshot()['secure']
        s.account_stall()  # what the loop does after select(), before any status reply
        assert s.snapshot()['secure'] and s.snapshot()['state'] == 'locked'
        print('PASS a status reply right after resume does not call the frozen lock screen stale')
        s = lock.Supervisor(directory, start_seconds=1, heartbeat_seconds=1)
        s.last_tick = time.monotonic() - 2  # a slow retire() before this child was spawned
        s.started = s.last_heartbeat = s.readiness_since = time.monotonic() - .1
        now = s.account_stall()
        assert max(s.started, s.last_heartbeat, s.readiness_since) <= now
        print('PASS a child spawned during a slow retire() gets no clock in the future')
        s = lock.Supervisor(directory); s.child = Child()
        s.secure = s.poured = True; s.phase = 'locked'
        s.last_heartbeat -= 10
        assert not s.snapshot()['secure'] and s.snapshot()['state'] != 'locked'
        a, b = socket.socketpair()
        s.connections[a] = dict(trusted=False, buffer=b'', wait=False, deadline=time.monotonic()+2)
        responses = []
        s.respond = lambda conn, value: responses.append(value)
        s.handle(a, dict(version=1, event='authenticated-exit'))
        assert responses == [dict(state='invalid_request')]
        a.close(); b.close()
        print('PASS stale heartbeat and public forged events cannot confirm or unlock')
        for outcome in ('locked', 'deadline', 'failed', 'refused'):
            s = lock.Supervisor(directory); s.child = Child()
            public, peer = socket.socketpair()
            s.connections[public] = dict(trusted=False, buffer=b'', wait=False, deadline=0)
            replies = []; s.respond = lambda conn, value: replies.append(value)
            with patch.object(s, 'prepare_sleep') as prepare:
                before = time.monotonic()
                s.handle(public, dict(version=1, command='confirm'))
                assert before + lock.WAIT_SECONDS <= s.connections[public]['deadline']
                assert s.sleep_generation == 0 and not s.sleep_pending
                prepare.assert_not_called()
            s.secure = True; s.phase = 'locked'
            s.tick()
            assert not replies, 'confirmation returned before full pour'
            if outcome == 'locked':
                s.poured = True
            elif outcome == 'deadline':
                s.connections[public]['deadline'] = 0
            else:
                s.phase = outcome
                s.finished = outcome == 'refused'
            s.tick()
            response, = replies
            assert response['state'] == ('locked' if outcome == 'locked' else 'lock_failed')
            assert s.sleep_generation == 0 and not s.sleep_pending
            public.close(); peer.close(); s.selector.close()
        for mode in ('--confirm', '--wait'):
            for state, secure, poured, expected in (
                    ('locked', True, True, 0), ('locked', True, False, 1),
                    ('locked', False, True, 1), ('lock_failed', False, False, 1),
                    ('invalid_request', False, False, 1)):
                with patch.object(sys, 'argv', ['emaki-lock', mode]), \
                        patch.object(lock, 'runtime_directory', return_value=directory), \
                        patch.object(lock, 'client', return_value=dict(state=state, secure=secure, poured=poured)) as client:
                    assert lock.main() == expected
                    client.assert_called_once_with(directory, mode[2:])
        print('PASS confirm retains capture, waits for full pour, and shares wait failure/deadline/CLI semantics')
        for backend, secure, poured, terminal, attempts, expected in (
                ('quickshell', False, False, False, 0, True),
                ('quickshell', False, False, True, 0, True),
                ('quickshell', True, True, False, 0, False),
                ('quickshell', True, False, False, 0, False),
                ('hyprlock', False, False, False, 1, False),
                ('hyprlock', True, True, False, 1, False),
                ('hyprlock', False, False, True, 1, False)):
            s = lock.Supervisor(directory); child = s.child = Child()
            s.backend = backend; s.secure = secure; s.poured = poured
            s.phase = 'locked' if secure else 'failed' if terminal else 'starting'
            s.finished = terminal; s.fallback_attempts = attempts
            public, peer = socket.socketpair()
            s.connections[public] = dict(trusted=False, buffer=b'', wait=False, deadline=0)
            starts = []; replies = []
            s.respond = lambda conn, value: replies.append(value)
            def start_fallback(fallback=False):
                assert child.killed and s.child is None, 'replacement started before retiring primary'
                starts.append(fallback); s.backend = 'hyprlock'; s.phase = 'starting'
            with patch.object(s, 'spawn', start_fallback), patch.object(s, 'prepare_sleep') as prepare:
                s.handle(public, dict(version=1, command='fallback-wait'))
                assert starts == ([True] if expected else [])
                assert child.killed is expected
                if terminal and attempts:
                    assert replies == [dict(state='failed', secure=False, poured=False)]
                else:
                    assert s.connections[public]['wait'] and s.connections[public]['generation'] == 1
                    assert s.sleep_pending and not s.finished
                    prepare.assert_called_once_with()
            public.close(); peer.close(); s.selector.close()
        s = lock.Supervisor(directory); s.backend = 'hyprlock'; s.phase = 'recovering'
        s.fallback_attempts = 1; s.retry_at = time.monotonic() + 2
        public, peer = socket.socketpair()
        s.connections[public] = dict(trusted=False, buffer=b'', wait=False, deadline=0)
        replies = []; s.respond = lambda conn, value: replies.append(value)
        s.handle(public, dict(version=1, command='fallback-wait'))
        assert replies == [dict(state='lock_failed', secure=False, poured=False)]
        assert not s.connections[public]['wait'], 'fallback retry must not wait for a new primary cycle'
        public.close(); peer.close(); s.selector.close()
        with patch.object(sys, 'argv', ['emaki-lock', '--fallback', '--wait']), \
                patch.object(lock, 'runtime_directory', return_value=directory), \
                patch.object(lock, 'client', return_value=dict(state='locked', secure=True, poured=True)) as client:
            assert lock.main() == 0
            client.assert_called_once_with(directory, 'fallback-wait')
        print('PASS sleep fallback retires only an unconfirmed primary, preserves confirmed locks, and never duplicates hyprlock')
        s = lock.Supervisor(directory); s.child = Child()
        trusted, trusted_peer = socket.socketpair(); public, public_peer = socket.socketpair()
        s.connections[trusted] = dict(trusted=True, buffer=b'', wait=False)
        s.connections[public] = dict(trusted=False, buffer=b'', wait=False, deadline=time.monotonic()+2)
        s.prepare_sleep = lambda: None
        replies = []; s.respond = lambda conn, value: replies.append(value)
        s.handle(public, dict(version=1, command='wait'))
        s.handle(trusted, dict(version=1, event='state', secure=True, poured=True, phase='locked', sleepGeneration=0))
        s.tick(); assert not replies
        s.handle(trusted, dict(version=1, event='state', secure=True, poured=True, phase='locked', sleepGeneration=1))
        s.tick(); assert replies[0]['state'] == 'locked'
        s.connections.pop(public)
        s.started -= 100
        s.handle(trusted, dict(version=1, event='state', secure=True, poured=False, phase='locked'))
        replaced = []; s.spawn = lambda fallback=False: replaced.append(fallback)
        s.tick(); assert not replaced
        for conn in (trusted, trusted_peer, public, public_peer): conn.close()
        print('PASS sleep barrier rejects queued old state; hotplug preserves established lock')
        s = lock.Supervisor(directory, start_seconds=.1); s.child = Child()
        s.started = s.readiness_since = time.monotonic() - 100
        s.secure = True; s.poured = False; s.phase = 'locked'; s.outputs_active = False
        replacements = []; s.spawn = lambda fallback=False: replacements.append(fallback)
        s.tick(); assert not replacements and s.child.poll() is None
        s.outputs_active = True; s.established = True
        s.tick(); assert not replacements and s.child.poll() is None
        s.poured = True
        assert s.snapshot()['state'] == 'locked'
        s.tick(); assert not replacements
        s.phase = 'drain'
        public, peer = socket.socketpair()
        s.connections[public] = dict(trusted=False, buffer=b'', wait=False, deadline=time.monotonic()+2)
        requests = []; replies = []
        s.prepare_sleep = lambda: requests.append(s.sleep_generation)
        s.respond = lambda conn, value: replies.append(value)
        s.handle(public, dict(version=1, command='lock'))
        assert requests == [1] and s.sleep_pending and not s.authenticated_exit
        public.close(); peer.close()
        print('PASS logical poured confirmation needs no frame counter despite advisory outputsActive; drain lock revokes success')
        with patch.dict(os.environ, XDG_RUNTIME_DIR=str(directory), WAYLAND_DISPLAY='wayland-one'):
            first = lock.runtime_directory()
            with patch.dict(os.environ, WAYLAND_DISPLAY=str(directory / 'wayland-one')):
                assert lock.runtime_directory() == first
            with patch.dict(os.environ, WAYLAND_DISPLAY='wayland-two'):
                assert lock.runtime_directory() != first
        captured = []
        def fake_spawn(*args, **kwargs): captured.append(kwargs['env']); return Child()
        with patch.dict(os.environ, LC_ALL='ru_RU.UTF-8', LANG='ru_RU.UTF-8', LC_CTYPE='ru_RU.UTF-8', WAYLAND_DEBUG='1', MALLOC_CONF='thp:always'):
            with patch.object(lock.subprocess, 'Popen', fake_spawn), patch.object(lock, 'process_identity', return_value={'start': 'fixture', 'boot': 'fixture'}):
                lock.Supervisor(directory).spawn()
        assert captured[0]['LC_MESSAGES'] == 'C' and 'LC_ALL' not in captured[0]
        assert captured[0]['LC_CTYPE'] == 'ru_RU.UTF-8' and captured[0]['LANG'] == 'ru_RU.UTF-8'
        assert 'WAYLAND_DEBUG' not in captured[0]
        assert captured[0]['MALLOC_CONF'] == 'thp:always'
        print('PASS per-compositor namespaces; stable PAM messages retain Unicode locale and disable trace')
        service = (ROOT / 'systemd/emaki-shell.service').read_text()
        assert 'Environment=MALLOC_CONF=' not in service
        for original in (None, '', 'thp:always,dirty_decay_ms:123'):
            environment = dict(os.environ)
            environment.pop('MALLOC_CONF', None)
            if original is not None:
                environment['MALLOC_CONF'] = original
            captured.clear()
            with patch.dict(os.environ, environment, clear=True):
                with patch.object(lock.subprocess, 'Popen', fake_spawn), patch.object(lock, 'process_identity', return_value={'start': 'fixture', 'boot': 'fixture'}):
                    lock.Supervisor(directory).spawn()
            assert captured[0]['MALLOC_CONF'] == (original or 'thp:never')
        print('PASS lock launch defaults unset/empty allocator config and preserves user tuning; unit does not override it')
        # Mod+L, the idle policy and the power menu start the locker from niri's
        # environment, the session menu from emaki-shell's (which exports the variable).
        for inherited in (None, '1'):
            environment = dict(os.environ)
            environment.pop('QS_DISABLE_CRASH_HANDLER', None)
            if inherited is not None:
                environment['QS_DISABLE_CRASH_HANDLER'] = inherited
            captured.clear()
            with patch.dict(os.environ, environment, clear=True):
                with patch.object(lock.subprocess, 'Popen', fake_spawn), patch.object(lock, 'process_identity', return_value={'start': 'fixture', 'boot': 'fixture'}):
                    lock.Supervisor(directory).spawn()
            assert captured[0].get('QS_DISABLE_CRASH_HANDLER') == '1', (inherited, captured[0].get('QS_DISABLE_CRASH_HANDLER'))
        print('PASS the Quickshell locker runs without the crash handler whoever requested the lock')
        s = lock.Supervisor(directory); s.child = Child(); s.backend = 'hyprlock'
        read, write = os.pipe(); stream = os.fdopen(read, 'rb', buffering=0)
        os.write(write, b'noise\nnoise onLockLocked called\n')
        s.read_fallback(stream)
        assert not s.secure
        os.write(write, b'\x1b[1;32mDEBUG \x1b[0m]: onLockLocked called\n')
        s.read_fallback(stream)
        assert s.snapshot()['state'] == 'locked'
        stream.close(); os.close(write)
        print('PASS fallback requires exact native compositor-locked callback')

        s = lock.Supervisor(directory); s.child = Child(); s.child.returncode = 0
        s.backend = 'hyprlock'; s.secure = False
        starts = []; s.spawn = lambda fallback=False: starts.append(fallback)
        s.tick()
        assert s.finished and s.phase == 'refused' and not starts and s.retry_at is None
        print('PASS fallback exit is terminal without marker; refusal never retries')
        # Crash of both lockers: a new quickshell->hyprlock cycle after 2, 5, 10 s, then stop.
        s = lock.Supervisor(directory)
        starts = []; s.spawn = lambda fallback=False: starts.append(fallback)
        for attempt, delay in enumerate(lock.RETRY_BACKOFF):
            s.child = Child(); s.child.returncode = -11 if attempt else 1
            s.backend = 'hyprlock'; s.secure = attempt == 1; s.phase = 'locked' if attempt == 1 else 'starting'
            before = time.monotonic()
            s.tick()
            assert not s.finished and s.child is None and s.snapshot()['state'] == 'recovering'
            assert not s.snapshot()['secure'] and not s.snapshot()['poured']
            assert before + delay <= s.retry_at <= time.monotonic() + delay, (attempt, delay)
            s.tick(); assert starts == [False] * attempt, 'no restart before the delay'
            s.retry_at = time.monotonic() - 1
            s.tick(); assert starts == [False] * (attempt + 1) and s.retry_at is None
        s.child = Child(); s.child.returncode = 1; s.backend = 'hyprlock'; s.secure = False
        s.tick()
        assert s.finished and s.phase == 'failed' and len(starts) == len(lock.RETRY_BACKOFF)
        print('PASS both lockers crashed: bounded retry with backoff, then failure')
        # A waiter during the backoff gets its answer at its own deadline, not before.
        s = lock.Supervisor(directory); s.child = Child(); s.child.returncode = 1; s.backend = 'hyprlock'
        public, peer = socket.socketpair()
        s.connections[public] = dict(trusted=False, buffer=b'', wait=True, deadline=time.monotonic() + 100, generation=0)
        replies = []; s.respond = lambda conn, value: replies.append(value)
        s.tick(); assert not replies and s.retry_at is not None
        s.connections[public]['deadline'] = 0
        s.tick(); assert replies == [dict(state='lock_failed', backend='hyprlock', secure=False, poured=False)]
        public.close(); peer.close()
        print('PASS a waiter outlives a crash only until its own deadline')
        # A live fallback that never confirmed blocks later requests until a lock request replaces it.
        s = lock.Supervisor(directory, start_seconds=.1); s.child = Child(); child = s.child
        s.backend = 'hyprlock'; s.fallback_attempts = 1; s.started = time.monotonic() - 1
        s.tick(); assert s.phase == 'failed' and not s.finished and s.child is child
        public, peer = socket.socketpair()
        s.connections[public] = dict(trusted=False, buffer=b'', wait=False, deadline=time.monotonic() + 2)
        replies = []; s.respond = lambda conn, value: replies.append(value)
        starts = []
        def restart(fallback=False):
            starts.append(fallback); s.phase = 'starting'; s.backend = 'quickshell'
        s.spawn = restart
        s.handle(public, dict(version=1, command='lock'))
        assert child.killed and starts == [False] and s.child is None and replies[0]['state'] == 'starting'
        public.close(); peer.close()
        print('PASS a lock request replaces a failed, unconfirmed live fallback with a fresh locker')
        # A fresh cycle may use the fallback again.
        s = lock.Supervisor(directory); s.fallback_attempts = 1
        with patch.object(lock.subprocess, 'Popen', lambda *a, **k: Child()), patch.object(lock, 'process_identity', return_value={'start': 'f', 'boot': 'f'}):
            s.spawn(False)
        assert s.fallback_attempts == 0
        print('PASS a new quickshell cycle resets the fallback budget')
        s = lock.Supervisor(directory); s.child = Child(); s.child.returncode = 0
        s.backend = 'hyprlock'; s.secure = s.poured = True; s.phase = 'locked'
        s.tick()
        assert s.finished and s.phase == 'unlocked'
        s = lock.Supervisor(directory); s.child = Child(); s.phase = 'lost'
        assert s.snapshot()['state'] == 'recovering'
        s.backend = 'hyprlock'; s.phase = 'starting'
        assert s.snapshot()['state'] == 'recovering'
        public, peer = socket.socketpair()
        s.connections[public] = dict(trusted=False, buffer=b'', wait=False, deadline=time.monotonic()+2)
        replies = []; s.respond = lambda conn, value: replies.append(value)
        s.finished = True; s.phase = 'refused'
        s.handle(public, dict(version=1, command='lock'))
        assert replies == [dict(state='refused', secure=False, poured=False)]
        public.close(); peer.close()
        for state in ('refused', 'recovering', 'stopping'):
            with patch.object(sys, 'argv', ['emaki-lock']), patch.object(lock, 'runtime_directory', return_value=directory):
                with patch.object(lock, 'client', return_value=dict(state=state, secure=False, poured=False)):
                    assert lock.main() == 1
        print('PASS confirmed fallback unlock, markerless refusal and recovery have distinct public results')
        captured = []
        with patch.dict(os.environ, EMAKI_LOCK_REDUCED_MOTION='1'):
            with patch.object(lock.subprocess, 'Popen', lambda argv, **kwargs: captured.append((argv, kwargs))):
                lock.launch_supervisor(directory)
        argv, kwargs = captured[0]
        assert argv[:4] == ['systemd-run', '--user', '--scope', '--quiet']
        assert '--slice=app.slice' in argv and '--supervise' == argv[-1]
        assert '--property=TimeoutStopSec=5s' in argv
        assert kwargs['stderr'] == subprocess.DEVNULL
        assert kwargs['env']['EMAKI_LOCK_REDUCED_MOTION'] == '1'
        print('PASS isolated scope launch explicitly preserves reduced-motion setting')
        previous = signal.getsignal(signal.SIGTERM)
        observed = []
        def observe_stop(supervisor):
            handler = signal.getsignal(signal.SIGTERM)
            handler(signal.SIGTERM, None)
            with patch.object(lock.subprocess, 'Popen', side_effect=AssertionError('must not spawn while stopping')):
                supervisor.spawn(True)
                supervisor.tick()
            observed.append((supervisor.stopping, supervisor.finished, supervisor.phase))
        try:
            with patch.object(sys, 'argv', ['emaki-lock', '--supervise']), patch.object(lock, 'runtime_directory', return_value=directory):
                with patch.object(lock.Supervisor, 'run', observe_stop):
                    assert lock.main() == 0
            assert observed == [(True, True, 'stopping')]
        finally:
            signal.signal(signal.SIGTERM, previous)
        print('PASS SIGTERM stops supervision without spawning into the stopping scope')
        for content in ('{invalid', '[]', 'null', '{"pid":1}', '{"pid":"123"}',
                        '{"pid":123,"backend":"quickshell","identity":{"start":null,"boot":"fixture"}}'):
            path = directory / 'child.json'; path.write_text(content)
            log = io.StringIO()
            with contextlib.redirect_stderr(log), patch.object(lock, 'process_identity', side_effect=AssertionError('invalid identity cannot select a process')):
                assert not lock.Supervisor(directory).recover_orphan()
            assert not path.exists() and 'discarded invalid child identity' in log.getvalue()
            assert content not in log.getvalue()
        print('PASS malformed orphan records are logged and removed without signalling any process')
        # Identity mismatch is harmless even when that PID is alive; the pidfd
        # recovery path then kills precisely the matching process we started.
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        try:
            s = lock.Supervisor(directory)
            identity = lock.process_identity(child.pid)
            assert identity is not None
            record = dict(pid=child.pid, backend='quickshell', identity=dict(identity, start='wrong'))
            (directory / 'child.json').write_text(json.dumps(record))
            assert not s.recover_orphan() and child.poll() is None
            record['identity'] = identity
            (directory / 'child.json').write_text(json.dumps(record))
            assert s.recover_orphan() and child.wait(timeout=2) == -9
            assert not (directory / 'child.json').exists()
        finally:
            if child.poll() is None:
                child.kill(); child.wait()
        print('PASS persisted orphan identity and pidfd retirement reject PID reuse')
        class Reply:
            def __init__(self, response): self.response = response
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def settimeout(self, value): self.timeout = value
            def sendall(self, value): pass
            def recv(self, count): return self.response
        with patch.object(lock, 'connect', side_effect=[Reply(b''), Reply(lock.wire(dict(state='starting')))]):
            assert lock.client(directory, 'lock')['state'] == 'starting'
        print('PASS EOF at supervisor completion retries the explicit request once')
        with patch.object(lock, 'connect', side_effect=[Reply(lock.wire(dict(state='finished'))), Reply(lock.wire(dict(state='starting')))]):
            assert lock.client(directory, 'lock')['state'] == 'starting'
        print('PASS terminal reply at supervisor completion retries the explicit request once')
        log = io.StringIO(); calls = []
        def absent_scope(argv, **kwargs):
            calls.append((argv, kwargs))
            if argv[0] == 'systemd-run':
                raise FileNotFoundError('private exception text')
            return Child()
        with contextlib.redirect_stderr(log), patch.object(lock.subprocess, 'Popen', absent_scope):
            _, scoped = lock.launch_supervisor(directory)
        assert not scoped and len(calls) == 2 and calls[1][0][-1] == '--supervise'
        assert calls[1][1]['start_new_session'] and 'cgroup isolation' in log.getvalue()
        assert 'private exception text' not in log.getvalue()
        print('PASS missing systemd-run starts a logged setsid supervisor')
        calls.clear()
        with contextlib.redirect_stderr(log), patch.object(lock.subprocess, 'Popen', absent_scope):
            _, scoped = lock.launch_supervisor(directory, fallback=True)
        assert not scoped and len(calls) == 2
        assert all('--fallback' in argv for argv, _ in calls)
        with patch.object(lock, 'connect', side_effect=[None, Reply(lock.wire(dict(state='locked', secure=True, poured=True)))]), \
                patch.object(lock, 'launch_supervisor', return_value=(Child(), True)) as launch:
            assert lock.client(directory, 'fallback-wait')['state'] == 'locked'
            launch.assert_called_once_with(directory, fallback=True)
        print('PASS fallback request starts hyprlock directly with or without a user scope')
        failed = Child(); failed.returncode = 1
        starts = []
        def scope_launch(directory, scoped=True):
            starts.append(scoped)
            return (failed if scoped else Child()), scoped
        with patch.object(lock, 'launch_supervisor', scope_launch):
            with patch.object(lock, 'connect', side_effect=[None, None, Reply(lock.wire(dict(state='starting')))]):
                assert lock.client(directory, 'lock')['state'] == 'starting'
        assert starts == [True, False]
        print('PASS failed user manager launch falls back once to setsid before reporting failure')
        class Clock:
            now = 0
            def monotonic(self): return self.now
            def sleep(self, duration): self.now += duration
        clock = Clock(); starts = []
        reply = Reply(lock.wire(dict(state='locked', secure=True, poured=True)))
        exited = Child(); exited.returncode = 0
        def raced_launch(directory, scoped=True):
            starts.append(scoped)
            return exited, scoped
        def raced_connect(directory):
            return reply if len(starts) == 2 else None
        with patch.object(lock, 'connect', raced_connect), patch.object(lock, 'launch_supervisor', raced_launch):
            with patch.object(lock.time, 'monotonic', clock.monotonic), patch.object(lock.time, 'sleep', clock.sleep):
                assert lock.client(directory, 'wait')['state'] == 'locked'
        assert starts == [True, True] and clock.now < .2
        assert reply.timeout == lock.WAIT_SECONDS + 1
        clock = Clock(); starts = []
        with patch.object(lock, 'connect', return_value=None), patch.object(lock, 'launch_supervisor', raced_launch):
            with patch.object(lock.time, 'monotonic', clock.monotonic), patch.object(lock.time, 'sleep', clock.sleep):
                assert lock.client(directory, 'wait')['state'] == 'lock_failed'
        assert starts == [True, True] and clock.now < .2
        print('PASS exited launcher retries immediately once; wait budget is intact and a repeated race fails promptly')
        s = lock.Supervisor(directory)
        s.fallback_attempts = 1
        with patch.object(lock.subprocess, 'Popen', side_effect=AssertionError('must not retry')):
            s.spawn(True)
        assert s.finished and s.phase == 'failed'
        print('PASS recovery attempt cap cannot relock after foreign owner exits')


if __name__ == '__main__':
    run()
