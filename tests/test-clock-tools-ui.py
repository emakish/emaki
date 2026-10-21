#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise clock controls and, with --evidence DIR, render each view in a private, silent Qt session."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
# Rendering needs the shipped fonts, which the package build machine does not have; make check runs the controls only.
parser.add_argument('--evidence', type=Path, help='write light/dark views of every tab here')
args = parser.parse_args()
SHOTS = ROOT / '.cache/clock-tools'
SHOTS.mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory(prefix='emaki-clock-tools-ui-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    subprocess.run(['python3', str(ROOT / 'scripts/render-paths'), '--source', str(base)], check=True)
    for name in ('ClockToolsTest', 'ClockToolsRender'):
        shutil.copyfile(ROOT / f'tests/fixtures/{name}.qml', base / f'shell/{name}.qml')
    for name in ('home', 'config', 'state', 'data', 'cache', 'runtime', 'bin'):
        (base / name).mkdir(mode=0o700)
    stub = base / 'bin/emaki-shell-health'
    stub.write_text('#!/bin/sh\nexit 0\n')
    stub.chmod(0o755)
    env = dict(os.environ, HOME=str(base / 'home'),
               PATH=str(base / 'bin') + os.pathsep + os.environ['PATH'],
               XDG_CONFIG_HOME=str(base / 'config'), XDG_CONFIG_DIRS=str(base / 'config'),
               XDG_STATE_HOME=str(base / 'state'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_RUNTIME_DIR=str(runtime_path(base)), QT_QPA_PLATFORM='offscreen',
               QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               EMAKI_BIN='', EMAKI_SETTINGS_PROFILE='', EMAKI_SHELL_NOTIFICATIONS='0',
               EMAKI_SHELL_TRAY='0', EMAKI_SHELL_CLIPBOARD_RECORDER='0',
               EMAKI_NOTES_HELPER=str(base / 'absent-helper'),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_SCALE_FACTOR', 'QT_QPA_PLATFORMTHEME'):
        env.pop(name, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/ClockToolsTest.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=35)
    output = result.stdout + result.stderr
    (SHOTS / 'controls.log').write_text(output)
    assert result.returncode == 0 and 'CLOCK_TOOLS_RESULT 11 0' in output, output
    assert not any(error in output for error in ('TypeError:', 'ReferenceError:', 'Binding loop')), output
    if not args.evidence:
        print('PASS: clock tool controls and keyboard access (views render with --evidence DIR)')
        raise SystemExit(0)
    args.evidence.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('clock_render', ROOT / 'tests/render-shell.py')
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    for theme in ('light', 'dark'):
        for view in ('timer', 'stopwatch', 'alarms', 'world', 'shade', 'calendar'):
            path = args.evidence / f'{view}-{theme}.png'
            output = renderer.render(base / 'shell/ClockToolsRender.qml', png=path,
                                     properties={'dark': theme == 'dark', 'view': view, 'integrated': view in ('shade', 'calendar')},
                                     width=560, height=720, warmup=350, milliseconds=1,
                                     extra_env={'HOME': str(base / 'home'), 'EMAKI_BIN': '',
                                                'EMAKI_SETTINGS_PROFILE': ''})
            (args.evidence / f'{view}-{theme}.log').write_text(output)
            assert path.stat().st_size > 0
            assert not any(error in output for error in ('TypeError:', 'ReferenceError:', 'Binding loop')), output
print('PASS: clock tool controls, keyboard access, and four views and integrated shade rendered in light/dark')
