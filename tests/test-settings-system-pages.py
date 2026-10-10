#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Open every system settings page through the launcher with private providers."""
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
OUTPUT = ROOT / '.cache/evidence/s6'
OUTPUT.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='settings-system-pages-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    shutil.copyfile(ROOT / 'tests/fixtures/SettingsIpcHarness.qml', base / 'shell/SettingsIpcHarness.qml')
    with (base / 'shell/qmldir').open('a') as output:
        output.write('\nSettingsIpcHarness 1.0 SettingsIpcHarness.qml\n')
    for name in ('home', 'config', 'data', 'cache', 'state', 'bin'):
        (base / name).mkdir(mode=0o700)
    replies = {
        'emaki-settings-power': {'schema_version': 1, 'state': 'ready',
            'values': {'blank_battery': 330, 'blank_ac': 600, 'lock_delay': 300, 'lid': 'system'},
            'defaults': {}, 'batteries': [{'name': 'BAT0', 'percent': 76, 'charging': True,
                                          'health': 94, 'charge_limit': 80}]},
        'emaki-settings-apps': {'schema_version': 1, 'ok': True, 'applications': [], 'startup': []},
        'emaki-settings-about': {'schema_version': 1, 'status': 'ready', 'details': {
            'version': '0.5.0', 'channel': 'testing', 'computer': 'Portable computer',
            'processor': 'Example processor', 'memory': '16 GiB', 'graphics': 'Integrated graphics',
            'disk': '512 GiB total · 240 GiB free', 'firmware': 'Firmware 1.0',
            'kernel': '6.18', 'window_manager': 'niri 26.04'}},
        'emaki-update-channel': {'schema_version': 1, 'status': 'read', 'channel': 'stable', 'editable': True},
        'emaki-machine-settings': {'schema_version': 1, 'status': 'read', 'settings': [
            {'key': key, 'value': value, 'editable': True, 'source': 'machine'} for key, value in
            [('locale.language', 'en_US.UTF-8'), ('locale.formats', ''), ('time.zone', 'UTC'), ('time.automatic', True)]]},
    }
    for name, reply in replies.items():
        path = base / 'bin' / name
        path.write_text('#!/usr/bin/env python3\nprint(' + repr(json.dumps(reply)) + ')\n')
        path.chmod(0o700)
    (base / 'shell/Test.qml').write_text('''// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell
SettingsIpcHarness {
 id: root
 property var pages: ["battery", "apps", "region", "about", "updates", "lock"]
 property int step: 0
 Component.onCompleted: Qt.callLater(() => controller.open(pages[0]))
 Timer {
  interval: 500; running: true; repeat: true
  onTriggered: {
   if (root.step >= root.pages.length) { console.log("SYSTEM_PAGES_PASS"); Qt.quit(); return; }
   const page = root.pages[root.step];
   if (root.controller.view.page !== page) { console.error("Wrong page"); Qt.exit(1); return; }
   root.controller.view.grabToImage(function (image) {
    if (!image.saveToFile(Quickshell.env("SYSTEM_SHOT_DIR") + "/" + page + ".png")) { Qt.exit(1); return; }
    root.step++;
    if (root.step < root.pages.length) root.controller.open(root.pages[root.step]);
   });
  }
 }
}''')
    env = dict(os.environ, HOME=str(base / 'home'), USER='Emaki', XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_SCALE_FACTOR='1',
               QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
               PATH=str(base / 'bin') + os.pathsep + os.environ['PATH'], SYSTEM_SHOT_DIR=str(OUTPUT),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/Test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=15)
    log = result.stdout + result.stderr
    (OUTPUT / 'system-pages.log').write_text(log)
    assert result.returncode == 0 and 'SYSTEM_PAGES_PASS' in log, log
    for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
        assert error not in log, log
    for page in ('battery', 'apps', 'region', 'about', 'updates', 'lock'):
        assert (OUTPUT / (page + '.png')).stat().st_size > 1000
print('PASS: all six system pages open in the launcher and render with fixture providers')
