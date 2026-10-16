#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run login switches against isolated personal directories and real command arguments."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / 'scripts/emaki-autostart'
COMMANDS = {
    'wallpaper': ['emaki-session-wallpaper'],
    'clipboard': ['python3', '-B', '@EMAKI_DATADIR@/shell/helpers/clipboard_store.py', '--watch'],
    'automount': ['udiskie', '--no-automount'],
    'shell': ['systemctl', '--user', 'start', '--no-block', 'emaki-shell.service'],
    'sleep-guard': ['systemctl', '--user', 'start', '--no-block', 'emaki-sleep-guard.service'],
}


class AutostartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='emaki-autostart-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'home with spaces'
        self.home.mkdir()
        self.env = dict(os.environ, HOME=str(self.home))
        self.env.pop('XDG_CONFIG_HOME', None)

    def run_switch(self, name, *args):
        return subprocess.run([str(WRAPPER), name, sys.executable, '-c',
                               'import json,sys; print(json.dumps(sys.argv[1:])); sys.exit(23)',
                               *args], env=self.env, capture_output=True, text=True)

    def snapshot(self):
        return {str(p.relative_to(self.home)): None if p.is_dir() else p.read_bytes()
                for p in self.home.rglob('*')}

    def test_default_preserves_arguments_exit_status_and_writes_nothing(self):
        args = ('a b', '', 'quote"', 'literal;$HOME', 'line\nbreak')
        before = self.snapshot()
        for name in COMMANDS:
            with self.subTest(name=name):
                result = self.run_switch(name, *args)
                self.assertEqual(result.returncode, 23, result.stderr)
                self.assertEqual(json.loads(result.stdout), list(args))
        self.assertEqual(self.snapshot(), before)

    def test_each_marker_independently_disables_and_removal_reenables(self):
        directory = self.home / '.config/emaki/autostart-disabled'
        directory.mkdir(parents=True)
        for disabled in COMMANDS:
            marker = directory / disabled
            marker.touch()
            before = self.snapshot()
            for name in COMMANDS:
                with self.subTest(disabled=disabled, name=name):
                    result = self.run_switch(name)
                    self.assertEqual(result.returncode, 0 if name == disabled else 23)
                    self.assertEqual(result.stdout, '' if name == disabled else '[]\n')
                    if name == disabled:
                        self.assertEqual(len(result.stderr.splitlines()), 1)
                        self.assertIn(str(marker), result.stderr)
                    else:
                        self.assertEqual(result.stderr, '')
            self.assertEqual(self.snapshot(), before)
            marker.unlink()
            self.assertEqual(self.run_switch(disabled).returncode, 23)

    def test_trusted_shell_opt_out_has_reserved_exit_status(self):
        directory = self.home / '.config/emaki/autostart-disabled'
        directory.mkdir(parents=True)
        (directory / 'shell').touch()
        self.env['WAYLAND_SOCKET'] = '73'
        self.assertEqual(self.run_switch('shell').returncode, 78)
        self.env.pop('WAYLAND_SOCKET')
        self.assertEqual(self.run_switch('shell').returncode, 0)

    def test_trusted_shell_captures_session_before_fixing_path(self):
        self.env.update(WAYLAND_SOCKET='73', PATH='/personal/bin',
                        QT_QPA_PLATFORMTHEME='personal-theme',
                        EMAKI_SHELL_SESSION_ENV='old snapshot',
                        EMAKI_SHELL_ORIGINAL_MALLOC_CONF='old malloc',
                        EMAKI_SHELL_PROCESS_ID='old process')
        markers = ('EMAKI_LOGIN_HANDOFF', 'EMAKI_SESSION_START', 'EMAKI_SESSION_SKIP_INTRO',
                   'EMAKI_STARTUP_COVER', 'EMAKI_SESSION_TAKEOVER')
        self.env.update({key: 'private-login-value' for key in markers})
        result = subprocess.run([str(WRAPPER), 'shell', sys.executable, '-I', '-c',
                                 'import json,os; print(json.dumps(dict(os.environ)))'],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        environment = json.loads(result.stdout)
        self.assertEqual(environment['PATH'], '@EMAKI_TRUSTED_PATH@')
        self.assertEqual(environment['EMAKI_LOGIN_HANDOFF'], 'private-login-value')
        session = json.loads(environment['EMAKI_SHELL_SESSION_ENV'])
        self.assertEqual(session['PATH'], '/personal/bin')
        self.assertEqual(session['QT_QPA_PLATFORMTHEME'], 'personal-theme')
        for key in ('WAYLAND_SOCKET', 'EMAKI_SHELL_SESSION_ENV',
                    'EMAKI_SHELL_ORIGINAL_MALLOC_CONF', 'EMAKI_SHELL_PROCESS_ID', *markers):
            self.assertNotIn(key, session)

    def test_missing_home_without_absolute_xdg_starts_command(self):
        for home in (None, ''):
            for xdg in (None, '', 'relative-config'):
                for key, value in (('HOME', home), ('XDG_CONFIG_HOME', xdg)):
                    if value is None:
                        self.env.pop(key, None)
                    else:
                        self.env[key] = value
                for name in COMMANDS:
                    with self.subTest(home=home, xdg=xdg, name=name):
                        result = self.run_switch(name, 'a b', '')
                        self.assertEqual(result.returncode, 23, result.stderr)
                        self.assertEqual(json.loads(result.stdout), ['a b', ''])

    def test_absolute_xdg_marker_without_home_still_disables(self):
        self.env.pop('HOME', None)
        self.env['XDG_CONFIG_HOME'] = str(self.home / 'custom config')
        directory = Path(self.env['XDG_CONFIG_HOME']) / 'emaki/autostart-disabled'
        directory.mkdir(parents=True)
        (directory / 'shell').touch()
        self.assertEqual(self.run_switch('shell').returncode, 0)

    def test_xdg_override_ignores_default_directory(self):
        default = self.home / '.config/emaki/autostart-disabled'
        default.mkdir(parents=True)
        (default / 'clipboard').touch()
        custom = self.home / 'custom config'
        self.env['XDG_CONFIG_HOME'] = str(custom)
        self.assertEqual(self.run_switch('clipboard').returncode, 23)
        directory = custom / 'emaki/autostart-disabled'
        directory.mkdir(parents=True)
        (directory / 'clipboard').touch()
        before = self.snapshot()
        self.assertEqual(self.run_switch('clipboard').returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_empty_and_relative_xdg_use_home(self):
        directory = self.home / '.config/emaki/autostart-disabled'
        directory.mkdir(parents=True)
        (directory / 'clipboard').touch()
        for value in ('', 'relative-config'):
            self.env['XDG_CONFIG_HOME'] = value
            self.assertEqual(self.run_switch('clipboard').returncode, 0)

    def test_dangling_marker_is_still_an_off_switch(self):
        directory = self.home / '.config/emaki/autostart-disabled'
        directory.mkdir(parents=True)
        (directory / 'clipboard').symlink_to('missing')
        self.assertEqual(self.run_switch('clipboard').returncode, 0)

    def test_invalid_switch_does_not_start_command(self):
        self.assertEqual(self.run_switch('../clipboard').returncode, 64)
        result = subprocess.run([str(WRAPPER)], env=self.env, capture_output=True)
        self.assertEqual(result.returncode, 64)

    def test_interpreter_ignores_inherited_python_search_paths(self):
        modules = Path(self.temp.name) / 'modules'
        modules.mkdir()
        marker = Path(self.temp.name) / 'injected'
        (modules / 'sitecustomize.py').write_text(
            f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
        result = subprocess.run([str(WRAPPER), 'invalid', 'unused'],
                                env=dict(self.env, PYTHONPATH=str(modules),
                                         PYTHONHOME=str(modules), PATH=str(modules)),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertFalse(marker.exists())

    def test_every_desktop_startup_uses_its_own_switch(self):
        actual = {}
        for path in (ROOT / 'niri').glob('*.kdl'):
            for line in path.read_text().splitlines():
                if not line.strip().startswith(('spawn-at-startup ', 'spawn-sh-at-startup ', 'spawn-trusted-shell-at-startup ')):
                    continue
                tokens = shlex.split(line.strip(), comments=False)
                if tokens[0] == 'spawn-trusted-shell-at-startup':
                    self.assertEqual(path.name, 'fork-rules.kdl')
                    self.assertEqual(tokens[1:], ['@EMAKI_BINDIR@/emaki-autostart',
                                                  'shell', '@EMAKI_BINDIR@/emaki-shell'])
                    continue
                self.assertEqual(tokens[:2], ['spawn-at-startup', 'emaki-autostart'])
                self.assertNotIn(tokens[2], actual)
                actual[tokens[2]] = tokens[3:]
        self.assertEqual(actual, COMMANDS)
        self.assertIn('scripts/emaki-autostart', (ROOT / 'Makefile').read_text())
        self.assertIn('/usr/bin/emaki-autostart\n',
                      (ROOT / 'packaging/emaki-config/expected-files.list').read_text())

    def test_documented_no_action_shortcut_validates(self):
        config = Path(self.temp.name) / 'personal.kdl'
        config.write_text(f'include "{ROOT}/niri/default.kdl"\n'
                          'binds { Mod+V { spawn-sh "true"; }; }\n')
        result = subprocess.run(['niri', 'validate', '-c', str(config)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
