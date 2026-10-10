#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise session settings adapters without touching session services or hardware."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / '.cache/evidence/p2a'
EVIDENCE.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='system-settings-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    shutil.copyfile(ROOT / 'tests/fixtures/SystemSettingsPageTest.qml', base / 'shell/test.qml')
    definitions = base / 'shell/qmldir'
    if 'SystemSettingsPage 1.0 SystemSettingsPage.qml' not in definitions.read_text():
        with definitions.open('a') as stream:
            stream.write('\nSystemSettingsPage 1.0 SystemSettingsPage.qml\n')
    for part in ('home', 'config', 'data', 'cache', 'state'):
        (base / part).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
               EMAKI_BIN='', EMAKI_SHELL_TRAY='0',
               SYSTEM_SETTINGS_SHOT=str(EVIDENCE / 'system-settings.png'),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=40)
    output = result.stdout + result.stderr
    (EVIDENCE / 'system-settings.log').write_text(output)
    assert result.returncode == 0 and 'SYSTEM_SETTINGS_RESULT 2 0' in output, output
    for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
        assert error not in output, output
print('PASS: settings session controls, unavailable/pending guards, live values and no probes')
