#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise Sound settings using private, in-memory devices and settings."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / '.cache/evidence/s3'
EVIDENCE.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='sound-page-', dir='/tmp') as temporary:
    base = Path(temporary)
    shell = base / 'shell'
    shell.mkdir()
    names = ('SoundSettingsPage', 'SoundSettingsBackend')
    for name in names:
        shutil.copyfile(ROOT / f'shell/{name}.qml', shell / f'{name}.qml')
    (shell / 'qmldir').write_text(''.join(f'{name} 1.0 {name}.qml\n' for name in names))
    shutil.copyfile(ROOT / 'shell/ShellPalette.qml', shell / 'ShellPalette.qml')
    with (shell / 'qmldir').open('a') as stream:
        stream.write('singleton ShellPalette 1.0 ShellPalette.qml\n')
    shutil.copytree(ROOT / 'shell/settings', shell / 'settings')
    shutil.copyfile(ROOT / 'tests/fixtures/SoundSettingsPageTest.qml', shell / 'test.qml')
    for part in ('home', 'config', 'data', 'cache', 'state'):
        (base / part).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
               SOUND_SETTINGS_SHOT=str(EVIDENCE / 'sound.png'),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(shell / 'test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=40)
    output = result.stdout + result.stderr
    (EVIDENCE / 'sound-page.log').write_text(output)
    assert result.returncode == 0 and 'SOUND_PAGE_RESULT 2 0' in output, output
    for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
        assert error not in output, output
print('PASS: Sound page devices, levels, mute, streams, errors and meter lifetime')
