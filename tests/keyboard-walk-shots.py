#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render a keyboard-only walk through the real launcher and two panels."""
from runtime_fixture import runtime_path
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
# Fail-before evidence, opt-in: KEYBOARD_WALK_BASELINE=<an exported older source tree> replays the
# old captioned clock step with that tree's shell. Never git history.
BASELINE = os.environ.get('KEYBOARD_WALK_BASELINE')
SHOTS = ROOT / '.cache/n81k-evidence' / ('keyboard-walk-before' if BASELINE else 'keyboard-walk')
SHOTS.mkdir(parents=True, exist_ok=True)
for obsolete in ('10-clock-right.png', '11-clock-tab.png'):
    (SHOTS / obsolete).unlink(missing_ok=True)
with tempfile.TemporaryDirectory(prefix='emaki-launcher-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    shutil.copyfile(ROOT / 'tests/fixtures/SystemMockup.qml', base / 'shell/SystemMockup.qml')
    with (base / 'shell/qmldir').open('a') as definitions:
        definitions.write('\nSystemMockup 1.0 SystemMockup.qml\n')
    fixture = (ROOT / 'tests/fixtures/KeyboardWalkShots.qml').read_text()
    if BASELINE:
        # Replay the old captioned clock step against the same focus assertion.
        fixture = fixture.replace(
            'keyClick(Qt.Key_Down);\n                const firstDay',
            'keyClick(Qt.Key_Right);\n                const firstDay')
        for path in (base / 'shell').rglob('*'):
            original = Path(BASELINE) / path.relative_to(base)
            if path.is_file() and original.is_file():
                shutil.copyfile(original, path)

    shutil.copyfile(ROOT / 'tests/fixtures/SystemMockup.qml', base / 'shell/SystemMockup.qml')
    with (base / 'shell/qmldir').open('a') as definitions:
        definitions.write('\nSystemMockup 1.0 SystemMockup.qml\n')
    fixture = fixture.replace('import "../../shell" as Shell\n', '').replace('Shell.', '')
    (base / 'shell/launcher-test.qml').write_text(fixture)
    for name in ('home', 'config', 'data', 'cache', 'state', 'runtime'):
        (base / name).mkdir(mode=0o700)
    (base / 'data/applications').mkdir()
    (base / 'data/applications/fixture-app.desktop').write_text(
        '[Desktop Entry]\nType=Application\nName=Keyboard fixture\nExec=true\nIcon=utilities-terminal\n')
    (base / 'state/emaki').mkdir()
    (base / 'state/emaki/recent.json').write_text(
        '{"version":1,"entries":[{"kind":"app","ref":"fixture-app","time":1}]}')
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1', KEYBOARD_SHOTS=str(SHOTS),
               EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_TRAY='0',
               EMAKI_SHELL_CLIPBOARD_RECORDER='0',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/launcher-test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    # The completed signal precedes QtTest's cleanupTestCase counter increment.
    (SHOTS / 'walk.log').write_text(output)
    assert result.returncode == 0 and 'KEYBOARD_WALK_RESULT 2 0' in output, output
    print('PASS: keyboard-only launcher, sound and clock frames in ' + str(SHOTS))
