#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real scene key dispatch, ownership policy, focus shots and isolated mutants.

Offscreen focus restoration is a policy simulation, not Wayland compositor proof.
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
SHOTS = ROOT / '.cache/evidence/n6b'
SHOTS.mkdir(parents=True, exist_ok=True)


def run_case(name, mutation=None, fixture='ShellKeyboardTest.qml', marker='SHELL_KEYBOARD_RESULT 2 0'):
    with tempfile.TemporaryDirectory(prefix='emaki-keys-') as temporary:
        base = Path(temporary)
        shutil.copytree(ROOT / 'shell', base / 'shell')
        shutil.copytree(ROOT / 'niri', base / 'niri')
        shutil.copyfile(ROOT / 'tests/fixtures' / fixture, base / 'shell/test.qml')
        # Keep the real update entry path while replacing its external application.
        updates = base / 'shell/UpdateService.qml'
        updates.write_text(updates.read_text().replace(
            'command: AppLaunch.command("emaki-update-manager", ["/usr/bin/emaki-update-manager"])',
            'command: ["/usr/bin/true"]'))
        if mutation:
            path, before, after = mutation
            target = base / path
            text = target.read_text()
            assert before in text, (name, before)
            target.write_text(text.replace(before, after, 1))
        if fixture == 'SurfaceKeyboardTest.qml':
            shutil.copyfile(ROOT / 'tests/fixtures/OffscreenPanelWindow.qml', base / 'shell/OffscreenPanelWindow.qml')
            with (base / 'shell/qmldir').open('a') as definitions:
                definitions.write('\nOffscreenPanelWindow 1.0 OffscreenPanelWindow.qml\n')
            target = base / 'shell/Surfaces.qml'
            source = target.read_text().replace('PanelWindow {', 'OffscreenPanelWindow {')
            source = source.replace('WlrLayershell.keyboardFocus', 'testKeyboardFocus').replace('WlrLayershell.namespace', 'testNamespace').replace('WlrLayershell.layer', 'testLayer').replace('BackgroundEffect.blurRegion', 'testBlurRegion')
            source = re.sub(r'anchors \{\s*(?:(?:top|bottom|left|right): true\s*)+\}', '', source)
            target.write_text(source)
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
        if mutation:
            counts = re.search(re.escape(marker.split()[0]) + r' (\d+) (\d+)', output)
            assert not passed and counts and int(counts[2]) > 0, output
            print('PASS: rejected ' + name)
        else:
            assert passed, output
            print('PASS: ' + name)


run_case('surface-policy', fixture='SurfaceKeyboardTest.qml', marker='SURFACE_KEYBOARD_RESULT 2 0')
run_case('scene-keyboard')
run_case('mutant-escape', ('shell/ShellScene.qml', 'function closeAll(): void {\n        closePanels(false);',
                         'function closeAll(): void {\n        return;'))
run_case('mutant-return', ('shell/Dock.qml', 'keyboardActive = false;', 'keyboardActive = true;'))

# Missing shipped entry bindings must invalidate the same contract as production.
def entry_bindings(config):
    commands = {
        'Mod+B': '"keyboard" "open" "bar"',
        'Mod+J': '"keyboard" "open" "dock"',
        'Mod+Ctrl+S': '"system" "open" "sound"',
        'Mod+Slash': '"keyboard" "open" "shortcuts"',
    }
    return all(re.search(r'^\s*' + re.escape(key) + r'\s+[^\n{]*\{\s*spawn "emaki-shell" "call" '
                         + re.escape(command) + r';\s*\}', config, re.M)
               for key, command in commands.items())


config = (ROOT / 'niri/default.kdl').read_text()
assert entry_bindings(config), 'Missing keyboard shell entry point'
for key in ('Mod+B', 'Mod+J', 'Mod+Ctrl+S', 'Mod+Slash'):
    mutant = '\n'.join(line for line in config.splitlines() if not re.match(r'\s*' + re.escape(key) + r'\s', line))
    assert not entry_bindings(mutant), 'Removed binding escaped the contract: ' + key
    print('PASS: rejected missing real ' + key + ' binding')

# Mutate the actual layer binding before the private backend substitution.
surfaces = (ROOT / 'shell/Surfaces.qml').read_text()
for layer, identifier in [('bar', 'top'), ('dock', 'dockWindow'), ('panel', 'overlay')]:
    binding = re.search(r'WlrLayershell.keyboardFocus: [^\n]+', surfaces.split('id: ' + identifier + '\n', 1)[1])[0]
    run_case('mutant-exclusive-' + layer,
             ('shell/Surfaces.qml', binding, 'WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive'),
             fixture='SurfaceKeyboardTest.qml', marker='SURFACE_KEYBOARD_RESULT 2 0')
run_case('mutant-focus-loss', ('shell/Surfaces.qml', '                controller.closeAll();', '                return;'),
         fixture='SurfaceKeyboardTest.qml', marker='SURFACE_KEYBOARD_RESULT 2 0')
run_case('mutant-logo-order', ('shell/LeftIslands.qml', 'property bool keyboardFirst: true', 'property bool keyboardFirst: false'))
run_case('mutant-bar-ring', ('shell/KeyboardTarget.qml', 'FocusRing {}', 'FocusRing { shown: false }'))
run_case('mutant-dock-ring', ('shell/Dock.qml', 'shown: dock.keyboardActive && parent.width > 0', 'shown: false'))

run_case('dock-clock-keyboard', fixture='DockClockKeyboardTest.qml', marker='DOCK_CLOCK_KEYBOARD_PASS')
