#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Spatial arrow neighbours and scrolling with real offscreen key dispatch."""
from runtime_fixture import runtime_path
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
# Fail-before evidence: KEYBOARD_BASELINE=<an exported 0.8.0 source tree> runs the same walk
# with that tree's Keyboard.js. The test never reads git history or private documents.
BASELINE = os.environ.get('KEYBOARD_BASELINE')
with tempfile.TemporaryDirectory(prefix='emaki-spatial-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    fixture = (ROOT / 'tests/fixtures/SpatialKeyboardTest.qml').read_text()
    fixture = fixture.replace('import "../../shell" as Shell\n', '').replace('Shell.', '')
    (base / 'shell/spatial-test.qml').write_text(fixture)
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
    if BASELINE:
        shutil.copyfile(Path(BASELINE) / 'shell/Keyboard.js', base / 'shell/Keyboard.js')
    result = subprocess.run(['qs', '-p', str(base / 'shell/spatial-test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    # The completed signal precedes QtTest's cleanupTestCase counter increment.
    (ROOT / '.cache').mkdir(exist_ok=True)  # a fresh public checkout has none yet
    (ROOT / '.cache' / ('spatial-baseline.log' if BASELINE else 'spatial-current.log')).write_text(output)
    assert result.returncode == 0 and 'SPATIAL_KEYBOARD_RESULT 2 0' in output, output
    print('PASS: spatial neighbours, reading-order Tab, hidden/clipped filtering and focus scrolling')
