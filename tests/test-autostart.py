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
    'clipboard': ['python3', '-B', '/usr/share/emaki/shell/helpers/clipboard_store.py', '--watch'],
    'authentication': ['env', 'QT_QUICK_CONTROLS_STYLE=Fusion', '/usr/lib/polkit-kde-authentication-agent-1'],
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

    def test_every_desktop_startup_uses_its_own_switch(self):
        actual = {}
        for path in (ROOT / 'niri').glob('*.kdl'):
            for line in path.read_text().splitlines():
                if not line.strip().startswith(('spawn-at-startup ', 'spawn-sh-at-startup ')):
                    continue
                tokens = shlex.split(line.strip(), comments=False)
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
