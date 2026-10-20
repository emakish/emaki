#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Load the shared notes surfaces and exercise storage conflicts offscreen."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import sys

ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser()
parser.add_argument('--evidence', type=Path)
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix='emaki-notes-ui-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    shutil.copyfile(Path(__file__).with_name('NotesTest.qml'), base / 'shell/test.qml')
    module = base / 'shell/qmldir'
    text = module.read_text()
    for name in ('NotesService', 'NotesView', 'NotesButton', 'NotesChecklist', 'NotesIsland'):
        if name + ' 1.0' not in text:
            text += ('singleton ' if name == 'NotesService' else '') + name + ' 1.0 ' + name + '.qml\n'
    module.write_text(text)
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', HOME=str(base / 'home'), QS_DISABLE_CRASH_HANDLER='1',
               EMAKI_NOTES_HELPER=str(ROOT / 'notes/backend.py'),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for name in ('home', 'config', 'data', 'state', 'cache', 'runtime'):
        (base / name).mkdir(mode=0o700)
        if name != 'home': env['XDG_' + name.upper() + ('_DIR' if name == 'runtime' else '_HOME')] = str(base / name)
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'): env.pop(name, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'], env=env, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'NOTES_TEST_PASS' in output and 'NOTES_RESULT 2 0' in output, output
    assert not any(error in output for error in ('TypeError:', 'ReferenceError:', 'Binding loop')), output
    app = (ROOT / 'notes/ui/shell.qml').read_text().replace('@EMAKI_DATADIR@', str(base))
    app = app.replace('id: root', 'id: root\n    Timer { interval: 500; running: true; onTriggered: Qt.quit() }', 1)
    (base / 'app.qml').write_text(app)
    result = subprocess.run(['qs', '-p', str(base / 'app.qml'), '--no-color'], env=env, capture_output=True, text=True, timeout=20)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'Failed to load' not in output, output
    assert not any(error in output for error in ('TypeError:', 'ReferenceError:', 'Binding loop')), output
print('PASS: notes QML, create/save, live conflict and save-copy, pinned visibility and morph')

if args.evidence:
    sys.path.insert(0, str(ROOT / 'tests'))
    spec = importlib.util.spec_from_file_location('render_shell', ROOT / 'tests/render-shell.py')
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    with tempfile.TemporaryDirectory(prefix='notes-window-render-') as temporary:
        base = Path(temporary)
        home = base / 'home'
        home.mkdir(mode=0o700)
        target = base / 'shell'
        shutil.copytree(ROOT / 'shell', target)
        shutil.copyfile(ROOT / 'tests/fixtures/NotesWindowRender.qml', target / 'fixture.qml')
        for dark in (False, True):
            theme = 'dark' if dark else 'light'
            for state, width, selected, empty in (
                    ('wide', 1040, True, False),
                    ('narrow-list', 390, False, False),
                    ('narrow-note', 390, True, False),
                    ('minimum-note', 320, True, False),
                    ('empty', 1040, False, True)):
                output = renderer.render(target / 'fixture.qml',
                    png=args.evidence / (state + '-' + theme + '.png'),
                    properties={'dark': dark, 'selected': selected, 'empty': empty, 'width': width, 'height': 740},
                    width=width, height=740,
                    extra_env={'HOME': str(home), 'EMAKI_NOTES_HELPER': str(home / 'absent-helper')})
                assert not any(error in output for error in ('TypeError:', 'ReferenceError:', 'Binding loop')), output
    print('PASS: Notes wide, narrow list/editor, minimum width and empty states rendered in light and dark')
