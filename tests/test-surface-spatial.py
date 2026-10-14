#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise spatial settings and dock navigation with real key events."""
from runtime_fixture import runtime_path
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import reaper
reaper.guard()

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / '.cache/evidence/n81k-surfaces'
SHOTS.mkdir(parents=True, exist_ok=True)


def run_case(name, mutation=None, fixture='SurfaceSpatialTest.qml', marker='SURFACE_SPATIAL_RESULT 8 0', baseline=False):
    with tempfile.TemporaryDirectory(prefix='emaki-keys-') as temporary:
        base = Path(temporary)
        shutil.copytree(ROOT / 'shell', base / 'shell')
        shutil.copytree(ROOT / 'niri', base / 'niri')
        shutil.copyfile(ROOT / 'tests/fixtures' / fixture, base / 'shell/test.qml')
        if baseline:
            for relative in ('Dock.qml', 'CapturePanel.qml', 'SettingsView.qml',
                             'settings/SettingsSliderRow.qml', 'settings/SettingsToggleRow.qml',
                             'settings/SettingsComboBox.qml', 'Welcome.qml', 'SudoKeyWarning.qml',
                             'ReportView.qml'):
                shutil.copyfile(Path(baseline) / 'shell' / relative, base / 'shell' / relative)
        if mutation:
            path, before, after = mutation
            target = base / path
            text = target.read_text()
            assert before in text, (name, before)
            target.write_text(text.replace(before, after, 1))
        for part in ('home', 'config', 'data', 'cache', 'state', 'runtime'):
            (base / part).mkdir(mode=0o700)
        (base / 'bin').mkdir()
        niri = base / 'bin/niri'
        niri.write_text('#!/bin/sh\n# Copyright (C) 2026 Artur Yakymenko\n'
                        '# SPDX-License-Identifier: GPL-3.0-or-later\n'
                        'printf \'%s\\n\' \'{"compositor":"26.04 (v26.04+emaki.12)"}\'\n')
        niri.chmod(0o755)
        (base / 'data/applications').mkdir()
        (base / 'data/applications/fixture-app.desktop').write_text(
            '[Desktop Entry]\nType=Application\nName=Keyboard fixture\nExec=/usr/bin/true\nIcon=utilities-terminal\n')
        (base / 'state/emaki').mkdir()
        (base / 'state/emaki/dock.json').write_text(
            '{"version":1,"on":true,"auto_hide":true,"pinned":["fixture-app"]}')
        env = dict(os.environ, PATH=str(base / 'bin') + os.pathsep + os.environ['PATH'],
                   HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
                   XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
                   XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
                   XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', KEYBOARD_SHOTS=str(SHOTS),
                   EMAKI_BIN='', EMAKI_SETTINGS_PROFILE='', EMAKI_SHELL_NOTIFICATIONS='0',
                   EMAKI_SHELL_TRAY='0', EMAKI_SHELL_CLIPBOARD_RECORDER='0',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
            env.pop(key, None)
        if mutation:
            env['KEYBOARD_SKIP_SHOTS'] = '1'
        result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                                env=env, capture_output=True, text=True, timeout=35)
        output = result.stdout + result.stderr
        (SHOTS / (name + '.log')).write_text(output)
        passed = result.returncode == 0 and marker in output
        if mutation or baseline:
            counts = re.search(re.escape(marker.split()[0]) + r' (\d+) (\d+)', output)
            assert not passed and counts and int(counts[2]) > 0, output
            print('PASS: rejected ' + name)
        else:
            assert passed, output
            print('PASS: ' + name)


run_case('spatial-surfaces', fixture='SurfaceSpatialTest.qml', marker='SURFACE_SPATIAL_RESULT 8 0')
run_case('mutant-dock-up', ('shell/Dock.qml', 'menu(keyboardKey);\n                if (popupOpen)', 'if (popupOpen)'), fixture='SurfaceSpatialTest.qml', marker='SURFACE_SPATIAL_RESULT 8 0')
run_case('mutant-slider-navigation', ('shell/settings/SettingsSliderRow.qml', 'scope.keyboardNavigate(event);', 'event.accepted = true;'), fixture='SurfaceSpatialTest.qml', marker='SURFACE_SPATIAL_RESULT 8 0')
run_case('mutant-choice-navigation', ('shell/settings/SettingsComboBox.qml', 'scope.keyboardNavigate(event);', 'event.accepted = true;'), fixture='SurfaceSpatialTest.qml', marker='SURFACE_SPATIAL_RESULT 8 0')
run_case('mutant-settings-spatial', ('shell/SettingsView.qml', '} else {\n            Keyboard.handle(root, event);', '} else {\n            return;'), fixture='SurfaceSpatialTest.qml', marker='SURFACE_SPATIAL_RESULT 8 0')
run_case('mutant-toggle-enter', ('shell/settings/SettingsToggleRow.qml', 'Keys.onReturnPressed: clicked()', 'Keys.onReturnPressed: {}'), fixture='SurfaceSpatialTest.qml', marker='SURFACE_SPATIAL_RESULT 8 0')
run_case('mutant-capture-navigation', ('shell/CapturePanel.qml', 'Keyboard.handle(panel, event);', 'event.accepted = true;'), fixture='SurfaceSpatialTest.qml', marker='SURFACE_SPATIAL_RESULT 8 0')
# Fail-before evidence, opt-in: KEYBOARD_BASELINE=<an exported 0.8.0 source tree> puts that
# release's surface files in; they must fail the same walk. Never git history.
if os.environ.get('KEYBOARD_BASELINE'):
    run_case('baseline-surfaces', baseline=os.environ['KEYBOARD_BASELINE'])
run_case('mutant-welcome-navigation', ('shell/Welcome.qml', 'Keyboard.handle(welcome, event);', 'event.accepted = true;'))
run_case('mutant-warning-navigation', ('shell/SudoKeyWarning.qml', 'Keyboard.handle(warning, event)', 'event.accepted = true'))
run_case('mutant-report-navigation', ('shell/ReportView.qml', 'Keyboard.handle(view, event);', 'event.accepted = true;'))
