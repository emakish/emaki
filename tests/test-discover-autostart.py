#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the packaged reminder through the session's real autostart generator."""
import configparser
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = Path('/usr/lib/systemd/user-generators/systemd-xdg-autostart-generator')
ENTRY = 'emaki-discover-notifier.desktop'


class DiscoverAutostartTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='emaki-discover-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        subprocess.run(['bash', '-eu', '-c',
                        'source "$1/PKGBUILD"; srcdir=$1; pkgdir=$2; package', '_',
                        str(ROOT / 'packaging/emaki-apps'), str(self.base)], check=True)
        self.entry = self.base / 'etc/xdg/autostart' / ENTRY
        self.assertTrue(self.entry.is_file(), 'Rich package must install the reminder')
        self.home = self.base / 'home'
        self.home.mkdir()
        self.env = dict(PATH=os.defpath, HOME=str(self.home),
                        XDG_CONFIG_HOME=str(self.home / '.config'),
                        XDG_CONFIG_DIRS=str(self.base / 'etc/xdg'),
                        XDG_CURRENT_DESKTOP='niri')

    def generate(self):
        self.assertTrue(GENERATOR.is_file(), 'systemd autostart generator is required')
        # The host need not have Discover installed. Only replace the executable in
        # the scratch package; the production path is checked separately below.
        executable = self.base / 'DiscoverNotifier'
        executable.write_text('#!/bin/sh\nexit 0\n')
        executable.chmod(0o755)
        self.entry.write_text(self.entry.read_text().replace('/usr/lib/DiscoverNotifier', str(executable)))
        output = self.base / 'generated'
        output.mkdir()
        subprocess.run([str(GENERATOR), str(output), str(output), str(output)],
                       env=self.env, check=True, capture_output=True)
        return output

    def test_session_generates_and_accepts_reminder(self):
        desktop = configparser.ConfigParser(interpolation=None)
        desktop.read(self.entry)
        self.assertEqual(desktop['Desktop Entry']['Exec'], '/usr/lib/DiscoverNotifier --check-delay 20')
        self.assertEqual(desktop['Desktop Entry']['TryExec'], '/usr/lib/DiscoverNotifier')
        session = configparser.ConfigParser(interpolation=None, strict=False)
        session.read(ROOT / 'systemd/niri-emaki.service')
        self.assertIn('xdg-desktop-autostart.target', session['Unit']['Wants'].split())
        login = configparser.ConfigParser(interpolation=None)
        login.read(ROOT / 'niri/niri-emaki.desktop')
        self.assertEqual(login['Desktop Entry']['DesktopNames'], 'niri')
        output = self.generate()
        services = list(output.glob('app-emaki*autostart.service'))
        self.assertEqual(len(services), 1)
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(services[0])
        self.assertIn('DiscoverNotifier', unit['Service']['ExecStart'])
        self.assertTrue((output / 'xdg-desktop-autostart.target.wants' / services[0].name).is_symlink())
        condition = shlex.split(unit['Service']['ExecCondition'])
        self.assertEqual(subprocess.run(condition, env=self.env, capture_output=True).returncode, 0)
        self.assertNotEqual(subprocess.run(condition, env=dict(self.env, XDG_CURRENT_DESKTOP='KDE'),
                                           capture_output=True).returncode, 0)

    def test_person_can_disable_reminder(self):
        personal = self.home / '.config/autostart' / ENTRY
        personal.parent.mkdir(parents=True)
        personal.write_text('[Desktop Entry]\nType=Application\nHidden=true\n')
        original = personal.read_bytes()
        output = self.generate()
        self.assertEqual(list(output.glob('app-emaki*autostart.service')), [])
        self.assertEqual(personal.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
