#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compact notification geometry across screen, calendar, event and media budgets."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import reaper

reaper.guard()

ROOT = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix='notification-budget-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    override = os.environ.get('EMAKI_TEST_CLOCK_SOURCE')
    if override:
        shutil.copyfile(override, base / 'shell/ClockBody.qml')
    shutil.copyfile(ROOT / 'tests/fixtures/NotificationBudgetTest.qml', base / 'shell/test.qml')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', HOME=str(base / 'home'), XDG_DATA_DIRS=str(base / 'data'),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for name in ('home', 'config', 'data', 'state', 'cache', 'runtime'):
        (base / name).mkdir(mode=0o700)
        if name != 'home':
            env['XDG_' + name.upper() + ('_DIR' if name == 'runtime' else '_HOME')] = str(base / name)
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_SCALE_FACTOR',
                 'QT_SCREEN_SCALE_FACTORS', 'QT_QPA_PLATFORMTHEME',
                 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH'):
        env.pop(name, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'NOTIFICATION_BUDGET_RESULT 16 0' in output, output
    assert not any(error in output for error in ('TypeError:', 'ReferenceError:', 'Binding loop')), output
print('PASS: maximal compact notification budget, exact footer boundary, scrolling and pointer access')
