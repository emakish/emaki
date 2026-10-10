#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Every shell component is registered in shell/qmldir; an unregistered one stops the whole shell."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent
SHELL = ROOT / 'shell'


def registered(directory):
    names = set()
    for line in (directory / 'qmldir').read_text().splitlines():
        match = re.fullmatch(r'(?:singleton\s+)?([A-Z]\w*)\s+[\d.]+\s+(\S+\.qml)', line.strip())
        if match:
            names.add((match[1], match[2]))
    return names


class Qmldir(unittest.TestCase):
    def test_every_component_is_registered(self):
        # Lower-case files (shell.qml, lock.qml, greeter.qml, session-cover.qml) are entry points, not types.
        directories = {path.parent for path in SHELL.rglob('*.qml')}
        for directory in directories:
            with self.subTest(directory=directory.relative_to(ROOT)):
                self.assertTrue((directory / 'qmldir').is_file())
                components = {path.name for path in directory.glob('*.qml') if path.name[0].isupper()}
                listed = {file for _, file in registered(directory)}
                self.assertEqual(sorted(components - listed), [], 'add these to the local qmldir')

    def test_every_registration_has_its_file(self):
        for directory in {path.parent for path in SHELL.rglob('qmldir')}:
            for name, file in registered(directory):
                with self.subTest(name=name):
                    self.assertEqual(Path(file).stem, name)
                    self.assertTrue((directory / file).is_file(), file)


if __name__ == '__main__':
    unittest.main()
