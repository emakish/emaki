#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise real isolated cleanup paths, preview binding and filesystem accounting."""
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import emaki_storage as storage


class Storage(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='storage-')
        self.base = Path(self.temp.name)
        self.trash = self.base / 'Trash'
        (self.trash / 'files').mkdir(parents=True)
        (self.trash / 'info').mkdir()
        self.env = patch.dict(os.environ, XDG_DATA_HOME=str(self.base))
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def file(self, relative, text='fixture', root=None):
        path = (root or self.trash / 'files') / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    @contextlib.contextmanager
    def root_fixture(self):
        original = os.fstat
        def fstat(fd):
            value = original(fd)
            fields = {name: getattr(value, name) for name in dir(value) if name.startswith('st_')}
            fields['st_uid'] = 0
            return types.SimpleNamespace(**fields)
        with patch.object(storage.os, 'fstat', side_effect=fstat), patch.object(storage.os, 'geteuid', return_value=0):
            yield

    def test_trash_preview_does_not_delete_and_cleanup_is_exact(self):
        self.file('folder/one')
        self.file('folder/two')
        self.file('folder.trashinfo', root=self.trash / 'info')
        outside = self.file('personal', root=self.base)
        plan = storage.dispatch('preview', 'trash')
        self.assertEqual(plan['status'], 'preview')
        self.assertEqual(len(plan['entries']), 4)
        self.assertTrue(outside.exists())
        self.assertTrue((self.trash / 'files/folder/one').exists())
        result = storage.dispatch('cleanup', 'trash', plan['token'])
        self.assertEqual(result['status'], 'cleaned')
        self.assertEqual(result['removed_count'], 4)
        self.assertEqual(list((self.trash / 'files').iterdir()), [])
        self.assertTrue(outside.exists())

    def test_preview_lists_newlines_and_symlinks_without_following_them(self):
        outside = self.file('personal', root=self.base)
        link = self.trash / 'files/link\nname'
        link.symlink_to(outside)
        preview = storage.dispatch('preview', 'trash')
        self.assertEqual(preview['entries'][0]['path'], str(link))
        self.assertEqual(storage.dispatch('cleanup', 'trash', preview['token'])['status'], 'cleaned')
        self.assertEqual(outside.read_text(), 'fixture')
        self.assertFalse(link.is_symlink())

    def test_stale_content_and_added_entries_refuse_before_any_delete(self):
        first = self.file('one')
        second = self.file('two')
        for mutation in (lambda: first.write_text('changed'), lambda: self.file('three')):
            preview = storage.dispatch('preview', 'trash')
            mutation()
            with self.assertRaisesRegex(RuntimeError, 'changed after'):
                storage.dispatch('cleanup', 'trash', preview['token'])
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())

    def test_inode_replacement_with_same_size_refuses(self):
        item = self.file('one')
        preview = storage.dispatch('preview', 'trash')
        replacement = self.file('replacement')
        replacement.replace(item)
        with self.assertRaises(RuntimeError):
            storage.dispatch('cleanup', 'trash', preview['token'])
        self.assertTrue(item.exists())

    def test_trash_directory_symlink_is_refused(self):
        files = self.trash / 'files'
        files.rmdir()
        files.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(OSError):
            storage.dispatch('preview', 'trash')

    def test_elevated_trash_is_refused(self):
        with patch.object(storage.os, 'geteuid', return_value=0):
            with self.assertRaisesRegex(RuntimeError, 'own account'):
                storage.dispatch('preview', 'trash')

    def test_system_cleanup_requires_root(self):
        with patch.object(storage.os, 'geteuid', return_value=1000), patch.object(storage, 'inventory') as inventory:
            for kind in ('logs', 'cache'):
                with self.assertRaisesRegex(RuntimeError, 'Authentication'):
                    storage.dispatch('preview', kind)
            inventory.assert_not_called()

    def test_incomplete_trash_refuses_but_absent_trash_is_empty(self):
        (self.trash / 'info').rmdir()
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            storage.dispatch('preview', 'trash')
        (self.trash / 'files').rmdir()
        self.trash.rmdir()
        self.assertEqual(storage.dispatch('preview', 'trash')['entries'], [])

    def test_mount_boundary_and_device_entries_refuse(self):
        self.file('one')
        original = storage.mount_id
        def mounted(fd):
            return original(fd) + (1 if stat.S_ISREG(os.fstat(fd).st_mode) else 0)
        with patch.object(storage, 'mount_id', side_effect=mounted):
            with self.assertRaisesRegex(RuntimeError, 'nested mount'):
                storage.dispatch('preview', 'trash')
        os.mkfifo(self.trash / 'files/pipe')
        with self.assertRaisesRegex(RuntimeError, 'regular file'):
            storage.dispatch('preview', 'trash')

    def test_entry_limit_fails_without_partial_preview(self):
        self.file('one')
        self.file('two')
        with patch.object(storage, 'MAX_ENTRIES', 1):
            with self.assertRaisesRegex(RuntimeError, 'too many'):
                storage.dispatch('preview', 'trash')

    def test_hardlinks_outside_trash_survive(self):
        personal = self.file('personal', root=self.base)
        os.link(personal, self.trash / 'files/one')
        preview = storage.dispatch('preview', 'trash')
        self.assertEqual(preview['reclaimable_bytes'], 0)
        self.assertEqual(storage.dispatch('cleanup', 'trash', preview['token'])['status'], 'cleaned')
        self.assertEqual(personal.read_text(), 'fixture')

    def test_multiple_links_in_trash_are_all_removed(self):
        first = self.file('one')
        os.link(first, self.trash / 'files/two')
        preview = storage.dispatch('preview', 'trash')
        result = storage.dispatch('cleanup', 'trash', preview['token'])
        self.assertEqual(result['status'], 'cleaned')
        self.assertEqual(result['removed_count'], 2)

    def test_failed_payload_removal_keeps_trash_restore_metadata(self):
        self.file('one')
        metadata = self.file('one.trashinfo', root=self.trash / 'info')
        plan = storage.trash_inventory()
        with patch.object(storage.os, 'unlink', side_effect=PermissionError):
            result = storage.clean(plan, plan.token())
        self.assertEqual(result['status'], 'error')
        self.assertTrue(metadata.exists())

    def test_changed_file_between_scan_and_clean_refuses_before_delete(self):
        path = self.file('one')
        plan = storage.trash_inventory()
        token = plan.token()
        path.write_text('after inventory')
        with self.assertRaises(RuntimeError):
            storage.clean(plan, token)
        self.assertTrue(path.exists())

    def test_mid_cleanup_failure_reports_partial_without_overclaim(self):
        self.file('one')
        self.file('two')
        plan = storage.trash_inventory()
        unlink = os.unlink
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise PermissionError()
            return unlink(*args, **kwargs)
        with patch.object(storage.os, 'unlink', side_effect=fail_second):
            result = storage.clean(plan, plan.token())
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['removed_count'], 1)
        self.assertIn('Remaining files were kept', result['message'])

    def test_only_old_archived_journals_are_selected(self):
        logs = self.base / 'journals'
        machine = logs / ('a' * 32)
        machine.mkdir(parents=True)
        archived = 'system@' + 'b' * 32 + '-' + 'c' * 16 + '-' + 'd' * 16 + '.journal'
        old = self.file(archived, root=machine)
        active = self.file('system.journal', root=machine)
        corrupt = self.file('system.journal~', root=machine)
        recent = self.file(archived.replace('system@', 'user-1000@'), root=machine)
        os.utime(old, (time.time() - 8 * 86400,) * 2)
        with self.root_fixture(), patch.object(storage, 'LOG_ROOTS', (logs,)):
            preview = storage.dispatch('preview', 'logs')
            self.assertEqual([entry['path'] for entry in preview['entries']], [str(old)])
            result = storage.dispatch('cleanup', 'logs', preview['token'])
        self.assertEqual(result['status'], 'cleaned')
        self.assertFalse(old.exists())
        self.assertTrue(all(path.exists() for path in (active, corrupt, recent)))

    def test_cache_uses_backend_candidates_and_retention_token(self):
        cache = self.base / 'cache'
        cache.mkdir()
        old = self.file('old.pkg.tar.zst', root=cache)
        protected = self.file('protected.pkg.tar.zst', root=cache)
        source = dict(roots=[cache], candidates=[old], protected_versions=['protected=1'], state={'generation': 1})
        with self.root_fixture(), patch.object(storage.storage_arch, 'package_guard', contextlib.nullcontext), patch.object(storage.storage_arch, 'cache_inventory', return_value=source):
            preview = storage.dispatch('preview', 'cache')
            source['state']['generation'] = 2
            with self.assertRaises(RuntimeError):
                storage.dispatch('cleanup', 'cache', preview['token'])
            source['state']['generation'] = 1
            result = storage.dispatch('cleanup', 'cache', preview['token'])
        self.assertEqual(result['status'], 'cleaned')
        self.assertFalse(old.exists())
        self.assertTrue(protected.exists())

    def test_cache_candidate_outside_root_is_rejected(self):
        cache = self.base / 'cache'
        cache.mkdir()
        outside = self.file('personal', root=self.base)
        source = dict(roots=[cache], candidates=[outside], protected_versions=[], state={})
        with self.root_fixture(), patch.object(storage.storage_arch, 'cache_inventory', return_value=source):
            with self.assertRaises(RuntimeError):
                storage.cache_inventory()
        self.assertTrue(outside.exists())

    def test_json_errors_and_fixed_command_grammar(self):
        for args in ([], ['cleanup', 'trash', '/personal', '--json'], ['preview', 'snapshots', '--json'],
                     ['status', '--root', str(self.base)], ['cleanup', 'trash', 'a' * 64, '--json', 'extra']):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), patch.object(storage, 'dispatch') as dispatch:
                self.assertEqual(storage.main(args), 2)
                dispatch.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())['status'], 'error')

    def test_installed_import_does_not_add_source_backend_path(self):
        installed = self.base / 'libexec' / 'emaki'
        installed.mkdir(parents=True)
        (installed / 'emaki_storage.py').write_text((ROOT / 'scripts/emaki_storage.py').read_text())
        for name, content in [('emaki_paths.py', ''), ('storage_arch.py', ''),
                              ('emaki_update_space.py', 'mount_details = None\n')]:
            (installed / name).write_text(content)
        code = ('import sys; sys.path.insert(0, ' + repr(str(installed)) + '); '
                'import emaki_storage; assert ' + repr(str(installed.parent / 'update-manager'))
                + ' not in sys.path')
        subprocess.run([sys.executable, '-I', '-c', code], check=True, capture_output=True, text=True)

    def test_btrfs_pool_uses_ioctl_uuid_not_subvolume_fsid(self):
        def ioctl(fd, number, data):
            self.assertEqual(number, 0x8400941f)
            data[16:32] = bytes.fromhex('12345678123456781234567812345678')
        with patch.object(storage, 'mount_details', return_value=('/dev/fixture', 'btrfs')), patch.object(storage.fcntl, 'ioctl', side_effect=ioctl):
            data = storage.filesystem(self.base)
        self.assertEqual(data['pool'], 'btrfs:12345678-1234-5678-1234-567812345678')
        self.assertGreater(data['total_bytes'], data['available_bytes'])

    def test_shared_pool_marking_and_free_space_deduplication(self):
        def filesystem(path):
            return dict(path=str(path), pool='same', available_bytes=123, shared_with=None)
        with patch.object(storage, 'filesystem', side_effect=filesystem):
            pools = storage.filesystems()
            self.assertEqual(pools[1]['shared_with'], '/')
            self.assertEqual(storage.free_pools(['/', str(self.base)]), {'same': 123})

    def test_status_partial_failures_are_unknown_not_zero(self):
        with patch.object(storage.storage_arch, 'snapshot_usage', side_effect=RuntimeError), patch.object(storage.storage_arch, 'cache_roots', side_effect=RuntimeError), patch.object(storage, 'LOG_ROOTS', ()), patch.object(storage, 'filesystems', return_value=[]):
            result = storage.status()
        self.assertEqual(result['status'], 'ready')
        self.assertIsNone(result['categories']['snapshots']['bytes'])
        self.assertIsNone(result['categories']['cache']['bytes'])
        self.assertEqual(result['categories']['logs']['bytes'], 0)

    def test_cache_usage_counts_foreign_owned_download_directories(self):
        cache = self.base / 'cache'
        download = cache / 'download-abcdef'
        download.mkdir(parents=True, mode=0o700)
        partial = self.file('partial', root=download)
        archive = self.file('package.pkg.tar.zst', root=cache)
        original = storage.child_info

        def foreign(fd, name, expected_mount):
            info = original(fd, name, expected_mount)
            fields = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
            fields['st_uid'] = 970 if name in ('download-abcdef', 'partial') else 0
            return types.SimpleNamespace(**fields)

        real_fstat = os.fstat
        foreign_inodes = {download.stat().st_ino, partial.stat().st_ino}

        def fstat(fd):
            info = real_fstat(fd)
            fields = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
            fields['st_uid'] = 970 if info.st_ino in foreign_inodes else 0
            return types.SimpleNamespace(**fields)

        with patch.object(storage.os, 'fstat', side_effect=fstat), \
                patch.object(storage, 'child_info', side_effect=foreign), \
                patch.object(storage.storage_arch, 'cache_roots', return_value=[cache]), \
                patch.object(storage.storage_arch, 'snapshot_usage', return_value={}), \
                patch.object(storage, 'LOG_ROOTS', ()), patch.object(storage, 'filesystems', return_value=[]):
            result = storage.status()
            self.assertEqual(result['categories']['cache']['bytes'],
                             sum(path.stat().st_blocks * 512 for path in (download, partial, archive)))
            for kind in ('cache', 'logs'):
                with self.subTest(kind=kind), self.assertRaisesRegex(RuntimeError, 'ownership'):
                    fields = {key: getattr(partial.stat(), key) for key in dir(partial.stat())
                              if key.startswith('st_')}
                    fields['st_uid'] = 970
                    storage.Inventory(kind, 0).add(cache, Path('partial'), types.SimpleNamespace(**fields))

    def test_extent_estimates_exclude_shared_and_refuse_uncertain_maps(self):
        def mapped(flags):
            def ioctl(fd, number, data):
                self.assertEqual(number, 0xc020660b)
                self.assertEqual(struct.unpack_from('=I', data, 16)[0], 0)
                struct.pack_into('=I', data, 20, len(flags))
                for index, value in enumerate(flags):
                    struct.pack_into('=Q', data, 32 + index * 56 + 16, 4096)
                    struct.pack_into('=I', data, 32 + index * 56 + 40, value)
            return ioctl
        for flags, expected in [([0, 1], 8192), ([0x2000, 1], 4096), ([0x2001], 0),
                                ([9], None), ([3], None), ([0x201], None), ([0], None), ([], 0)]:
            with self.subTest(flags=flags), patch.object(storage.fcntl, 'ioctl', side_effect=mapped(flags)):
                self.assertEqual(storage.file_extents(0), expected)

    def test_preview_keeps_known_lower_bound_with_compressed_or_unreadable_files(self):
        for name in ('known', 'compressed', 'inline', 'unreadable'):
            self.file(name)

        def extents(fd):
            name = Path(os.readlink(f'/proc/self/fd/{fd}')).name
            if name == 'unreadable':
                raise OSError('fixture unreadable map')
            return 4096 if name == 'known' else None

        with patch.object(storage, 'file_extents', side_effect=extents):
            preview = storage.dispatch('preview', 'trash')
        self.assertIsNone(preview['reclaimable_bytes'])
        self.assertEqual(preview['reclaimable_lower_bound_bytes'], 4096)
        self.assertEqual(preview['reclaimable_unknown_files'], 3)

    def test_preview_all_unknown_and_all_known_estimates(self):
        self.file('one')
        for extent, expected, unknown in ((None, None, 1), (4096, 4096, 0), (0, 0, 0)):
            with self.subTest(extent=extent), patch.object(storage, 'file_extents', return_value=extent):
                preview = storage.dispatch('preview', 'trash')
            self.assertEqual(preview['reclaimable_bytes'], expected)
            self.assertEqual(preview['reclaimable_lower_bound_bytes'], extent or 0)
            self.assertEqual(preview['reclaimable_unknown_files'], unknown)


if __name__ == '__main__':
    unittest.main()
