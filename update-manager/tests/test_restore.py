#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check restore receipt mapping and the fixed rollback delegation offline."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import restore


class RestorePoints(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def point(self, number, kind='pre', description='pacman -Syu', pre='', stamp='2026-10-10 12:00:00'):
        directory = self.root / str(number)
        (directory / 'snapshot').mkdir(parents=True)
        (directory / 'info.xml').write_text(
            f'<snapshot><type>{kind}</type><num>{number}</num><date>{stamp}</date>'
            f'<description>{escape(description)}</description><pre_num>{pre}</pre_num></snapshot>')
        return directory

    def test_recognizes_only_full_update_commands_without_execution(self):
        for command in ('pacman -Syu', 'pacman -Su', '/bin/pacman -Syyuu --noconfirm',
                        'pacman --sync --sysupgrade', 'pacman -S --sysupgrade'):
            with self.subTest(command=command):
                self.assertTrue(restore.full_update(command))
        for command in ('pacman -S package', 'pacman -R package', 'manual before update',
                        'echo pacman -Syu', 'pacman -S -- -u', 'pacman "', 'pacman -Qu'):
            with self.subTest(command=command):
                self.assertFalse(restore.full_update(command))

    def test_mapping_post_to_pre_and_order_and_incomplete_boundary(self):
        self.point(2)
        self.point(3, 'post', 'linux emaki-config', pre='2', stamp='2026-10-10 12:05:00')
        self.point(4, stamp='2026-10-10 13:00:00')
        self.point(5, 'single')
        self.point(6, description='pacman -S application')
        rows = restore.list_points(self.root)
        self.assertEqual([row['id'].split(':')[0] for row in rows], ['4', '2'])
        self.assertFalse(rows[0]['completed'])
        self.assertIn('[PLACEHOLDER]', rows[0]['notice'])
        self.assertTrue(rows[1]['completed'])
        self.assertEqual(rows[1]['packages'], 'linux emaki-config')
        self.assertTrue(restore.TOKEN.fullmatch(rows[1]['id']))

    def test_timestamp_is_converted_before_local_date(self):
        self.point(2, stamp='2026-10-10 00:01:00')
        original = os.environ.get('TZ')
        try:
            os.environ['TZ'] = 'EST5'
            time.tzset()
            row = restore.list_points(self.root)[0]
            self.assertTrue(row['date'].startswith('2026-10-09 19:01:00'))
            self.assertEqual(row['timestamp'], '2026-10-10T00:01:00+00:00')
        finally:
            if original is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = original
            time.tzset()

    def test_malformed_deleted_symlink_and_oversize_receipts_are_not_offered(self):
        good = self.point(2)
        for number, content in ((3, '<broken'), (4, '<!DOCTYPE snapshot><snapshot/>'),
                                (5, ' ' * (restore.LIMIT + 1)), (6, '<snapshot><num>7</num></snapshot>')):
            (self.point(number) / 'info.xml').write_text(content)
        missing = self.point(8)
        (missing / 'snapshot').rmdir()
        (self.root / '9').symlink_to(good, target_is_directory=True)
        linked = self.point(10)
        (linked / 'info.xml').unlink()
        (linked / 'info.xml').symlink_to(good / 'info.xml')
        self.assertEqual(len(restore.list_points(self.root)), 1)

    def test_ambiguous_or_older_post_does_not_claim_completed_transaction(self):
        self.point(2)
        self.point(3, 'post', pre='2', stamp='2026-10-09 12:00:00')
        self.assertFalse(restore.list_points(self.root)[0]['completed'])
        self.point(4, 'post', pre='2')
        self.point(5, 'post', pre='2')
        self.assertFalse(restore.list_points(self.root)[0]['completed'])

    def test_stale_selection_and_injection_never_reach_rollback(self):
        directory = self.point(2)
        token = restore.list_points(self.root)[0]['id']
        (directory / 'info.xml').write_text((directory / 'info.xml').read_text().replace('12:00:00', '12:01:00'))
        with patch.object(restore, 'boot_state', return_value={'mode': 'normal'}), patch.object(restore, 'run') as run:
            for value in (token, '2', '../../etc/passwd', token + ';reboot'):
                with self.subTest(value=value), self.assertRaises(restore.Refused):
                    restore.restore_point(value, self.root)
            run.assert_not_called()

    def test_success_delegates_exact_selected_snapshot_and_never_reboots(self):
        self.point(2)
        token = restore.list_points(self.root)[0]['id']
        with patch.object(restore, 'boot_state', return_value={'mode': 'normal'}), \
                patch.object(restore, 'run', return_value=subprocess.CompletedProcess([], 0, 'ready', '')) as run:
            result = restore.restore_point(token, self.root)
        run.assert_called_once_with([restore.ROLLBACK, 'snapshot', '2'], timeout=None)
        self.assertTrue(result['ok'])
        self.assertTrue(result['restart'])
        self.assertIn('/home', result['message'])

    def test_refusal_preserves_existing_rollback_details(self):
        self.point(2)
        token = restore.list_points(self.root)[0]['id']
        with patch.object(restore, 'boot_state', return_value={'mode': 'normal'}), \
                patch.object(restore, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'A package transaction is active.')):
            result = restore.restore_point(token, self.root)
        self.assertFalse(result['ok'])
        self.assertFalse(result['restart'])
        self.assertEqual(result['details'], 'A package transaction is active.')

    def test_error_after_root_exchange_retains_restart_requirement(self):
        self.point(2)
        token = restore.list_points(self.root)[0]['id']
        with patch.object(restore, 'boot_state', side_effect=[{'mode': 'normal'}, {'mode': 'pending'}]), \
                patch.object(restore, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'sync failed')):
            result = restore.restore_point(token, self.root)
        self.assertFalse(result['ok'])
        self.assertTrue(result['restart'])

    def test_unsupported_snapshot_and_pending_boots_refuse_before_mutation(self):
        self.point(2)
        token = restore.list_points(self.root)[0]['id']
        for mode in ('snapshot', 'pending', 'unsupported'):
            with self.subTest(mode=mode), patch.object(restore, 'boot_state', return_value={'mode': mode}), \
                    patch.object(restore, 'run') as run, self.assertRaises(restore.Refused):
                restore.restore_point(token, self.root)
            run.assert_not_called()

    def test_fixed_cli_validates_argument_count_and_privilege(self):
        for arguments in ([], ['restore'], ['list', 'extra'], ['restore', '1'], ['anything'],
                          ['refresh-index', 'extra']):
            with self.subTest(arguments=arguments), self.assertRaises(restore.Refused):
                restore.validate(arguments)
        for arguments in (['restore', '2:' + 'a' * 64], ['refresh-index']):
            output = io.StringIO()
            with patch.object(restore.os, 'geteuid', return_value=1000), redirect_stdout(output), \
                    patch.object(restore, 'run') as run, patch.object(restore, 'list_points') as listing:
                self.assertEqual(restore.main(arguments), 1)
            self.assertFalse(json.loads(output.getvalue())['ok'])
            run.assert_not_called()
            listing.assert_not_called()

    def test_list_reports_pending_restore_without_index_or_lock(self):
        output = io.StringIO()
        with patch.object(restore.os, 'geteuid', return_value=1000), \
                patch.object(restore, 'boot_state', return_value={'mode': 'pending'}), redirect_stdout(output), \
                patch.object(restore, 'read_index') as reader, patch.object(restore.fcntl, 'flock') as lock:
            self.assertEqual(restore.main(['list']), 1)
        reader.assert_not_called()
        lock.assert_not_called()
        self.assertTrue(json.loads(output.getvalue())['restorePending'])

    def test_restoring_still_takes_shared_update_lock(self):
        output = io.StringIO()
        with patch.object(restore, 'LOCK', self.root / 'lock'), patch.object(restore.os, 'geteuid', return_value=0), \
                patch.object(restore.fcntl, 'flock', side_effect=BlockingIOError), redirect_stdout(output), \
                patch.object(restore, 'boot_state') as state:
            self.assertEqual(restore.main(['restore', '2:' + 'a' * 64]), 1)
        state.assert_not_called()
        self.assertIn('running', json.loads(output.getvalue())['message'])

    def root_owned_stat(self, descriptor):
        # Only ownership is simulated: these tests never create host root files.
        metadata = self.real_fstat(descriptor)
        return SimpleNamespace(st_uid=0, st_mode=metadata.st_mode,
                               st_size=metadata.st_size, st_mtime=metadata.st_mtime)

    def publish(self):
        with patch.object(restore, 'INDEX', self.root / 'index.json'), \
                patch.object(restore, 'SNAPSHOTS', self.root), patch.object(restore.os, 'geteuid', return_value=0):
            return restore.refresh_index()

    def read_public(self):
        self.real_fstat = os.fstat
        with patch.object(restore, 'INDEX', self.root / 'index.json'), \
                patch.object(restore.os, 'fstat', side_effect=self.root_owned_stat):
            return restore.read_index()

    def test_public_inventory_lists_without_root_snapshot_access_or_prompt(self):
        snapshot = self.point(2)
        snapshot.chmod(0o700)
        (snapshot / 'info.xml').chmod(0o600)
        published = self.publish()
        self.assertEqual((self.root / 'index.json').stat().st_mode & 0o777, 0o644)
        self.assertEqual(snapshot.stat().st_mode & 0o777, 0o700)
        self.assertEqual((snapshot / 'info.xml').stat().st_mode & 0o777, 0o600)
        output = io.StringIO()
        self.real_fstat = os.fstat
        with patch.object(restore.os, 'geteuid', return_value=1000), \
                patch.object(restore, 'INDEX', self.root / 'index.json'), \
                patch.object(restore.os, 'fstat', side_effect=self.root_owned_stat), \
                patch.object(restore, 'boot_state', return_value={'mode': 'normal'}), \
                patch.object(restore, 'list_points', side_effect=AssertionError('private snapshot read')), \
                patch.object(restore.fcntl, 'flock', side_effect=AssertionError('privileged lock')), redirect_stdout(output):
            self.assertEqual(restore.main(['list']), 0)
        self.assertEqual(json.loads(output.getvalue())['points'], published['points'])

    def test_scan_failure_replaces_previous_inventory_with_unavailable(self):
        self.point(2)
        self.publish()
        with patch.object(restore, 'list_points', side_effect=PermissionError):
            self.assertTrue(self.publish()['error'])
        with self.assertRaises(restore.Refused):
            self.read_public()
        self.assertEqual(json.loads((self.root / 'index.json').read_text())['points'], [])

    def test_refresh_observes_new_completed_and_deleted_points(self):
        self.point(2)
        self.publish()
        self.assertFalse(self.read_public()[0]['completed'])
        self.point(3, 'post', pre='2')
        self.publish()
        self.assertTrue(self.read_public()[0]['completed'])
        (self.root / '2' / 'snapshot').rmdir()
        self.publish()
        self.assertEqual(self.read_public(), [])

    def test_atomic_replace_does_not_follow_existing_index_symlink(self):
        victim = self.root / 'private'
        victim.write_text('private')
        victim.chmod(0o600)
        (self.root / 'index.json').symlink_to(victim)
        self.publish()
        self.assertEqual(victim.read_text(), 'private')
        self.assertEqual(victim.stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.root / 'index.json').is_symlink())
        self.assertFalse(list(self.root.glob('.emaki-restore-points-*')))

    def test_missing_symlink_nonregular_writable_stale_future_and_bad_index_refused(self):
        index = self.root / 'index.json'
        with self.assertRaises(restore.Refused):
            self.read_public()
        index.symlink_to(self.root / 'missing')
        with self.assertRaises(restore.Refused):
            self.read_public()
        index.unlink()
        os.mkfifo(index)
        with self.assertRaises(restore.Refused):
            self.read_public()
        index.unlink()
        for mode, age, content in ((0o666, 0, '{"points": [], "error": ""}'),
                                   (0o644, 121, '{"points": [], "error": ""}'),
                                   (0o644, -60, '{"points": [], "error": ""}'),
                                   (0o644, 0, 'broken'), (0o644, 0, '[]'),
                                   (0o644, 0, '{"points": [{}], "error": ""}')):
            with self.subTest(mode=mode, age=age, content=content):
                index.write_text(content)
                index.chmod(mode)
                stamp = time.time() - age
                os.utime(index, (stamp, stamp))
                with self.assertRaises(restore.Refused):
                    self.read_public()
        index.write_text('x' * (restore.INDEX_LIMIT + 1))
        with self.assertRaises(restore.Refused):
            self.read_public()

    def test_untrusted_owner_refused(self):
        self.publish()
        metadata = (self.root / 'index.json').stat()
        untrusted = SimpleNamespace(st_uid=1000, st_mode=metadata.st_mode,
                                    st_size=metadata.st_size, st_mtime=metadata.st_mtime)
        with patch.object(restore, 'INDEX', self.root / 'index.json'), \
                patch.object(restore.os, 'fstat', return_value=untrusted), self.assertRaises(restore.Refused):
            restore.read_index()

    def test_failed_publication_cleans_temporary_and_preserves_previous_index(self):
        self.point(2)
        self.publish()
        previous = (self.root / 'index.json').read_bytes()
        with patch.object(restore.os, 'replace', side_effect=OSError), self.assertRaises(OSError):
            self.publish()
        self.assertEqual((self.root / 'index.json').read_bytes(), previous)
        self.assertFalse(list(self.root.glob('.emaki-restore-points-*')))


if __name__ == '__main__':
    unittest.main()
