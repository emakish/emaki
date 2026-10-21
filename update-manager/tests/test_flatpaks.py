#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Flatpak wire parsing, source isolation and update refusal tests."""
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend
import catalog
import flatpaks

REF = 'org.example.App/x86_64/stable'
OLD = 'a' * 12
NEW = 'b' * 12


class Flatpaks(unittest.TestCase):
    def setUp(self):
        lookup = patch.object(flatpaks.shutil, 'which', return_value='/fixture/flatpak')
        lookup.start()
        self.addCleanup(lookup.stop)

    def test_optional_missing_program_performs_no_commands(self):
        with patch.object(flatpaks.shutil, 'which', return_value=None):
            command = Mock()
            self.assertEqual(flatpaks.discover(command), [])
            self.assertFalse(flatpaks.update(command, Mock()))
            command.assert_not_called()

    def test_scopes_refs_commits_and_missing_versions_are_kept_distinct(self):
        local = '\n'.join(f'{REF}\t{OLD}\t1\tflathub\t{s}' for s in ('user', 'system', 'work'))
        remote = f'{REF}\t{NEW}\t\tflathub\n'
        command = Mock(side_effect=[local, remote, remote, remote])
        rows = flatpaks.discover(command)
        self.assertEqual(len(rows), 3)
        self.assertEqual({row['installation'] for row in rows}, {'user', 'system', 'work'})
        self.assertTrue(all(row['new'] == NEW and row['old'] == '1' for row in rows))
        self.assertTrue(all(row['kind'] == 'flatpak' and not row['aur'] for row in rows))
        groups = catalog.summary(rows, [], [], '')['groups']
        self.assertEqual(len(groups), 3)
        self.assertIn('--installation=work', command.call_args.args[0])

    def test_different_remote_or_branch_does_not_update_an_installed_ref(self):
        local = f'{REF}\t{OLD}\t1\tflathub\tuser\n'
        remote = f'{REF}\t{NEW}\t2\tother\n{REF.replace("stable", "beta")}\t{NEW}\t2\tflathub\n'
        self.assertEqual(flatpaks.discover(Mock(side_effect=[local, remote])), [])

    def test_one_summary_groups_all_sources_and_keeps_aur_last(self):
        local = f'{REF}\t{OLD}\t1\tflathub\tuser\n'
        rows = flatpaks.discover(Mock(side_effect=[local, f'{REF}\t{NEW}\t2\tflathub\n']))
        rows += catalog.transaction('emaki|emaki-config|2|10\ncore|linux|2|20', {})
        rows.append(dict(source=catalog.AUR_SOURCE, name='foreign', old='1', new='2', aur=True, downloadSize=None))
        summary = catalog.summary(rows, [], [], '')
        self.assertEqual(sum(group['count'] for group in summary['groups']), 4)
        self.assertEqual(summary['downloadSize'], 30)
        self.assertEqual(summary['updates'][-1]['name'], 'foreign')
        self.assertEqual({row['source'] for row in summary['updates']},
                         {'Arch core', 'Emaki', catalog.AUR_SOURCE, 'Flatpak (user)'})

    def test_same_version_with_new_commit_is_an_update(self):
        local = f'{REF}\t{OLD}\t1\tflathub\tuser\n'
        rows = flatpaks.discover(Mock(side_effect=[local, f'{REF}\t{NEW}\t1\tflathub\n']))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['old'], rows[0]['new']), (OLD, NEW))
        self.assertEqual(flatpaks.discover(Mock(side_effect=[local, f'{REF}\t{OLD}\t1\tflathub\n'])), [])

    def test_bad_columns_refs_commits_and_duplicate_records_refuse(self):
        for raw in ('garbage', f'--bad\t{OLD}\t1\tx\tuser', f'{REF}\tbad\t1\tx\tuser',
                    f'{REF}\t{OLD}\t1\tx\t--bad', f'{REF}\t{OLD}\t1\tx\tuser\n' * 2):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                flatpaks.installed(Mock(return_value=raw))

    def test_local_status_drops_removed_or_updated_apps_without_remote_calls(self):
        local = f'{REF}\t{OLD}\t1\tflathub\tuser\n'
        rows = flatpaks.discover(Mock(side_effect=[local, f'{REF}\t{NEW}\t2\tflathub\n']))
        self.assertEqual(flatpaks.pending(rows, Mock(return_value=local)), rows)
        command = Mock(return_value=local.replace(OLD, NEW))
        self.assertEqual(flatpaks.pending(rows, command), [])
        self.assertEqual(command.call_args.args[0][1], 'list')

    def test_updates_have_sleep_and_lid_inhibition_and_only_fixed_arguments(self):
        command = Mock(side_effect=[f'{REF}\t{OLD}\t1\tflathub\tuser\n', 'updated'])
        emit = Mock()
        self.assertTrue(flatpaks.update(command, emit))
        self.assertEqual(emit.call_args_list[0].kwargs['phase'], 'flatpak')
        args = command.call_args.args[0]
        self.assertEqual(args[0], 'systemd-inhibit')
        self.assertIn('--what=sleep:handle-lid-switch', args)
        self.assertEqual(args[-4:], ['update', '--user', '--noninteractive', '--assumeyes'])


class UpdateSequence(unittest.TestCase):
    def transaction(self, ok, *, code=0, flatpak_error=None):
        process = Mock()
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        process.stdout = io.StringIO(json.dumps(dict(type='finished', ok=ok, restart=False,
                                                     signOut=False, message='repository result')) + '\n')
        process.wait.return_value = code
        with patch.object(backend.subprocess, 'Popen', return_value=process), \
                patch.object(backend, 'rollback_pending', return_value=False), \
                patch.object(backend.reminder, 'status', return_value=dict(restart=False, reminder=False, restartPackages=[])), \
                patch.object(backend.flatpaks, 'update', return_value=True, side_effect=flatpak_error) as update, \
                patch.object(backend, 'emit') as emit, \
                patch.object(backend.reminder, 'notify_completed') as notify, \
                patch.object(backend.reminder, 'offer_report') as offer:
            backend.apply(Path('/unused'))
        self.assertEqual(notify.call_count, int(ok and code == 0 and flatpak_error is None))
        self.assertEqual(offer.call_count, int(not (ok and code == 0 and flatpak_error is None)))
        return update, emit

    def test_repository_refusal_never_runs_flatpak(self):
        update, emit = self.transaction(False)
        update.assert_not_called()
        self.assertFalse(emit.call_args.kwargs['ok'])

    def test_nonzero_helper_exit_never_runs_flatpak_even_with_success_event(self):
        update, emit = self.transaction(True, code=1)
        update.assert_not_called()
        self.assertFalse(emit.call_args.kwargs['ok'])

    def test_success_finishes_only_after_flatpak_and_failure_is_partial(self):
        update, emit = self.transaction(True)
        update.assert_called_once()
        self.assertTrue(emit.call_args.kwargs['ok'])
        update, emit = self.transaction(True, flatpak_error=RuntimeError('offline'))
        self.assertFalse(emit.call_args.kwargs['ok'])
        self.assertIn('System updates finished', emit.call_args.kwargs['message'])


if __name__ == '__main__':
    unittest.main()
