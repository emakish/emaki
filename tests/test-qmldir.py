#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every shell component is registered in shell/qmldir; an unregistered one stops the whole shell."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent
SHELL = ROOT / 'shell'


def registered():
    names = set()
    for line in (SHELL / 'qmldir').read_text().splitlines():
        match = re.fullmatch(r'(?:singleton\s+)?([A-Z]\w*)\s+[\d.]+\s+(\S+\.qml)', line.strip())
        if match:
            names.add((match[1], match[2]))
    return names


class Qmldir(unittest.TestCase):
    def test_every_component_is_registered(self):
        # Lower-case files (shell.qml, lock.qml, greeter.qml, session-cover.qml) are entry points, not types.
        components = {path.name for path in SHELL.glob('*.qml') if path.name[0].isupper()}
        listed = {file for _, file in registered()}
        self.assertEqual(sorted(components - listed), [], 'add these to shell/qmldir')

    def test_every_registration_has_its_file(self):
        for name, file in registered():
            with self.subTest(name=name):
                self.assertEqual(Path(file).stem, name)
                self.assertTrue((SHELL / file).is_file(), file)


if __name__ == '__main__':
    unittest.main()
