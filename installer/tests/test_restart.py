# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Restart preparation and requests without touching the host power state."""
from types import SimpleNamespace
from pathlib import Path
import tempfile
import os
import select
import signal
import subprocess
import sys
import time
import threading
import unittest
from unittest.mock import Mock, patch

from emaki_installer import daemon
from emaki_installer import restart as restart_module
from emaki_installer.errors import Code, InstallError
from emaki_installer.protocol import Controller, Job
from emaki_installer.runtime import Runner
from support import FakeInventory, config


class RestartTests(unittest.TestCase):
    def shutdown(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / 'shutdown'
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
        return path

    def controller(self):
        controller = Controller(FakeInventory(), None)
        controller.job = Job('finished')
        controller.emit('done', seconds=1, log_path='log')
        self.addCleanup(controller.job.history.close)
        return controller

    def test_production_factory_uses_controller_skip_event(self):
        controller = self.controller()
        api = SimpleNamespace(version='4.5')
        with patch.object(daemon, 'Worker') as worker:
            daemon.make_worker(api, controller.inventory, controller)
        self.assertIs(worker.call_args.kwargs['skip_update'], controller.job.skip_update)

    def test_controller_skip_stops_production_factory_download(self):
        downloading = threading.Event()
        workers, commands = [], []
        inventory = FakeInventory()
        api = SimpleNamespace(version='4.5')
        controller = Controller(inventory, lambda broker: daemon.make_worker(api, inventory, broker))

        class DownloadRunner:
            def chroot(self, argv, target, **kwargs):
                commands.append(argv)
                downloading.set()
                if not kwargs['cancelled'].wait(2):
                    raise AssertionError('The controller skip did not reach the download.')
                raise InstallError(Code.CANCELLED, 'Update download stopped.')

        def run_update(worker, plan, cancelled):
            workers.append(worker)
            worker.runner = DownloadRunner()
            worker.release_pacman_lock = lambda: None
            worker.update()
            worker.update_notice()
            worker.emit('done', warnings=worker.warnings, seconds=1, log_path='log')

        ack = controller.handle({'type': 'plan', 'id': 'plan', 'config': config()})[0]
        result = controller.handle({'type': 'confirm', 'id': 'confirm',
                                    'plan_id': ack['plan_id'], 'token': ack['token']})
        self.addCleanup(controller.job.history.close)
        with patch.object(daemon.Worker, 'run', run_update):
            thread = result[1]()
            try:
                self.assertTrue(downloading.wait(2))
                self.assertTrue(controller.handle({'type': 'skip_update', 'id': 'skip'})[0]['ok'])
            finally:
                thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(controller.job.terminal['type'], 'done')
        self.assertEqual(len(controller.job.terminal['warnings']), 1)
        self.assertIn('one full update', controller.job.terminal['warnings'][0])
        self.assertFalse(controller.job.cancelled.is_set())
        self.assertFalse(workers[0].update_attempted)
        self.assertEqual(len(commands), 1)
        self.assertIn('-Syw', commands[0])

    def test_prepare_then_restart_through_controller(self):
        controller = self.controller()
        controller.prepare_reboot_fn = Mock()
        controller.reboot_fn = Mock()
        response = controller.handle({'type': 'prepare_reboot', 'id': 'prepare'})[0]
        self.assertTrue(response['ok'])
        self.assertFalse(response['forced_reboot'])
        controller.prepare_reboot_fn.assert_called_once_with()
        self.assertTrue(controller.handle({'type': 'reboot', 'id': 'restart'})[0]['ok'])
        controller.reboot_fn.assert_called_once_with()

    def test_preparation_requires_completed_target_cleanup(self):
        controller = Controller(FakeInventory(), None)
        controller.prepare_reboot_fn = Mock()
        for terminal in (None, {'type': 'error'}):
            controller.job = Job('unfinished', terminal=terminal)
            self.addCleanup(controller.job.history.close)
            response = controller.handle({'type': 'prepare_reboot', 'id': 'prepare'})[0]
            self.assertFalse(response['ok'])
        controller.prepare_reboot_fn.assert_not_called()

    def test_failed_restart_allows_another_attempt(self):
        controller = self.controller()
        controller.reboot_fn = Mock(side_effect=InstallError(Code.COMMAND_FAILED,
                                                            restart_module.RESTART_ERROR))
        self.assertFalse(controller.handle({'type': 'reboot', 'id': 'restart'})[0]['ok'])
        self.assertFalse(controller.stopping)

    def test_preparation_failure_is_reported_before_removal(self):
        controller = self.controller()
        controller.prepare_reboot_fn = Mock(side_effect=InstallError(Code.COMMAND_FAILED,
                                          'Could not prepare to restart. Keep the USB stick connected and try again.'))
        response = controller.handle({'type': 'prepare_reboot', 'id': 'prepare'})[0]
        self.assertFalse(response['ok'])
        self.assertIn('Keep the USB stick connected', response['msg'])
        self.assertTrue(controller.stopping)

    def test_controller_reports_forced_preparation(self):
        controller = self.controller()
        controller.prepare_reboot_fn = Mock(return_value=True)
        response = controller.handle({'type': 'prepare_reboot', 'id': 'prepare'})[0]
        self.assertTrue(response['ok'])
        self.assertTrue(response['forced_reboot'])

    def test_preparation_prevents_new_disk_work_even_after_a_failed_reply(self):
        for forced in (False, True, InstallError(Code.COMMAND_FAILED, 'No reply.')):
            with self.subTest(forced=forced):
                controller = self.controller()
                controller.prepare_reboot_fn = Mock(return_value=forced)
                if isinstance(forced, Exception):
                    controller.prepare_reboot_fn.side_effect = forced
                controller.handle({'type': 'prepare_reboot', 'id': 'prepare'})
                self.assertTrue(controller.stopping)
                response = controller.handle({'type': 'plan', 'id': 'plan', 'config': config()})[0]
                self.assertEqual(response['code'], Code.BUSY.value)
                controller.reboot_fn = Mock(side_effect=InstallError(Code.COMMAND_FAILED, 'No restart.'))
                controller.handle({'type': 'reboot', 'id': 'restart'})
                self.assertTrue(controller.stopping)
                controller.prepare_reboot_fn = Mock(return_value=False)
                self.assertTrue(controller.handle({'type': 'prepare_reboot', 'id': 'retry'})[0]['ok'])

    def test_resident_restart_locks_at_startup_and_syncs_in_preparation(self):
        self.addCleanup(signal.signal, signal.SIGTERM, signal.getsignal(signal.SIGTERM))
        libc = Mock()
        libc.mlockall.return_value = 0
        libc.sleep.return_value = 0
        restart = daemon.ResidentReboot(Mock(), libc=libc, shutdown=self.shutdown())
        restart.pin()
        restart.prepare()
        self.assertEqual([call[0] for call in libc.mock_calls], ['mlockall', 'sync'])
        libc.mlockall.assert_called_once_with(3)
        self.assertFalse(restart.ready)
        restart.ready = True
        libc.reset_mock()
        with self.assertRaisesRegex(InstallError, 'Could not restart'):
            restart()
        self.assertEqual([call[0] for call in libc.mock_calls], ['kill', 'sleep', 'reboot'])
        libc.reboot.assert_called_once_with(0x01234567)

    def test_shutdown_generation_finishes_before_sync(self):
        order = Mock()
        shutdown = self.shutdown()
        shutdown.unlink()

        def generate(*args, **kwargs):
            shutdown.write_text('#!/bin/sh\nexit 0\n')
            shutdown.chmod(0o755)

        order.runner.run.side_effect = generate
        restart = daemon.ResidentReboot(order.runner, libc=order.libc, shutdown=shutdown)
        restart.prepare()
        self.assertFalse(restart.ready)
        order.runner.run.assert_called_once_with(
            ['systemctl', 'start', 'mkinitcpio-generate-shutdown-ramfs.service'],
            timeout=120, termination_timeout=2)
        self.assertEqual([call[0] for call in order.mock_calls], ['runner.run', 'libc.sync'])

    def test_shutdown_failure_or_condition_skip_still_syncs(self):
        for outcome in ('failure', 'os-error', 'missing', 'not-executable', 'directory', 'log-error'):
            with self.subTest(outcome=outcome):
                runner, libc = Mock(), Mock()
                shutdown = self.shutdown()
                if outcome == 'failure':
                    runner.run.side_effect = InstallError(Code.COMMAND_FAILED, 'Generation failed.')
                elif outcome == 'os-error':
                    runner.run.side_effect = OSError('Generation could not start.')
                elif outcome == 'not-executable':
                    shutdown.chmod(0o644)
                else:
                    shutdown.unlink()
                    if outcome == 'directory':
                        shutdown.mkdir()
                restart = daemon.ResidentReboot(runner, libc=libc, shutdown=shutdown)
                if outcome == 'log-error':
                    runner.log.side_effect = OSError('Log is full.')
                restart.prepare()
                runner.log.assert_called_once()
                self.assertIn('failed or was skipped', runner.log.call_args.args[0])
                libc.sync.assert_called_once_with()
                libc.mlockall.assert_not_called()

    def test_missing_or_ejected_medium_is_not_reported_prepared(self):
        for size in ('0', None, 'invalid'):
            with self.subTest(size=size):
                medium = self.shutdown().with_name('size')
                if size is not None:
                    medium.write_text(size)
                runner, libc = Mock(), Mock()
                restart = daemon.ResidentReboot(runner, libc=libc, medium_size=medium)
                with self.assertRaises((InstallError, OSError, ValueError)):
                    restart.prepare()
                runner.run.assert_not_called()
                libc.sync.assert_not_called()

    def test_medium_removed_during_sync_is_not_reported_prepared(self):
        medium = self.shutdown().with_name('size')
        medium.write_text('4096')
        libc = Mock()
        libc.sync.side_effect = medium.unlink
        restart = daemon.ResidentReboot(Mock(), libc=libc, shutdown=self.shutdown(),
                                       medium_size=medium)
        with self.assertRaises(OSError):
            restart.prepare()
        libc.sync.assert_called_once_with()

    def test_residency_failure_does_not_allow_restart(self):
        libc = Mock()
        libc.mlockall.return_value = -1
        restart = daemon.ResidentReboot(Mock(), libc=libc, shutdown=self.shutdown())
        with self.assertRaisesRegex(InstallError, 'Keep the USB stick connected'):
            restart.pin()
        with self.assertRaises(InstallError):
            restart()
        libc.kill.assert_not_called()
        libc.reboot.assert_not_called()

    def test_interrupted_wait_still_reaches_resident_fallback_without_file_reads(self):
        self.addCleanup(signal.signal, signal.SIGTERM, signal.getsignal(signal.SIGTERM))
        libc = Mock()
        libc.sleep.side_effect = [6, 0]
        restart = daemon.ResidentReboot(Mock(), libc=libc, shutdown=self.shutdown())
        restart.ready = True
        with patch('builtins.open', side_effect=AssertionError('Restart read a file.')), \
                patch.object(Path, 'is_file', side_effect=AssertionError('Restart checked a file.')):
            with self.assertRaisesRegex(InstallError, 'Could not restart'):
                restart()
        self.assertEqual([call.args for call in libc.sleep.call_args_list], [(8,), (6,)])
        libc.reboot.assert_called_once_with(0x01234567)

    def test_forced_restart_uses_only_reboot_syscall(self):
        libc = Mock()
        restart = daemon.ResidentReboot(Mock(), libc=libc, shutdown=self.shutdown())
        restart.ready = True
        with self.assertRaises(InstallError):
            restart(force=True)
        self.assertEqual([call[0] for call in libc.mock_calls], ['reboot'])

    def trace_guard(self, outcome='normal', fallback_delay=0.15, isolate=lambda: None):
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.addCleanup(os.close, write_fd)
        libc, runner = Mock(), Mock()

        def event(letter):
            os.write(write_fd, letter)

        def lock(flags):
            event(b'L')
            return -1 if outcome == 'pin-failure' else 0

        def run(*args, **kwargs):
            event(b'G')
            if outcome == 'stalled-runner':
                time.sleep(0.4)
                event(b'X')
            if outcome == 'child-status':
                status = subprocess.call([sys.executable, '-c', 'raise SystemExit(7)'])
                if status != 7:
                    raise AssertionError('Child status was lost.')
            if outcome == 'worker-crash':
                os._exit(2)
            if outcome == 'ramfs-failure':
                raise OSError('Missing live medium.')

        def sync():
            event(b'S')
            if outcome == 'stalled-sync':
                time.sleep(2)
            if outcome == 'sync-error':
                raise OSError('Sync failed.')

        libc.mlockall.side_effect = lock
        libc.sync.side_effect = sync
        def request_pid1(pid, number):
            if pid != 1 or number != signal.SIGRTMIN + 5:
                raise AssertionError('Unexpected restart signal.')
            event(b'K')
            if outcome == 'normal-shutdown':
                # A scope stop may arrive immediately upon the PID 1 request.
                os.kill(os.getpid(), signal.SIGTERM)
            return 0

        libc.kill.side_effect = request_pid1
        def sleep(seconds):
            event(b'W')
            if outcome in ('normal-wait', 'normal-shutdown'):
                time.sleep(seconds)
            return 0

        libc.sleep.side_effect = sleep
        libc.reboot.side_effect = lambda *args: event(b'B') or -1
        runner.run.side_effect = run
        runner.log.side_effect = lambda line: event(b'I') if 'Restart scope adoption failed:' in line else None
        restart = daemon.ResidentReboot(runner, libc=libc, shutdown=self.shutdown())
        guard = daemon.RestartGuard(restart, prepare_timeout=0.1, fallback_delay=fallback_delay,
                                    isolate=isolate)
        self.addCleanup(self.close_guard, guard)
        return guard, read_fd

    @staticmethod
    def close_guard(guard):
        try:
            guard.close()
        except (OSError, ChildProcessError):
            pass
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            try:
                if os.waitpid(guard.pid, os.WNOHANG)[0]:
                    return
            except ChildProcessError:
                return
            time.sleep(0.01)
        try:
            os.kill(guard.pid, signal.SIGKILL)
            os.waitpid(guard.pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass

    def read_trace(self, fd, count, timeout=2):
        result = b''
        deadline = time.monotonic() + timeout
        while len(result) < count:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
                break
            result += os.read(fd, count - len(result))
        return result

    def test_guard_normal_path_waits_for_request_past_fallback_deadline(self):
        guard, trace = self.trace_guard()
        with self.assertRaises(InstallError):
            guard()
        self.assertFalse(guard.prepare())
        self.assertEqual(self.read_trace(trace, 3), b'LGS')
        self.assertEqual(self.read_trace(trace, 1, timeout=0.3), b'')
        self.assertFalse(guard.prepare())
        with self.assertRaises(InstallError):
            guard()
        self.assertEqual(self.read_trace(trace, 3), b'KWB')

    def test_guard_ramfs_failure_retains_normal_restart(self):
        guard, trace = self.trace_guard('ramfs-failure')
        self.assertFalse(guard.prepare())
        with self.assertRaises(InstallError):
            guard()
        self.assertEqual(self.read_trace(trace, 6), b'LGSKWB')

    def test_slow_service_cleanup_stays_inside_preparation_deadline(self):
        # Scale the 120 + 2 command budgets together; exercise actual Runner
        # process termination while the medium and shutdown artifact stay present.
        runner = Runner(lambda line: None)
        run = runner.run

        def delayed_service(argv, **kwargs):
            self.assertEqual(kwargs, {'timeout': 120, 'termination_timeout': 2})
            return run([sys.executable, '-c',
                        'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(10)'],
                       timeout=0.05, termination_timeout=0.05)

        runner.run = delayed_service
        libc = Mock()
        libc.mlockall.return_value = 0
        restart = daemon.ResidentReboot(runner, libc=libc, shutdown=self.shutdown(),
                                       medium_size=self.shutdown().with_name('size'))
        restart.medium_size.write_text('4096')
        guard = daemon.RestartGuard(restart, prepare_timeout=0.8, isolate=lambda: None)
        self.addCleanup(self.close_guard, guard)
        self.assertFalse(guard.prepare())
        self.assertIsNone(guard.forced_deadline)

    def test_guard_stalled_preparation_is_bounded_and_forces_on_request(self):
        for outcome, expected in (('stalled-runner', b'LGB'), ('stalled-sync', b'LGSB')):
            with self.subTest(outcome=outcome):
                guard, trace = self.trace_guard(outcome, fallback_delay=1)
                start = time.monotonic()
                self.assertTrue(guard.prepare())
                self.assertLess(time.monotonic() - start, 1)
                with self.assertRaises(InstallError):
                    guard()
                self.assertEqual(self.read_trace(trace, len(expected)), expected)

    def test_guard_failure_forces_automatically_without_ui_request(self):
        for outcome, expected in (('worker-crash', b'LGB'), ('sync-error', b'LGSB'),
                                  ('stalled-runner', b'LGB'), ('stalled-sync', b'LGSB')):
            with self.subTest(outcome=outcome):
                guard, trace = self.trace_guard(outcome)
                self.assertTrue(guard.prepare())
                self.assertEqual(self.read_trace(trace, len(expected)), expected)

    def test_guard_disconnected_client_does_not_cancel_forced_restart(self):
        guard, trace = self.trace_guard('stalled-runner')
        self.assertTrue(guard.prepare())
        guard.close()
        self.assertEqual(self.read_trace(trace, 3), b'LGB')

    def test_guard_cannot_start_when_pinning_fails(self):
        with self.assertRaisesRegex(InstallError, 'restart service could not start'):
            self.trace_guard('pin-failure')

    def test_guard_preparation_child_restores_sigchld_for_real_exit_status(self):
        guard, trace = self.trace_guard('child-status')
        self.assertFalse(guard.prepare())
        self.assertEqual(self.read_trace(trace, 3), b'LGS')

    def test_guard_kills_stalled_preparation_child(self):
        guard, trace = self.trace_guard('stalled-runner')
        self.assertTrue(guard.prepare())
        # The child would emit X after 0.4 seconds if SIGKILL were omitted.
        self.assertEqual(self.read_trace(trace, 4, timeout=0.7), b'LGB')

    def test_guard_fork_failure_forces_fallback(self):
        with patch.object(os, 'fork', side_effect=OSError('No process slot.')):
            self.assertFalse(restart_module.prepare_child(Mock(), -1, -1, 0.1))

    def test_guard_death_before_prepare_has_truthful_manual_recovery(self):
        guard, trace = self.trace_guard()
        os.kill(guard.pid, signal.SIGKILL)
        os.waitpid(guard.pid, 0)
        for attempt in range(2):
            with self.assertRaisesRegex(InstallError, 'Automatic restart is unavailable'):
                guard.prepare()
        self.assertEqual(self.read_trace(trace, 2, timeout=0.1), b'L')

    def test_guard_unarmed_close_never_reboots(self):
        guard, trace = self.trace_guard()
        guard.close()
        self.assertEqual(self.read_trace(trace, 2, timeout=0.2), b'L')

    def test_late_prepare_reply_cannot_finish_normal_reboot_wait(self):
        guard, trace = self.trace_guard('normal-wait')
        # Simulate a previous request timing out just before its reply arrives.
        guard.timeout = 0
        with self.assertRaises(InstallError):
            guard.prepare()
        guard.timeout = 1
        self.assertFalse(guard.prepare())
        # A late reply with an old request id must not end the 12-second wait.
        # The real eight-second fallback must run before the error is returned.
        os.write(guard.command, restart_module.PACKET.pack(b'P', 1, 0))
        start = time.monotonic()
        with self.assertRaisesRegex(InstallError, 'Could not restart'):
            guard()
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, 8)
        self.assertLess(elapsed, 12)
        self.assertEqual(self.read_trace(trace, 6), b'LGSKWB')

    def test_scope_creation_waits_for_actual_separate_cgroup(self):
        with patch.object(subprocess, 'run') as run, \
                patch.object(Path, 'read_text', side_effect=[
                    '0::/system.slice/emaki-installerd.service\n',
                    '0::/system.slice/emaki-installer-restart.scope\n']), \
                patch.object(time, 'sleep') as sleep:
            restart_module.isolate_guardian()
        argv = run.call_args.args[0]
        self.assertIn('StartTransientUnit', argv)
        self.assertIn('emaki-installer-restart.scope', argv)
        self.assertEqual(argv[argv.index('PIDs') + 1:argv.index('PIDs') + 4],
                         ['au', '1', str(os.getpid())])
        self.assertEqual(argv[argv.index('DefaultDependencies') + 1:argv.index('DefaultDependencies') + 3],
                         ['b', 'true'])
        self.assertEqual(run.call_args.kwargs['timeout'], restart_module.SCOPE_COMMAND_TIMEOUT)
        self.assertNotIn('PartOf', argv)
        self.assertNotIn('BindsTo', argv)
        sleep.assert_called_once_with(0.01)

    def test_guard_scope_command_failures_log_and_allow_installation(self):
        failures = [
            FileNotFoundError('busctl'),
            subprocess.CalledProcessError(1, 'busctl', stderr='Bus not ready'),
            subprocess.CalledProcessError(1, 'busctl', stderr='Unit already exists'),
            subprocess.TimeoutExpired('busctl', 5),
        ]
        for failure in failures:
            with self.subTest(failure=failure), patch.object(subprocess, 'run', side_effect=failure):
                guard, trace = self.trace_guard(isolate=restart_module.isolate_guardian)
                self.assertFalse(guard.adopted)
                with patch.object(Path, 'read_text', side_effect=FileNotFoundError('Scope collected')):
                    guard.check_installation()
                self.assertFalse(guard.prepare())
                self.assertEqual(self.read_trace(trace, 4), b'ILGS')

    def test_guard_cgroup_failures_log_and_allow_installation(self):
        for unreadable in (False, True):
            with self.subTest(unreadable=unreadable), patch.object(subprocess, 'run'), \
                    patch.object(Path, 'read_text',
                                 side_effect=OSError('No cgroup') if unreadable else None,
                                 return_value='0::/system.slice/emaki-installerd.service\n'), \
                    patch.object(restart_module, 'SCOPE_CGROUP_TIMEOUT', 0):
                guard, trace = self.trace_guard(isolate=restart_module.isolate_guardian)
                self.assertFalse(guard.adopted)
                with patch.object(Path, 'read_text', side_effect=FileNotFoundError('Scope collected')):
                    guard.check_installation()
                self.assertFalse(guard.prepare())
                self.assertEqual(self.read_trace(trace, 4), b'ILGS')

    def test_failed_adoption_blocks_until_old_scope_empties_or_disappears(self):
        for removed in (False, True):
            with self.subTest(removed=removed):
                isolate = Mock(side_effect=OSError('Unit already exists'))
                guard, trace = self.trace_guard(isolate=isolate)
                self.assertFalse(guard.adopted)
                self.assertEqual(self.read_trace(trace, 2), b'IL')
                scope = self.shutdown().with_name('cgroup.procs')
                scope.write_text(f'{os.getpid()}\n')
                with patch.object(restart_module, 'SCOPE_PROCS', scope):
                    for attempt in range(2):
                        with self.assertRaises(InstallError) as caught:
                            guard.check_installation()
                        self.assertEqual(caught.exception.code, Code.RESTART_PENDING)
                        self.assertEqual(str(caught.exception), restart_module.PENDING_RESTART_ERROR)
                    if removed:
                        scope.unlink()
                    else:
                        scope.write_text('')
                    guard.check_installation()
                    self.assertFalse(guard.prepare())
                    self.assertEqual(self.read_trace(trace, 2), b'GS')

    def test_late_adoption_allows_own_guardian_but_not_an_old_one(self):
        guard, trace = self.trace_guard(isolate=Mock(side_effect=OSError('Adoption timed out')))
        self.assertFalse(guard.adopted)
        scope = self.shutdown().with_name('cgroup.procs')
        with patch.object(restart_module, 'SCOPE_PROCS', scope):
            scope.write_text(f'{guard.pid}\n')
            guard.check_installation()
            scope.write_text(f'{guard.pid}\n{os.getpid()}\n')
            with self.assertRaises(InstallError) as caught:
                guard.check_installation()
            self.assertEqual(caught.exception.code, Code.RESTART_PENDING)
            scope.write_text(f'{guard.pid}\n')
            guard.check_installation()

    def test_unreadable_scope_blocks_only_until_membership_can_be_checked(self):
        guard, trace = self.trace_guard(isolate=Mock(side_effect=OSError('Bus unavailable')))
        self.assertFalse(guard.adopted)
        scope = self.shutdown().with_name('cgroup.procs')
        scope.mkdir()
        with patch.object(restart_module, 'SCOPE_PROCS', scope):
            with self.assertRaises(InstallError) as caught:
                guard.check_installation()
            self.assertEqual(caught.exception.code, Code.RESTART_PENDING)
            scope.rmdir()
            scope.write_text('')
            guard.check_installation()

    def test_successful_adoption_does_not_block_on_its_own_scope(self):
        guard, trace = self.trace_guard()
        self.assertTrue(guard.adopted)
        scope = self.shutdown().with_name('cgroup.procs')
        scope.write_text(f'{guard.pid}\n')
        with patch.object(restart_module, 'SCOPE_PROCS', scope), \
                patch.object(Path, 'read_text', side_effect=AssertionError('Own scope was polled.')):
            guard.check_installation()

    def test_startup_budget_retains_memory_lock_time_after_scope_adoption(self):
        receive = restart_module.receive_packet
        with patch.object(restart_module, 'receive_packet', wraps=receive) as packet:
            guard, trace = self.trace_guard()
        self.assertEqual(packet.call_args.args[1], 17)
        self.assertEqual(self.read_trace(trace, 1), b'L')

    def test_installer_orders_bus_before_attempting_scope_adoption(self):
        unit = (Path(__file__).resolve().parents[1] / 'systemd/emaki-installerd.service').read_text()
        settings = dict(line.split('=', 1) for line in unit.splitlines() if '=' in line and not line.startswith('#'))
        self.assertIn('dbus.socket', settings['After'].split())
        self.assertIn('dbus.socket', settings['Wants'].split())

    def test_own_restart_survives_scope_stop_until_eight_second_fallback(self):
        guard, trace = self.trace_guard('normal-shutdown')
        self.assertFalse(guard.prepare())
        self.assertEqual(self.read_trace(trace, 3), b'LGS')
        errors = []

        def request():
            try:
                guard()
            except InstallError as exc:
                errors.append(str(exc))

        thread = threading.Thread(target=request)
        start = time.monotonic()
        thread.start()
        try:
            # The fake PID 1 sends a real SIGTERM before returning. A second
            # real signal during the wait must not cancel the fallback either.
            self.assertEqual(self.read_trace(trace, 2), b'KW')
            os.kill(guard.pid, signal.SIGTERM)
            self.assertEqual(self.read_trace(trace, 1, timeout=0.3), b'')
            self.assertTrue(thread.is_alive())
            self.assertEqual(self.read_trace(trace, 1, timeout=9), b'B')
            elapsed = time.monotonic() - start
            self.assertGreaterEqual(elapsed, 8)
            self.assertLess(elapsed, 12)
        finally:
            thread.join(13)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [restart_module.RESTART_ERROR])
        # A failed syscall ends the own-request exemption from scope stops.
        os.kill(guard.pid, signal.SIGTERM)
        deadline = time.monotonic() + 1
        status = None
        while time.monotonic() < deadline:
            pid, value = os.waitpid(guard.pid, os.WNOHANG)
            if pid:
                status = value
                break
            time.sleep(0.01)
        self.assertEqual(status, signal.SIGTERM)

    def test_shutdown_termination_cancels_forced_countdown(self):
        # Exercise PID 1 stopping the scope before shutdown.target, with either
        # a connected or lost client and a caller that ignores termination.
        for connected in (True, False):
            with self.subTest(connected=connected):
                previous = signal.signal(signal.SIGTERM, signal.SIG_IGN)
                try:
                    guard, trace = self.trace_guard('sync-error', fallback_delay=0.4)
                finally:
                    signal.signal(signal.SIGTERM, previous)
                self.assertTrue(guard.prepare())
                self.assertEqual(self.read_trace(trace, 3), b'LGS')
                if not connected:
                    guard.close()
                os.kill(guard.pid, signal.SIGTERM)
                # A mutant that ignores SIGTERM must fail promptly instead of
                # leaving the test stuck waiting for an idle connected child.
                deadline = time.monotonic() + 1
                status = None
                while time.monotonic() < deadline:
                    pid, value = os.waitpid(guard.pid, os.WNOHANG)
                    if pid:
                        status = value
                        break
                    time.sleep(0.01)
                self.assertEqual(status, signal.SIGTERM)
                self.assertEqual(self.read_trace(trace, 1, timeout=0.5), b'')


if __name__ == '__main__':
    unittest.main()
