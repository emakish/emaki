#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise shell recovery without a graphical session or a real user manager."""
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load_helper():
    loader = importlib.machinery.SourceFileLoader('shell_health', str(ROOT / 'scripts/emaki-shell-health'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ShellHealth(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='emaki-shell-health-')
        self.addCleanup(self.temp.cleanup)
        # Keep intentional symlink cases distinct from a runner's short TMPDIR alias.
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / 'home'
        self.override = self.home / '.config/emaki/shell'
        self.packaged = self.base / 'packaged'
        self.runtime = self.base / 'runtime'
        for path in (self.override, self.packaged, self.runtime):
            path.mkdir(parents=True)
        self.invocation = 'a' * 32
        self.env = mock.patch.dict(os.environ, {
            'HOME': str(self.home), 'XDG_RUNTIME_DIR': str(self.runtime),
            'XDG_CONFIG_HOME': str(self.home / '.config'),
            'INVOCATION_ID': self.invocation,
            'MONITOR_INVOCATION_ID': self.invocation,
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        stdout = mock.patch('sys.stdout', new_callable=io.StringIO)
        self.stdout = stdout.start()
        self.addCleanup(stdout.stop)
        self.helper = load_helper()
        self.helper.PACKAGED = self.packaged
        patch = mock.patch.object(Path, 'home', return_value=self.home)
        patch.start()
        self.addCleanup(patch.stop)
        self.calls = []
        self.journal = ''
        self.fail_action = None
        patch = mock.patch.object(self.helper.subprocess, 'run', side_effect=self.command)
        patch.start()
        self.addCleanup(patch.stop)

    def command(self, argv, **kwargs):
        argv = list(map(str, argv))
        self.calls.append(argv)
        output = ''
        if Path(argv[0]).name == 'journalctl':
            output = self.journal
        elif 'show' in argv:
            output = self.invocation + '\n'
        failed = self.fail_action is not None and self.fail_action in argv
        result = subprocess.CompletedProcess(argv, int(failed), output, '')
        if failed and kwargs.get('check'):
            raise subprocess.CalledProcessError(1, argv, output=output)
        return result

    def launch(self, directory=None):
        directory = directory or self.override
        return self.helper.select_shell(str(directory), record=True)

    def test_standalone_launch_without_runtime_keeps_explicit_override(self):
        del os.environ['XDG_RUNTIME_DIR']
        self.assertEqual(self.launch(), str(self.override))
        self.assertFalse(self.calls)

    def broken_file(self, relative='components/Panel.qml'):
        source = self.override / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'broken personal contents\n')
        target = self.packaged / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('import QtQuick\nItem {}\n')
        self.journal = ('Failed to load configuration\n'
                        f'  caused by {source.as_uri()}[12:4]: Unexpected token\n')
        return source

    def actions(self, action):
        return [args for args in self.calls
                if Path(args[0]).name == 'systemctl' and action in args]

    def assert_notice_without_restart(self):
        self.assertFalse(self.actions('restart'))
        self.assertTrue(any('emaki-shell-recovery.service' in args
                            for args in self.actions('start')))

    def test_broken_override_preserved_and_packaged_restart_selected(self):
        source = self.broken_file()
        original = source.read_bytes()
        self.launch()
        self.helper.reconcile()
        self.assertEqual(source.read_bytes(), (self.packaged / 'components/Panel.qml').read_bytes())
        backups = list(source.parent.glob(source.name + '.broken-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(self.helper.select_shell(str(self.override)), str(self.packaged))
        self.assertEqual(len(self.actions('reset-failed')), 1)
        self.assertEqual(len(self.actions('restart')), 1)
        self.assertIn('--no-block', self.actions('restart')[0])
        self.assertIn('emaki-shell.service', self.actions('restart')[0])
        log = self.helper.runtime_dir() / 'errors.log'
        self.assertIn('Unexpected token', log.read_text())
        self.assertIn('Unexpected token', self.stdout.getvalue())
        self.assertTrue(any(self.invocation in ' '.join(args)
                            for args in self.calls if Path(args[0]).name == 'journalctl'))

    def test_relative_qml_cause_is_set_aside(self):
        source = self.broken_file('Panel.qml')
        self.journal = 'Failed to load configuration\n  caused by @Panel.qml[12:4]: Syntax error\n'
        self.launch()
        self.helper.reconcile()
        self.assertEqual(source.read_bytes(), (self.packaged / 'Panel.qml').read_bytes())
        self.assertEqual(len(self.actions('restart')), 1)

    def test_normal_launch_records_override_without_starting_services(self):
        self.assertEqual(self.launch(), str(self.override))
        record = json.loads((self.helper.runtime_dir() / 'launch.json').read_text())
        self.assertEqual(record['shell'], str(self.override))
        self.assertEqual(record['invocation'], self.invocation)
        self.assertEqual(self.calls, [])

    def test_last_javascript_cause_is_repaired_without_moving_panel(self):
        panel = self.broken_file()
        original = panel.read_bytes()
        script = self.broken_file('components/helpers.js')
        self.journal = ('Failed to load configuration\n'
                        '  caused by @shell.qml[13:1]: Type Panel unavailable\n'
                        '  caused by @components/Panel.qml[2:1]: Script qs:@/qs/components/helpers.js unavailable\n'
                        '  caused by @components/helpers.js[1:29]: Expected token `identifier`\n')
        self.launch()
        self.helper.reconcile()
        self.assertEqual(panel.read_bytes(), original)
        self.assertEqual(script.read_bytes(), (self.packaged / 'components/helpers.js').read_bytes())
        self.assertEqual(len(self.actions('restart')), 1)
        self.assertFalse(list(panel.parent.glob('Panel.qml.broken-*')))

    def test_import_failures_do_not_move_working_files(self):
        source = self.broken_file('shell.qml')
        original = source.read_bytes()
        self.launch()
        for message in ('module "Fake" plugin "fakeplugin" not found',
                        'module "X" is not installed',
                        'Script qs:@/qs/components/helpers.js unavailable',
                        'Cannot load library /usr/lib/qt6/qml/Fake/libfakeplugin.so: missing symbol'):
            with self.subTest(message=message):
                self.calls.clear()
                self.journal = f'Failed to load configuration\n  caused by @shell.qml[2:1]: {message}\n'
                self.helper.reconcile()
                self.assert_notice_without_restart()
                self.assertEqual(source.read_bytes(), original)
                self.assertFalse(list(source.parent.glob('*.broken-*')))

    def test_unsupported_or_external_last_cause_preserves_panel(self):
        source = self.broken_file()
        original = source.read_bytes()
        self.launch()
        for cause in ('@components/data.json[1:29]: Unexpected token',
                      'file:///usr/share/Fake/helpers.js[1:29]: Syntax error',
                      '@components/missing.js[1:29]: Syntax error',
                      'an unrecognised error format'):
            with self.subTest(cause=cause):
                self.calls.clear()
                self.journal = ('Failed to load configuration\n'
                                '  caused by @components/Panel.qml[2:1]: Type unavailable\n'
                                f'  caused by {cause}\n')
                self.helper.reconcile()
                self.assert_notice_without_restart()
                self.assertEqual(source.read_bytes(), original)

    def test_arbitrary_home_directory_is_never_repaired(self):
        self.override = self.home / 'Projects/panel'
        source = self.broken_file()
        original = source.read_bytes()
        self.launch()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertEqual(source.read_bytes(), original)

    def test_custom_xdg_config_root_is_repaired(self):
        os.environ['XDG_CONFIG_HOME'] = str(self.home / 'settings')
        self.override = self.home / 'settings/emaki/shell'
        source = self.broken_file()
        self.launch()
        self.helper.reconcile()
        self.assertEqual(len(self.actions('restart')), 1)
        self.assertEqual(source.read_bytes(), (self.packaged / 'components/Panel.qml').read_bytes())

    def test_git_ancestors_and_nested_worktrees_are_preserved(self):
        source = self.broken_file()
        original = source.read_bytes()
        self.launch()
        for parent in (source.parent, self.override, self.override.parent,
                       self.home / '.config', self.home):
            for kind in ('directory', 'file', 'symlink'):
                with self.subTest(parent=parent, kind=kind):
                    entry = parent / '.git'
                    if kind == 'directory':
                        entry.mkdir()
                    elif kind == 'file':
                        entry.write_text('gitdir: /missing/worktree\n')
                    else:
                        entry.symlink_to(self.base / 'missing-git')
                    self.calls.clear()
                    self.helper.reconcile()
                    self.assert_notice_without_restart()
                    self.assertEqual(source.read_bytes(), original)
                    entry.rmdir() if kind == 'directory' else entry.unlink()

    def test_successful_repair_notice_is_consumed_once_after_restart(self):
        source = self.broken_file()
        self.launch()
        self.helper.reconcile()
        self.assertEqual(len(self.actions('restart')), 1)
        pending = self.helper.runtime_dir() / 'repair-notice.json'
        self.assertTrue(pending.exists())
        self.stdout.seek(0)
        self.stdout.truncate()
        self.helper.repair_notice()
        data = json.loads(self.stdout.getvalue())
        self.assertEqual(data['backup'], str(next(source.parent.glob('*.broken-*')).relative_to(self.override)))
        self.assertFalse(pending.exists())
        self.stdout.seek(0)
        self.stdout.truncate()
        self.helper.repair_notice()
        self.assertEqual(self.stdout.getvalue(), '')
        self.assertTrue((self.helper.runtime_dir() / 'repair.txt').exists())

    def test_packaged_failure_never_restarts(self):
        source = self.broken_file()
        self.launch(self.packaged)
        self.journal = 'Failed to load configuration\n' + (self.packaged / 'components/Panel.qml').as_uri() + ':12:4: Bad token\n'
        self.helper.reconcile()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_second_failure_does_not_repeat_repair(self):
        source = self.broken_file()
        self.launch()
        self.helper.reconcile()
        self.calls.clear()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertEqual(len(list(source.parent.glob(source.name + '.broken-*'))), 1)

    def test_missing_launch_record_does_not_touch_override(self):
        source = self.broken_file()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_missing_invocation_does_not_touch_override(self):
        source = self.broken_file()
        self.launch()
        record = self.helper.runtime_dir() / 'launch.json'
        data = json.loads(record.read_text())
        data['invocation'] = ''
        record.write_text(json.dumps(data))
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_stale_failed_invocation_does_not_touch_override(self):
        source = self.broken_file()
        self.launch()
        self.invocation = 'b' * 32
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_monitor_invocation_mismatch_does_not_touch_override(self):
        source = self.broken_file()
        self.launch()
        os.environ['MONITOR_INVOCATION_ID'] = 'c' * 32
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_outside_home_override_is_preserved(self):
        outside = self.base / 'outside'
        outside.mkdir()
        source = outside / 'Panel.qml'
        source.write_text('broken contents')
        (self.packaged / source.name).write_text('packaged contents')
        self.launch(outside)
        self.journal = f'Failed to load configuration\n{source.as_uri()}[12:4]: Syntax error\n'
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_identical_packaged_file_is_preserved(self):
        source = self.broken_file()
        source.write_bytes((self.packaged / 'components/Panel.qml').read_bytes())
        self.launch()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_only_deepest_cause_is_set_aside(self):
        source = self.broken_file()
        entry = self.override / 'shell.qml'
        entry.write_text('personal entry point')
        (self.packaged / 'shell.qml').write_text('packaged entry point')
        self.journal = ('Failed to load configuration\n'
                        '  caused by @shell.qml[13:1]: Type Panel unavailable\n'
                        f'  caused by {source.as_uri()}[12:4]: Syntax error\n')
        self.launch()
        self.helper.reconcile()
        self.assertTrue(entry.exists())
        self.assertEqual(source.read_bytes(), (self.packaged / 'components/Panel.qml').read_bytes())
        self.assertEqual(len(self.actions('restart')), 1)

    def test_failed_restore_preserves_original_and_opens_notice_without_restart(self):
        source = self.broken_file()
        original = source.read_bytes()
        self.launch()
        with mock.patch.object(self.helper.shutil, 'copyfileobj', side_effect=OSError('copy failed')):
            self.helper.reconcile()
        self.assert_notice_without_restart()
        backups = list(source.parent.glob(source.name + '.broken-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(self.helper.select_shell(str(self.override)), str(self.packaged))

    def test_file_symlink_is_preserved(self):
        source = self.broken_file()
        source.unlink()
        target = self.home / 'unrelated.qml'
        target.write_bytes(b'personal data')
        source.symlink_to(target)
        self.launch()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.is_symlink())
        self.assertEqual(target.read_bytes(), b'personal data')

    def test_symlink_ancestor_is_preserved(self):
        source = self.broken_file()
        directory = source.parent
        moved = self.home / 'other-components'
        directory.rename(moved)
        directory.symlink_to(moved, target_is_directory=True)
        self.launch()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_no_packaged_counterpart_means_no_move(self):
        source = self.broken_file()
        (self.packaged / 'components/Panel.qml').unlink()
        self.launch()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_runtime_error_without_load_failure_means_no_move(self):
        source = self.broken_file()
        self.journal = f'{source.as_uri()}:12:4: ReferenceError: missing is not defined\n'
        self.launch()
        self.helper.reconcile()
        self.assert_notice_without_restart()
        self.assertTrue(source.exists())

    def test_reset_failure_prevents_restart(self):
        self.broken_file()
        self.launch()
        self.fail_action = 'reset-failed'
        self.helper.reconcile()
        self.assert_notice_without_restart()

    def test_restart_failure_shows_recovery(self):
        self.broken_file()
        self.launch()
        self.fail_action = 'restart'
        self.helper.reconcile()
        self.assertEqual(len(self.actions('restart')), 1)
        self.assertTrue(any('emaki-shell-recovery.service' in args
                            for args in self.actions('start')))


if __name__ == '__main__':
    unittest.main()
