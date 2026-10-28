# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stale whole-disk signatures must be removed before any Windows resize."""
import copy
import json
from contextlib import nullcontext
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from emaki_installer import arch_backend
from emaki_installer.arch_backend import DeviceOperations
from emaki_installer.errors import Code, InstallError
from emaki_installer.planner import make_plan
from support import RecordingRunner, alongside_on
from test_alongside import windows


class StaleZfsAlongsideTests(unittest.TestCase):
    def setUp(self):
        switch = alongside_on()
        switch.start()
        self.addCleanup(switch.stop)
        config, inv = windows()
        inv['disks'][0]['_stale_label'] = 'zfs_member'
        self.plan = make_plan(config, inv)
        self.runner = RecordingRunner()
        self.ops = DeviceOperations(self.plan, self.runner)
        self.ops.reviewed_disk = Mock(side_effect=lambda: nullcontext(42))
        self.ops.check_disk_sequence = Mock()
        self.ops.changed = Mock()
        self.ops.verify_alongside_layout = Mock()
        metadata = patch.object(arch_backend, 'gpt_metadata_regions',
                                return_value=[(0, 34 * 512), (self.plan.disk['size_bytes'] - 33 * 512, 33 * 512)],
                                create=True)
        metadata.start()
        self.addCleanup(metadata.stop)
        self.device = '/proc/self/fd/42'
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        work = patch.object(arch_backend, 'WORK', Path(self.temp.name))
        work.start()
        self.addCleanup(work.stop)
        self.table = {'partitiontable': {
            'label': 'gpt', 'sectorsize': 512, 'firstlba': 34,
            'lastlba': self.plan.disk['size_bytes'] // 512 - 34,
            'partitions': [dict(node=self.device + 'p' + str(p['number']),
                                start=p['start_bytes'] // 512, size=p['size_bytes'] // 512,
                                uuid=p['_partuuid'], type=p['_parttype'])
                           for p in self.plan.disk['partitions']]}}
        self.runner.responses[('sfdisk', '--json', self.device)] = json.dumps(self.table)
        self.signatures = [{'type': 'zfs_member', 'offset': '0x44000', 'length': 16}]
        self.runner.responses[('wipefs', '--json', '--output', 'TYPE,OFFSET,LENGTH',
                               '--types', 'zfs_member', self.device)] = json.dumps({'signatures': self.signatures})
        self.disk_probe = ('blkid', '--probe', '--output', 'export', self.device)
        self.runner.responses[self.disk_probe] = 'PTTYPE=gpt\n'
        for part in self.plan.disk['partitions']:
            self.runner.responses[self.part_probe(part)] = f'TYPE={part["fs"]}\nUUID={part["uuid"]}\n'

    def part_probe(self, part):
        return ('blkid', '--probe', '--output', 'export', '--offset', str(part['start_bytes']),
                '--size', str(part['size_bytes']), self.device)

    def test_wipe_and_fresh_verification_precede_windows_resize(self):
        before = copy.deepcopy(self.plan.disk['partitions'])

        def resize(*args, **kwargs):
            commands = [cmd for cmd, ignored in self.runner.commands]
            writes = [cmd for cmd in commands if cmd[:2] == ['wipefs', '--force']]
            self.assertEqual(len(writes), 1, 'stale ZFS must be removed before Windows resize')
            wipe = writes[0]
            self.assertEqual(wipe[:5], ['wipefs', '--force', '--all', '--types', 'zfs_member'])
            backup = Path(wipe[5].removeprefix('--backup='))
            self.assertEqual(backup.parent, Path(self.temp.name))
            self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
            self.assertEqual(wipe[6:], [self.device])
            after = commands[commands.index(wipe) + 1:]
            self.assertIn(list(self.disk_probe), after)
            for part in before:
                self.assertIn(list(self.part_probe(part)), after)
            self.assertTrue(all(opts.get('pass_fds') == (42,) for cmd, opts in self.runner.commands))
            self.ops.changed.assert_called_once()
            self.assertEqual(self.plan.disk['partitions'], before)

        with patch.object(arch_backend, 'resize_partition', side_effect=resize) as shrink, \
                patch.object(arch_backend, 'zero_zfs_labels') as zero:
            self.ops.partition(SimpleNamespace(wipe=False))
        shrink.assert_called_once()
        zero.assert_not_called()

    def assert_stopped(self, *, changed=False):
        with patch.object(arch_backend, 'resize_partition') as shrink, \
                patch.object(arch_backend, 'zero_zfs_labels') as zero:
            with self.assertRaises(InstallError):
                self.ops.partition(SimpleNamespace(wipe=False))
        shrink.assert_not_called()
        zero.assert_not_called()
        self.ops.verify_alongside_layout.assert_not_called()
        self.assertEqual(self.ops.changed.called, changed)

    def test_surviving_label_refuses_before_windows_resize(self):
        self.runner.responses[self.disk_probe] = 'TYPE=zfs_member\nPTTYPE=gpt\n'
        self.assert_stopped(changed=True)

    def test_failed_wipe_or_disk_probe_refuses_before_windows_resize(self):
        for command in ('wipefs', 'blkid'):
            with self.subTest(command=command):
                self.ops.changed.reset_mock()
                self.runner.fail = lambda argv: (argv[:2] == ['wipefs', '--force'] if command == 'wipefs'
                                                else tuple(argv) == self.disk_probe)
                self.assert_stopped(changed=True)

    def test_partition_filesystem_change_after_wipe_refuses(self):
        original_run = self.runner.run

        def run(argv, **kwargs):
            result = original_run(argv, **kwargs)
            if argv[:2] == ['wipefs', '--force']:
                self.runner.responses[self.part_probe(self.plan.disk['partitions'][1])] = 'TYPE=ntfs\nUUID=changed\n'
            return result

        self.runner.run = run
        self.assert_stopped(changed=True)

    def test_partition_probe_failure_and_changed_review_refuse_before_wipe(self):
        part = self.plan.disk['partitions'][1]
        self.runner.fail = lambda argv: tuple(argv) == self.part_probe(part)
        self.assert_stopped()
        self.runner.fail = None
        self.runner.responses[self.part_probe(part)] = 'TYPE=ext4\nUUID=uuid-2\n'
        self.assert_stopped()

    def test_partition_and_both_gpt_regions_are_never_wiped(self):
        part = self.plan.disk['partitions'][1]
        end = (self.table['partitiontable']['lastlba'] + 1) * 512
        for start, length in ((part['start_bytes'], 16), (part['start_bytes'] - 8, 16),
                              (part['start_bytes'] + part['size_bytes'] - 8, 16),
                              (16 * 1024, 16), (end - 8, 16), (end, 16)):
            with self.subTest(start=start, length=length):
                self.runner.commands.clear()
                self.runner.responses[('wipefs', '--json', '--output', 'TYPE,OFFSET,LENGTH',
                                       '--types', 'zfs_member', self.device)] = json.dumps({'signatures': [
                    {'type': 'zfs_member', 'offset': hex(start), 'length': length}]})
                self.assert_stopped()
                self.assertFalse(any(cmd[:2] == ['wipefs', '--force'] for cmd, _ in self.runner.commands))

    def test_bad_signature_report_refuses_before_wipe(self):
        key = ('wipefs', '--json', '--output', 'TYPE,OFFSET,LENGTH', '--types', 'zfs_member', self.device)
        for value in ('invalid json', '{}', '{"signatures": null}',
                      '{"signatures": [{"type": "gpt", "offset": "0x44000", "length": 16}]}',
                      '{"signatures": [{"type": "zfs_member", "offset": "0x44000", "length": 0}]}'):
            with self.subTest(value=value):
                self.runner.responses[key] = value
                self.assert_stopped()

    def test_changed_gpt_after_wipe_refuses(self):
        original_run = self.runner.run

        def run(argv, **kwargs):
            result = original_run(argv, **kwargs)
            if argv[:2] == ['wipefs', '--force']:
                self.table['partitiontable']['partitions'][0]['uuid'] = 'changed'
                self.runner.responses[('sfdisk', '--json', self.device)] = json.dumps(self.table)
            return result

        self.runner.run = run
        self.assert_stopped(changed=True)

    def test_already_clear_label_still_verifies_without_a_write(self):
        self.runner.responses[('wipefs', '--json', '--output', 'TYPE,OFFSET,LENGTH',
                               '--types', 'zfs_member', self.device)] = '{"signatures": []}'
        with patch.object(arch_backend, 'resize_partition') as shrink:
            self.ops.partition(SimpleNamespace(wipe=False))
        shrink.assert_called_once()
        self.ops.changed.assert_not_called()
        self.assertFalse(any(cmd[:2] == ['wipefs', '--force'] for cmd, _ in self.runner.commands))

    def test_disk_without_stale_label_uses_existing_path(self):
        self.plan.disk.pop('_stale_label')
        with patch.object(arch_backend, 'resize_partition') as shrink:
            self.ops.partition(SimpleNamespace(wipe=False))
        shrink.assert_called_once_with(self.plan, self.runner, self.ops.changed)
        self.assertEqual(self.runner.commands, [])
        self.ops.reviewed_disk.assert_not_called()


if __name__ == '__main__':
    unittest.main()
