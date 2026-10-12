# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The journal check cannot write its origin, and failures forbid resize."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from emaki_installer import inventory
from emaki_installer.arch_backend import resize_partition
from emaki_installer.errors import Code, InstallError
from emaki_installer.ntfs_probe import CLEANUP_REFUSAL, REFUSAL, check_ntfs_journal
from emaki_installer.planner import make_plan
from support import alongside_on
from test_alongside import windows, gpt_text, OK


class JournalRunner:
    def __init__(self, fail=None, status=None):
        self.commands = []
        self.options = []
        self.fail = fail
        self.status = status
        self.active_mapping = None

    def run(self, args, **kwargs):
        self.commands.append(args)
        self.options.append(kwargs)
        if args[:2] == ['dmsetup', 'create']:
            self.active_mapping = args[2]
        if self.fail and self.fail(args):
            raise InstallError(Code.COMMAND_FAILED, 'injected failure')
        if args[:2] == ['dmsetup', 'remove']:
            self.active_mapping = None
        if args[:2] == ['dmsetup', 'info']:
            return self.active_mapping or ''
        if args[:3] == ['losetup', '--find', '--show']:
            return '/dev/loop10' if '--read-only' in args else '/dev/loop11'
        if args[:2] == ['blockdev', '--getro']:
            return '1'
        if args[:2] == ['blockdev', '--getsize64']:
            return '1048576'
        if args[:2] == ['dmsetup', 'status']:
            return self.status or '0 2048 snapshot 16/131072 8'
        return ''


class RacingJournalRunner(JournalRunner):
    """A udev reader outlives the probe and the first settle/remove pair."""
    def __init__(self, probe_error=None):
        super().__init__()
        self.mapping = None
        self.loops = set()
        self.readers = 0
        self.busy_removals = 0
        self.probe_error = probe_error

    def run(self, args, **kwargs):
        answer = super().run(args, **kwargs)
        if args[:3] == ['losetup', '--find', '--show']:
            self.loops.add(answer)
        if args[:2] == ['dmsetup', 'create']:
            self.mapping = args[2]
            self.readers = 2
        if args[0] == 'ntfs-3g.probe' and self.probe_error:
            raise self.probe_error
        if args[:2] == ['udevadm', 'settle']:
            self.readers = max(0, self.readers - 1)
        if args[:2] == ['dmsetup', 'remove']:
            if self.readers:
                self.busy_removals += 1
                raise InstallError(Code.COMMAND_FAILED, 'Device or resource busy')
            self.mapping = None
        if args[:2] == ['losetup', '--detach']:
            if self.mapping:
                raise AssertionError('Backing loop detached while snapshot still exists')
            self.loops.remove(args[-1])
        return answer


class JournalTests(unittest.TestCase):
    def check(self, runner):
        with tempfile.TemporaryDirectory() as work:
            check_ntfs_journal(runner, '/dev/windows', 1048576, work=work)
            self.assertEqual(list(Path(work).iterdir()), [])

    def test_only_snapshot_is_opened_writable_and_cleanup_precedes_success(self):
        runner = JournalRunner()
        self.check(runner)
        commands = runner.commands
        self.assertIn(['losetup', '--find', '--show', '--read-only', '--sector-size', '512',
                       '--', '/dev/windows'], commands)
        create = next(c for c in commands if c[:2] == ['dmsetup', 'create'])
        self.assertEqual(create[-1], '0 2048 snapshot /dev/loop10 /dev/loop11 N 8')
        self.assertIn(['ntfs-3g.probe', '--readwrite', '/dev/mapper/' + create[2]], commands)
        self.assertEqual(commands[-3:], [['dmsetup', 'remove', '--retry', create[2]],
                                        ['losetup', '--detach', '/dev/loop11'],
                                        ['losetup', '--detach', '/dev/loop10']])
        self.assertFalse(any('merge' in ' '.join(c) for c in commands))

    def test_udev_race_repeated_clean_probes_leave_no_mapping_or_loops(self):
        runner = RacingJournalRunner()
        for iteration in range(12):
            with self.subTest(iteration=iteration):
                self.check(runner)
                self.assertIsNone(runner.mapping)
                self.assertEqual(runner.loops, set())
        self.assertEqual(runner.busy_removals, 12)
        for command, options in zip(runner.commands, runner.options):
            if command[0] == 'udevadm':
                self.assertEqual(command, ['udevadm', 'settle', '--timeout=5'])
                self.assertEqual(options['timeout'], 5)
            if command[:2] == ['dmsetup', 'remove']:
                self.assertIn('--retry', command)
                self.assertEqual(options['timeout'], 10)

    def test_hibernation_diagnostic_survives_racing_cleanup(self):
        diagnostic = 'Windows is hibernated, refused to mount.'
        runner = RacingJournalRunner(InstallError(Code.COMMAND_FAILED, 'probe exited',
                                                   output=diagnostic, returncode=14))
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaises(InstallError) as refused:
                check_ntfs_journal(runner, '/dev/windows', 1048576, work=work)
            self.assertEqual(refused.exception.output, diagnostic)
            self.assertEqual(list(Path(work).iterdir()), [])
        self.assertIsNone(runner.mapping)
        self.assertEqual(runner.loops, set())
        self.assertEqual(runner.busy_removals, 1)

    def test_failed_settle_still_removes_snapshot_but_refuses(self):
        runner = JournalRunner(fail=lambda c: c[0] == 'udevadm')
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaisesRegex(InstallError, CLEANUP_REFUSAL):
                check_ntfs_journal(runner, '/dev/windows', 1048576, work=work)
            self.assertEqual(list(Path(work).iterdir()), [])
        self.assertTrue(any(c[:2] == ['dmsetup', 'remove'] for c in runner.commands))
        self.assertEqual(runner.commands[-2:], [['losetup', '--detach', '/dev/loop11'],
                                               ['losetup', '--detach', '/dev/loop10']])

    def test_dirty_journal_setup_probe_status_and_cleanup_fail_closed(self):
        for prefix in (['losetup', '--find'], ['blockdev', '--getro'],
                       ['dmsetup', 'create'], ['ntfs-3g.probe', '--readwrite'],
                       ['dmsetup', 'status'], ['dmsetup', 'remove'], ['losetup', '--detach']):
            with self.subTest(prefix=prefix), self.assertRaises(InstallError):
                self.check(JournalRunner(fail=lambda c: c[:len(prefix)] == prefix))
        for status in ('0 2048 snapshot Invalid', '0 2048 snapshot Overflow', '',
                       '0 4096 snapshot 16/131072 8'):
            runner = JournalRunner(status=status or 'unknown')
            with self.subTest(status=status), self.assertRaises(InstallError):
                self.check(runner)

    def test_cannot_detach_snapshot_backing_devices_after_remove_failure(self):
        runner = JournalRunner(fail=lambda c: c[:2] == ['dmsetup', 'remove'])
        with self.assertRaises(InstallError):
            self.check(runner)
        self.assertFalse(any(c[:2] == ['losetup', '--detach'] for c in runner.commands))
        self.assertEqual(sum(c[:2] == ['dmsetup', 'remove'] for c in runner.commands), 3)

    def test_failed_overlay_detach_still_releases_origin(self):
        runner = JournalRunner(fail=lambda c: c == ['losetup', '--detach', '/dev/loop11'])
        with self.assertRaisesRegex(InstallError, CLEANUP_REFUSAL):
            self.check(runner)
        self.assertIn(['losetup', '--detach', '/dev/loop10'], runner.commands)

    def test_failed_remove_with_confirmed_absence_cleans_backing_but_refuses(self):
        runner = JournalRunner()
        original = runner.run
        def run(command, **options):
            answer = original(command, **options)
            if command[:2] == ['dmsetup', 'remove']:
                raise InstallError(Code.COMMAND_FAILED, 'udev wait failed after removal')
            return answer
        runner.run = run
        with tempfile.TemporaryDirectory() as work:
            with self.assertRaisesRegex(InstallError, CLEANUP_REFUSAL):
                check_ntfs_journal(runner, '/dev/windows', 1048576, work=work)
            self.assertEqual(list(Path(work).iterdir()), [])
        self.assertIsNone(runner.active_mapping)
        self.assertEqual(runner.commands[-2:], [['losetup', '--detach', '/dev/loop11'],
                                               ['losetup', '--detach', '/dev/loop10']])

    def test_failed_mapping_listing_cannot_authorize_loop_detach(self):
        runner = JournalRunner(fail=lambda c: c[:2] in (
            ['dmsetup', 'remove'], ['dmsetup', 'info']))
        with self.assertRaisesRegex(InstallError, CLEANUP_REFUSAL):
            self.check(runner)
        self.assertFalse(any(c[:2] == ['losetup', '--detach'] for c in runner.commands))

    def test_readonly_origin_required_and_unknown_loop_output_refuses(self):
        for answer in ('0', '/dev/windows', '/dev/loop10 extra'):
            runner = JournalRunner()
            original = runner.run
            def run(c, **kw):
                result = original(c, **kw)
                if answer == '0' and c[:2] == ['blockdev', '--getro']:
                    return answer
                if answer != '0' and c[:3] == ['losetup', '--find', '--show']:
                    return answer
                return result
            runner.run = run
            with self.subTest(answer=answer), self.assertRaises(InstallError):
                self.check(runner)
            self.assertFalse(any(c[0] == 'ntfs-3g.probe' for c in runner.commands))

    def test_journal_refusal_blocks_offer_and_exact_resize_before_any_write(self):
        config, inv = windows()
        disk = inv['disks'][0]
        part = disk['partitions'][1]
        runner = Mock()
        def run(c, **kw):
            if c[:2] == ['sgdisk', '--verify']:
                return 'No problems found.'
            if c[0] == 'sgdisk':
                return gpt_text(disk['partitions'][int(c[1].split('=')[1]) - 1]['_gpt'])
            if c[0] == 'ntfsresize':
                return OK
            raise AssertionError(c)
        runner.run.side_effect = run
        failure = InstallError(Code.SHRINK_BOUNDS, REFUSAL)
        with alongside_on(), patch.object(inventory, 'check_ntfs_journal', side_effect=failure), \
                patch.object(inventory, 'bitlocker_signature', return_value=False):
            plan = make_plan(config, inv)
            offer = inventory.Inventory(runner)._ntfs_shrink(disk, part)
            self.assertIn(REFUSAL, offer['reason'])
            runner.reset_mock()
            changed = Mock()
            with self.assertRaises(InstallError) as refused:
                resize_partition(plan, runner, changed)
            self.assertEqual(refused.exception.code, Code.SHRINK_BOUNDS)
            self.assertEqual(str(refused.exception), REFUSAL)
            changed.assert_not_called()
            self.assertFalse(any(c.args[0][0] == 'ntfsresize' for c in runner.run.call_args_list))


if __name__ == '__main__':
    unittest.main()
