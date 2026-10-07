#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Device helpers reach existing installations through the desktop packages."""
import configparser
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def recipe_dependencies(name):
    path = ROOT / 'packaging' / name
    result = subprocess.run(
        ['bash', '-eu', '-c', 'startdir=$1; source "$1/PKGBUILD"; printf "%s\\n" "${depends[@]}"',
         '_', str(path)], text=True, capture_output=True, check=True)
    return result.stdout.splitlines()


class DeviceTests(unittest.TestCase):
    def test_bluetooth_dependency_uses_session_autostart(self):
        session = configparser.ConfigParser(interpolation=None, strict=False)
        session.read(ROOT / 'systemd/niri-emaki.service')
        self.assertIn('xdg-desktop-autostart.target', session['Unit']['Wants'].split())
        for name in ('emaki-config', 'emaki-desktop'):
            self.assertIn('blueman', recipe_dependencies(name))
        for name in ('packages-extra.txt', 'profile/packages.x86_64'):
            self.assertIn('blueman', (ROOT / 'iso' / name).read_text().splitlines())

    def test_rich_package_installs_visible_printer_entry(self):
        with tempfile.TemporaryDirectory(prefix='emaki-printers-') as temporary:
            subprocess.run(
                ['bash', '-eu', '-c', 'source "$1/PKGBUILD"; srcdir=$1; pkgdir=$2; package',
                 '_', str(ROOT / 'packaging/emaki-apps'), temporary], check=True)
            entry = configparser.ConfigParser(interpolation=None)
            entry.read(Path(temporary) / 'usr/share/applications/emaki-printers.desktop')
            desktop = entry['Desktop Entry']
            self.assertEqual(desktop['Name'], 'Printers')
            self.assertEqual(desktop['Exec'], 'kcmshell6 kcm_printer_manager')
            self.assertEqual(desktop['TryExec'], 'kcmshell6')
            self.assertFalse(desktop.getboolean('NoDisplay', fallback=False))
            self.assertFalse(desktop.getboolean('Hidden', fallback=False))
            self.assertNotIn('OnlyShowIn', desktop)
            self.assertNotIn('NotShowIn', desktop)
        self.assertIn('print-manager', recipe_dependencies('emaki-apps'))

    def test_nvk_reaches_live_and_existing_desktops(self):
        self.assertIn('vulkan-nouveau', recipe_dependencies('emaki-desktop'))
        for name in ('packages-extra.txt', 'profile/packages.x86_64'):
            self.assertIn('vulkan-nouveau', (ROOT / 'iso' / name).read_text().splitlines())


if __name__ == '__main__':
    unittest.main()
