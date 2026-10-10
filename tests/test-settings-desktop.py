#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Desktop pages with real input, isolated portal responses and rejected mutations."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / '.cache/evidence/s5'
EVIDENCE.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='settings-desktop-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    for name in ('SettingsDesktopTest.qml', 'SettingsIpcHarness.qml'):
        shutil.copyfile(ROOT / 'tests/fixtures' / name, base / 'shell' / name)
    with (base / 'shell/qmldir').open('a') as stream:
        stream.write('\nSettingsIpcHarness 1.0 SettingsIpcHarness.qml\n')
    for part in ('home', 'config', 'data', 'cache', 'state'):
        (base / part).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(base / 'home'), USER='Emaki',
               XDG_CONFIG_HOME=str(base / 'config'), XDG_CONFIG_DIRS=str(base / 'config'),
               XDG_DATA_HOME=str(base / 'data'), XDG_DATA_DIRS=str(base / 'data'),
               XDG_CACHE_HOME=str(base / 'cache'), XDG_STATE_HOME=str(base / 'state'),
               XDG_RUNTIME_DIR=str(runtime_path(base)), QT_QPA_PLATFORM='offscreen',
               QT_QUICK_BACKEND='software', QT_SCALE_FACTOR='1',
               QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
               EMAKI_BIN='', EMAKI_SHELL_TRAY='0', SETTINGS_SHOT_DIR=str(EVIDENCE),
               SETTINGS_CHOSEN_IMAGE=str(base / 'chosen picture.png'),
               SETTINGS_SECOND_IMAGE=str(base / 'evening.png'),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME',
                'QT_SCREEN_SCALE_FACTORS', 'QT_LOGGING_RULES'):
        env.pop(key, None)
    from PIL import Image
    Image.new('RGB', (80, 60), '#86a7a0').save(env['SETTINGS_CHOSEN_IMAGE'])
    Image.new('RGB', (80, 60), '#735b6a').save(env['SETTINGS_SECOND_IMAGE'])
    for state in ('ready', 'cancelled', 'failed'):
        helper = base / (state + '.py')
        helper.write_text('import json, os, sys, time\n'
                          'request = json.loads(sys.stdin.readline())\n'
                          'time.sleep(.2)\n'
                          'assert request == {"op": "wallpaper-choose"}, request\n'
                          'print(json.dumps({"schema_version": 1, "state": ' + repr(state) +
                          ', "path": os.environ["SETTINGS_CHOSEN_IMAGE"]}))\n')
        env['SETTINGS_CHOOSER_' + state.upper()] = str(helper)

    def run(label, expected=True):
        shot_dir = EVIDENCE if expected else base / ('shots-' + label)
        shot_dir.mkdir(exist_ok=True)
        env['SETTINGS_SHOT_DIR'] = str(shot_dir)
        result = subprocess.run(['qs', '-p', str(base / 'shell/SettingsDesktopTest.qml'), '--no-color'],
                                env=env, capture_output=True, text=True, timeout=50)
        output = result.stdout + result.stderr
        (EVIDENCE / ('desktop-' + label + '.log')).write_text(output)
        passed = result.returncode == 0 and 'SETTINGS_DESKTOP_RESULT 7 0' in output
        if expected:
            assert passed, output
            for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
                assert error not in output, output
        else:
            assert not passed and 'SETTINGS_DESKTOP_RESULT' in output, output
        return output

    run('baseline')
    for page in ('wallpaper', 'panel', 'windows'):
        for suffix in ('', '-narrow'):
            assert (EVIDENCE / ('desktop-' + page + suffix + '.png')).stat().st_size > 1000
    print('PASS: panel/window inputs, resets, busy/rejection, wallpaper portal outcomes, search and six screenshots')
    mutations = [
        ('window-key', 'settings/SettingsRow.qml',
         'catalog.set(settingKey, String(next))',
         'catalog.set(settingKey === "windows.default_column_width" ? "appearance.gaps" : settingKey, String(next))'),
        ('wallpaper-reset', 'SettingsWallpaperPage.qml',
         'page.catalog.reset("appearance.wallpaper")',
         'page.catalog.reset("appearance.gaps")'),
        ('portal-cancel', 'SettingsWallpaperPage.qml',
         'result.state === "ready"',
         '(result.state === "ready" || result.state === "cancelled")'),
    ]
    for label, filename, before, after in mutations:
        path = base / 'shell' / filename
        original = path.read_text()
        assert original.count(before) == 1, (filename, before)
        try:
            path.write_text(original.replace(before, after))
            run(label, expected=False)
            print('PASS: rejected mutant ' + label)
        finally:
            path.write_text(original)
