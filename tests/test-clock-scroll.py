#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check scrolling drops on the production clock panel in a private offscreen session."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix='emaki-clock-scroll-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    shutil.copyfile(ROOT / 'tests/fixtures/ClockScrollTest.qml', base / 'shell/test.qml')
    for name in ('home', 'config', 'state', 'data', 'cache', 'runtime'):
        (base / name).mkdir(mode=0o700)
    shots = Path(os.environ.get('EMAKI_CLOCK_SHOTS', str(base / 'shots')))
    shots.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, EMAKI_CLOCK_SHOTS=str(shots), HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_STATE_HOME=str(base / 'state'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_RUNTIME_DIR=str(runtime_path(base)), QT_QPA_PLATFORM='offscreen',
               QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_SCALE_FACTOR'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'CLOCK_SCROLL_RESULT 3 0' in output, output
    assert not any(error in output for error in ('TypeError:', 'ReferenceError:', 'Binding loop')), output
    for theme in ('light', 'dark'):
        for state in ('scroll', 'scrolled'):
            assert (shots / f'clock-{state}-{theme}.png').stat().st_size > 0
print('PASS: scroll and editor clear hover; today follows scroll and clips below header; light/dark captures')
