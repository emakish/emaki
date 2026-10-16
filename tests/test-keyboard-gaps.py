#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keyboard gaps left by the 0.8.1 live check, each with an isolated mutant.

KeyboardGapsTest.qml: the snapshot prompt's two actions (arrows, Tab, Enter, Escape, hover-look
and strong rims), the sudo card's Tab order to Cancel and Allow and the sudo key warning's
buttons, and the focus ring's pixels over light and dark backdrops (the shell has no dark plate:
the plate is translucent, so a dark wallpaper is what darkens it).
KeyboardGapsSceneTest.qml: pointer use of the bar, its panels and the dock in the real scene
shows no focus ring, and Escape closes a pointer-opened panel without the bar taking the keyboard.

Offscreen focus is a policy simulation, not Wayland compositor proof.
"""
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
LOGS = ROOT / '.cache/evidence/keyboard-gaps'
LOGS.mkdir(parents=True, exist_ok=True)


def run_case(name, fixture, marker, mutation=None):
    with tempfile.TemporaryDirectory(prefix='emaki-gaps-') as temporary:
        base = Path(temporary)
        shutil.copytree(ROOT / 'shell', base / 'shell')
        shutil.copytree(ROOT / 'niri', base / 'niri')
        shutil.copyfile(ROOT / 'tests/fixtures' / fixture, base / 'shell/test.qml')
        # Keep the real update entry path while replacing its external application.
        updates = base / 'shell/UpdateService.qml'
        launch_command = 'command: AppLaunch.command("emaki-update-manager", ["emaki-update-manager", "--updates"])'
        assert updates.read_text().count(launch_command) == 1, 'Update launch fixture must match production'
        updates.write_text(updates.read_text().replace(launch_command, 'command: ["true"]'))
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
            '{"version":1,"on":true,"auto_hide":false,"pinned":["fixture-app"]}')
        env = dict(os.environ, PATH=str(base / 'bin') + os.pathsep + os.environ['PATH'],
                   HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
                   XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
                   XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
                   XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1',
                   EMAKI_BIN='', EMAKI_SETTINGS_PROFILE='', EMAKI_SHELL_NOTIFICATIONS='0',
                   EMAKI_SHELL_TRAY='0', EMAKI_SHELL_CLIPBOARD_RECORDER='0',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH'):
            env.pop(key, None)
        result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                                env=env, capture_output=True, text=True, timeout=60)
        output = result.stdout + result.stderr
        (LOGS / (name + '.log')).write_text(output)
        counts = re.search(re.escape(marker) + r' (\d+) (\d+)', output)
        passed = result.returncode == 0 and counts is not None and int(counts[1]) > 0 and int(counts[2]) == 0
        if mutation:
            assert not passed and counts and int(counts[2]) > 0, output
            failures = sorted(set(re.findall(r'KEYBOARD_GAPS_FAIL (.+?) focus=', output)))
            print('PASS: rejected ' + name + (' (' + '; '.join(failures[:3]) + ')' if failures else ''))
        else:
            assert passed, output
            assert 'TypeError' not in output and 'ReferenceError' not in output, output
            for line in re.findall(r'KEYBOARD_GAPS_CONTRAST .+', output):
                print('  ' + line)
            print('PASS: ' + name + ' (' + counts[1] + ' QML tests)')


GAPS = ('KeyboardGapsTest.qml', 'KEYBOARD_GAPS_RESULT')
SCENE = ('KeyboardGapsSceneTest.qml', 'KEYBOARD_GAPS_SCENE_RESULT')

run_case('snapshot, sudo and palette keyboard gaps', *GAPS)
# Item 9: Right must reach Keep this state, its rim must be the strong one.
run_case('mutant-snapshot-right', *GAPS, mutation=(
    'shell/SnapshotPrompt.qml',
    'Keys.onRightPressed: (keep.visible && keep.enabled ? keep : later).forceActiveFocus()',
    'Keys.onRightPressed: later.forceActiveFocus()'))
run_case('mutant-snapshot-pale-keep', *GAPS, mutation=(
    'shell/SnapshotPrompt.qml', 'strong: action.accent', 'strong: false'))
# The dark Keep this state: one dark ink cannot face both the dark plate and the bright accent.
run_case('mutant-keep-one-ink-rim', *GAPS, mutation=(
    'shell/SnapshotPrompt.qml', 'outerInk: LiquidPalette.focusOuterOnAccent', 'outerInk: "transparent"'))
# Item 18: Return on a SettingsButton (Cancel, the warning's buttons) must activate it.
run_case('mutant-sudo-return', *GAPS, mutation=(
    'shell/settings/SettingsButton.qml',
    'Keys.onReturnPressed: event => {\n        if (!event.isAutoRepeat)\n            root.clicked();',
    'Keys.onReturnPressed: event => {\n        if (false)\n            root.clicked();'))
# Item 24: a ring drawn without ink is not seen on either backdrop.
run_case('mutant-invisible-ring', *GAPS, mutation=(
    'shell/FocusRing.qml',
    'color: LiquidPalette.flatDrop\n    border.color: strong ? strongInk : LiquidPalette.flatDropRim',
    'color: "transparent"\n    border.color: strong ? strongInk : "transparent"'))
# Item 24: a rim in light ink, as a dark plate would want, is lost on the light plate. The
# rim comes from ShellPalette (tokens.toml) since the themes round; LiquidPalette passes it on.
run_case('mutant-light-ink-rim', *GAPS, mutation=(
    'shell/LiquidPalette.qml',
    'flatDropRim: ShellPalette.flatDropRim',
    'flatDropRim: Qt.rgba(1, 1, 1, .16)'))
# Dark shades the selected drop to a third of the accent: the warm dark ink is lost there.
run_case('mutant-dark-ink-on-dark-selected-drop', *GAPS, mutation=(
    'shell/LiquidPalette.qml',
    'focusOnSelected: ShellPalette.dark ? Qt.rgba(flatDropRim.r, flatDropRim.g, flatDropRim.b, focusOnAccent.a) : focusOnAccent',
    'focusOnSelected: focusOnAccent'))

run_case('pointer pass on bar, panels and dock', *SCENE)
# Item 23: pointer focus must not draw the keyboard ring, Escape must not hand the bar the keyboard.
run_case('mutant-ring-ignores-pointer', *SCENE, mutation=(
    'shell/FocusRing.qml', 'visible: shown && keyboardMode', 'visible: shown'))
run_case('mutant-pointer-escape-grabs-bar', *SCENE, mutation=(
    'shell/ShellScene.qml',
    'if (!target || !enabled || !focusedOutput',
    'if (!enabled || !focusedOutput'))
run_case('mutant-pointer-opens-keyboard-panel', *SCENE, mutation=(
    'shell/ShellScene.qml',
    'scene.openSystem(page, scene.barKeyboardActive);',
    'scene.openSystem(page, true);'))
