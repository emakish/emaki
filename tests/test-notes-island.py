#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pinned notes visibility, shared island growth and outside-click dismissal."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / '.cache/evidence/notes'
SHOTS.mkdir(parents=True, exist_ok=True)
for theme in ('light', 'dark'):
    with tempfile.TemporaryDirectory(prefix='emaki-notes-island-') as temporary:
        base = Path(temporary)
        shutil.copytree(ROOT / 'shell', base / 'shell')
        shutil.copyfile(ROOT / 'tests/fixtures/NotesIslandTest.qml', base / 'shell/test.qml')
        palette = base / 'shell/Appearance.qml'
        source = palette.read_text()
        source = source.replace('readonly property bool dark: Logic.isDark(theme, lightTime, darkTime, now)', f'readonly property bool dark: {str(theme == "dark").lower()}')
        palette.write_text(source)
        for name in ('home', 'config', 'data', 'state', 'cache', 'bin'):
            (base / name).mkdir(mode=0o700)
        for command in ('emaki-shell-health',):
            stub = base / 'bin' / command
            stub.write_text('#!/bin/sh\nexit 0\n')
            stub.chmod(0o755)
        env = dict(os.environ, PATH=str(base / 'bin') + os.pathsep + os.environ['PATH'], HOME=str(base / 'home'),
                   XDG_CONFIG_HOME=str(base / 'config'), XDG_CONFIG_DIRS=str(base / 'config'),
                   XDG_DATA_HOME=str(base / 'data'), XDG_DATA_DIRS=str(base / 'data'), XDG_STATE_HOME=str(base / 'state'),
                   XDG_CACHE_HOME=str(base / 'cache'), XDG_RUNTIME_DIR=str(runtime_path(base)),
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                   EMAKI_BIN='', EMAKI_SETTINGS_PROFILE='', EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_TRAY='0',
                   EMAKI_SHELL_CLIPBOARD_RECORDER='0', EMAKI_NOTES_HELPER=str(base / 'absent-helper'),
                   NOTES_SHOT=str(SHOTS / ('island-' + theme + '.png')),
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
            env.pop(name, None)
        result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'], env=env,
                                capture_output=True, text=True, timeout=35)
        output = result.stdout + result.stderr
        (SHOTS / ('island-' + theme + '.log')).write_text(output)
        assert result.returncode == 0 and 'NOTES_ISLAND_RESULT 2 0' in output, output
        assert not any(error in output for error in ('TypeError:', 'ReferenceError:', 'Binding loop')), output
print('PASS: pinned island presence, growth, in-place snapshots, outside dismissal, last unpin and keyboard return, restart backoff; light/dark screenshots')
