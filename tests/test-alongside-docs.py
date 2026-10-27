#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Operational alongside documentation agrees with the shipped source switch."""
import ast
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def section(name, start, end):
    text = (ROOT / name).read_text()
    return text.split(start, 1)[1].split(end, 1)[0]


class AlongsideDocs(unittest.TestCase):
    def current_sections(self):
        current = {
            'installer/README.md': section('installer/README.md', '## Alongside Windows',
                                           'With the switch on'),
        }
        return current

    def test_current_docs_match_source_default_and_enablement_release(self):
        tree = ast.parse((ROOT / 'installer/emaki_installer/constants.py').read_text())
        default = next(node.value.value for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == 'ALONGSIDE'
                               for target in node.targets))
        for name, text in self.current_sections().items():
            with self.subTest(document=name):
                self.assertIn(f'ALONGSIDE = {default}', text)
                self.assertIn('0.8.0', text)
                self.assertNotRegex(text, re.compile(r'(?:ships|shipped) off', re.I))

    def test_current_release_gate_docs_name_acceptance_and_exception(self):
        for name, text in self.current_sections().items():
            with self.subTest(document=name):
                self.assertIn('PASS', text)
                self.assertIn('owner-signed exception', text)



if __name__ == '__main__':
    unittest.main()
