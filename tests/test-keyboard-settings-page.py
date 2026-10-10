#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise keyboard controls with an isolated settings catalog and no device access."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from runtime_fixture import runtime_path
import reaper

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / '.cache/evidence/s4'
MUTANTS = {
    'native-layout-sequence': ('readonly property var layouts: layoutList(layoutsRow?.value)',
                               'readonly property var layouts: Array.isArray(layoutsRow?.value) ? layoutsRow.value : []'),
    'recorder-cancel-focus': ('focusPolicy: Qt.TabFocus', 'focusPolicy: Qt.StrongFocus'),
    'layout-guard': ('if (layoutsEditable && next.length > 0 && next.length <= 4)', 'if (true)'),
    'layout-order': ('[next[index], next[destination]] = [next[destination], next[index]];',
                     '[next[index], next[destination]] = [next[index], next[destination]];'),
    'shortcut-submit': ('shortcut.requestValue(text.trim());', 'console.log("submission omitted");'),
}


def run(name, mutation=None):
    with tempfile.TemporaryDirectory(prefix='keyboard-settings-') as temporary:
        base = Path(temporary)
        shutil.copytree(ROOT / 'shell', base / 'shell')
        shutil.copyfile(ROOT / 'tests/fixtures/KeyboardSettingsPageTest.qml', base / 'shell/test.qml')
        definitions = base / 'shell/qmldir'
        if 'KeyboardSettingsPage 1.0 KeyboardSettingsPage.qml' not in definitions.read_text():
            with definitions.open('a') as stream:
                stream.write('\nKeyboardSettingsPage 1.0 KeyboardSettingsPage.qml\n')
        if mutation:
            page = base / 'shell/KeyboardSettingsPage.qml'
            source = page.read_text()
            assert source.count(mutation[0]) == 1, f'{name}: mutation anchor changed'
            page.write_text(source.replace(*mutation))
        for part in ('home', 'config', 'data', 'cache', 'state'):
            (base / part).mkdir(mode=0o700)
        env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
                   XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
                   XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
                   XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
                   EMAKI_BIN='', EMAKI_SHELL_TRAY='0',
                   KEYBOARD_SETTINGS_SHOT=str(EVIDENCE / 'keyboard-settings.png'),
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
            env.pop(key, None)
        result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                                env=env, capture_output=True, text=True, timeout=40)
        output = result.stdout + result.stderr
        (EVIDENCE / f'keyboard-settings-{name}.log').write_text(output)
        marker = re.search(r'KEYBOARD_SETTINGS_RESULT (\d+) (\d+)', output)
        assert marker, output[-6000:]
        for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
            assert error not in output, output[-6000:]
        if mutation:
            assert int(marker[2]) > 0, f'{name}: mutation survived'
        else:
            assert result.returncode == 0 and int(marker[2]) == 0, output[-6000:]
        print(f'PASS: keyboard settings {name}')


if __name__ == '__main__':
    reaper.guard()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mutants', action='store_true')
    args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    run('baseline')
    if args.mutants:
        for name, mutation in MUTANTS.items():
            run(name, mutation)
