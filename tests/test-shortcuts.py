#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shortcut reference follows shipped KDL, includes and replacement bindings."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import os
import shutil
import subprocess
from runtime_fixture import runtime_path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('shortcuts', ROOT / 'shell/helpers/shortcuts.py')
shortcuts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shortcuts)


class Shortcuts(unittest.TestCase):
    def test_keyboard_sheet(self):
        with tempfile.TemporaryDirectory(prefix='shortcuts-') as directory:
            base = Path(directory)
            shutil.copytree(ROOT / 'shell', base / 'shell')
            directory_file = base / 'shell/qmldir'
            if 'ShortcutSheet 1.0 ShortcutSheet.qml' not in directory_file.read_text():
                with directory_file.open('a') as stream:
                    stream.write('\nShortcutSheet 1.0 ShortcutSheet.qml\n')
            shutil.copytree(ROOT / 'niri', base / 'niri')
            shutil.copyfile(ROOT / 'tests/fixtures/ShortcutKeyboardTest.qml', base / 'shell/test.qml')
            for name in ('home', 'config', 'data', 'cache', 'state'):
                (base / name).mkdir(mode=0o700)
            env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
                       XDG_DATA_HOME=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
                       XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
                       QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1')
            for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
                env.pop(key, None)
            result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                                    env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('SHORTCUT_KEYBOARD_RESULT 2 0', result.stdout + result.stderr)

    def test_real_defaults_and_removed_binding(self):
        config = ROOT / 'niri/default.kdl'
        rows = shortcuts.read_shortcuts(config)
        self.assertGreater(len(rows), 50)
        self.assertIn({'keys': 'Super+D', 'description': 'Open launcher'}, rows)
        self.assertNotIn('Ctrl+Alt+Delete', [row['keys'] for row in rows])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'default.kdl'
            text = config.read_text()
            path.write_text('\n'.join(line for line in text.splitlines()
                                      if not line.startswith('include ') and 'Mod+D ' not in line))
            changed = shortcuts.read_shortcuts(path)
            self.assertNotIn('Super+D', [row['keys'] for row in changed])
            self.assertEqual(len(rows) - 1, len(changed))

    def test_shipped_reference_includes_fork_binding_but_explicit_config_does_not(self):
        helper = ROOT / 'shell/helpers/shortcuts.py'
        for arguments, expected in (([], True), (['--config', str(ROOT / 'niri/default.kdl')], False)):
            with self.subTest(arguments=arguments):
                result = subprocess.run(['python3', str(helper), *arguments],
                                        capture_output=True, text=True, check=True)
                reference = json.loads(result.stdout)
                self.assertEqual(reference['state'], 'ready')
                proof = {'keys': 'Ctrl+Alt+Delete',
                         'description': 'Show that the password window is real'}
                self.assertEqual(proof in reference['rows'], expected)

    def test_fork_entry_points_include_prompt_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            shutil.copytree(ROOT / 'niri', base / 'niri')
            personal = base / 'personal.kdl'
            personal.write_text(f'include "{base}/niri/default.kdl"\n')
            system = base / 'system.kdl'
            system.write_text((base / 'niri/system.kdl').read_text().replace(
                '@EMAKI_DATADIR@/niri/', f'{base}/niri/'))
            for name in ('fork.kdl', 'fork-system.kdl'):
                with self.subTest(entry=name):
                    entry = base / 'niri' / name
                    entry.write_text(entry.read_text().replace(
                        '~/.config/niri/config.kdl', str(personal)).replace(
                        '/etc/niri/config.kdl', str(system)).replace(
                        '~/.config/emaki/niri-emaki.kdl', str(base / 'optional.kdl')))
                    rows = shortcuts.read_shortcuts(entry)
                    self.assertIn({'keys': 'Super+D', 'description': 'Open launcher'}, rows)
                    self.assertIn({'keys': 'Ctrl+Alt+Delete',
                                   'description': 'Show that the password window is real'}, rows)

    def test_surface_entry_bindings(self):
        bindings = {
            binding[0]: actions
            for head, children in shortcuts.nodes((ROOT / 'niri/default.kdl').read_text())
            if head[0] == 'binds'
            for binding, actions in children
        }
        required = {
            'Mod+B': ['keyboard', 'open', 'bar'],
            'Mod+J': ['keyboard', 'open', 'dock'],
            'Mod+Ctrl+S': ['system', 'open', 'sound'],
            'Mod+Slash': ['keyboard', 'open', 'shortcuts'],
            'Mod+Period': ['keyboard', 'open', 'shortcuts'],
        }
        for key, arguments in required.items():
            with self.subTest(key=key):
                self.assertEqual(bindings.get(key),
                                 [(['spawn', 'emaki-shell', 'call', *arguments], [])])
        self.assertLess(list(bindings).index('Mod+Slash'), list(bindings).index('Mod+Period'))
        for key in ('Mod+Shift+Slash', 'Mod+Shift+Period'):
            with self.subTest(key=key):
                self.assertEqual(bindings.get(key), [(['show-hotkey-overlay'], [])])

    def test_nested_includes_replacements_comments_and_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / 'child.kdl').write_text('binds { Mod+A { spawn "first"; }; Mod+B { spawn "with // comment"; }; }')
            (path / 'root.kdl').write_text('''/* binds { imaginary; } */
include "child.kdl"
/- binds { Mod+C { ignored; }; }
binds {
    Mod+A hotkey-overlay-title="Replacement" { spawn "second"; }
    /- Mod+D { ignored; }
    Mod+E { spawn r#"literal; {}"#; }
}
''')
            rows = shortcuts.read_shortcuts(path / 'root.kdl')
            self.assertEqual(rows, [
                {'keys': 'Super+A', 'description': 'Replacement'},
                {'keys': 'Super+B', 'description': 'spawn with // comment'},
                {'keys': 'Super+E', 'description': 'spawn literal; {}'},
            ])

    def test_cycle_and_broken_config_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'root.kdl'
            for content in ('include "root.kdl"', 'binds { Mod+D { spawn "x"; }'):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    shortcuts.read_shortcuts(path)

    def test_optional_includes_missing_and_present(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = base / 'root.kdl'
            child = base / 'optional.kdl'
            for include in ('include optional=true "optional.kdl"',
                            'include "optional.kdl" optional=true'):
                with self.subTest(include=include):
                    path.write_text(include + '\nbinds { Mod+A { spawn "base"; }; }')
                    self.assertEqual(shortcuts.read_shortcuts(path), [
                        {'keys': 'Super+A', 'description': 'spawn base'},
                    ])
                    child.write_text('binds { Mod+Ctrl+Escape hotkey-overlay-title="Revoke sudo keys" { spawn "revoke"; }; }')
                    self.assertEqual(shortcuts.read_shortcuts(path), [
                        {'keys': 'Super+Ctrl+Escape', 'description': 'Revoke sudo keys'},
                        {'keys': 'Super+A', 'description': 'spawn base'},
                    ])
                    child.unlink()

    def test_required_and_invalid_optional_includes_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            path = base / 'root.kdl'
            (base / 'directory').mkdir()
            (base / 'broken.kdl').write_text('binds {')
            for include in ('include "missing.kdl"',
                            'include optional=false "missing.kdl"',
                            'include "missing.kdl" optional=false',
                            'include optional=maybe "missing.kdl"',
                            'include optional=true "directory"',
                            'include optional=true "broken.kdl"',
                            'include optional=true "root.kdl"'):
                with self.subTest(include=include):
                    path.write_text(include)
                    with self.assertRaises(ValueError):
                        shortcuts.read_shortcuts(path)


if __name__ == '__main__':
    unittest.main()
