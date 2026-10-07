#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Production battery warnings and drawer layout in an isolated offscreen scene."""
from runtime_fixture import runtime_path
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / '.cache/evidence'
EVIDENCE.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='battery-', dir=EVIDENCE) as folder:
    profile = Path(folder)
    for name in ('home', 'config', 'state', 'data', 'cache', 'runtime', 'tmp'):
        (profile / name).mkdir(mode=0o700)
    shutil.copytree(ROOT / 'shell', profile / 'qml')
    shutil.copyfile(ROOT / 'tests/fixtures/BatteryTest.qml', profile / 'qml/shell.qml')
    env = dict(os.environ, HOME=str(profile / 'home'),
               XDG_CONFIG_HOME=str(profile / 'config'), XDG_STATE_HOME=str(profile / 'state'),
               XDG_DATA_HOME=str(profile / 'data'), XDG_DATA_DIRS=str(profile / 'data'),
               XDG_CACHE_HOME=str(profile / 'cache'), XDG_RUNTIME_DIR=str(runtime_path(profile)),
               TMPDIR=str(profile / 'tmp'), QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_SCALE_FACTOR='1', QML_DISABLE_DISK_CACHE='1', PYTHONDONTWRITEBYTECODE='1',
               EMAKI_BIN='/usr/bin/true', EMAKI_SETTINGS_PROFILE='', EMAKI_SHELL_NOTIFICATIONS='0',
               EMAKI_SHELL_TRAY='0', EMAKI_TEST_MPRIS='0',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'))
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_SCREEN_SCALE_FACTORS',
                 'QT_LOGGING_RULES', 'QT_QPA_PLATFORMTHEME'):
        env.pop(name, None)
    process = subprocess.Popen(['qs', '-p', str(profile / 'qml'), '--no-color'], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=20)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
    (EVIDENCE / 'battery-layout.log').write_text(output)
    assert process.returncode == 0, output
    assert 'BATTERY LAYOUT PASS' in output and 'BATTERY LAYOUT FAIL' not in output, output
    assert 'Failed to start IPC server' not in output, output
    for line in output.splitlines():
        assert ' ERROR' not in line and 'Binding loop' not in line and 'TypeError' not in line, output
print('Battery layout: discharge/recharge, DND/fullscreen/overview, modal panels and drawer restoration: PASS')
