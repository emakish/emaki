# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Restart preparation and requests without touching the host power state."""
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock, patch

from emaki_installer import daemon
from emaki_installer.errors import Code, InstallError
from emaki_installer.protocol import Controller, Job
from support import FakeInventory, config


class RestartTests(unittest.TestCase):
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
        self.assertIn('skipped', controller.job.terminal['warnings'][0])
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
                                                            'Could not restart. Try again.'))
        self.assertFalse(controller.handle({'type': 'reboot', 'id': 'restart'})[0]['ok'])
        self.assertFalse(controller.stopping)

    def test_preparation_failure_is_reported_before_removal(self):
        controller = self.controller()
        controller.prepare_reboot_fn = Mock(side_effect=InstallError(Code.COMMAND_FAILED,
                                          'Could not prepare to restart. Keep the USB stick connected and try again.'))
        response = controller.handle({'type': 'prepare_reboot', 'id': 'prepare'})[0]
        self.assertFalse(response['ok'])
        self.assertIn('Keep the USB stick connected', response['msg'])
        self.assertFalse(controller.stopping)

    def test_resident_restart_syncs_and_locks_before_prompt(self):
        libc = Mock()
        libc.mlockall.return_value = 0
        libc.reboot.return_value = -1
        libc.sleep.return_value = 0
        restart = daemon.ResidentReboot(libc=libc)
        restart.prepare()
        self.assertEqual([call[0] for call in libc.mock_calls], ['sync', 'mlockall'])
        libc.reset_mock()
        with self.assertRaisesRegex(InstallError, 'Could not restart'):
            restart()
        self.assertEqual([call[0] for call in libc.mock_calls], ['kill', 'sleep', 'reboot'])
        libc.reboot.assert_called_once_with(0x01234567)

    def test_residency_failure_does_not_allow_restart(self):
        libc = Mock()
        libc.mlockall.return_value = -1
        restart = daemon.ResidentReboot(libc=libc)
        with self.assertRaisesRegex(InstallError, 'Keep the USB stick connected'):
            restart.prepare()
        with self.assertRaises(InstallError):
            restart()
        libc.kill.assert_not_called()
        libc.reboot.assert_not_called()

    def test_interrupted_wait_still_reaches_resident_fallback(self):
        libc = Mock()
        libc.mlockall.return_value = 0
        libc.sleep.side_effect = [6, 0]
        restart = daemon.ResidentReboot(libc=libc)
        restart.prepare()
        with patch('builtins.open', side_effect=AssertionError('Restart read a file.')):
            with self.assertRaisesRegex(InstallError, 'Could not restart'):
                restart()
        self.assertEqual([call.args for call in libc.sleep.call_args_list], [(8,), (6,)])
        libc.reboot.assert_called_once_with(0x01234567)


if __name__ == '__main__':
    unittest.main()
