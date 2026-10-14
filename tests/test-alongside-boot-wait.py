#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Alongside evidence waits for boot services, using a simulated guest and clock."""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('alongside', ROOT / 'tests/vm/alongside-update-check.py')
alongside = importlib.util.module_from_spec(spec)
spec.loader.exec_module(alongside)


PREFIX = 'Update boot protection: '
JOURNALS = {
    'accepted': [PREFIX + 'Waiting for a usable local login or desktop session.',
                 PREFIX + 'A usable session is present; waiting for fifteen stable seconds.',
                 PREFIX + 'The updated boot was accepted.', 'Finished Accept an updated system.'],
    'timed_out': [PREFIX + 'Waiting for a usable local login or desktop session.',
                  PREFIX + 'Acceptance timed out: no session stayed usable for fifteen seconds.'],
    'deferred': [PREFIX + 'Acceptance deferred: no update from an earlier boot is pending.'],
    'unconfirmed': ['Finished Accept an updated system.'],
}


class Guest:
    vm = Path('/unused')

    def __init__(self, mode='success', journal='accepted', menu=None, menu_done=25):
        self.now = 0
        self.mode = mode
        self.journal = journal
        # The snapshot menu follows the other boot jobs by default.
        self.menu = mode if menu is None else menu
        self.menu_done = menu_done
        self.read_at = []
        self.shots = []
        self.polled = set()

    def wait(self, *args):
        pass

    def sleep(self, seconds):
        self.now += seconds

    def shot(self, label):
        self.shots.append(self.now)
        self.read_at.append(self.now)

    def sudo(self, command, **kwargs):
        if command == 'journalctl -b -o cat -u emaki-update-boot --no-pager':
            self.read_at.append(self.now)
            return SimpleNamespace(returncode=0, stdout='\n'.join(JOURNALS[self.journal]).encode() + b'\n',
                                   stderr=b'')
        if command.startswith('systemctl show '):
            unit = command.split()[2]
            self.polled.add(unit)
            mode = self.menu if unit == 'emaki-snapshot-menu.service' else self.mode
            done = self.now >= {'emaki-update-boot.service': 20,
                                'emaki-snapshot-menu.service': self.menu_done}.get(unit, 25)
            state = dict(LoadState='loaded', ActiveState='inactive', Result='success',
                         ExecMainStatus='0', ExecMainExitTimestampMonotonic='0',
                         ConditionResult='yes', ConditionTimestampMonotonic='1')
            if mode == 'skip':
                state['ConditionResult'] = 'no'
            elif mode == 'missing':
                state['LoadState'] = 'not-found'
            elif mode == 'failed':
                state.update(ActiveState='failed', Result='exit-code', ExecMainStatus='1')
            elif mode == 'never':
                # Inactive and never started: no condition check and no exit recorded.
                state['ConditionTimestampMonotonic'] = '0'
            elif done and mode != 'timeout':
                state['ExecMainExitTimestampMonotonic'] = '25000000'
            elif self.now >= 2:
                state['ActiveState'] = 'activating'
            output = '\n'.join(f'{key}={value}' for key, value in state.items()).encode()
        else:
            self.read_at.append(self.now)
            output = b'boot journal\n'
        return SimpleNamespace(returncode=0, stdout=output, stderr=b'')


class BootWaitTests(unittest.TestCase):
    def run_boot(self, guest):
        self.records = {}
        def record(vm, name, data):
            self.records[name] = data if isinstance(data, bytes) else data.encode()
        with patch.object(alongside.time, 'monotonic', side_effect=lambda: guest.now), \
                patch.object(alongside.time, 'sleep', side_effect=guest.sleep), \
                patch.object(alongside, 'record', side_effect=record), redirect_stdout(io.StringIO()):
            alongside.wait_boot(guest, 'trial')

    def acceptance(self):
        return json.loads(self.records['boot-trial-acceptance.json'])['state']

    def test_ssh_before_trial_completion_does_not_capture_early(self):
        guest = Guest()
        self.run_boot(guest)
        self.assertTrue(guest.read_at)
        self.assertGreaterEqual(min(guest.read_at), 25)
        self.assertEqual(guest.polled, {'emaki-update-boot.service', 'emaki-boot-complete.service',
                                        'emaki-snapshot-menu.service'})

    def test_condition_skip_needs_no_trial_delay(self):
        guest = Guest('skip')
        self.run_boot(guest)
        self.assertEqual(guest.now, 0)
        self.assertEqual(len(guest.polled), 3)
        self.assertEqual(self.acceptance(), 'not_required')

    def wait_services(self, guest):
        with patch.object(alongside.time, 'monotonic', side_effect=lambda: guest.now), \
                patch.object(alongside.time, 'sleep', side_effect=guest.sleep):
            return alongside.wait_boot_services(guest)

    def test_a_running_snapshot_menu_delays_the_journal_and_menu(self):
        # The menu job is still running when the other jobs are done; grub.cfg and
        # the boot journal are read only after it ends.
        for finish in (80, 25 + 600):
            with self.subTest(finish=finish):
                guest = Guest(menu_done=finish)
                self.run_boot(guest)
                self.assertGreaterEqual(min(guest.read_at), finish)
                self.assertIn('emaki-snapshot-menu.service', guest.polled)
                self.assertIn('grub.cfg', self.records)
                self.assertEqual(self.acceptance(), 'accepted')
        outcomes = self.wait_services(Guest(menu_done=80))
        self.assertEqual(outcomes['emaki-snapshot-menu.service'], 'completed')

    def test_a_failed_or_stuck_snapshot_menu_fails_with_its_journal(self):
        for mode in ('failed', 'missing', 'timeout'):
            with self.subTest(mode=mode):
                guest = Guest(menu=mode)
                with self.assertRaisesRegex(RuntimeError, 'emaki-snapshot-menu.service') as raised:
                    self.run_boot(guest)
                self.assertEqual(guest.shots, [])
                self.assertEqual(sorted(self.records), ['boot-trial-failed.txt'])
                self.assertEqual(self.records['boot-trial-failed.txt'],
                                 (str(raised.exception) + '\n').encode() + b'boot journal\n')
                if mode == 'timeout':
                    # Its own five minutes and the boot loader retry before it.
                    self.assertGreater(guest.now, 25 + 600)
                    self.assertLessEqual(guest.now, 25 + 630)

    def test_a_skipped_snapshot_menu_is_not_waited_for(self):
        guest = Guest(menu='skip')
        self.run_boot(guest)
        self.assertEqual(min(guest.read_at), 25)
        self.assertEqual(self.acceptance(), 'accepted')
        outcomes = self.wait_services(Guest(menu='skip'))
        self.assertEqual(outcomes, {'emaki-update-boot.service': 'completed',
                                    'emaki-boot-complete.service': 'completed',
                                    'emaki-snapshot-menu.service': 'skipped'})
        # Never started is not skipped: without a condition record the wait goes on.
        guest = Guest(menu='never')
        with self.assertRaisesRegex(RuntimeError, 'timed out waiting for boot service emaki-snapshot-menu'):
            self.run_boot(guest)

    def test_failed_missing_and_stuck_services_keep_the_boot_journal(self):
        for mode in ('failed', 'missing', 'timeout'):
            with self.subTest(mode=mode):
                guest = Guest(mode)
                with self.assertRaisesRegex(RuntimeError, 'emaki-update-boot') as raised:
                    self.run_boot(guest)
                self.assertEqual(guest.shots, [])
                self.assertLessEqual(guest.now, 420)
                self.assertEqual(sorted(self.records), ['boot-trial-failed.txt'])
                self.assertEqual(self.records['boot-trial-failed.txt'],
                                 (str(raised.exception) + '\n').encode() + b'boot journal\n')

    def test_a_journal_capture_failure_keeps_the_wait_failure(self):
        guest = Guest('failed')
        with patch.object(alongside, 'boot_status', side_effect=OSError('connection lost')), \
                self.assertRaisesRegex(RuntimeError, 'boot service emaki-update-boot.service failed'):
            self.run_boot(guest)
        self.assertIn(b'Boot journal capture failed: connection lost', self.records['boot-trial-failed.txt'])

    def test_trial_end_is_not_acceptance(self):
        for journal in ('accepted', 'deferred', 'unconfirmed'):
            with self.subTest(journal=journal):
                self.run_boot(Guest(journal=journal))
                self.assertEqual(self.acceptance(), journal)
                self.assertIn('boot-trial.txt', self.records)
        guest = Guest(journal='timed_out')
        with self.assertRaisesRegex(RuntimeError, 'not accepted'):
            self.run_boot(guest)
        # The evidence of the rejected boot is complete before the phase fails.
        self.assertEqual(self.acceptance(), 'timed_out')
        self.assertEqual(len(guest.shots), 1)
        self.assertIn('boot-trial.txt', self.records)
        self.assertIn(b'Acceptance timed out', self.records['boot-trial-update.txt'])


if __name__ == '__main__':
    unittest.main()
