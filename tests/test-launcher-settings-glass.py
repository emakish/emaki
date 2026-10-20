#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The launcher settings drop keeps a crisp gear above its thin glass."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix='launcher-settings-glass-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    shutil.copyfile(ROOT / 'tests/fixtures/LauncherSettingsGlassTest.qml', base / 'shell/test.qml')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', HOME=str(base / 'home'),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for name in ('home', 'config', 'data', 'state', 'cache', 'runtime'):
        (base / name).mkdir(mode=0o700)
        if name != 'home':
            env['XDG_' + name.upper() + ('_DIR' if name == 'runtime' else '_HOME')] = str(base / name)
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
        env.pop(name, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=20)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'LAUNCHER_SETTINGS_GLASS_RESULT 2 0' in output, output
    assert not any(error in output for error in ('TypeError:', 'ReferenceError:', 'Binding loop')), output
print('PASS: launcher settings optics, on-glass gear and normal mode restoration')
