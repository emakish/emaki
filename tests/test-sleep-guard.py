#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
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
        self.delay_usec = 20_000_000
        self.session_type = 'wayland'
        self.session_uid = os.getuid()
        self.inhibitors = []
        self.docked = False

    def get_cached_property(self, name):
        return Reply(self.delay_usec) if name == 'InhibitDelayMaxUSec' else None

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

    def get_connection(self):
        return self

    def call_sync(self, method, params, *_rest):
        from gi.repository import GLib
        if method == 'org.freedesktop.login1':
            interface, member, arguments = _rest[:3]
            if arguments.unpack() == ('org.freedesktop.login1.Manager', 'Docked'):
                assert (interface, member) == ('org.freedesktop.DBus.Properties', 'Get')
                self.events.append('Get Manager.Docked')
                return Reply((self.docked,))
            self.assert_property_request(interface, member, arguments)
            name = arguments.unpack()[1]
            self.events.append(f'Get Session.{name}')
            values = {'Id': '7', 'Type': self.session_type,
                      'User': (self.session_uid, '/org/freedesktop/login1/user/_1000')}
            return Reply((values[name],))
        if method == 'ListInhibitors':
            return Reply((self.inhibitors,))
        if method != 'GetSession':
            raise AssertionError(method)
        session, = params.unpack()
        self.events.append(f'GetSession {session}')
        if session not in self.sessions:
            raise GLib.Error('no such session')
        return Reply((self.sessions[session],))

    @staticmethod
    def assert_property_request(interface, member, arguments):
        assert (interface, member, arguments.unpack()[0]) == (
            'org.freedesktop.DBus.Properties', 'Get', 'org.freedesktop.login1.Session')


class GuardCase(unittest.TestCase):
    def guard(self, **options):
        self.events = []
        options.setdefault('environ', {'XDG_SESSION_ID': '7'})
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
            return subprocess.CompletedProcess(argv, outcome, stdout='inactive\ninactive\ninactive\n', stderr='')
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
        self.assertEqual((path, events), (SESSION, ['GetSession 7', 'Get Session.Type', 'Get Session.User']))

    def test_session_falls_back_to_the_users_display(self):
        # A user service has XDG_SESSION_ID only when the session imported it.
        events = []
        path = GUARD['session_path'](Logind(events), {}, lambda: ('7', SESSION))
        self.assertEqual((path, events), (SESSION, ['Get Session.Type', 'Get Session.User']))

    def test_session_must_be_wayland_and_owned_by_this_user(self):
        for use_environment in (True, False):
            for session_type, uid in (('tty', os.getuid()), ('wayland', os.getuid() + 1)):
                with self.subTest(environment=use_environment, session_type=session_type, uid=uid):
                    proxy = Logind([])
                    proxy.session_type, proxy.session_uid = session_type, uid
                    environ = {'XDG_SESSION_ID': '7'} if use_environment else {}
                    with self.assertRaises(ValueError):
                        GUARD['session_path'](proxy, environ, lambda: ('7', SESSION))

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
                        'NIRI_SOCKET': str(self.socket), 'XDG_SESSION_ID': '7'}

    def policy_guard(self, policy, environ=None):
        guard = self.guard(policy=policy, environ=environ or self.environ)
        # Keep ordinary scenarios quick; the budget cases cover the reported range.
        guard.delay_window = 1.5
        return guard

    def state_flag(self):
        return self.state / 'emaki' / 'sleep-lock-failed'

    def runtime_flag(self):
        return self.runtime / 'emaki-sleep-lock-failed'

    def fail_lock(self, guard, failure, extra=None):
        results = {'/usr/bin/emaki-lock --wait': failure,
                   '/usr/bin/emaki-lock --fallback --wait': failure,
                   '/usr/bin/emaki-lock status': 1, **(extra or {})}
        held = []

        def run(argv, **kwargs):
            self.events.append(' '.join(argv))
            held.append((argv[0], guard.inhibitor is not None))
            outcome = results.get(' '.join(argv), results.get(argv[0], 0))
            if callable(outcome):
                outcome = outcome()
            if isinstance(outcome, BaseException):
                raise outcome
            return subprocess.CompletedProcess(argv, outcome, stdout='inactive\ninactive\ninactive\n', stderr='')
        with patch('subprocess.run', side_effect=run), patch('sys.stderr') as errors:
            guard.prepare_for_sleep(True)
        self.assertTrue(errors.write.called)
        return held


class FailurePolicy(FlagCase):
    FAILURES = {'exit': 1, 'timeout': subprocess.TimeoutExpired('emaki-lock', 1),
                'missing': FileNotFoundError('x')}

    def test_default_and_obsolete_values_fail_closed(self):
        self.assertEqual(GUARD['DEFAULT_FAILURE_POLICY'], 'end-session')
        self.assertEqual(GUARD['failure_policy']({}), 'end-session')
        for value in ('sleep', 'sleep-relock', 'nonsense'):
            with self.subTest(value=value), patch('sys.stderr'):
                self.assertEqual(GUARD['failure_policy']({'EMAKI_SLEEP_LOCK_FAILURE': value}), 'end-session')
        self.assertEqual(set(GUARD['FAILURE_POLICIES']), {'end-session', 'stay-awake'})

    def test_primary_success_never_terminates_or_leaves_notice(self):
        guard = self.policy_guard('end-session')
        with self.commands():
            guard.prepare_for_sleep(True)
        self.assertEqual(self.events, ['/usr/bin/emaki-lock --wait'])
        self.assertIsNone(guard.inhibitor)
        self.assertFalse(self.state_flag().exists() or self.runtime_flag().exists())

    def test_fallback_success_preserves_session(self):
        for name, failure in self.FAILURES.items():
            with self.subTest(failure=name):
                guard = self.policy_guard('end-session')
                with self.commands({'/usr/bin/emaki-lock --wait': failure}), patch('sys.stderr') as errors:
                    guard.prepare_for_sleep(True)
                logged = ''.join(call.args[0] for call in errors.write.call_args_list)
                self.assertEqual(len(logged.strip().splitlines()), 1)
                self.assertIn('primary', logged.lower())
                self.assertEqual(self.events, ['/usr/bin/emaki-lock --wait',
                                               '/usr/bin/emaki-lock --fallback --wait'])
                self.assertIsNone(guard.inhibitor)
                self.assertFalse(self.state_flag().exists() or self.runtime_flag().exists())

    def test_both_failures_request_termination_before_release(self):
        for name, failure in self.FAILURES.items():
            with self.subTest(failure=name):
                guard = self.policy_guard('end-session')
                held = self.fail_lock(guard, failure)
                self.assertEqual(self.events, ['/usr/bin/emaki-lock --wait',
                                               '/usr/bin/emaki-lock --fallback --wait',
                                               '/usr/bin/emaki-lock status',
                                               '/usr/bin/loginctl terminate-session 7',
                                               ' '.join(ShutdownRegression.SHUTDOWN),
                                               ' '.join(ShutdownRegression.SHOW)])
                self.assertTrue(all(was_held for _program, was_held in held))
                self.assertIsNone(guard.inhibitor)
                self.assertEqual(self.state_flag().read_text(),
                                 f'end-session\nboot={BOOT}\nniri={self.socket}\n')
                self.assertFalse(self.runtime_flag().exists())

    def test_termination_failure_and_timeout_always_release(self):
        for name, failure in self.FAILURES.items():
            with self.subTest(failure=name):
                guard = self.policy_guard('end-session')
                self.fail_lock(guard, 1, {'/usr/bin/loginctl': failure})
                self.assertIsNone(guard.inhibitor)
                self.assertIn('/usr/bin/loginctl terminate-session 7', self.events)
                self.assertEqual(self.runtime_flag().read_text(), 'end-session-failed\n')
                self.assertFalse(self.state_flag().exists())

    def test_missing_session_id_warns_the_running_shell_and_releases(self):
        guard = self.policy_guard('end-session', {**self.environ, 'XDG_SESSION_ID': ''})
        with self.assertRaisesRegex(ValueError, 'No graphical session ID'):
            self.fail_lock(guard, 1)
        self.assertIsNone(guard.inhibitor)
        self.assertEqual(self.runtime_flag().read_text(), 'end-session-failed\n')
        self.assertFalse(self.state_flag().exists())

    def test_budget_comes_from_logind_and_is_refreshed_on_reacquire(self):
        guard = self.guard()
        self.assertEqual(guard.delay_window, 20)
        guard.release()
        guard.proxy.delay_usec = 2_000_000
        guard.acquire()
        self.assertEqual(guard.delay_window, 2)

    def test_missing_budget_refuses_to_hold_an_inhibitor(self):
        guard = self.guard()
        guard.release()
        with patch.object(guard.proxy, 'get_cached_property', return_value=None):
            with self.assertRaises(ValueError):
                guard.acquire()
        self.assertIsNone(guard.inhibitor)

    def test_command_timeouts_fit_each_reported_budget(self):
        for window in (0.1, 1, 5, 20, 60):
            with self.subTest(window=window):
                guard = self.policy_guard('end-session')
                guard.delay_window = window
                clock = [100.0]
                calls = []
                def run(argv, **kwargs):
                    timeout = kwargs['timeout']
                    calls.append((list(argv), timeout, clock[0]))
                    self.assertIsNotNone(guard.inhibitor)
                    self.assertGreater(timeout, 0)
                    self.assertLessEqual(clock[0] + timeout, 100.0 + window)
                    clock[0] += timeout
                    raise subprocess.TimeoutExpired(argv, timeout)
                with patch('time.monotonic', side_effect=lambda: clock[0]), \
                        patch('time.sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
                        patch('subprocess.run', side_effect=run), patch('sys.stderr'):
                    guard.prepare_for_sleep(True)
                commands = [argv for argv, _timeout, _at in calls]
                self.assertEqual(commands[:4], [
                    ['/usr/bin/emaki-lock', '--wait'],
                    ['/usr/bin/emaki-lock', '--fallback', '--wait'],
                    ['/usr/bin/emaki-lock', 'status'],
                    ['/usr/bin/loginctl', 'terminate-session', '7']])
                self.assertTrue(all(argv in (ShutdownRegression.SHUTDOWN, ShutdownRegression.SHOW)
                                    for argv in commands[4:]))
                self.assertLessEqual(clock[0], 100.0 + window)
                self.assertIsNone(guard.inhibitor)

    def test_wake_reacquires_without_launching_an_unconfirmed_locker(self):
        guard = self.policy_guard('end-session')
        self.fail_lock(guard, 1)
        self.events.clear()
        with self.commands():
            guard.prepare_for_sleep(False)
        self.assertEqual(self.events, ['inhibit sleep delay'])

    def test_stay_awake_lid_path_suspends_only_after_confirmed_lock(self):
        guard = self.policy_guard('stay-awake')
        with self.commands():
            guard.lid_closed(True, docked=False)
        self.assertEqual(self.events, ['/usr/bin/emaki-lock --wait', '/usr/bin/systemctl suspend'])
        self.assertIsNotNone(guard.lid_inhibitor)
        self.events.clear()
        with self.commands({'/usr/bin/emaki-lock --wait': 1}), patch('sys.stderr'):
            guard.lid_closed(True, docked=False)
        self.assertEqual(self.events, ['/usr/bin/emaki-lock --wait'])
        self.assertEqual(self.runtime_flag().read_text(), 'stay-awake\n')

    def test_transaction_lid_reuses_sleep_lock_fallback_without_suspending(self):
        for policy in ('end-session', 'stay-awake'):
            with self.subTest(policy=policy):
                guard = self.policy_guard(policy)
                guard.proxy.inhibitors = [('sleep:handle-lid-switch', 'Emaki packages',
                                           'Packages', 'block', 0, 321)]
                delay = guard.inhibitor
                with self.commands({'/usr/bin/emaki-lock --wait': 1}), patch('sys.stderr'):
                    guard.lid_closed(True, docked=False)
                self.assertEqual(self.events, ['/usr/bin/emaki-lock --wait',
                                               '/usr/bin/emaki-lock --fallback --wait'])
                self.assertEqual(guard.inhibitor, delay)
                self.assertFalse(guard.sleeping)

    def test_transaction_lid_reads_fresh_docked_state(self):
        for policy in ('end-session', 'stay-awake'):
            guard = self.policy_guard(policy)
            guard.proxy.inhibitors = [('sleep:handle-lid-switch', 'Emaki packages',
                                       'Packages', 'block', 0, 321)]
            for docked in (True, False, True):
                with self.subTest(policy=policy, docked=docked):
                    self.events.clear()
                    guard.proxy.docked = docked
                    with patch.object(guard.proxy, 'get_cached_property', return_value=Reply(not docked)), self.commands():
                        guard.lid_closed(True)
                    expected = ['Get Manager.Docked']
                    if not docked:
                        expected.append('/usr/bin/emaki-lock --wait')
                    self.assertEqual(self.events, expected)

    def assert_transaction_lock_failure_preserves_session(self, guard, failure):
        guard.proxy.inhibitors = [('sleep:handle-lid-switch', 'Emaki packages',
                                   'Packages', 'block', 0, 321)]
        delay = guard.inhibitor
        with self.commands({'/usr/bin/emaki-lock': failure}), patch('sys.stderr') as errors:
            guard.lid_closed(True, docked=False)
        self.assertEqual(self.events, ['/usr/bin/emaki-lock --wait',
                                       '/usr/bin/emaki-lock --fallback --wait',
                                       '/usr/bin/emaki-lock status'])
        self.assertEqual(guard.inhibitor, delay)
        self.assertFalse(guard.sleeping)
        self.assertIn('staying awake', ''.join(str(call.args[0]) for call in errors.write.call_args_list))
        self.assertTrue(self.state_flag().read_text().startswith('stay-awake\nboot='))
        self.assertFalse(self.runtime_flag().exists())

    def test_transaction_double_lock_failure_preserves_session(self):
        for policy in ('end-session', 'stay-awake'):
            for name, failure in self.FAILURES.items():
                with self.subTest(policy=policy, failure=name):
                    self.assert_transaction_lock_failure_preserves_session(self.policy_guard(policy), failure)

    def test_mutant_ending_transaction_session_is_detected(self):
        path = ROOT / 'scripts/emaki-sleep-guard'
        source = path.read_text()
        original = "self.flag_next_session('stay-awake')"
        self.assertEqual(source.count(original), 1)
        namespace = {'__name__': 'sleep_guard_mutant'}
        exec(compile(source.replace(original, 'self.end_session()'), str(path), 'exec'), namespace)
        guard = self.policy_guard('end-session')
        guard.lid_closed = namespace['SleepGuard'].lid_closed.__get__(guard)
        with self.assertRaises(AssertionError):
            self.assert_transaction_lock_failure_preserves_session(guard, 1)

    def test_mutant_ignoring_transaction_lid_is_detected(self):
        guard = self.policy_guard('end-session')
        guard.proxy.inhibitors = [('sleep:handle-lid-switch', 'Emaki packages',
                                   'Packages', 'block', 0, 321)]
        with patch.object(guard, 'transaction_inhibits_lid', return_value=False), self.commands():
            guard.lid_closed(True, docked=False)
        with self.assertRaises(AssertionError):
            self.assertIn('/usr/bin/emaki-lock --wait', self.events)

    def test_only_root_transaction_block_uses_transaction_lid_path(self):
        entries = [
            ('sleep:handle-lid-switch', 'Emaki packages', 'Packages', 'block', 1000, 321),
            ('sleep:handle-lid-switch', 'Someone else', 'Packages', 'block', 0, 321),
            ('sleep', 'Emaki packages', 'Packages', 'block', 0, 321),
            ('sleep:handle-lid-switch', 'Emaki packages', 'Packages', 'delay', 0, 321),
        ]
        guard = self.policy_guard('end-session')
        for entry in entries:
            with self.subTest(entry=entry):
                guard.proxy.inhibitors = [entry]
                with self.commands():
                    guard.lid_closed(True, docked=False)
                self.assertEqual(self.events, [])
        guard.proxy.inhibitors = [('sleep:handle-lid-switch', 'Emaki packages',
                                   'Packages', 'block', 0, 321)]
        with self.commands():
            guard.lid_closed(False, docked=False)
        self.assertEqual(self.events, [])

    def test_external_sleep_under_stay_awake_still_fails_closed(self):
        guard = self.policy_guard('stay-awake')
        self.fail_lock(guard, 1)
        self.assertIn('/usr/bin/loginctl terminate-session 7', self.events)
        self.assertIsNone(guard.inhibitor)
        self.assertFalse(any('suspend' in event or 'hibernate' in event for event in self.events))


class ShutdownRegression(FlagCase):
    """A fake clock, delayed lockers and user-manager jobs; never touches a session."""
    SHUTDOWN = ['/usr/bin/systemctl', '--user', 'start', '--no-block',
                '--job-mode=replace-irreversibly', 'niri-shutdown.target']
    SHOW = ['/usr/bin/systemctl', '--user', 'show', '--property=ActiveState', '--value',
            'niri-emaki.service', 'niri.service', 'graphical-session.target']

    def scenario(self, *, login_result=0, shutdown_result=0, state='inactive',
                 fallback_delay=None, late_lock=False, poll_states=None, poll_result=0,
                 compositor='niri-emaki.service'):
        guard = self.policy_guard('end-session')
        guard.delay_window = 20
        clock = [100.0]
        calls = []
        shutdown_at = [None]
        observed_inactive = [False]
        attempt_flag = [None]

        def run(argv, **kwargs):
            calls.append((list(argv), clock[0], kwargs['timeout']))
            self.assertIsNotNone(guard.inhibitor)
            self.assertLessEqual(clock[0] + kwargs['timeout'], 120)
            stdout = ''
            result = 0
            if argv == ['/usr/bin/emaki-lock', '--wait']:
                clock[0] += kwargs['timeout']
                raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
            elif argv == ['/usr/bin/emaki-lock', '--fallback', '--wait']:
                elapsed = kwargs['timeout'] if fallback_delay is None else fallback_delay
                clock[0] += min(elapsed, kwargs['timeout'])
                if fallback_delay is None or elapsed > kwargs['timeout']:
                    raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
            elif argv == ['/usr/bin/emaki-lock', 'status']:
                result = 0 if late_lock else 1
            elif argv == ['/usr/bin/loginctl', 'terminate-session', '7']:
                # Until every shutdown check succeeds, a crash must not leave a success flag.
                attempt_flag[0] = self.state_flag().read_text().splitlines()[0]
                clock[0] += 0.05
                result = login_result
            elif argv == self.SHUTDOWN:
                clock[0] += 0.05
                shutdown_at[0] = clock[0]
                result = shutdown_result
            elif argv == self.SHOW:
                self.assertIsNotNone(shutdown_at[0], 'polling must follow the shutdown request')
                clock[0] += min(0.01, kwargs['timeout'])
                ready = clock[0] - shutdown_at[0] >= 0.30
                states = ['inactive', 'inactive', state]
                states[self.SHOW[-3:].index(compositor)] = (
                    'inactive' if ready and state == 'inactive' else 'deactivating')
                stdout = '\n'.join(states) + '\n'
                if poll_states is not None:
                    stdout = '\n'.join(poll_states) + '\n'
                result = poll_result
                observed_inactive[0] = result == 0 and stdout == 'inactive\ninactive\ninactive\n'
            else:
                self.fail(f'unexpected command: {argv}')
            return subprocess.CompletedProcess(argv, result, stdout=stdout, stderr='')

        with patch('subprocess.run', side_effect=run), \
                patch('time.monotonic', side_effect=lambda: clock[0]), \
                patch('time.sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)), \
                patch('sys.stderr') as errors:
            guard.prepare_for_sleep(True)
        self.assertIsNone(guard.inhibitor)
        self.assertLessEqual(clock[0], 120)
        self.logs = ''.join(call.args[0] for call in errors.write.call_args_list)
        self.calls = [argv for argv, _at, _timeout in calls]
        self.elapsed = clock[0] - 100
        self.shutdown_elapsed = None if shutdown_at[0] is None else clock[0] - shutdown_at[0] + 0.10
        self.observed_inactive = observed_inactive[0]
        self.attempt_flag = attempt_flag[0]
        return guard

    def test_shutdown_is_proven_before_releasing_the_inhibitor(self):
        self.scenario()
        self.assertIn(self.SHUTDOWN, self.calls)
        self.assertIn(self.SHOW, self.calls)
        self.assertTrue(self.observed_inactive)
        self.assertEqual(self.attempt_flag, 'end-session-failed')
        self.assertLessEqual(self.shutdown_elapsed, 2 + 1e-9)
        self.assertEqual(self.state_flag().read_text().splitlines()[0], 'end-session')

    def test_each_compositor_must_finish_stopping_before_release(self):
        for compositor in ('niri-emaki.service', 'niri.service'):
            with self.subTest(compositor=compositor):
                self.scenario(compositor=compositor)
                self.assertTrue(self.observed_inactive)
                self.assertGreaterEqual(self.calls.count(self.SHOW), 2)
                self.assertEqual(self.state_flag().read_text().splitlines()[0], 'end-session')
                self.assertFalse(self.runtime_flag().exists())

    def test_refused_termination_leaves_the_failure_notice(self):
        self.scenario(login_result=1)
        self.assertEqual(self.runtime_flag().read_text(), 'end-session-failed\n')
        self.assertFalse(self.state_flag().exists())
        self.assertIn('failed', self.logs)
        self.assertIn('loginctl', self.logs)

    def test_shutdown_request_failure_cannot_claim_success(self):
        self.scenario(shutdown_result=1)
        self.assertEqual(self.runtime_flag().read_text(), 'end-session-failed\n')
        self.assertFalse(self.state_flag().exists())
        self.assertIn('failed', self.logs)

    def test_shutdown_that_never_finishes_is_bounded_and_cannot_claim_success(self):
        self.scenario(state='active')
        self.assertFalse(self.observed_inactive)
        self.assertIsNotNone(self.shutdown_elapsed)
        self.assertLessEqual(self.shutdown_elapsed, 2 + 1e-9)
        self.assertEqual(self.runtime_flag().read_text(), 'end-session-failed\n')
        self.assertFalse(self.state_flag().exists())

    def test_shutdown_requires_all_units_and_a_successful_complete_query(self):
        for states, result in ((('active', 'inactive', 'inactive'), 0),
                               (('inactive', 'active', 'inactive'), 0),
                               (('inactive', 'inactive', 'active'), 0),
                               (('inactive', 'inactive'), 0),
                               (('inactive', 'inactive', 'inactive'), 1)):
            with self.subTest(states=states, result=result):
                self.scenario(poll_states=states, poll_result=result)
                self.assertIn(self.SHOW, self.calls)
                self.assertLessEqual(self.shutdown_elapsed, 2 + 1e-9)
                self.assertFalse(self.observed_inactive)
                self.assertEqual(self.runtime_flag().read_text(), 'end-session-failed\n')
                self.assertFalse(self.state_flag().exists())

    def test_three_second_fallback_preserves_the_session(self):
        self.scenario(fallback_delay=3)
        self.assertNotIn(['/usr/bin/loginctl', 'terminate-session', '7'], self.calls)
        self.assertFalse(self.state_flag().exists())
        self.assertEqual(len(self.logs.strip().splitlines()), 1)
        self.assertIn('primary', self.logs.lower())

    def test_lock_confirmed_after_fallback_timeout_preserves_the_session(self):
        self.scenario(late_lock=True)
        self.assertIn(['/usr/bin/emaki-lock', 'status'], self.calls)
        self.assertNotIn(['/usr/bin/loginctl', 'terminate-session', '7'], self.calls)
        self.assertFalse(self.state_flag().exists())


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
        self.fail_lock(guard, 1)
        # A shell restarted under the same niri (same boot, same socket) is not the next login.
        self.assertEqual(self.notices(str(self.socket), True), [])
        self.assertTrue(self.state_flag().exists())
        self.assertEqual(self.notices(self.LATER, False), [], 'the runtime path never takes the state flag')
        self.assertEqual(self.notices(self.LATER, True), ['end-session'])
        self.assertFalse(self.state_flag().exists())
        self.assertEqual(self.notices(self.LATER, True), [], 'told once')

    def test_failed_transaction_lock_is_told_once_at_next_login(self):
        guard = self.policy_guard('end-session')
        guard.proxy.inhibitors = [('sleep:handle-lid-switch', 'Emaki packages',
                                   'Packages', 'block', 0, 321)]
        with self.commands({'/usr/bin/emaki-lock': 1}), patch('sys.stderr'):
            guard.lid_closed(True, docked=False)
        self.assertEqual(self.notices(str(self.socket), True), [])
        self.assertEqual(self.notices(self.LATER, True), ['stay-awake'])
        self.assertEqual(self.notices(self.LATER, True), [])

    def test_refused_termination_is_told_once_in_the_running_session(self):
        guard = self.policy_guard('end-session')
        self.fail_lock(guard, 1, {'/usr/bin/loginctl': 1})
        self.assertFalse(self.state_flag().exists())
        self.assertEqual(self.notices(str(self.socket), False), ['end-session-failed'])
        self.assertEqual(self.notices(str(self.socket), False), [])
        self.assertEqual(self.notices(self.LATER, True), [])

    def test_a_flag_from_another_boot_or_an_older_guard_is_told(self):
        flag = self.state_flag()
        flag.parent.mkdir(parents=True, exist_ok=True)
        for text in ('end-session\n', f'end-session\nboot=another-boot\nniri={self.socket}\n'):
            with self.subTest(flag=text):
                flag.write_text(text)
                self.assertEqual(self.notices(str(self.socket), True), ['end-session'])
                self.assertFalse(flag.exists())

    def test_every_flag_name_has_a_known_name_and_its_own_sentence(self):
        names = {'sleep', 'sleep-relock', 'end-session', 'stay-awake', 'end-session-failed'}
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

    def get_cached_property(self, name):
        return super().get_cached_property(name)


class StartupValidation(unittest.TestCase):
    """Startup ordering without a system bus, display or notification socket."""

    def run_main(self, *, lookup_fails=False, preparing=False):
        from gi.repository import GLib
        self.events = []
        acquired = {}
        ready = []
        signals = []
        test = self

        class Proxy(LogindProxy):
            def call_sync(self, method, params, *rest):
                if method == 'GetSession':
                    held = acquired.get('guard')
                    test.events.append(f'held during lookup: {held is not None and held.inhibitor is not None}')
                    if lookup_fails:
                        raise GLib.Error('no such session')
                return super().call_sync(method, params, *rest)

            def get_cached_property(self, name):
                if name == 'PreparingForSleep':
                    return Reply(preparing)
                return super().get_cached_property(name)

        class Loop:
            def run(self):
                pass

            def quit(self):
                pass

        original_acquire = GUARD['SleepGuard'].acquire

        def acquire(guard):
            original_acquire(guard)
            acquired['guard'] = guard

        def prepare(guard, sleeping):
            self.assertTrue(sleeping)
            self.assertIsNotNone(guard.inhibitor)
            self.assertIn(GUARD['signal'].SIGTERM, signals)
            self.events.append('startup sleep guarded')

        proxy = Proxy(self.events, lambda: bool(ready))
        with patch.object(GUARD['Gio'].DBusProxy, 'new_for_bus_sync', return_value=proxy), \
                patch.object(GUARD['GLib'], 'MainLoop', Loop), \
                patch.object(GUARD['GLib'], 'unix_signal_add', side_effect=lambda _priority, sig, _callback: signals.append(sig)), \
                patch.object(GUARD['SleepGuard'], 'acquire', acquire), \
                patch.object(GUARD['SleepGuard'], 'prepare_for_sleep', prepare), \
                patch.dict(GUARD['main'].__globals__, {'notify_ready': lambda: ready.append(True)}), \
                patch.dict(os.environ, {'XDG_SESSION_ID': '7', 'EMAKI_SLEEP_LOCK_FAILURE': 'end-session'}), \
                patch('sys.stderr'):
            status = GUARD['main']()
        self.assertTrue(not acquired or acquired['guard'].inhibitor is None, 'startup cleanup must release')
        self.ready = ready
        return status

    def test_failed_lookup_still_holds_the_delay(self):
        self.assertEqual(self.run_main(lookup_fails=True), 1)
        self.assertIn('held during lookup: True', self.events)
        self.assertEqual(self.ready, [])

    def test_startup_during_sleep_registers_termination_handling_first(self):
        self.assertEqual(self.run_main(preparing=True), 0)
        self.assertIn('held during lookup: True', self.events)
        self.assertIn('startup sleep guarded', self.events)


class Readiness(unittest.TestCase):
    """Type=notify: main() sends READY=1 with RESTART_RESET=1 once logind's delay inhibitor is
    held, and nothing when it never gets that far, so systemd's RestartSteps= slow down only a
    guard that cannot start. Fakes for logind and the main loop; systemd's notify socket is a
    datagram socket of this test."""

    def setUp(self):
        directory = tempfile.mkdtemp(prefix='em-', dir='/tmp')
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
        self.assertEqual(self.events, ['notified before Inhibit: False', 'inhibit sleep delay',
                                       'GetSession 7', 'Get Session.Type', 'Get Session.User', 'Get Session.Id',
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
