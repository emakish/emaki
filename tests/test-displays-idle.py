#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Display idle waits and open-page updates without a compositor or user settings."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from runtime_fixture import runtime_path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = Path(os.environ.get('DISPLAY_IDLE_SOURCE', ROOT))
spec = importlib.util.spec_from_file_location('display_idle', SOURCE / 'shell/helpers/displays.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class DisplayIdleTest(unittest.TestCase):
    def test_idle_wait_has_no_periodic_wakeups(self):
        controller = Mock(pending=None)
        controller.response.return_value = {}
        with patch.object(backend, 'Displays', return_value=controller), \
                patch.object(backend.signal, 'signal'), patch.object(backend, 'print'), \
                patch.object(backend.select, 'select', return_value=([sys.stdin], [], [])) as select, \
                patch.object(backend.os, 'read', return_value=b''):
            backend.main()
        self.assertIsNone(select.call_args.args[3], 'idle helper must block until input')

    def test_closed_page_is_idle_and_open_page_follows_output_changes(self):
        with tempfile.TemporaryDirectory(prefix='display-idle-') as temporary:
            base = Path(temporary)
            shutil.copytree(ROOT / 'shell', base / 'shell')
            for file in ('DisplaysService.qml', 'SettingsController.qml'):
                shutil.copyfile(SOURCE / 'shell' / file, base / 'shell' / file)
            subprocess.run([sys.executable, str(ROOT / 'scripts/render-paths'), '--source', str(base)], check=True)
            shutil.copyfile(ROOT / 'tests/fixtures/DisplaysIdleTest.qml', base / 'shell/test.qml')
            for part in ('home', 'config', 'data', 'cache', 'state'):
                (base / part).mkdir(mode=0o700)
            env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
                       XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
                       XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
                       XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
                       QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                       QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
                       DISPLAYS_HELPER=str(ROOT / 'tests/fixtures/displays-page-helper.py'),
                       DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                       DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
            for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
                env.pop(key, None)
            result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                                    env=env, capture_output=True, text=True, timeout=20)
            output = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, output)
            self.assertIn('DISPLAY_IDLE_RESULT 2 0', output)
            # The session controller, not individual display controls, owns visibility.
            controller = (SOURCE / 'shell/SettingsController.qml').read_text()
            self.assertIn('active: controller.opened && controller.page === "displays"', controller)


if __name__ == '__main__':
    unittest.main()
