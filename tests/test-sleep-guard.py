#!/usr/bin/env python3
"""Sleep guard behaviour with fakes for logind and for every command it runs.

Nothing here opens a bus, locks, suspends or ends a session: the logind proxy is a
fake object and subprocess.run is patched in every test.
"""
import ast
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import reaper
reaper.guard()  # nothing this test starts outlives it

sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
GUARD = runpy.run_path(str(ROOT / 'scripts/emaki-sleep-guard'))
SESSION = '/org/freedesktop/login1/session/_37'
BOOT = Path('/proc/sys/kernel/random/boot_id').read_text().strip()


class Reply:
    def __init__(self, value):
        self.value = value

    def unpack(self):
        return self.value


class Logind:
    """The calls the guard makes on the login1 Manager proxy, recorded in order."""

    def __init__(self, events):
        self.events = events
        self.sessions = {'7': SESSION}

    def call_with_unix_fd_list_sync(self, method, params, *_rest):
        from gi.repository import Gio, GLib
        what, _who, _why, mode = params.unpack()
        reader, writer = os.pipe()
        os.close(reader)
        fds = Gio.UnixFDList.new()
        handle = fds.append(writer)
        os.close(writer)
        self.events.append(f'inhibit {what} {mode}')
        return GLib.Variant('(h)', (handle,)), fds

    def call_sync(self, method, params, *_rest):
        from gi.repository import GLib
        if method != 'GetSession':
            raise AssertionError(method)
        session, = params.unpack()
        self.events.append(f'GetSession {session}')
        if session not in self.sessions:
            raise GLib.Error('no such session')
        return Reply((self.sessions[session],))


class GuardCase(unittest.TestCase):
    def guard(self, **options):
        self.events = []
        guard = GUARD['SleepGuard'](Logind(self.events), **options)
        self.addCleanup(guard.release)
        self.addCleanup(guard.release_lid)
        guard.acquire()
        self.events.clear()
        return guard

    def commands(self, results=None):
        """Patch subprocess.run; record argv and return rc from results by program name."""
        results = results or {}

        def run(argv, **kwargs):
            self.events.append(' '.join(argv))
            outcome = results.get(' '.join(argv), results.get(argv[0], 0))
            if isinstance(outcome, BaseException):
                raise outcome
            return subprocess.CompletedProcess(argv, outcome)
        return patch('subprocess.run', side_effect=run)


class SessionLock(GuardCase):
    def test_lock_signal_starts_the_locker(self):
        guard = self.guard()
        with self.commands():
            guard.session_signal('Lock')
        self.assertEqual(self.events, ['/usr/bin/emaki-lock'])

    def test_unlock_signal_is_ignored(self):
        # Nothing may open the session without the password, logind included.
        guard = self.guard()
        with self.commands():
            guard.session_signal('Unlock')
            guard.session_signal('PauseDevice')
        self.assertEqual(self.events, [])

    def test_a_failing_locker_does_not_stop_the_guard(self):
        for failure in (1, subprocess.TimeoutExpired('emaki-lock', 1), FileNotFoundError('missing')):
            with self.subTest(failure=failure):
                guard = self.guard()
                with self.commands({'/usr/bin/emaki-lock': failure}), patch('sys.stderr') as errors:
                    guard.session_signal('Lock')
                self.assertTrue(errors.write.called)
                self.assertIsNotNone(guard.inhibitor)

    def test_session_comes_from_the_environment(self):
        events = []
        path = GUARD['session_path'](Logind(events), {'XDG_SESSION_ID': '7'}, lambda: self.fail('no fallback'))
        self.assertEqual((path, events), (SESSION, ['GetSession 7']))

    def test_session_falls_back_to_the_users_display(self):
        # A user service has XDG_SESSION_ID only when the session imported it.
        events = []
        path = GUARD['session_path'](Logind(events), {}, lambda: ('7', SESSION))
        self.assertEqual((path, events), (SESSION, []))

    def test_no_session_means_no_subscription(self):
        self.assertIsNone(GUARD['session_path'](Logind([]), {}, lambda: ('', '/')))

    def test_only_the_lock_member_is_subscribed(self):
        calls = []

        class Connection:
            def signal_subscribe(self, *args):
                calls.append(args)
                return 1

        GUARD['subscribe_lock'](Connection(), SESSION, lambda name: None)
        (sender, interface, member, path, arg0, _flags, _callback), = calls
        self.assertEqual((sender, interface, member, path, arg0),
                         ('org.freedesktop.login1', 'org.freedesktop.login1.Session', 'Lock', SESSION, None))




class FlagCase(GuardCase):
    """Private state and runtime directories for the notice flags, and a failing lock."""

    def setUp(self):
        self.state = Path(ROOT / '.cache' / f'guard-state-{os.getpid()}')
        self.runtime = Path(ROOT / '.cache' / f'guard-runtime-{os.getpid()}')
        for folder in (self.state, self.runtime):
            folder.mkdir(exist_ok=True)
            for item in folder.rglob('*'):
                if item.is_file():
                    item.unlink()
        # Stands in for niri's IPC socket: niri removes it when it exits.
        self.socket = self.runtime / 'niri.wayland-1.4242.sock'
        self.socket.touch()
        self.environ = {'XDG_STATE_HOME': str(self.state), 'XDG_RUNTIME_DIR': str(self.runtime),
                        'NIRI_SOCKET': str(self.socket)}

    def policy_guard(self, policy, environ=None):
        # Timers the guard sets (GLib's in main()) are recorded here and run by the test.
        self.scheduled = []
        guard = self.guard(policy=policy, environ=environ or self.environ,
                           later=lambda seconds, callback: self.scheduled.append((seconds, callback)))
        # logind's 20 s would make every end-session failure wait 19 s here.
        guard.delay_window = 1.5
        return guard

    def state_flag(self):
        return self.state / 'emaki' / 'sleep-lock-failed'

    def runtime_flag(self):
        return self.runtime / 'emaki-sleep-lock-failed'

    def niri_quits(self, code=1):
        """The real niri on quit: it stops its loop before it answers, so the command fails
        ("error communicating with niri", exit 1) although niri exits and removes its socket."""
        def outcome():
            self.socket.unlink()
            return code
        return outcome

    def fail_lock(self, guard, failure, extra=None):
        results = {'/usr/bin/emaki-lock --wait': failure, **(extra or {})}
        held = []

        def run(argv, **kwargs):
            self.events.append(' '.join(argv))
            held.append((argv[0], guard.inhibitor is not None))
            outcome = results.get(' '.join(argv), results.get(argv[0], 0))
            if callable(outcome):
                outcome = outcome()
            if isinstance(outcome, BaseException):
                raise outcome
            return subprocess.CompletedProcess(argv, outcome)
        with patch('subprocess.run', side_effect=run), patch('sys.stderr') as errors:
            guard.prepare_for_sleep(True)
        self.assertTrue(errors.write.called)
        return held


class FailurePolicy(FlagCase):
    """What the guard does when the lock is not confirmed before sleep: one switch.

    The default is today's behaviour ('sleep'). The other values exist so that the
    owner's answer to the lid-close question is one value, not new code.
    """
    FAILURES = {'exit': 1, 'timeout': subprocess.TimeoutExpired('emaki-lock', 1), 'missing': FileNotFoundError('x')}

    def test_the_default_is_todays_behaviour(self):
        self.assertEqual(GUARD['DEFAULT_FAILURE_POLICY'], 'sleep')
        self.assertEqual(GUARD['failure_policy']({}), 'sleep')
        with patch('sys.stderr') as errors:
            self.assertEqual(GUARD['failure_policy']({'EMAKI_SLEEP_LOCK_FAILURE': 'nonsense'}), 'sleep')
        self.assertTrue(errors.write.called)
        for policy in GUARD['FAILURE_POLICIES']:
            self.assertEqual(GUARD['failure_policy']({'EMAKI_SLEEP_LOCK_FAILURE': policy}), policy)
        self.assertEqual(set(GUARD['FAILURE_POLICIES']), {'sleep', 'sleep-relock', 'end-session', 'stay-awake'})
        self.assertEqual(self.guard().policy, 'sleep')

    def test_sleep_logs_flags_and_releases(self):
        # The default still sleeps unlocked; the person is told after waking.
        for name, failure in self.FAILURES.items():
            with self.subTest(failure=name):
                self.runtime_flag().unlink(missing_ok=True)
                guard = self.policy_guard('sleep')
                self.fail_lock(guard, failure)
                self.assertEqual(self.events, ['/usr/bin/emaki-lock --wait'])
                self.assertIsNone(guard.inhibitor)
                self.assertEqual(self.runtime_flag().read_text(), 'sleep\n')
                self.assertFalse(self.state_flag().exists())
                with self.commands():
                    guard.prepare_for_sleep(False)
                self.assertEqual(self.events[1:], ['inhibit sleep delay'])

    def test_sleep_relock_blanks_flags_and_locks_first_after_wake(self):
        for name, failure in self.FAILURES.items():
            with self.subTest(failure=name):
                guard = self.policy_guard('sleep-relock')
                held = self.fail_lock(guard, failure)
                self.assertEqual(self.events, ['/usr/bin/emaki-lock --wait', 'niri msg action power-off-monitors'])
                self.assertEqual(held[1], ('niri', True), 'monitors go off before the delay is released')
                self.assertIsNone(guard.inhibitor)
                self.assertEqual(self.runtime_flag().read_text(), 'sleep-relock\n')
                self.assertFalse(self.state_flag().exists())
                with self.commands():
                    guard.prepare_for_sleep(False)
                self.assertEqual(self.events[2:], ['/usr/bin/emaki-lock', 'inhibit sleep delay'])
                guard.release()
                with self.commands():
                    guard.prepare_for_sleep(False)
                self.assertEqual(self.events[4:], ['inhibit sleep delay'], 'relock happens once')

    def test_end_session_quits_niri_and_keeps_the_delay_until_stopped(self):
        # niri's exit code says nothing (see niri_quits); its socket going away does.
        for name, failure in self.FAILURES.items():
            for code in (1, 0):
                with self.subTest(failure=name, quit_exit=code):
                    self.socket.touch()
                    self.state_flag().unlink(missing_ok=True)
                    guard = self.policy_guard('end-session')
                    held = self.fail_lock(guard, failure, {'niri': self.niri_quits(code)})
                    self.assertEqual(self.events, ['/usr/bin/emaki-lock --wait', 'niri msg action quit --skip-confirmation'])
                    self.assertEqual(held[1], ('niri', True))
                    # The session has ended; systemd stops this service with it and
                    # main() releases then. logind's InhibitDelayMaxSec bounds the wait.
                    self.assertIsNotNone(guard.inhibitor)
                    # The flag names the session that ended, so only a later one shows it.
                    self.assertEqual(self.state_flag().read_text(),
                                     f'end-session\nboot={BOOT}\nniri={self.socket}\n')
                    self.assertFalse(self.runtime_flag().exists())

    def test_end_session_waits_for_a_slow_niri_while_holding_the_delay(self):
        guard = self.policy_guard('end-session')
        later = threading.Timer(0.2, self.socket.unlink)
        later.start()
        self.addCleanup(later.cancel)
        self.fail_lock(guard, 1, {'niri': 1})
        self.assertFalse(self.socket.exists())
        self.assertIsNotNone(guard.inhibitor)
        self.assertEqual(self.state_flag().read_text().splitlines()[0], 'end-session')
        self.assertFalse(self.runtime_flag().exists())

    def test_end_session_that_did_not_end_says_so_and_releases(self):
        # niri answered (or not) but kept running: the session goes on, unlocked. The
        # guard holds the delay until one second before logind's own deadline, then
        # releases, and the notice says the session was not ended.
        outcomes = {'exit 0': 0, 'exit 1': 1, 'missing': FileNotFoundError('niri'),
                    'timeout': subprocess.TimeoutExpired('niri', 5)}
        for name, outcome in outcomes.items():
            with self.subTest(niri=name):
                self.runtime_flag().unlink(missing_ok=True)
                self.state_flag().unlink(missing_ok=True)
                guard = self.policy_guard('end-session')
                started = time.monotonic()
                self.fail_lock(guard, 1, {'niri': outcome})
                waited = time.monotonic() - started
                self.assertGreaterEqual(waited, guard.delay_window - 1 - 0.05)
                self.assertTrue(self.socket.exists())
                self.assertIsNone(guard.inhibitor)
                self.assertEqual(self.runtime_flag().read_text(), 'end-session-failed\n')
                # niri was asked to quit and may still go after the release; the runtime
                # directory goes with the session, so the state directory keeps it too.
                self.assertEqual(self.state_flag().read_text(),
                                 f'end-session-failed\nboot={BOOT}\nniri={self.socket}\n')
                self.assertEqual([seconds for seconds, _callback in self.scheduled], [GUARD['LATE_END_WAIT']])

    def test_end_session_without_a_niri_socket_cannot_tell_an_ended_session(self):
        environ = dict(self.environ)
        del environ['NIRI_SOCKET']
        guard = self.policy_guard('end-session', environ)
        started = time.monotonic()
        self.fail_lock(guard, 1, {'niri': 1})
        # Without a socket `niri msg` reaches no niri: nothing is asked to quit, at once.
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(self.events, ['/usr/bin/emaki-lock --wait'])
        self.assertIsNone(guard.inhibitor)
        self.assertEqual(self.runtime_flag().read_text(), 'end-session-failed\n')
        self.assertFalse(self.state_flag().exists())
        self.assertEqual(self.scheduled, [])

    def test_end_session_with_a_niri_socket_that_is_not_there_quits_nothing(self):
        # NIRI_SOCKET names a path that does not exist when the lock fails (a stale import,
        # a niri other than the one on screen): no niri answers there, and its absence after
        # a quit would say nothing. The guard must not report the running session as ended.
        self.socket.unlink()
        guard = self.policy_guard('end-session')
        started = time.monotonic()
        self.fail_lock(guard, 1, {'niri': 1})
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(self.events, ['/usr/bin/emaki-lock --wait'])
        self.assertIsNone(guard.inhibitor, 'nothing ends, so the delay is not held to the deadline')
        self.assertEqual(self.runtime_flag().read_text(), 'end-session-failed\n')
        self.assertFalse(self.state_flag().exists())
        self.assertEqual(self.scheduled, [])

    def test_stay_awake_holds_the_lid_and_suspends_only_after_a_confirmed_lock(self):
        guard = self.policy_guard('stay-awake')
        guard.release(); guard.release_lid(); self.events.clear(); guard.acquire()
        self.assertEqual(self.events, ['inhibit sleep delay', 'inhibit handle-lid-switch block'])
        self.addCleanup(guard.release_lid)
        self.events.clear()
        with self.commands():
            guard.lid_closed(True, docked=False)
        self.assertEqual(self.events, ['/usr/bin/emaki-lock --wait', '/usr/bin/systemctl suspend'])
        self.assertIsNotNone(guard.lid_inhibitor, 'the lid inhibitor is held for the whole session')
        self.events.clear()
        with self.commands():
            guard.lid_closed(False, docked=False)
            guard.lid_closed(True, docked=True)
        self.assertEqual(self.events, [], 'lid open and docked lid close do nothing')

    def test_stay_awake_never_suspends_after_a_failed_lock(self):
        for name, failure in self.FAILURES.items():
            with self.subTest(failure=name):
                guard = self.policy_guard('stay-awake')
                with self.commands({'/usr/bin/emaki-lock --wait': failure}), patch('sys.stderr') as errors:
                    guard.lid_closed(True, docked=False)
                self.assertEqual(self.events, ['/usr/bin/emaki-lock --wait'])
                self.assertTrue(errors.write.called)
                self.assertEqual(self.runtime_flag().read_text(), 'stay-awake\n')
                # Sleep requested by other means cannot be refused: lock attempt, log, release.
                self.events.clear()
                self.fail_lock(guard, failure)
                self.assertEqual(self.events, ['/usr/bin/emaki-lock --wait'])
                self.assertIsNone(guard.inhibitor)

    def test_no_policy_sleeps_by_itself(self):
        # The rule "never suspend automatically": only stay-awake runs systemctl, and only
        # from a lid close (a human gesture that logind would otherwise act on) after a
        # confirmed lock. Checked above; here: no policy sleeps from a failed lock.
        for policy in GUARD['FAILURE_POLICIES']:
            for failure in self.FAILURES.values():
                guard = self.policy_guard(policy)
                self.fail_lock(guard, failure)
                self.assertFalse(any('suspend' in event or 'hibernate' in event for event in self.events), (policy, self.events))
                guard.release()
                with self.commands():
                    guard.prepare_for_sleep(False)

    def test_action_policies_leave_time_for_the_action(self):
        # logind waits InhibitDelayMaxSec=20 at most; today's 18 s stays for 'sleep'.
        self.assertEqual(self.guard().lock_timeout, 18)
        for policy in ('sleep-relock', 'end-session', 'stay-awake'):
            self.assertEqual(self.policy_guard(policy).lock_timeout, 12)
        # The guard's window is logind's, and end-session keeps time to see niri go.
        logind = (ROOT / 'systemd/logind.conf.d/50-emaki.conf').read_text()
        self.assertIn(f"InhibitDelayMaxSec={GUARD['DELAY_WINDOW']}\n", logind)
        self.assertEqual(self.guard().delay_window, GUARD['DELAY_WINDOW'])
        self.assertLessEqual(GUARD['ACTION_LOCK_TIMEOUT'] + GUARD['ACTION_TIMEOUT'] + 2, GUARD['DELAY_WINDOW'] - 1)

    def test_success_leaves_no_flag_under_any_policy(self):
        for policy in GUARD['FAILURE_POLICIES']:
            guard = self.policy_guard(policy)
            with self.commands():
                guard.prepare_for_sleep(True)
            self.assertEqual(self.events, ['/usr/bin/emaki-lock --wait'])
            self.assertFalse(self.state_flag().exists() or self.runtime_flag().exists())
            guard.release()


class NoticeAcrossSessions(FlagCase):
    """The guard's flags read by the shell's real helper (shell/helpers/system-tools.py,
    op sleep-lock-flags). The shell asks it with session_start=true at every start of
    the shell, a restart inside the same session included, and with false when the
    runtime flag appears. Only fakes: no lock, no sleep, no session ends."""
    HELPER = ROOT / 'shell/helpers/system-tools.py'
    LATER = '/run/user/1000/niri.wayland-1.5151.sock'

    def notices(self, niri, session_start):
        environ = dict(os.environ, XDG_STATE_HOME=str(self.state), XDG_RUNTIME_DIR=str(self.runtime),
                       NIRI_SOCKET=niri)
        done = subprocess.run([sys.executable, '-B', str(self.HELPER)], check=True, capture_output=True, text=True,
                              input=json.dumps({'op': 'sleep-lock-flags', 'session_start': session_start}) + '\n',
                              env=environ, timeout=10)
        reply = json.loads(done.stdout)
        self.assertEqual(reply['state'], 'ready', reply)
        return reply['policies']

    def test_an_ended_session_is_told_once_in_the_next_session_only(self):
        guard = self.policy_guard('end-session')
        self.fail_lock(guard, 1, {'niri': self.niri_quits()})
        # A shell restarted under the same niri (same boot, same socket) is not the next login.
        self.assertEqual(self.notices(str(self.socket), True), [])
        self.assertTrue(self.state_flag().exists())
        self.assertEqual(self.notices(self.LATER, False), [], 'the runtime path never takes the state flag')
        self.assertEqual(self.notices(self.LATER, True), ['end-session'])
        self.assertFalse(self.state_flag().exists())
        self.assertEqual(self.notices(self.LATER, True), [], 'told once')

    def test_a_session_that_was_not_ended_is_told_at_once(self):
        guard = self.policy_guard('end-session')
        self.fail_lock(guard, 1, {'niri': 1})
        self.assertEqual(self.notices(str(self.socket), False), ['end-session-failed'])
        self.assertEqual(self.notices(str(self.socket), True), [])

    def test_a_stale_niri_socket_never_tells_of_an_ended_session(self):
        # The guard's NIRI_SOCKET is not there (stale import, another niri): nothing quits,
        # the live session (its shell has the real socket) is told at once that it was not
        # ended, and no later session hears that one was.
        self.socket.unlink()
        guard = self.policy_guard('end-session')
        self.fail_lock(guard, 1, {'niri': 1})
        live = '/run/user/1000/niri.wayland-1.777.sock'
        self.assertEqual(self.notices(live, False), ['end-session-failed'])
        self.assertEqual(self.notices(live, True), [])
        self.assertEqual(self.notices(self.LATER, True), [])

    def test_a_session_that_ends_after_the_guard_gave_up_is_told_in_the_next_one(self):
        # niri goes only after the guard's wait (slow, or frozen by the sleep that followed
        # the release); the runtime directory with its flag is removed at logout.
        guard = self.policy_guard('end-session')
        self.fail_lock(guard, 1, {'niri': 1})
        self.socket.unlink()
        self.runtime_flag().unlink()
        # systemd stops the guard with the session: its check never runs.
        self.assertEqual(self.notices(self.LATER, True), ['end-session-failed'])
        self.assertEqual(self.notices(self.LATER, True), [], 'told once')

    def test_the_guard_drops_the_next_sessions_flag_once_this_one_went_on(self):
        # LATE_END_WAIT after the release niri is still there and the shell has taken the
        # runtime flag (the person was told): the next login is not told again.
        guard = self.policy_guard('end-session')
        self.fail_lock(guard, 1, {'niri': 1})
        (_seconds, check), = self.scheduled
        self.assertEqual(self.notices(str(self.socket), False), ['end-session-failed'])
        self.assertFalse(check(), 'the check runs once')
        self.assertFalse(self.state_flag().exists())
        self.assertEqual(self.notices(self.LATER, True), [])

    def test_the_next_sessions_flag_stays_while_this_one_is_not_told_or_has_ended(self):
        for case in ('runtime flag not taken', 'niri gone'):
            with self.subTest(case):
                for flag in (self.state_flag(), self.runtime_flag()):
                    flag.unlink(missing_ok=True)
                self.socket.touch()
                guard = self.policy_guard('end-session')
                self.fail_lock(guard, 1, {'niri': 1})
                (_seconds, check), = self.scheduled
                if case == 'niri gone':
                    self.socket.unlink()
                    self.runtime_flag().unlink()
                check()
                self.assertTrue(self.state_flag().exists())
                # The runtime directory goes at logout, with a flag no shell took.
                self.runtime_flag().unlink(missing_ok=True)
                self.assertEqual(self.notices(self.LATER, True), ['end-session-failed'])

    def test_the_check_leaves_a_newer_flag_alone(self):
        guard = self.policy_guard('end-session')
        self.fail_lock(guard, 1, {'niri': 1})
        (_seconds, check), = self.scheduled
        self.runtime_flag().unlink()
        self.state_flag().write_text('end-session\nboot=another-boot\nniri=/run/user/1000/niri.other.sock\n')
        check()
        self.assertEqual(self.state_flag().read_text().splitlines()[0], 'end-session')

    def test_a_flag_from_another_boot_or_an_older_guard_is_told(self):
        flag = self.state_flag()
        flag.parent.mkdir(parents=True, exist_ok=True)
        for text in ('end-session\n', f'end-session\nboot=another-boot\nniri={self.socket}\n'):
            with self.subTest(flag=text):
                flag.write_text(text)
                self.assertEqual(self.notices(str(self.socket), True), ['end-session'])
                self.assertFalse(flag.exists())

    def test_every_flag_name_has_a_known_name_and_its_own_sentence(self):
        names = set(GUARD['FAILURE_POLICIES']) | {GUARD['SESSION_NOT_ENDED']}
        known, = (ast.literal_eval(node.value) for node in ast.parse(self.HELPER.read_text()).body
                  if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == 'SLEEP_LOCK_POLICIES')
        self.assertEqual(set(known), names)
        store = (ROOT / 'shell/NotificationStore.qml').read_text()
        function = store[store.index('function systemSleepLock'):]
        function = function[:function.index('\n    }\n')]
        # 'sleep' is the plain sentence every unknown name gets too.
        self.assertEqual(set(re.findall(r'policy === "([a-z-]+)"', function)), names - {'sleep'})


class LogindProxy(Logind):
    """The login1 Manager proxy main() builds: the calls of Logind, what main() connects to,
    and at every Inhibit whether systemd has been told anything yet."""

    def __init__(self, events, notified, refuse=False):
        super().__init__(events)
        self.notified, self.refuse = notified, refuse

    def call_with_unix_fd_list_sync(self, method, params, *rest):
        from gi.repository import GLib
        self.events.append(f'notified before Inhibit: {self.notified()}')
        if self.refuse:
            raise GLib.Error('Access denied')
        return super().call_with_unix_fd_list_sync(method, params, *rest)

    def connect(self, *_args):
        return 1

    def get_connection(self):
        return self

    def signal_subscribe(self, *_args):
        return 1

    def get_cached_property(self, _name):
        return None


class Readiness(unittest.TestCase):
    """Type=notify: main() sends READY=1 with RESTART_RESET=1 once logind's delay inhibitor is
    held, and nothing when it never gets that far, so systemd's RestartSteps= slow down only a
    guard that cannot start. Fakes for logind and the main loop; systemd's notify socket is a
    datagram socket of this test."""

    def setUp(self):
        directory = tempfile.mkdtemp(prefix='notify-', dir=ROOT / '.cache')
        self.addCleanup(shutil.rmtree, directory, True)
        self.path = os.path.join(directory, 'notify')
        self.receiver = self.listen(self.path)

    def listen(self, address):
        receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(receiver.close)
        receiver.bind(address)
        return receiver

    def notified(self):
        try:
            self.receiver.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        except BlockingIOError:
            return False
        return True

    def messages(self, receiver=None):
        found = []
        while True:
            try:
                found.append((receiver or self.receiver).recv(4096, socket.MSG_DONTWAIT).decode())
            except BlockingIOError:
                return found

    def run_main(self, environ, refuse=False, no_bus=False):
        """main() with a main loop that returns at once; its status. self.events: what logind
        and the loop saw, self.logged: the guard's log."""
        from gi.repository import GLib
        self.events = []
        proxy = LogindProxy(self.events, self.notified, refuse)
        test = self

        class Loop:
            def run(self):
                test.events.append(f'notified before the loop: {test.notified()}')
                test.events.append(f'NOTIFY_SOCKET left for children: {"NOTIFY_SOCKET" in os.environ}')

            def quit(self):
                pass

        def bus(*_args):
            if no_bus:
                raise GLib.Error('Could not connect: No such file or directory')
            return proxy
        with patch.object(GUARD['Gio'].DBusProxy, 'new_for_bus_sync', side_effect=bus), \
                patch.object(GUARD['GLib'], 'MainLoop', Loop), patch.object(GUARD['GLib'], 'unix_signal_add'), \
                patch.dict(os.environ, {'XDG_SESSION_ID': '7', **environ}), patch('sys.stderr') as errors:
            os.environ.pop('EMAKI_SLEEP_LOCK_FAILURE', None)
            status = GUARD['main']()
        self.logged = ''.join(call.args[0] for call in errors.write.call_args_list)
        return status

    def test_ready_and_restart_reset_once_the_inhibitor_is_held(self):
        self.assertEqual(self.run_main({'NOTIFY_SOCKET': self.path}), 0, self.logged)
        self.assertEqual(self.events, ['notified before Inhibit: False', 'inhibit sleep delay', 'GetSession 7',
                                       'notified before the loop: True', 'NOTIFY_SOCKET left for children: False'])
        # One message: systemd 262 handles its READY=1 first, which makes the service running,
        # the only state (with stopping) in which it takes RESTART_RESET=1 (service.c:5777).
        self.assertEqual(self.messages(), ['READY=1\nRESTART_RESET=1'])

    def test_a_guard_that_cannot_start_sends_nothing(self):
        for name, options in (('no system bus', {'no_bus': True}), ('inhibitor refused', {'refuse': True})):
            with self.subTest(name):
                self.assertEqual(self.run_main({'NOTIFY_SOCKET': self.path}, **options), 1)
                self.assertIn('startup failed', self.logged)
                self.assertEqual(self.messages(), [])

    def test_an_abstract_notify_socket(self):
        name = f'emaki-test-notify-{os.getpid()}-{time.monotonic_ns()}'
        receiver = self.listen('\0' + name)
        self.assertEqual(self.run_main({'NOTIFY_SOCKET': '@' + name}), 0, self.logged)
        self.assertEqual(self.messages(receiver), ['READY=1\nRESTART_RESET=1'])

    def test_outside_systemd_or_with_a_dead_socket_the_guard_runs_on(self):
        environ = {key: value for key, value in os.environ.items() if key != 'NOTIFY_SOCKET'}
        with patch.dict(os.environ, environ, clear=True):
            self.assertEqual(self.run_main({}), 0, self.logged)
        self.assertEqual(self.logged, '')
        self.assertEqual(self.run_main({'NOTIFY_SOCKET': self.path + '-missing'}), 0)
        self.assertIn('could not notify systemd', self.logged)
        self.assertEqual(self.messages(), [])


class PackageReadme(unittest.TestCase):
    def test_readme_states_the_lock_timeouts_of_the_code(self):
        # packaging/README.md describes the guard to packagers; its numbers follow the script.
        text = ' '.join((ROOT / 'packaging/README.md').read_text().split())
        for phrase in (f"{GUARD['LOCK_TIMEOUT']}-second timeout",
                       f"{GUARD['ACTION_LOCK_TIMEOUT']} seconds when `EMAKI_SLEEP_LOCK_FAILURE`"):
            self.assertTrue(phrase in text, f'packaging/README.md does not say "{phrase}"')


if __name__ == '__main__':
    unittest.main(verbosity=2)
