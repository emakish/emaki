#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise display controls with isolated output and Night Light fixtures."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / '.cache/evidence/s2'
EVIDENCE.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='displays-page-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    subprocess.run(['python3', str(ROOT / 'scripts/render-paths'), '--source', str(base)], check=True)
    shutil.copyfile(ROOT / 'tests/fixtures/DisplaysPageTest.qml', base / 'shell/test.qml')
    shutil.copyfile(ROOT / 'tests/fixtures/displays-page-helper.py', base / 'shell/helpers/displays.py')
    for part in ('home', 'config', 'data', 'cache', 'state'):
        (base / part).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
               EMAKI_BIN='', EMAKI_SHELL_TRAY='0',
               DISPLAYS_HELPER=str(ROOT / 'tests/fixtures/displays-page-helper.py'),
               DISPLAYS_SHOT=str(EVIDENCE / 'displays-page.png'),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=40)
    output = result.stdout + result.stderr
    (EVIDENCE / 'displays-page.log').write_text(output)
    assert result.returncode == 0 and 'DISPLAYS_PAGE_RESULT 4 0' in output, output
    for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
        assert error not in output, output
    mutants = [
        ('DisplaysSettingsPage.qml', 'displays.busy || displays.pending || !displays.ready',
         'displays.busy || !displays.ready', 'pending-guard'),
        ('DisplaysService.qml', 'busy || !worker.running || closing',
         '!worker.running || closing', 'command-serialization'),
        ('DisplaysSettingsPage.qml', 'wireMode(next)',
         'wireMode(modes[0])', 'resolution-mode'),
        ('DisplaysSettingsPage.qml', 'activeOutputs.length === 1 ? activeOutputs[0].name : displays.main',
         'displays.main', 'implicit-main'),
        ('DisplaysSettingsPage.qml', 'root.resolutionLabel(monitor.modelData)',
         'monitor.modelData.logical.width + " × " + monitor.modelData.logical.height', 'preview-resolution'),
    ]
    for filename, original, replacement, label in mutants:
        path = base / 'shell' / filename
        source = path.read_text()
        assert original in source
        path.write_text(source.replace(original, replacement))
        result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                                env=env, capture_output=True, text=True, timeout=40)
        output = result.stdout + result.stderr
        (EVIDENCE / ('displays-page-mutant-' + label + '.log')).write_text(output)
        assert 'DISPLAYS_PAGE_RESULT 4 0' not in output, label
        assert 'MISMATCH' in output or 'CHECK FAILED' in output, output
        path.write_text(source)
print('PASS: display modes, implicit main, preview resolution, resets, movement, pending guard, unavailable outputs and schedule controls; five mutants rejected')
