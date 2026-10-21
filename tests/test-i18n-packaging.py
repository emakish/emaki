#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check staged catalogue inventories without installing a system package."""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CataloguePackaging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='emaki-i18n-package-')
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.base = Path(cls.temporary.name)
        cls.source = cls.base / 'i18n'
        cls.source.mkdir()
        for name in ('install.py', 'catalogues.py'):
            shutil.copy2(ROOT / 'i18n' / name, cls.source / name)
        for path in (ROOT / 'i18n').glob('*/*'):
            if path.suffix in ('.po', '.ts'):
                target = cls.source / path.relative_to(ROOT / 'i18n')
                target.parent.mkdir(exist_ok=True)
                shutil.copy2(path, target)
        shutil.copytree(ROOT / 'i18n/locale', cls.source / 'locale')
        cls.env = os.environ.copy()
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET',
                     'QT_QPA_PLATFORMTHEME', 'DBUS_SESSION_BUS_ADDRESS'):
            cls.env.pop(name, None)
        for name, directory in (('HOME', 'home'), ('XDG_RUNTIME_DIR', 'runtime'),
                                ('XDG_CONFIG_HOME', 'config'), ('XDG_CACHE_HOME', 'cache'),
                                ('XDG_DATA_HOME', 'data')):
            path = cls.base / directory
            path.mkdir(mode=0o700)
            cls.env[name] = str(path)
        cls.env['QT_QPA_PLATFORM'] = 'offscreen'

    def run_install(self, *arguments):
        return subprocess.run([sys.executable, str(self.source / 'install.py'), *arguments],
                              env=self.env, text=True, capture_output=True, timeout=30)

    def test_staged_inventory(self):
        result = self.run_install('--check-built')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        staged = self.base / 'staged'
        result = self.run_install('--destination', str(staged))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        actual = {path.relative_to(staged).as_posix() for path in staged.rglob('*')
                  if path.is_file()}
        inventory = ROOT / 'packaging/emaki-config/expected-files.list'
        expected = {line.split('/emaki/locale/', 1)[1]
                    for line in inventory.read_text().splitlines() if '/emaki/locale/' in line}
        self.assertTrue(expected)
        self.assertEqual(actual, expected)
        for relative in actual:
            path = staged / relative
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644, relative)
            self.assertEqual(path.read_bytes(), (self.source / 'locale' / relative).read_bytes())
        # The standalone installer packages only its gettext domain in the Python module.
        inventory = ROOT / 'packaging/emaki-installer/expected-files.list'
        expected_installer = {line.split('/emaki_installer/locale/', 1)[1]
                              for line in inventory.read_text().splitlines()
                              if '/emaki_installer/locale/' in line}
        self.assertTrue(expected_installer)
        self.assertEqual({name for name in actual if name.endswith('/LC_MESSAGES/installer.mo')},
                         expected_installer)

    def test_missing_and_wrong_portuguese_fallbacks(self):
        locale = self.source / 'locale'
        aliases = sorted(locale.glob('*_pt.qm')) + sorted((locale / 'pt/LC_MESSAGES').glob('*.mo'))
        self.assertEqual(len(aliases), 9)
        for alias in aliases:
            original = alias.read_bytes()
            for missing in (True, False):
                with self.subTest(alias=alias.relative_to(locale), missing=missing):
                    try:
                        if missing:
                            alias.unlink()
                        else:
                            alias.write_bytes(b'invalid catalogue')
                        result = self.run_install('--check-built')
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn('Missing or stale Portuguese fallback', result.stderr)
                        self.assertIn(str(alias), result.stderr)
                    finally:
                        alias.write_bytes(original)
        result = self.run_install('--check-built')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
