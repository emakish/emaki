#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Calendar editor selection, custom and all-day durations, conflicts and deletion."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument('--evidence', type=Path)
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix='calendar-events-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    shutil.copyfile(ROOT / 'tests/fixtures/CalendarEventsTest.qml', base / 'shell/test.qml')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', HOME=str(base / 'home'),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for name in ('home', 'config', 'data', 'state', 'cache', 'runtime'):
        (base / name).mkdir(mode=0o700)
        if name != 'home':
            env['XDG_' + name.upper() + ('_DIR' if name == 'runtime' else '_HOME')] = str(base / name)
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
        env.pop(name, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=20)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'CALENDAR_EVENTS_RESULT 6 0' in output, output
    assert not any(error in output for error in ('TypeError:', 'ReferenceError:', 'Binding loop')), output
print('PASS: calendar selection, editing, duration, reminder, conflict and deletion')

if args.evidence:
    spec = importlib.util.spec_from_file_location('render_shell', ROOT / 'tests/render-shell.py')
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    with tempfile.TemporaryDirectory(prefix='calendar-render-') as temporary:
        home = Path(temporary) / 'home'
        home.mkdir(mode=0o700)
        render_env = {'HOME': str(home), 'EMAKI_NOTES_HELPER': str(home / 'absent-helper')}
        target = Path(temporary) / 'shell'
        shutil.copytree(ROOT / 'shell', target)
        shutil.copyfile(ROOT / 'tests/fixtures/CalendarEventsRender.qml', target / 'fixture.qml')
        for dark in (False, True):
            renderer.render(target / 'fixture.qml',
                            png=args.evidence / ('calendar-dark.png' if dark else 'calendar-light.png'),
                            properties={'dark': dark}, width=560, height=650, extra_env=render_env)
        shutil.copyfile(ROOT / 'tests/fixtures/ClockNotesRender.qml', target / 'shade.qml')
        for dark in (False, True):
            for notes in (False, True):
                name = ('shade-notes-' if notes else 'shade-calendar-') + ('dark' if dark else 'light')
                renderer.render(target / 'shade.qml', png=args.evidence / (name + '.png'),
                                properties={'dark': dark, 'notes': notes}, width=560, height=650, extra_env=render_env)
            renderer.render(target / 'shade.qml',
                            png=args.evidence / ('shade-scrolled-dark.png' if dark else 'shade-scrolled-light.png'),
                            properties={'dark': dark, 'scrolled': True}, width=560, height=650, extra_env=render_env)
    print('PASS: calendar editor and clock shade views rendered in light and dark')
