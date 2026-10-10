#!/usr/bin/python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Managed terminal reads and argument expansion, without opening a terminal."""
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/emaki-terminal'))


class Terminal(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        (self.root / 'emaki').mkdir()
        self.path = self.root / 'emaki/settings.toml'
        self.env = patch.dict(os.environ, XDG_CONFIG_HOME=str(self.root))
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_password_result_waits_for_enter_and_preserves_exit_status(self):
        for code in (0, 1, 10):
            receipt = self.root / ('receipt-' + str(code))
            output = io.StringIO()
            with patch('subprocess.run', return_value=types.SimpleNamespace(returncode=code)) as run, patch('builtins.input', return_value='') as wait, contextlib.redirect_stdout(output):
                self.assertEqual(MODULE['password_prompt'](str(receipt)), code)
            run.assert_called_once_with(['passwd'], check=False)
            wait.assert_called_once()
            self.assertEqual(receipt.read_text(), str(code))
            self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
            self.assertIn('Password changed.' if code == 0 else 'Password not changed.', output.getvalue())

    def test_password_status_uses_receipt_not_terminal_exit_code(self):
        for code in (0, 1, None):
            def terminal(command, **kwargs):
                self.assertEqual(command[:2], ['emaki-terminal', '--'])
                self.assertEqual(command[-2], '--password-prompt')
                if code is not None:
                    Path(command[-1]).write_text(str(code))
                return types.SimpleNamespace(returncode=0)
            output = io.StringIO()
            with patch('subprocess.run', side_effect=terminal), contextlib.redirect_stdout(output):
                MODULE['change_password']()
            reply = json.loads(output.getvalue())
            self.assertEqual(reply['status'], 'changed' if code == 0 else 'unchanged' if code == 1 else 'unconfirmed')

    def test_detached_password_prompt_still_waits_without_receipt_directory(self):
        with patch('subprocess.run', return_value=types.SimpleNamespace(returncode=0)), patch('builtins.input', return_value='') as wait, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(MODULE['password_prompt'](self.root / 'gone' / 'receipt'), 0)
            wait.assert_called_once()

    def test_missing_file_uses_default(self):
        self.assertIsNone(MODULE['selected_terminal']())
        with patch('os.execvp') as execute:
            MODULE['main'](['--', 'printf', 'a b', '$(false)'])
            execute.assert_called_once_with('kitty', ['kitty', '-e', 'printf', 'a b', '$(false)'])

    def test_default_query_matches_launch_fallback_without_launching(self):
        output = io.StringIO()
        with patch('os.execvp') as execute, contextlib.redirect_stdout(output):
            MODULE['main'](['--default', '--json'])
            execute.assert_not_called()
            reply = json.loads(output.getvalue())
            MODULE['main']([])
            execute.assert_called_once_with(reply['executable'], [reply['executable']])

    def test_saved_terminal(self):
        self.path.write_text('schema_version = 1\n[defaults]\nterminal = "test.desktop"\n')
        self.assertEqual(MODULE['selected_terminal'](), 'test.desktop')

    def test_corrupt_schema_and_id_are_refused(self):
        for text in ('bad = [', 'schema_version = 2', 'schema_version = true',
                     'schema_version = 1\n[defaults]\nterminal = "../test.desktop"',
                     'schema_version = 1\n[defaults]\nterminal = 1', ' ' * 65537):
            with self.subTest(text=text[:80]):
                self.path.write_text(text)
                with self.assertRaises(ValueError):
                    MODULE['selected_terminal']()

    def test_symlink_and_fifo_are_refused_without_blocking(self):
        target = self.root / 'target'
        target.write_text('schema_version = 1')
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            MODULE['selected_terminal']()
        self.path.unlink()
        os.mkfifo(self.path)
        with self.assertRaises(ValueError):
            MODULE['selected_terminal']()

    def test_selected_entry_launch_and_check(self):
        self.path.write_text('schema_version = 1\n[defaults]\nterminal = "test.desktop"\n')
        from unittest.mock import Mock
        app = Mock()
        app.get_executable.return_value = 'terminal'
        app.get_categories.return_value = 'TerminalEmulator;'
        app.get_commandline.return_value = 'terminal --title "two words"'
        app.launch.return_value = True
        gio = types.SimpleNamespace(DesktopAppInfo=types.SimpleNamespace(new=lambda ident: app))
        gi = types.ModuleType('gi')
        gi.require_version = lambda *args: None
        repository = types.ModuleType('gi.repository')
        repository.Gio = gio
        with patch.dict('sys.modules', {'gi': gi, 'gi.repository': repository}), patch('shutil.which', return_value='/bin/terminal'):
            MODULE['main'](['--check', 'test.desktop'])
            MODULE['main'](['--check-terminal', 'test.desktop'])
            with patch.object(app, 'get_categories', return_value='WebBrowser;'), self.assertRaises(ValueError):
                MODULE['main'](['--check-terminal', 'test.desktop'])
            app.launch.assert_not_called()
            MODULE['main']([])
            app.launch.assert_called_once_with([], None)
            with patch('os.execvp') as execute:
                MODULE['main'](['--', 'printf', 'one two'])
                execute.assert_called_once_with('terminal', ['terminal', '--title', 'two words', '-e', 'printf', 'one two'])
            with patch('shutil.which', return_value=None), self.assertRaises(ValueError):
                MODULE['main'](['--check', 'test.desktop'])
            for missing in ('entry', 'executable'):
                with self.subTest(missing=missing):
                    with patch.object(gio.DesktopAppInfo, 'new', return_value=None if missing == 'entry' else app), patch('shutil.which', return_value=None), patch('os.execvp') as execute:
                        MODULE['main'](['--', 'printf', 'one two'])
                        execute.assert_called_once_with('kitty', ['kitty', '-e', 'printf', 'one two'])

    def test_personal_mime_defaults_override_managed_fallback(self):
        # Use the real desktop resolver, including a later external "set as default".
        applications = self.root / 'data/applications'
        managed = self.root / 'state/emaki/defaults'
        packaged = self.root / 'packaged'
        for directory in (applications, managed, packaged):
            directory.mkdir(parents=True)
        for name in ('managed', 'personal', 'package'):
            (applications / (name + '.desktop')).write_text(
                '[Desktop Entry]\nType=Application\nName=' + name + '\nExec=/usr/bin/true %u\nMimeType=text/html;\n')
        associations = '[Default Applications]\ntext/html={}.desktop;\n'
        (managed / 'mimeapps.list').write_text(associations.format('managed'))
        (packaged / 'mimeapps.list').write_text(associations.format('package'))
        environment = dict(os.environ, XDG_CONFIG_HOME=str(self.root),
                           XDG_DATA_HOME=str(self.root / 'data'), XDG_DATA_DIRS=str(self.root / 'empty'),
                           XDG_CONFIG_DIRS=str(managed) + ':' + str(packaged), XDG_CURRENT_DESKTOP='niri',
                           LC_ALL='C')
        def selected():
            reply = subprocess.run(['/usr/bin/gio', 'mime', 'text/html'], env=environment,
                                   text=True, capture_output=True, check=True, timeout=5)
            return reply.stdout.splitlines()[0].rsplit(': ', 1)[-1]
        self.assertEqual(selected(), 'managed.desktop')
        (self.root / 'mimeapps.list').write_text(associations.format('personal'))
        self.assertEqual(selected(), 'personal.desktop')
        (self.root / 'mimeapps.list').unlink()
        self.assertEqual(selected(), 'managed.desktop')

    def test_exec_expansion_preserves_literal_arguments(self):
        class App:
            def get_commandline(self):
                return 'terminal --title "hello world" %% %c %k %i %U'
            def get_name(self):
                return 'Terminal name'
            def get_filename(self):
                return '/tmp/a file.desktop'
            def get_string(self, key):
                return 'icon'
        self.assertEqual(MODULE['command_argv'](App()),
                         ['terminal', '--title', 'hello world', '%', 'Terminal name',
                          '/tmp/a file.desktop', '--icon', 'icon'])
        for code in ('%Q', 'prefix%f', 'prefix%c'):
            with patch.object(App, 'get_commandline', return_value='terminal ' + code):
                with self.assertRaises(ValueError):
                    MODULE['command_argv'](App())


if __name__ == '__main__':
    unittest.main()
