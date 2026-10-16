#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Storage retention fixtures never inspect or change the running package DB."""
from contextlib import nullcontext
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'update-manager'))
SPEC = importlib.util.spec_from_file_location('storage_arch', Path(__file__).resolve().parents[1] / 'update-manager/storage_arch.py')
backend = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backend)
SNAPSHOT_PATHS = backend.snapshot_paths


class Retention(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.database = root / 'database'
        self.cache = root / 'cache'
        self.holds = root / 'holds.json'
        self.snapshots = root / 'snapshots'
        self.cache.mkdir()
        self.database.mkdir()
        self.snapshots.mkdir()
        self.record(self.database / 'local', 'app', '2-1')
        self.calls = []
        for name, value in [('DATABASE', self.database), ('CACHE', self.cache), ('HOLDS', self.holds),
                            ('SNAPSHOTS', self.snapshots)]:
            self.enterContext(patch.object(backend, name, value))
        self.enterContext(patch.object(backend, 'configuration'))
        self.enterContext(patch.object(backend, 'quota_status', return_value=1))
        self.enterContext(patch.object(backend, 'snapshot_paths', return_value=[]))
        # Fixture roots are user-owned; production ownership checks are tested
        # separately and every filesystem operation remains inside this fixture.
        self.enterContext(patch.object(backend, 'safe', side_effect=self.fixture_safe))
        self.enterContext(patch.object(backend, 'command', side_effect=self.command))

    def fixture_safe(self, path, directory=False):
        backend.require(not path.is_symlink())
        backend.require(path.is_dir() if directory else path.is_file())
        return path

    def record(self, directory, name, version):
        path = directory / f'{name}-{version}'
        path.mkdir(parents=True)
        (path / 'desc').write_text(f'%NAME%\n{name}\n\n%VERSION%\n{version}\n')

    def archive(self, version, name='app'):
        path = self.cache / f'{name}-{version}-x86_64.pkg.tar.zst'
        path.write_text(name + ' ' + version)
        return path

    def command(self, args):
        self.calls.append(args)
        if args[0].endswith('emaki-rollback'):
            return json.dumps({'mode': 'normal'})
        if args[:2] == ['pacman', '-Qp']:
            return Path(args[-1]).read_text()
        if args[:3] == ['btrfs', 'subvolume', 'show']:
            return 'Subvolume ID: ' + ('5' if args[-1] == '/' else '123') + '\n'
        if args[0] == 'vercmp':
            a, b = (int(value.split('-')[0]) for value in args[1:])
            return str((a > b) - (a < b))
        raise AssertionError(args)

    def test_installed_held_and_newest_three_survive(self):
        paths = [self.archive(f'{n}-1') for n in range(1, 8)]
        self.holds.write_text(json.dumps({'holds': {'app': '3-1'}}))
        signature = Path(str(paths[0]) + '.sig')
        signature.write_text('signature')
        orphan = self.archive('1-1', 'unknown')
        answer = backend.cache_inventory()
        self.assertEqual(answer['candidates'], sorted([paths[0], signature, paths[3]]))
        self.assertNotIn(orphan, answer['candidates'])
        self.assertIn('app 2-1', answer['protected_versions'])
        self.assertIn('app 3-1', answer['protected_versions'])
        json.dumps(answer['state'])

    def test_snapshot_versions_survive_regardless_of_age(self):
        for n in range(1, 7):
            self.archive(f'{n}-1')
        snapshot = self.snapshots / '1' / 'snapshot'
        self.record(snapshot / self.database.relative_to('/') / 'local', 'app', '1-1')
        with patch.object(backend, 'snapshot_paths', return_value=[snapshot]):
            answer = backend.cache_inventory()
        self.assertEqual([p.name for p in answer['candidates']], ['app-3-1-x86_64.pkg.tar.zst'])

    def test_unreadable_snapshot_blocks_every_candidate(self):
        with patch.object(backend, 'snapshot_paths', return_value=[self.snapshots / 'missing']):
            with self.assertRaises(backend.Refused):
                backend.cache_inventory()

    def test_corrupt_hold_blocks_every_candidate(self):
        self.holds.write_text('{')
        with self.assertRaises(ValueError):
            backend.cache_inventory()

    def test_symlink_archive_refused(self):
        original = self.archive('1-1')
        link = self.cache / 'link.pkg.tar.zst'
        link.symlink_to(original)
        with self.assertRaises(backend.Refused):
            backend.cache_inventory()

    def test_corrupt_archive_refused(self):
        self.archive('1-1').write_text('not valid metadata')
        with self.assertRaises(backend.Refused):
            backend.cache_inventory()

    def test_pending_rollback_refused(self):
        with patch.object(backend, 'command', return_value='{"mode":"pending"}'):
            with self.assertRaises(backend.Refused):
                backend.cache_inventory()

    def test_metadata_duplicate_refused(self):
        desc = next((self.database / 'local').glob('*/desc'))
        desc.write_text(desc.read_text() + '%VERSION%\n9-1\n')
        with self.assertRaises(backend.Refused):
            backend.read_versions(self.database / 'local')

    def test_qgroup_warning_never_claims_bytes(self):
        result = subprocess.CompletedProcess([], 0, '0/123 100 50\n', 'WARNING: inconsistent qgroups')
        with patch.object(backend, 'snapshot_paths', return_value=[self.snapshots]), \
             patch.object(backend.subprocess, 'run', return_value=result):
            self.assertIsNone(backend.snapshot_usage()['bytes'])

    def test_missing_qgroup_never_claims_lower_bound(self):
        result = subprocess.CompletedProcess([], 0, 'qgroupid rfer excl parent child path\n-------- ---- ---- ------ ----- ----\n0/999 100 50 - - @other\n', '')
        with patch.object(backend, 'snapshot_paths', return_value=[self.snapshots]), \
             patch.object(backend.subprocess, 'run', return_value=result), \
             patch.object(backend, 'command', side_effect=self.command):
            answer = backend.snapshot_usage()
        self.assertIsNone(answer['bytes'])
        self.assertNotIn('exclusive_bytes', answer)

    def test_exclusive_bytes_are_only_lower_bound(self):
        result = subprocess.CompletedProcess([], 0, 'qgroupid rfer excl parent child path\n-------- ---- ---- ------ ----- ----\n0/123 100 50 - - @snapshots/1/snapshot\n', '')
        with patch.object(backend, 'snapshot_paths', return_value=[self.snapshots]), \
             patch.object(backend.subprocess, 'run', return_value=result), \
             patch.object(backend, 'command', side_effect=self.command):
            answer = backend.snapshot_usage()
        self.assertIsNone(answer['bytes'])
        self.assertEqual(answer['exclusive_bytes'], 50)


class RolledBackRetention(unittest.TestCase):
    """All-list fixtures include the first original root, absent from -s."""
    UUID = '12345678-1234-1234-1234-123456789abc'
    FIRST = '@emaki-kept-20261010T120000Z-12345678'
    SECOND = '@emaki-kept-20261011T120000Z-23456789'
    record = Retention.record
    archive = Retention.archive
    fixture_safe = Retention.fixture_safe

    def setUp(self):
        Retention.setUp(self)
        self.top = Path(self.temporary.name) / 'top'
        self.top.mkdir()
        self.roots = {256: '@', 257: '@home', 258: '@snapshots',
                      300: '@snapshots/1/snapshot', 301: '@snapshots/2/snapshot'}
        self.snapshot_ids = {300, 301}
        self.live = 256
        self.enterContext(patch.object(backend, 'snapshot_paths', SNAPSHOT_PATHS))
        self.mount = self.enterContext(patch.object(backend, 'top_mount',
                                                   side_effect=lambda uuid: nullcontext(self.top)))
        for relative in self.roots.values():
            (self.top / relative).mkdir(parents=True)
        self.record(self.top / '@snapshots/1/snapshot' / self.database.relative_to('/') / 'local', 'app', '1-1')
        self.record(self.top / '@snapshots/2/snapshot' / self.database.relative_to('/') / 'local', 'app', '3-1')
        self.first_rollback()

    def receipt(self, name, previous, new):
        directory = self.top / '.emaki-rollback'
        directory.mkdir(exist_ok=True)
        path = directory / (name + '.json')
        path.write_text(json.dumps({'kept': name, 'previous_id': previous,
                                    'new_id': new, 'filesystem': self.UUID}))
        return path

    def first_rollback(self):
        self.roots[256] = self.FIRST
        self.roots[400] = '@'
        self.live = 400
        self.snapshot_ids.add(400)
        root = self.top / self.FIRST
        self.record(root / self.database.relative_to('/') / 'local', 'app', '4-1')
        hold = root / self.holds.relative_to('/')
        hold.parent.mkdir(parents=True, exist_ok=True)
        hold.write_text(json.dumps({'holds': {'app': '5-1'}}))
        self.receipt(self.FIRST, 256, 400)

    def second_rollback(self):
        self.roots[400] = self.SECOND
        self.roots[401] = '@'
        self.live = 401
        self.snapshot_ids.add(401)
        self.record(self.top / self.SECOND / self.database.relative_to('/') / 'local', 'app', '6-1')
        self.receipt(self.SECOND, 400, 401)

    def command(self, args):
        if args[:1] == ['findmnt']:
            return 'btrfs' if args[3] == 'FSTYPE' else self.UUID
        if args[:3] == ['btrfs', 'subvolume', 'list']:
            return ''.join(f'ID {identity} gen 10 top level 5 path '
                           f'{"<FS_TREE>/" if identity == 301 else ""}{relative}\n'
                           for identity, relative in self.roots.items()
                           if '-s' not in args or identity in self.snapshot_ids)
        if args[:3] == ['btrfs', 'subvolume', 'show']:
            if str(args[-1]) == '/':
                identity = self.live
            else:
                relative = str(Path(args[-1]).relative_to(self.top))
                identity = next(identity for identity, name in self.roots.items() if name == relative)
            return f'Subvolume ID: {identity}\n'
        return Retention.command(self, args)

    def inventory(self):
        for n in range(1, 12):
            self.archive(f'{n}-1')
        return backend.cache_inventory()

    def test_first_rollback_keeps_original_non_snapshot_and_holds(self):
        answer = self.inventory()
        self.assertEqual([p.read_text() for p in answer['candidates']],
                         ['app 6-1', 'app 7-1', 'app 8-1'])
        self.assertEqual(answer['state']['snapshots'],
                         ['/' + self.FIRST, '/@snapshots/1/snapshot', '/@snapshots/2/snapshot'])
        self.assertNotIn(256, self.snapshot_ids)
        self.mount.assert_called_once_with(self.UUID)

    def test_second_rollback_keeps_both_original_and_snapshot_roots(self):
        self.second_rollback()
        answer = self.inventory()
        self.assertEqual([p.read_text() for p in answer['candidates']], ['app 7-1', 'app 8-1'])
        self.assertIn('/' + self.SECOND, answer['state']['snapshots'])

    def test_original_layout_without_rollback_records(self):
        del self.roots[400]
        self.roots[256] = '@'
        self.live = 256
        self.snapshot_ids.remove(400)
        (self.top / '.emaki-rollback' / (self.FIRST + '.json')).unlink()
        answer = self.inventory()
        self.assertEqual(answer['state']['snapshots'],
                         ['/@snapshots/1/snapshot', '/@snapshots/2/snapshot'])

    def test_secure_boot_key_subvolume_is_neither_a_root_nor_unknown(self):
        self.roots[500] = '@secureboot'
        (self.top / '@secureboot').mkdir()
        answer = self.inventory()
        self.assertEqual(answer['state']['snapshots'],
                         ['/' + self.FIRST, '/@snapshots/1/snapshot', '/@snapshots/2/snapshot'])

    def test_known_empty_nested_subvolumes_survive_rollback(self):
        self.second_rollback()
        for identity, root in enumerate(('@', self.FIRST, self.SECOND), start=700):
            relative = root + '/var/lib/machines'
            self.roots[identity] = relative
            (self.top / relative).mkdir(parents=True)
        self.assertEqual([p.read_text() for p in self.inventory()['candidates']], ['app 7-1', 'app 8-1'])

    def test_populated_nested_subvolume_fails_closed(self):
        relative = self.FIRST + '/var/lib/portables'
        self.roots[700] = relative
        path = self.top / relative
        path.mkdir(parents=True)
        (path / 'unknown').touch()
        with self.assertRaises(backend.Refused):
            self.inventory()

    def test_retention_state_binds_subvolume_ids_and_receipts(self):
        first = self.inventory()['state']
        self.assertIn(('@', '0/400'), first['subvolumes'])
        self.assertIn((self.FIRST, '0/256'), first['subvolumes'])
        self.assertEqual(first['rollback_records'][self.FIRST]['previous_id'], 256)
        self.assertEqual(first, self.inventory()['state'])
        path = self.top / '.emaki-rollback' / (self.FIRST + '.json')
        record = json.loads(path.read_text())
        record['pinned'] = True
        path.write_text(json.dumps(record))
        self.assertNotEqual(first, self.inventory()['state'])

    def test_mismatched_receipt_id_fails_closed(self):
        self.receipt(self.FIRST, 999, 400)
        with self.assertRaises(backend.Refused):
            self.inventory()

    def test_mismatched_receipt_filesystem_fails_closed(self):
        path = self.receipt(self.FIRST, 256, 400)
        record = json.loads(path.read_text())
        record['filesystem'] = 'another-filesystem'
        path.write_text(json.dumps(record))
        with self.assertRaises(backend.Refused):
            self.inventory()

    def test_live_snapshot_requires_own_exchange_record(self):
        self.receipt(self.FIRST, 256, 999)
        with self.assertRaises(backend.Refused):
            self.inventory()

    def test_unknown_kept_root_even_if_not_snapshot_fails_closed(self):
        (self.top / '.emaki-rollback' / (self.FIRST + '.json')).unlink()
        with self.assertRaises(backend.Refused):
            self.inventory()

    def test_unknown_snapshot_fails_closed(self):
        self.roots[888] = '@unrecorded-copy'
        self.snapshot_ids.add(888)
        with self.assertRaises(backend.Refused):
            self.inventory()

    def test_missing_recorded_root_fails_closed(self):
        del self.roots[256]
        with self.assertRaises(backend.Refused):
            self.inventory()

    def test_incomplete_preparation_fails_closed(self):
        path = self.receipt(self.FIRST, 256, 400)
        record = json.loads(path.read_text())
        record['state'] = 'preparing'
        path.write_text(json.dumps(record))
        with self.assertRaises(backend.Refused):
            self.inventory()

    def test_live_root_must_match_mounted_identity(self):
        self.live = 256
        with self.assertRaises(backend.Refused):
            self.inventory()

    def test_snapshot_limit_counts_original_kept_roots(self):
        with patch.object(backend, 'MAX_SNAPSHOTS', 2), self.assertRaises(backend.Refused):
            self.inventory()

    def test_unreadable_kept_database_fails_closed(self):
        path = next((self.top / self.FIRST / self.database.relative_to('/') / 'local').glob('*/desc'))
        path.unlink()
        with self.assertRaises(backend.Refused):
            self.inventory()

    def test_corrupt_kept_holds_fail_closed(self):
        (self.top / self.FIRST / self.holds.relative_to('/')).write_text('{')
        with self.assertRaises(ValueError):
            self.inventory()

    def test_usage_includes_original_and_later_kept_roots_excludes_live(self):
        for second in (False, True):
            if second:
                self.second_rollback()
            wanted = [identity for identity, path in self.roots.items()
                      if path.startswith('@snapshots/') or path.startswith('@emaki-kept-')]
            table = f'0/{self.live} 10000 9000 - - @\n'
            table += ''.join(f'0/{identity} 100 10 1/9 - {self.roots[identity]}\n' for identity in wanted)
            table += '1/9 1000 200 - ' + ','.join(f'0/{identity}' for identity in wanted) + ' <0 member qgroups>\n'
            result = subprocess.CompletedProcess([], 0, table, '')
            with self.subTest(second=second), patch.object(backend.subprocess, 'run', return_value=result):
                answer = backend.snapshot_usage()
            self.assertEqual(answer['bytes'], 200)
            self.assertEqual(answer['exclusive_bytes'], 10 * len(wanted))


class QuotaAccounting(unittest.TestCase):
    TABLE = ('Qgroupid Referenced Exclusive Parent Child Path\n'
             '-------- ---------- --------- ------ ----- ----\n'
             '0/5 1000 600 - - @\n'
             '0/123 500 50 1/9 - @snapshots/1/snapshot\n'
             '0/124 500 70 1/9 - @snapshots/2/snapshot\n'
             '1/9 800 220 - 0/123,0/124 <0 member qgroups>\n')

    def usage(self, table=None, code=0, error='', status=10):
        paths = [Path('/.snapshots/1/snapshot'), Path('/.snapshots/2/snapshot')]
        result = subprocess.CompletedProcess([], code, table or self.TABLE, error)
        def identity(path):
            return '0/5' if path == '/' else ('0/123' if path == paths[0] else '0/124')
        with patch.object(backend, 'snapshot_paths', return_value=paths), \
             patch.object(backend, 'quota_status', return_value=status), \
             patch.object(backend, 'subvolume_id', side_effect=identity), \
             patch.object(backend.subprocess, 'run', return_value=result):
            return backend.snapshot_usage()

    def test_exact_aggregate_includes_shared_snapshot_extents(self):
        answer = self.usage()
        self.assertEqual(answer['bytes'], 220)
        self.assertEqual(answer['exclusive_bytes'], 120)

    def test_live_root_child_excludes_aggregate(self):
        table = self.TABLE.replace('0/5 1000 600 - -', '0/5 1000 600 1/9 -')
        table = table.replace('0/123,0/124 <', '0/5,0/123,0/124 <')
        answer = self.usage(table)
        self.assertIsNone(answer['bytes'])
        self.assertEqual(answer['exclusive_bytes'], 120)

    def test_unrelated_child_excludes_aggregate(self):
        table = self.TABLE + '0/777 50 10 1/9 - @unrelated\n'
        table = table.replace('0/123,0/124 <', '0/123,0/124,0/777 <')
        self.assertIsNone(self.usage(table)['bytes'])

    def test_nested_aggregate_resolves_all_descendants(self):
        table = self.TABLE.replace('0/124 500 70 1/9', '0/124 500 70 2/8')
        table = table.replace('1/9 800 220 - 0/123,0/124', '1/9 500 50 2/8 0/123')
        table += '2/8 800 220 - 1/9,0/124 <0 member qgroups>\n'
        self.assertEqual(self.usage(table)['bytes'], 220)

    def test_multiple_exact_aggregates_are_not_assumed_interchangeable(self):
        table = self.TABLE.replace('500 50 1/9', '500 50 1/9,1/10')
        table = table.replace('500 70 1/9', '500 70 1/9,1/10')
        table += '1/10 800 220 - 0/123,0/124 <0 member qgroups>\n'
        answer = self.usage(table)
        self.assertIsNone(answer['bytes'])
        self.assertEqual(answer['exclusive_bytes'], 120)

    def test_missing_nested_member_fails_closed(self):
        table = self.TABLE.replace('0/123,0/124 <', '0/123,0/124,1/99 <')
        answer = self.usage(table)
        self.assertIsNone(answer['bytes'])
        self.assertNotIn('exclusive_bytes', answer)

    def test_nonreciprocal_membership_fails_closed(self):
        table = self.TABLE.replace('0/124 500 70 1/9', '0/124 500 70 -')
        self.assertNotIn('exclusive_bytes', self.usage(table))

    def test_duplicate_identity_fails_closed(self):
        self.assertNotIn('exclusive_bytes', self.usage(self.TABLE + '0/123 1 1 - - other\n'))

    def test_disabled_and_inconsistent_remain_unavailable(self):
        for code, error in [(1, 'ERROR: quotas not enabled'), (0, 'WARNING: qgroup data inconsistent')]:
            with self.subTest(code=code):
                answer = self.usage(code=code, error=error)
                self.assertIsNone(answer['bytes'])
                self.assertNotIn('exclusive_bytes', answer)

    def status(self, flags=1, count=1, version=1):
        def ioctl(descriptor, request, buffer, mutable):
            self.assertEqual(struct.unpack_from('=Q', buffer)[0], 8)
            self.assertEqual(struct.unpack_from('=I', buffer, 56)[0], 240)
            self.assertEqual(request, 0xd0009411)
            struct.pack_into('=I', buffer, 64, count)
            struct.pack_into('=QQQII', buffer, 104, 10, 0, 0, 240, 40)
            struct.pack_into('<4Q', buffer, 136, version, 10, flags, 0)
        with patch.object(backend.os, 'open', return_value=99), \
             patch.object(backend.os, 'close'), patch.object(backend.fcntl, 'ioctl', side_effect=ioctl):
            return backend.quota_status()

    def test_full_healthy_quota_status(self):
        self.assertEqual(self.status(), 10)

    def test_simple_disabled_rescan_unknown_and_inconsistent_flags_refused(self):
        for flags in (0, 3, 5, 9, 17):
            with self.subTest(flags=flags), self.assertRaises(backend.Refused):
                self.status(flags=flags)

    def test_missing_status_or_future_version_refused(self):
        for arguments in ({'count': 0}, {'version': 2}):
            with self.subTest(arguments=arguments), self.assertRaises(backend.Refused):
                self.status(**arguments)


class ReadOnlyMount(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name)
        self.top = self.parent / 'mount'
        self.top.mkdir()
        self.enterContext(patch.object(backend.tempfile, 'mkdtemp', return_value=str(self.top)))
        self.libc = Mock()
        self.libc.unshare.return_value = 0
        self.enterContext(patch.object(backend.ctypes, 'CDLL', return_value=self.libc))

    def test_private_readonly_mount_unmounts_on_metadata_failure(self):
        with patch.object(backend, 'command') as command:
            with self.assertRaisesRegex(ValueError, 'metadata'):
                with backend.top_mount(RolledBackRetention.UUID) as top:
                    self.assertEqual(top, self.top)
                    raise ValueError('metadata')
        self.libc.unshare.assert_called_once_with(0x00020000)
        self.assertEqual(command.call_args_list[0].args[0], ['mount', '--make-rprivate', '/'])
        self.assertIn('ro,subvolid=5,nosuid,nodev,noexec', command.call_args_list[1].args[0])
        self.assertEqual(command.call_args_list[-1].args[0], ['umount', str(self.top)])
        self.assertFalse(self.top.exists())

    def test_mount_failure_removes_only_empty_mountpoint(self):
        with patch.object(backend, 'command', side_effect=['', backend.Refused('mount')]):
            with self.assertRaises(backend.Refused):
                with backend.top_mount(RolledBackRetention.UUID):
                    self.fail('mount failed')
        self.assertFalse(self.top.exists())

    def test_unmount_failure_never_traverses_mount_contents(self):
        sentinel = self.top / 'keep'
        sentinel.write_text('kept')
        with patch.object(backend, 'command', side_effect=['', '', backend.Refused('umount')]):
            with self.assertRaises(backend.Refused):
                with backend.top_mount(RolledBackRetention.UUID):
                    pass
        self.assertEqual(sentinel.read_text(), 'kept')

    def test_expired_scan_still_gets_unmount_budget(self):
        token = backend.DEADLINE.set(1)
        deadlines = []
        try:
            with patch.object(backend, 'command', side_effect=lambda args: deadlines.append(backend.DEADLINE.get())):
                with backend.top_mount(RolledBackRetention.UUID):
                    pass
            self.assertEqual(deadlines, [1, 1, None])
            self.assertEqual(backend.DEADLINE.get(), 1)
        finally:
            backend.DEADLINE.reset(token)


class Locks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.enterContext(patch.object(backend, 'DATABASE', self.root))
        self.enterContext(patch.object(backend, 'LOCKS', (self.root / 'guard',)))
        self.enterContext(patch.object(backend, 'configuration'))
        self.enterContext(patch.object(backend, 'safe', side_effect=lambda path, *args: path))
        original = os.fstat
        def owned(fd):
            fields = list(original(fd))
            fields[4] = 0
            return os.stat_result(fields)
        self.enterContext(patch.object(backend.os, 'fstat', side_effect=owned))

    def test_database_lock_exists_only_while_guard_held(self):
        with backend.package_guard():
            self.assertTrue((self.root / 'db.lck').exists())
        self.assertFalse((self.root / 'db.lck').exists())

    def test_existing_database_lock_is_preserved(self):
        lock = self.root / 'db.lck'
        lock.write_text('someone else')
        with self.assertRaises(backend.Refused):
            with backend.package_guard():
                self.fail('must not enter')
        self.assertEqual(lock.read_text(), 'someone else')

    def test_abandoned_database_lock_explains_manual_recovery(self):
        lock = self.root / 'db.lck'
        lock.write_text('gone creator')
        with patch.object(backend, 'lock_message', return_value='The lock appears abandoned. Only then remove it.', create=True):
            with self.assertRaisesRegex(backend.Refused, 'abandoned.*Only then remove'):
                with backend.package_guard():
                    self.fail('must not enter')
        self.assertEqual(lock.read_text(), 'gone creator')

    def test_live_database_lock_says_wait(self):
        lock = self.root / 'db.lck'
        lock.touch()
        with patch.object(backend, 'lock_message', return_value='Wait for it to finish.', create=True):
            with self.assertRaisesRegex(backend.Refused, 'Wait for it to finish'):
                with backend.package_guard():
                    self.fail('must not enter')
        self.assertTrue(lock.exists())

    def test_guard_keeps_descriptor_for_owner_detection(self):
        from emaki_package_lock import lock_message, LIVE
        with backend.package_guard():
            self.assertEqual(lock_message(self.root / 'db.lck'), LIVE)

    def test_exception_releases_owned_database_lock(self):
        with self.assertRaisesRegex(RuntimeError, 'fixture'):
            with backend.package_guard():
                raise RuntimeError('fixture')
        self.assertFalse((self.root / 'db.lck').exists())


class Paths(unittest.TestCase):
    def test_cache_roots_only_queries_fixed_configuration(self):
        with patch.object(backend, 'command', return_value=str(backend.CACHE) + '/\n') as query, \
             patch.object(backend, 'safe', side_effect=lambda path, *args: path) as validate:
            self.assertEqual(backend.cache_roots(), [backend.CACHE])
        query.assert_called_once_with(['pacman-conf', 'CacheDir'])
        validate.assert_called_once_with(backend.CACHE, True)

    def test_custom_cache_roots_refused(self):
        for value in ('/home/someone/cache\n', str(backend.CACHE) + '/../../other\n',
                      str(backend.CACHE) + '\n/another/cache\n'):
            with self.subTest(value=value), patch.object(backend, 'command', return_value=value):
                with self.assertRaises(backend.Refused):
                    backend.cache_roots()

    def test_inventory_entry_limit_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('one', 'two', 'three'):
                (root / name).touch()
            with self.assertRaises(backend.Refused):
                backend.entries(root, limit=2)

    def test_expired_inventory_never_starts_next_command(self):
        token = backend.DEADLINE.set(1)
        try:
            with patch.object(backend.time, 'monotonic', return_value=2), \
                 patch.object(backend.subprocess, 'run') as run:
                with self.assertRaises(backend.Refused):
                    backend.command(['pacman-conf', 'CacheDir'])
                run.assert_not_called()
        finally:
            backend.DEADLINE.reset(token)

    def test_each_command_uses_remaining_shared_budget(self):
        token = backend.DEADLINE.set(30)
        try:
            result = subprocess.CompletedProcess([], 0, 'ok', '')
            with patch.object(backend.time, 'monotonic', return_value=23), \
                 patch.object(backend.subprocess, 'run', return_value=result) as run:
                backend.command(['pacman-conf', 'CacheDir'])
                self.assertEqual(run.call_args.kwargs['timeout'], 7)
        finally:
            backend.DEADLINE.reset(token)

    def test_world_writable_parent_refused(self):
        with self.assertRaises(backend.Refused):
            backend.safe(Path('/tmp'), True)



if __name__ == '__main__':
    unittest.main()
