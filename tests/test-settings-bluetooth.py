#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise Bluetooth settings using an isolated service and device fixtures."""
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile

from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / '.cache/evidence/s1'
EVIDENCE.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='bluetooth-settings-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    shutil.copyfile(ROOT / 'tests/fixtures/SettingsBluetoothPageTest.qml', base / 'shell/test.qml')
    definitions = base / 'shell/qmldir'
    if 'SettingsBluetoothPage 1.0 SettingsBluetoothPage.qml' not in definitions.read_text():
        with definitions.open('a') as stream:
            stream.write('\nSettingsBluetoothPage 1.0 SettingsBluetoothPage.qml\n')
    for part in ('home', 'config', 'data', 'cache', 'state'):
        (base / part).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
               EMAKI_BIN='', EMAKI_SHELL_TRAY='0',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
        env.pop(key, None)
    def run(label, should_pass):
        result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                                env=env, capture_output=True, text=True, timeout=40)
        output = result.stdout + result.stderr
        (EVIDENCE / ('bluetooth-settings-' + label + '.log')).write_text(output)
        passed = result.returncode == 0 and 'BLUETOOTH_SETTINGS_RESULT 2 0' in output
        assert passed == should_pass, output
        for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
            assert error not in output, output
        if not should_pass:
            marker = re.search(r'BLUETOOTH_SETTINGS_RESULT \d+ (\d+)', output)
            assert marker and int(marker.group(1)) > 0, output

    run('normal', True)
    page_file = base / 'shell/SettingsBluetoothPage.qml'
    original = page_file.read_text()
    mutants = [
        ('pair-routing', '"bt-cancel-pair" : "bt-pair"', '"bt-cancel-pair" : "bt-connect"'),
        ('empty-battery', 'device.battery >= 0', 'device.battery > 0'),
        ('busy-guard', 'pending && kind !== "bt-cancel-pair"', 'false && kind !== "bt-cancel-pair"'),
    ]
    for label, before, after in mutants:
        assert original.count(before) == 1, before
        page_file.write_text(original.replace(before, after))
        run(label, False)
print('PASS: Bluetooth actions, pairing cancellation, battery, state guards and failures; 3 mutants rejected')
