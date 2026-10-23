#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Developer bundles, consent, transaction plans and user toolchain boundaries."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_arch as arch
import programs_install as helper
import programs_kits as kits
import programs_receipts as receipts


class Kits(unittest.TestCase):
    def test_catalog_all_packages_and_follow_up_state(self):
        installed = {name: '1' for row in kits.definitions() for name in row['packages']}
        with patch.object(kits, 'stable_installed', return_value=False), patch.object(kits, 'socket_enabled', return_value=False), patch.object(kits, 'receipt_for', return_value=None):
            rows = {row['id']: row for row in kits.catalog(installed)}
        self.assertTrue(rows['python']['installed'])
        self.assertFalse(rows['rust']['installed'])
        self.assertFalse(rows['docker']['installed'])
        self.assertTrue(rows['rust']['packagesInstalled'])
        self.assertIn('finish', rows['rust']['actions'])

    def test_partial_setup_keeps_undo(self):
        with patch.object(kits, 'stable_installed', return_value=False), patch.object(kits, 'receipt_for', return_value={'id': 'receipt'}):
            rust = next(row for row in kits.catalog({'rustup': '1'}) if row['id'] == 'rust')
        self.assertEqual(rust['actions'], ['finish', 'remove', 'undo'])

    def test_conflicts_block_catalog_and_preview_before_pacman(self):
        for identity, installed in [('rust', {'rust': '1'}), ('rust', {'cargo': '1', 'rustfmt': '1'}),
                                    ('nodejs', {'nodejs-lts-krypton': '1'})]:
            with self.subTest(identity=identity, installed=installed), patch.object(kits, 'receipt_for', return_value=None):
                row = next(row for row in kits.catalog(installed) if row['id'] == identity)
                self.assertNotIn('install', row['actions'])
                self.assertIn(next(iter(installed)), row['conflict'])
                with patch.object(arch, 'inventory', return_value=({}, installed)), patch.object(arch, 'run') as run:
                    plan = arch.install_plan('development/' + identity, database='/private')
                self.assertEqual(plan['code'], 'conflict')
                run.assert_not_called()

    def test_helper_revalidation_refuses_conflicting_provider(self):
        with patch.object(arch, 'private_database', return_value=contextlib.nullcontext('/private')), \
                patch.object(arch, 'inventory', return_value=({}, {'rust': '1'})), patch.object(arch, 'run') as run:
            with self.assertRaises(ValueError):
                helper.revalidate('development/rust', 'a' * 64)
        run.assert_not_called()

    def test_docker_setup_records_only_completed_changes(self):
        before = dict(uid=1000, user='person', enabled=False, member=False)
        with patch.object(kits, 'unit_call') as units, patch.object(kits, 'checked'):
            kits.configure_docker(before, True)
        self.assertTrue(before['socketEnabledByKit'])
        self.assertTrue(before['memberAdded'])
        before = dict(uid=1000, user='person', enabled=False, member=False)
        with patch.object(kits, 'unit_call', side_effect=[None, RuntimeError('start failed')]), \
                patch.object(kits, 'checked'):
            with self.assertRaises(RuntimeError):
                kits.configure_docker(before, True)
        self.assertTrue(before['socketEnabledByKit'])
        self.assertFalse(before.get('memberAdded', False))

    def test_docker_primary_group_never_removed(self):
        current = dict(uid=1000, user='person', enabled=True, member=True, primary=True)
        with patch.object(kits, 'docker_state', return_value=current), patch.object(kits, 'checked') as checked:
            kits.restore_docker(dict(current, member=False))
        checked.assert_not_called()

    def test_docker_removal_restores_receipt_and_preserves_existing_access(self):
        current = dict(uid=1000, user='person', enabled=True, member=True)
        backend = Mock()
        backend.versions.return_value = {}
        output = io.StringIO()
        with patch.object(helper, 'preinstall_snapshot', return_value=('1', 'token')), \
                patch.object(kits, 'receipt_for', return_value={'kit': {'docker': current}}), \
                patch.object(kits, 'restore_docker') as restore, \
                patch.object(helper.subprocess, 'run', return_value=Mock(returncode=0)), \
                patch.object(receipts, 'receipts', return_value=[]), contextlib.redirect_stdout(output):
            helper.remove_kit(kits.definition('docker'), {'targets': ['docker']}, backend, current)
        restore.assert_called_once_with(current)
        self.assertFalse(json.loads(output.getvalue())['signOut'])
        self.assertIn('Docker images and containers', json.loads(output.getvalue())['message'])

    def test_docker_removal_preserves_membership_not_added_by_kit(self):
        current = dict(uid=1000, user='person', enabled=True, member=True)
        prior = dict(current, member=False, memberAdded=False)
        with patch.object(helper, 'preinstall_snapshot', return_value=('1', 'token')), \
                patch.object(kits, 'receipt_for', return_value={'kit': {'docker': prior}}), \
                patch.object(kits, 'restore_docker') as restore, \
                patch.object(helper.subprocess, 'run', return_value=Mock(returncode=0)), \
                patch.object(receipts, 'receipts', return_value=[]), contextlib.redirect_stdout(io.StringIO()):
            helper.remove_kit(kits.definition('docker'), {'targets': ['docker']},
                              Mock(versions=Mock(return_value={})), current)
        self.assertTrue(restore.call_args.args[0]['member'])

    def test_docker_failed_package_removal_still_reports_signout(self):
        current = dict(uid=1000, user='person', enabled=True, member=True)
        prior = dict(current, enabled=False, member=False)
        output = io.StringIO()
        with patch.object(helper, 'preinstall_snapshot', return_value=('1', 'token')), \
                patch.object(kits, 'receipt_for', return_value={'kit': {'docker': prior}}), \
                patch.object(kits, 'restore_docker'), \
                patch.object(helper.subprocess, 'run', return_value=Mock(returncode=1)), \
                contextlib.redirect_stdout(output):
            helper.remove_kit(kits.definition('docker'), {'targets': ['docker']}, Mock(versions=Mock(return_value={'docker': '1'})), current)
        self.assertTrue(json.loads(output.getvalue())['signOut'])

    def test_invalid_targets_rejected(self):
        for target in ('development/unknown', 'development/--root', 'development-remove/docker-access'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                kits.target_info(target)

    def test_docker_access_explicit(self):
        self.assertFalse(kits.target_info('development/docker')['access'])
        self.assertTrue(kits.target_info('development/docker-access')['access'])
        with self.assertRaises(ValueError):
            kits.install_plan('rust', docker_access=True)

    def test_official_repositories_only(self):
        row = kits.definition('nodejs')
        packages = {'custom/nodejs': {'package': 'nodejs'}, 'extra/npm': {'package': 'npm'}}
        with self.assertRaises(ValueError):
            kits.targets(row, packages)
        packages['extra/nodejs'] = {'package': 'nodejs'}
        self.assertEqual(kits.targets(row, packages), ['extra/nodejs', 'extra/npm'])

    def test_bundle_preview_one_transaction_and_token_binds_all_packages(self):
        packages = {'extra/' + name: dict(package=name, version='1') for name in ('nodejs', 'npm')}
        with patch.object(arch, 'inventory', return_value=(packages, {})), patch.object(arch, 'run', return_value='extra|nodejs|1|1\nextra|npm|1|1\n') as run:
            first = arch.install_plan('development/nodejs', database='/private')
            self.assertEqual(first['targets'], ['extra/nodejs', 'extra/npm'])
            self.assertEqual(run.call_args.args[0][-2:], first['targets'])
            packages['extra/npm']['version'] = '2'
            self.assertNotEqual(first['token'], arch.install_plan('development/nodejs', database='/private')['token'])

    def test_remove_preserves_python_runtime_and_has_no_recursion(self):
        with patch.object(arch, 'run', return_value='python 1\npython-pip 1\npython-pipx 1\nuv 1\n'), patch.object(kits, 'command', return_value=Mock(returncode=0, stdout='python-pip\npython-pipx\nuv\n')) as command:
            plan = kits.remove_plan('python')
        self.assertEqual(plan['kept'], ['python'])
        self.assertEqual(plan['targets'], ['python-pip', 'python-pipx', 'uv'])
        self.assertEqual(command.call_args.args[0], ['pacman', '-Rp', '--print-format', '%n', '--', *plan['targets']])

    def test_remove_dependency_failure_refuses(self):
        with patch.object(arch, 'run', return_value='go 1\n'), patch.object(kits, 'command', return_value=Mock(returncode=1, stdout='', stderr='dependency failure')):
            self.assertFalse(kits.remove_plan('go')['ok'])

    def test_rust_never_runs_as_root(self):
        with patch.object(kits.os, 'geteuid', return_value=0), patch.object(kits, 'stream_user') as command:
            self.assertFalse(kits.finish_user('rust', Mock())['ok'])
        command.assert_not_called()

    def test_rust_user_setup_and_failure(self):
        emit = Mock()
        with patch.object(kits.os, 'geteuid', return_value=1000), patch.object(kits, 'stream_user', return_value=0) as command, patch.object(kits, 'stable_installed', return_value=True):
            self.assertTrue(kits.finish_user('rust', emit)['ok'])
        self.assertEqual(command.call_args.args[0], ['rustup', 'toolchain', 'install', 'stable', '--profile', 'default'])
        emit.assert_called_once()
        with patch.object(kits.os, 'geteuid', return_value=1000), patch.object(kits, 'stream_user', return_value=1):
            self.assertEqual(kits.finish_user('rust', Mock())['code'], 'partial')

    def test_user_progress_stream_and_timeout(self):
        emit = Mock()
        self.assertEqual(kits.stream_user([sys.executable, '-c', 'print("download progress")'], emit, timeout=5), 0)
        emit.assert_called_once_with('progress', message='download progress')
        with self.assertRaises(kits.subprocess.TimeoutExpired):
            kits.stream_user([sys.executable, '-c', 'import time; time.sleep(5)'], Mock(), timeout=0.02)

    def test_docker_group_needs_consent(self):
        state = dict(uid=1000, user='person', enabled=False, member=False)
        with patch.object(kits, 'checked') as checked:
            kits.configure_docker(state, False)
        self.assertFalse(any(call.args[0][0] == 'gpasswd' for call in checked.call_args_list))
        with patch.object(kits, 'checked') as checked:
            kits.configure_docker(state, True)
        self.assertEqual(checked.call_args.args[0], ['gpasswd', '--add', 'person', 'docker'])

    def test_docker_existing_group_preserved_and_changes_undone(self):
        state = dict(uid=1000, user='person', enabled=True, member=True)
        with patch.object(kits, 'checked') as checked, patch.object(kits, 'docker_state', return_value=state):
            kits.restore_docker(state)
        checked.assert_not_called()
        with patch.object(kits, 'checked') as checked, patch.object(kits, 'docker_state', return_value=state):
            kits.restore_docker(dict(state, enabled=False, member=False))
        self.assertEqual(checked.call_args.args[0], ['gpasswd', '--delete', 'person', 'docker'])
        self.assertTrue(any('DisableUnitFiles' in call.args[0] for call in checked.call_args_list))

    def test_docker_requires_real_person(self):
        for uid in (None, 0, -1, '1000'):
            with self.subTest(uid=uid), self.assertRaises(ValueError):
                kits.docker_state(uid)

    def test_docker_undo_uses_root_snapshot(self):
        self.assertTrue(receipts.needs_restore(dict(kind='arch', added={'docker': '1'}, kit={'id': 'docker'})))

    def test_receipt_kit_metadata_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            value = receipts.record('arch', 'development:go', 'Go', 'go', {}, {'go': '1'},
                                    directory=directory, kit={'id': 'go'})
            self.assertTrue(receipts.valid(value))
            value['kit']['id'] = 'unknown'
            self.assertFalse(receipts.valid(value))

    def test_install_bundle_reuses_update_snapshot_and_receipt_path(self):
        backend = Mock()
        backend.versions.side_effect = [{}, {}, {'go': '1'}]
        backend.changed.return_value = (False, False)
        output = io.StringIO()
        with tempfile.TemporaryFile(mode='w') as lock, patch.object(helper.os, 'open', return_value=10), patch.object(helper.os, 'fdopen', return_value=contextlib.nullcontext(lock)), patch.object(helper.fcntl, 'flock'), patch.object(helper, 'update_backend', return_value=backend), patch.object(helper, 'revalidate', return_value={'targets': ['extra/go']}), patch.object(helper.subprocess, 'run', return_value=Mock(returncode=0)) as run, patch.object(helper, 'preinstall_snapshot', return_value=('42', '42:' + 'a' * 64)) as snapshot, patch.object(kits, 'receipt_for', return_value=None), patch.object(receipts, 'record', return_value={'id': 'receipt'}) as record, contextlib.redirect_stdout(output):
            self.assertEqual(helper.worker('development/go', 'a' * 64), 0)
        snapshot.assert_called_once_with(required=True)
        self.assertEqual(run.call_count, 2)
        self.assertIn('--install-only', run.call_args.args[0])
        self.assertEqual(run.call_args.args[0][-1], 'extra/go')
        self.assertEqual(record.call_args.kwargs['kit'], {'id': 'go'})
        self.assertEqual(record.call_args.args[:4], ('arch', 'development:go', 'Go', 'go'))
        self.assertTrue(json.loads(output.getvalue().splitlines()[-1])['ok'])

    def test_docker_cleanup_failure_reports_snapshot_after_change(self):
        output = io.StringIO()
        with patch.object(helper, 'preinstall_snapshot', return_value=('42', '42:' + 'a' * 64)), patch.object(kits, 'receipt_for', return_value={'kit': {'docker': {'uid': 1000, 'user': 'person', 'enabled': False, 'member': False}}}), patch.object(kits, 'restore_docker', side_effect=RuntimeError('setup')), patch.object(helper.subprocess, 'run') as run, contextlib.redirect_stdout(output):
            self.assertEqual(helper.remove_kit(kits.definition('docker'), {'targets': ['docker']}, Mock(), {'uid': 1000, 'user': 'person', 'member': True}), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result['restoreToken'], '42:' + 'a' * 64)
        self.assertNotIn('notStarted', result)
        run.assert_not_called()

    def test_remove_snapshot_precedes_any_change(self):
        plan = dict(targets=['go'])
        backend = Mock()
        backend.versions.return_value = {}
        events = []
        with patch.object(helper, 'preinstall_snapshot', side_effect=lambda **kw: (events.append('snapshot') or ('1', 'token'))), patch.object(helper.subprocess, 'run', side_effect=lambda *a, **kw: (events.append('remove') or Mock(returncode=0))), contextlib.redirect_stdout(io.StringIO()):
            helper.remove_kit(kits.definition('go'), plan, backend, None)
        self.assertEqual(events, ['snapshot', 'remove'])

    def test_failed_snapshot_prevents_remove(self):
        with patch.object(helper, 'preinstall_snapshot', side_effect=RuntimeError('snapshot')), patch.object(helper.subprocess, 'run') as run:
            with self.assertRaises(RuntimeError):
                helper.remove_kit(kits.definition('go'), dict(targets=['go']), Mock(), None)
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
