#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The image check's system-map step: which state commands it runs and what passes. No VM."""
import importlib.util
from pathlib import Path
import unittest

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

    def test_the_shipped_page_promises_a_session_state(self):
        commands = boot.map_state_commands((ROOT / 'docs/system-map.md').read_text())
        self.assertIn(('wifi-recovery', 'emaki-shell call system status'), commands)
        self.assertTrue(all(command.split()[0].startswith('emaki') for _, command in commands))


if __name__ == '__main__':
    unittest.main()
