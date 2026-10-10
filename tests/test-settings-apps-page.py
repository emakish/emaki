#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise Apps choices, reset, pending guards and backend failure feedback."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='settings-apps-page-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    shutil.copyfile(ROOT / 'tests/fixtures/SettingsAppsPageTest.qml', base / 'shell/test.qml')
    definitions = base / 'shell/qmldir'
    if 'SettingsAppsPage 1.0 SettingsAppsPage.qml' not in definitions.read_text():
        with definitions.open('a') as stream:
            stream.write('\nSettingsAppsPage 1.0 SettingsAppsPage.qml\n')
    for part in ('home', 'config', 'data', 'cache', 'state', 'bin'):
        (base / part).mkdir(mode=0o700)
    helper = base / 'bin/emaki-settings-apps'
    helper.write_text('''#!/usr/bin/env python3
import json, sys
if sys.argv[1] != 'status':
    print(json.dumps({'ok': False, 'error': 'Fixture refused the change.'}))
    sys.exit(1)
print(json.dumps({'ok': True, 'applications': [{'id': 'editor.desktop', 'name': 'Editor', 'roles': ['editor']}], 'defaults': {'editor': {'id': 'editor.desktop', 'name': 'Editor'}}, 'startup': [{'id': 'fixture.desktop', 'name': 'Fixture', 'enabled': True}]}))
''')
    helper.chmod(0o700)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
               PATH=str(base / 'bin') + os.pathsep + os.environ['PATH'],
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'APPS_PAGE_RESULT 2 0' in output, output
    for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
        assert error not in output, output
print('PASS: Apps choices, reset, busy guard and visible backend failures')
