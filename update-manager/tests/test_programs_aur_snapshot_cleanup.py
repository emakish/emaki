#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Snapshots from refused installs are disposable; recovery points are retained."""
import contextlib
import io
import subprocess
import tempfile
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_aur_build as helper
import restore

DIGEST = 'a' * 64
DELETE = ['snapper', '--no-dbus', '-c', 'root', 'delete', '12']


class SnapshotCleanupTests(unittest.TestCase):
    def snapshot(self, metadata=None, readonly='ro=true', verification_error=None, deletion_error=None):
        calls = []

        def run(arguments, timeout=None):
            calls.append(arguments)
            if 'create' in arguments:
                output = '12'
            elif 'delete' in arguments:
                if deletion_error:
                    raise deletion_error
                output = ''
            elif arguments[:3] == ['btrfs', 'subvolume', 'show']:
                if verification_error:
                    raise verification_error
                output = 'subvolume'
            else:
                output = readonly
            return SimpleNamespace(returncode=0, stdout=output)

        self.enterContext(patch.object(helper, 'run', side_effect=run))
        self.enterContext(patch.object(restore, 'receipt', return_value=metadata or {'kind': 'pre', 'digest': DIGEST}))
        return calls

    def test_verified_snapshot_uses_number_cleanup_policy(self):
        calls = self.snapshot()
        self.assertEqual(helper.preinstall_snapshot(), ('12', '12:' + DIGEST))
        creation = calls[0]
        self.assertIn('--cleanup-algorithm', creation)
        self.assertEqual(creation[creation.index('--cleanup-algorithm') + 1], 'number')
        self.assertNotIn(DELETE, calls)

    def test_invalid_snapshot_is_deleted_before_refusal(self):
        calls = self.snapshot(readonly='ro=false')
        with self.assertRaises(RuntimeError):
            helper.preinstall_snapshot()
        self.assertEqual(calls[-1], DELETE)

    def test_verification_timeout_deletes_unused_snapshot(self):
        calls = self.snapshot(verification_error=subprocess.TimeoutExpired('btrfs', 20))
        with self.assertRaises(RuntimeError):
            helper.preinstall_snapshot()
        self.assertEqual(calls[-1], DELETE)

    def test_cleanup_failure_does_not_hide_snapshot_refusal(self):
        calls = self.snapshot(readonly='ro=false', deletion_error=OSError('delete failed'))
        with self.assertRaisesRegex(RuntimeError, 'snapshot could not be created'):
            helper.preinstall_snapshot()
        self.assertEqual(calls[-1], DELETE)

    def test_invalid_number_never_reaches_delete(self):
        with patch.object(helper, 'run', return_value=SimpleNamespace(returncode=0, stdout='12 13')) as run:
            with self.assertRaises(RuntimeError):
                helper.preinstall_snapshot()
        self.assertEqual(run.call_count, 1)


class WorkerSnapshotLifecycleTests(unittest.TestCase):
    def worker(self, fail_build=False, refuse_install=False, fail_receipt=False):
        state = {'base': '1'}
        calls = []
        plan = {'packages': [], 'builds': [{'packageBase': 'paint', 'commit': 'c' * 40,
                                         'packages': [{'package': 'paint', 'version': '1'}]}]}
        snapshots = iter(('11', '12'))

        def snapshot():
            number = next(snapshots)
            calls.append(['snapshot', number])
            return number, number + ':' + DIGEST

        def run(arguments, timeout=None):
            calls.append(arguments)
            return SimpleNamespace(returncode=0, stdout='paint 1\n' if '--print' in arguments else '')

        def stream(arguments, prefix=''):
            calls.append(arguments)
            if arguments[0] == 'systemd-inhibit':
                state['base'] = '2'
            elif '-U' in arguments:
                if refuse_install:
                    return 1, []
                state['paint'] = '1'
            elif '-Rn' in arguments:
                state.pop('paint', None)
            return 0, []

        def build(*args):
            if fail_build:
                raise RuntimeError('build failed')
            return 'unit', ['paint.pkg.tar.zst']

        receipts = SimpleNamespace(record=Mock(return_value={'id': 'r'},
                                               side_effect=RuntimeError('receipt failed') if fail_receipt else None))
        modules = (SimpleNamespace(BINDIR='/bin'), SimpleNamespace(Refused=ValueError), receipts, None)
        with tempfile.TemporaryFile(mode='w') as lock, contextlib.redirect_stdout(io.StringIO()), \
                patch.object(helper, 'modules', return_value=modules), \
                patch.object(helper.os, 'open', return_value=10), \
                patch.object(helper.os, 'fdopen', return_value=contextlib.nullcontext(lock)), \
                patch.object(helper.fcntl, 'flock'), patch.object(helper, 'require_update_root'), \
                patch.object(helper, 'reviewed_plan', return_value=plan), \
                patch.object(helper, 'preinstall_snapshot', side_effect=snapshot), \
                patch.object(helper, 'versions', side_effect=lambda: dict(state)), \
                patch.object(helper, 'run', side_effect=run), patch.object(helper, 'stream', side_effect=stream), \
                patch.object(helper, 'sandbox_build', side_effect=build), \
                patch.object(helper, 'collect', return_value=(Path('/nonexistent'), {'paint': ('/pkg/paint.pkg.tar.zst', '1')})), \
                patch.object(helper, 'remove_leftovers', return_value=[]), \
                patch.object(helper, 'update_backend', return_value=SimpleNamespace(changed=lambda *_: (False, False))):
            status = helper.worker('paint', DIGEST)
        return status, calls, state

    def test_preflight_snapshot_is_removed_before_committed_update(self):
        status, calls, state = self.worker()
        self.assertEqual(status, 0)
        preflight_delete = ['snapper', '--no-dbus', '-c', 'root', 'delete', '11']
        update_index = next(index for index, command in enumerate(calls) if command[0] == 'systemd-inhibit')
        self.assertLess(calls.index(preflight_delete), update_index)
        self.assertGreater(calls.index(['snapshot', '12']), update_index)
        self.assertNotIn(DELETE, calls)

    def test_build_failure_without_install_changes_discards_install_snapshot(self):
        status, calls, state = self.worker(fail_build=True)
        self.assertEqual(status, 1)
        self.assertIn(DELETE, calls)
        self.assertEqual(state, {'base': '2'})

    def test_refused_package_transaction_discards_install_snapshot(self):
        status, calls, state = self.worker(refuse_install=True)
        self.assertEqual(status, 1)
        self.assertIn(DELETE, calls)
        self.assertEqual(state, {'base': '2'})

    def test_failure_after_install_mutation_retains_recovery_snapshot(self):
        status, calls, state = self.worker(fail_receipt=True)
        self.assertEqual(status, 1)
        self.assertIn(['snapshot', '12'], calls)
        self.assertNotIn(DELETE, calls)
        self.assertEqual(state, {'base': '2'})


if __name__ == '__main__':
    unittest.main()
