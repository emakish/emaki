#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Private XDG fixtures for the apps provider, including unsafe entry refusals."""
import importlib.machinery
import importlib.util
import os
import json
import subprocess
import shutil
from pathlib import Path
import tempfile
import unittest
import types
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'scripts/emaki-settings-apps'
loader = importlib.machinery.SourceFileLoader('apps_provider', str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
apps = importlib.util.module_from_spec(spec)
loader.exec_module(apps)
ENTRY = '[Desktop Entry]\nType=Application\nName=Fixture app\nExec=fixture-app %U\n# Keep my comment\nX-Personal=keep\n'


class Apps(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='emaki-apps-')
        self.root = Path(self.temp.name)
        self.config = self.root / 'config'
        self.startup = self.config / 'autostart'
        self.startup.mkdir(parents=True)
        self.system = self.root / 'system'
        self.system.mkdir()
        self.source = self.system / 'fixture.desktop'
        self.source.write_text(ENTRY)
        self.environment = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.config)})
        self.environment.start()
        self.catalog = patch.object(apps, 'installed_apps', return_value=[{
            'id': 'fixture.desktop', 'name': 'Fixture app', 'roles': ['editor'],
            'filename': str(self.source)}])
        self.catalog.start()
        self.defaults = patch.object(apps, 'resolved_defaults', return_value={})
        self.defaults.start()

    def tearDown(self):
        self.catalog.stop()
        self.defaults.stop()
        self.environment.stop()
        self.temp.cleanup()

    def run_action(self, *args):
        return apps.run([*args, '--json'])

    def test_add_disable_reset_preserves_source_and_personal_fields(self):
        self.assertEqual(self.run_action('status')['startup'], [])
        self.assertTrue(self.run_action('add', 'fixture.desktop')['startup'][0]['enabled'])
        path = self.startup / 'fixture.desktop'
        self.assertFalse(self.run_action('set', 'fixture.desktop', 'false')['startup'][0]['enabled'])
        self.assertIn('Hidden=true\n', path.read_text())
        self.assertIn('# Keep my comment\nX-Personal=keep\n', path.read_text())
        self.assertTrue(self.run_action('reset', 'fixture.desktop')['startup'][0]['enabled'])
        self.assertNotIn('Hidden=', path.read_text())
        self.assertEqual(self.source.read_text(), ENTRY)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_errors_are_plain_and_hide_system_details(self):
        self.assertIn('already has a personal startup entry', apps.error_message(FileExistsError(17, 'File exists', '/private/example')))
        self.assertNotIn('/private', apps.error_message(OSError(5, 'Input/output error', '/private/example')))
        self.assertIn('could not be read', apps.error_message(apps.configparser.MissingSectionHeaderError('private', 1, 'bad')))

    def test_refuses_existing_add_unknown_and_traversal(self):
        self.run_action('add', 'fixture.desktop')
        for arguments in [('add', 'fixture.desktop'), ('add', 'missing.desktop'),
                          ('set', '../fixture.desktop', 'false'), ('set', 'fixture.desktop', 'maybe')]:
            with self.assertRaises((OSError, ValueError)):
                self.run_action(*arguments)
        self.assertTrue(self.run_action('status')['startup'][0]['enabled'])

    def test_refuses_symlink_and_hardlink_never_changes_target(self):
        target = self.startup / 'fixture.desktop'
        target.symlink_to(self.source)
        self.assertEqual(self.run_action('status')['startup'], [])
        with self.assertRaises(OSError):
            self.run_action('set', 'fixture.desktop', 'false')
        target.unlink()
        os.link(self.source, target)
        with self.assertRaises(ValueError):
            self.run_action('reset', 'fixture.desktop')
        self.assertEqual(self.source.read_text(), ENTRY)

    def test_refuses_symlink_directory(self):
        self.startup.rmdir()
        self.startup.symlink_to(self.system)
        with self.assertRaises(OSError):
            self.run_action('set', 'fixture.desktop', 'false')
        self.assertEqual(self.source.read_text(), ENTRY)

    def test_groups_and_hidden_action_remain_separate(self):
        text = ENTRY + 'Hidden=true\n[Desktop Action Other]\nName=Other\nHidden=true\n'
        (self.startup / 'fixture.desktop').write_text(text)
        self.run_action('reset', 'fixture.desktop')
        result = (self.startup / 'fixture.desktop').read_text()
        self.assertNotIn('Hidden=', result.split('[Desktop Action')[0])
        self.assertIn('Hidden=true', result.split('[Desktop Action')[1])

    def test_no_system_startup_entries_are_listed_or_changed(self):
        self.assertEqual(self.run_action('status')['startup'], [])
        with self.assertRaises(FileNotFoundError):
            self.run_action('set', 'fixture.desktop', 'false')
        self.assertEqual(self.source.read_text(), ENTRY)

    def test_refuses_unowned_entry(self):
        self.run_action('add', 'fixture.desktop')
        with patch.object(apps.os, 'geteuid', return_value=os.geteuid() + 1):
            with self.assertRaises(ValueError):
                self.run_action('set', 'fixture.desktop', 'false')
        self.assertTrue(self.run_action('status')['startup'][0]['enabled'])


class Defaults(unittest.TestCase):
    def test_real_xdg_precedence_and_terminal_default(self):
        with tempfile.TemporaryDirectory(prefix='emaki-app-defaults-') as temporary:
            root = Path(temporary)
            for name in ('config', 'system', 'data/applications', 'bin'):
                (root / name).mkdir(parents=True)
            executable = Path(shutil.which('true')).resolve()
            desktop = '[Desktop Entry]\nType=Application\nName={}\nExec=' + str(executable) + ' %u\nMimeType=x-scheme-handler/https;\n'
            for name in ('System Browser', 'Personal Browser', 'Desktop Browser'):
                (root / 'data/applications' / (name.split()[0] + '.desktop')).write_text(desktop.format(name))
            (root / 'data/applications/terminal.desktop').write_text(
                '[Desktop Entry]\nType=Application\nName=Fixture Terminal\nExec=' + str(executable) + '\nCategories=TerminalEmulator;\n')
            helper = root / 'bin/emaki-terminal'
            helper.write_text("#!/bin/sh\nprintf '%s\\n' " + repr(json.dumps({'executable': str(executable)})) + '\n')
            helper.chmod(0o700)
            association = '[Default Applications]\nx-scheme-handler/https={}.desktop;\n'
            (root / 'system/mimeapps.list').write_text(association.format('System'))
            env = dict(os.environ, XDG_CONFIG_HOME=str(root / 'config'),
                       XDG_CONFIG_DIRS=str(root / 'system'), XDG_DATA_HOME=str(root / 'data'),
                       XDG_DATA_DIRS=str(root / 'empty'), XDG_CURRENT_DESKTOP='niri',
                       PATH=str(root / 'bin') + os.pathsep + os.environ['PATH'])
            def read():
                command = ['python3', '-c', 'import runpy,json; m=runpy.run_path(' + repr(str(SOURCE)) + '); print(json.dumps(m["resolved_defaults"]()))']
                return json.loads(subprocess.run(command, env=env, capture_output=True, text=True, check=True, timeout=5).stdout)
            result = read()
            self.assertEqual(result['browser']['name'], 'System Browser')
            self.assertEqual(result['terminal']['name'], 'Fixture Terminal')
            self.assertIsNone(result['mail'])
            (root / 'config/mimeapps.list').write_text(association.format('Personal'))
            self.assertEqual(read()['browser']['name'], 'Personal Browser')
            (root / 'config/niri-mimeapps.list').write_text(association.format('Desktop'))
            self.assertEqual(read()['browser']['name'], 'Desktop Browser')


class Mutants(unittest.TestCase):
    def check_mutant(self, before, after, scenario):
        source = SOURCE.read_text()
        self.assertIn(before, source)
        mutant = types.ModuleType('apps_mutant')
        exec(compile(source.replace(before, after, 1), str(SOURCE), 'exec'), mutant.__dict__)
        fixture = Apps(scenario)
        fixture.setUp()
        try:
            # Exercise the same production tests against a concrete mutated implementation.
            with patch.dict(globals(), {'apps': mutant}):
                with patch.object(mutant, 'installed_apps', return_value=[{
                    'id': 'fixture.desktop', 'name': 'Fixture app', 'roles': ['editor'],
                    'filename': str(fixture.source)}]), patch.object(mutant, 'resolved_defaults', return_value={}):
                    # setUp is already active; directly capture the scenario assertion.
                    try:
                        getattr(fixture, scenario)()
                    except (AssertionError, OSError, ValueError):
                        return
                    self.fail('The safety scenario did not reject the mutant.')
        finally:
            fixture.tearDown()

    def test_symlink_guard_mutant(self):
        self.check_mutant('os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK',
                          'os.O_RDONLY | os.O_NONBLOCK',
                          'test_refuses_symlink_and_hardlink_never_changes_target')

    def test_system_directory_mutant(self):
        self.check_mutant("config = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')",
                          "config = Path(os.environ['XDG_CONFIG_HOME']).parent / 'system'",
                          'test_add_disable_reset_preserves_source_and_personal_fields')

    def test_hidden_group_guard_mutant(self):
        self.check_mutant("if inside and re.match(r'Hidden\\s*=', stripped):",
                          "if re.match(r'Hidden\\s*=', stripped):",
                          'test_groups_and_hidden_action_remain_separate')


if __name__ == '__main__':
    unittest.main()
