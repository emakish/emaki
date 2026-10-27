#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The image check's system-map step: which state commands it runs and what passes. No VM."""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('boot_map', ROOT / 'tests/vm/iso-boot-check.py')
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)

BLOCK = '''```system-map
component: {name}
what: A fixture.
files:
  /usr/bin/{name}
zone: package
state: {state}
change: Update the package.
never: Edit it.
rollback: Boot a snapshot.
```
'''


def page(*components):
    return '# Map\n\n' + '\n'.join(BLOCK.format(name=name, state=state) for name, state in components)


class MapStateTests(unittest.TestCase):
    def test_commands_are_selected_and_none_yet_is_skipped(self):
        text = page(('one', 'emaki-one status --json'), ('two', 'none yet: no reader exists'),
                    ('three', 'emaki-three call status'))
        self.assertEqual(boot.map_state_commands(text),
                         [('one', 'emaki-one status --json'), ('three', 'emaki-three call status')])

    def test_continued_state_line_is_one_command(self):
        text = page(('one', 'emaki-one call\n  status'))
        self.assertEqual(boot.map_state_commands(text), [('one', 'emaki-one call status')])

    def test_each_state_command_is_checked_separately(self):
        text = page(('one', 'emaki-one status --json; emaki-two status --json'))
        self.assertEqual(boot.map_state_commands(text),
                         [('one', 'emaki-one status --json'),
                          ('one-2', 'emaki-two status --json')])

    def test_quoted_semicolon_is_an_argument(self):
        text = page(('one', "emaki-one 'value;kept'; emaki-two status"))
        self.assertEqual(boot.map_state_commands(text),
                         [('one', "emaki-one 'value;kept'"), ('one-2', 'emaki-two status')])

    def test_every_split_command_receives_the_session_environment(self):
        command = "python3 -c 'import os,json; print(json.dumps(os.environ.get(\"MAP_SESSION\")))'"
        with mock.patch.object(boot, 'SESSION_ENV', 'MAP_SESSION=ready'):
            for _, part in boot.map_state_commands(page(('one', command + '; ' + command))):
                result = subprocess.run(boot.session_command(part), shell=True,
                                        capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(result.stdout), 'ready')

    def test_non_alongside_map_requires_a_clean_not_applicable_answer(self):
        component = 'alongside-test-boot-safety'
        self.assertTrue(boot.map_answer(component, '{"state": "not_alongside"}', False))
        self.assertFalse(boot.map_answer(component, '{"state": "refused"}', False))
        self.assertFalse(boot.map_answer(component, '[]', False))
        self.assertTrue(boot.map_answer(component, '{"state": "ready"}', True))
        self.assertFalse(boot.map_answer(component, '{"state": "not_alongside"}', True))
        self.assertFalse(boot.map_answer(component, '[]', True))

    def test_installed_programs_state_has_no_placeholders_or_consent_requirement(self):
        paths = ['docs/system-map.md']
        for path in paths:
            commands = boot.map_state_commands((ROOT / path).read_text())
            self.assertEqual([command for name, command in commands
                              if name in ('installed-programs', 'installed-programs-2')],
                             ['emaki-programs list --json', 'emaki-programs aur --json'])

    def test_an_empty_or_broken_map_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'no system-map blocks'):
            boot.map_state_commands('# Map\n')
        with self.assertRaisesRegex(ValueError, 'field rollback is missing'):
            boot.map_state_commands(page(('one', 'emaki-one')).replace('rollback: Boot a snapshot.\n', ''))

    def test_only_json_answers_pass(self):
        self.assertTrue(boot.json_answer('{"services": {}}\n'))
        self.assertTrue(boot.json_answer('[]'))
        self.assertFalse(boot.json_answer(''))
        self.assertFalse(boot.json_answer('running\n'))
        self.assertFalse(boot.json_answer('{"cut": '))

    def test_state_commands_get_the_session_sockets(self):
        # 0.5.0 image check: over SSH without these, niri answered "socket_not_configured" and the
        # shell IPC found no instance on display "unk".
        for name in ('XDG_RUNTIME_DIR=', 'DBUS_SESSION_BUS_ADDRESS=', 'NIRI_SOCKET=', 'WAYLAND_DISPLAY='):
            self.assertIn(name, boot.SESSION_ENV)

    def test_the_rules_read_only_commands_are_run(self):
        commands = dict(boot.rules_commands())
        self.assertEqual(commands, {'state': 'emaki state --json', 'map': 'emaki map --json',
                                    'history': 'emaki settings history --json'})
        self.assertNotIn('undo', commands)
        rules = (ROOT / boot.system_map.RULES).read_text()
        self.assertTrue(all(f'`{command}`' in rules for command in commands.values()))

    def test_a_rules_command_passes_with_json_and_a_partial_answer(self):
        self.assertTrue(boot.rules_answer(0, '{}'))
        self.assertTrue(boot.rules_answer(1, '{"status": "partial"}'))
        self.assertFalse(boot.rules_answer(2, '{}'))
        self.assertFalse(boot.rules_answer(0, 'Usage: emaki'))

    def test_the_shipped_page_promises_a_session_state(self):
        commands = boot.map_state_commands((ROOT / 'docs/system-map.md').read_text())
        self.assertIn(('wifi-recovery', 'emaki-shell call system status'), commands)
        self.assertIn(('trusted-authentication-path-2',
                       'niri-emaki msg --json trusted-shell-identity'), commands)
        self.assertTrue(all(command.split()[0].startswith(('emaki', 'niri-emaki'))
                            for _, command in commands))


if __name__ == '__main__':
    unittest.main()
