#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise settings controls with real keyboard and mouse events."""
from runtime_fixture import runtime_path
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix='emaki-controls-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell/settings', base / 'shell/settings')
    shutil.copyfile(ROOT / 'shell/ShellPalette.qml', base / 'shell/ShellPalette.qml')
    (base / 'shell/qmldir').write_text('singleton ShellPalette 1.0 ShellPalette.qml\n')
    fixture = (ROOT / 'tests/fixtures/SettingsControlsTest.qml').read_text()
    fixture = fixture.replace('import "../../shell/settings" as Settings', 'import "settings" as Settings')
    (base / 'shell/controls-test.qml').write_text(fixture)
    for name in ('home', 'config', 'data', 'cache', 'state', 'runtime'):
        (base / name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/controls-test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    # The completed signal precedes QtTest's cleanupTestCase counter increment.
    assert result.returncode == 0 and 'SETTINGS_CONTROLS_RESULT 6 0' in output, output
    print('PASS: settings controls, keyboard, reset, managed and unavailable states')
