#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise notification settings with an isolated catalog and server."""
from runtime_fixture import runtime_path
import os
import argparse
from pathlib import Path
import subprocess
import shutil
import tempfile
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser()
parser.add_argument('--screenshot', action='store_true')
args = parser.parse_args()
screenshot = ROOT / '.cache/evidence/s3/notifications.png'
if args.screenshot:
    screenshot.parent.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='emaki-notification-settings-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell/settings', base / 'shell/settings')
    shutil.copyfile(ROOT / 'shell/ShellPalette.qml', base / 'shell/ShellPalette.qml')
    (base / 'shell/qmldir').write_text('singleton ShellPalette 1.0 ShellPalette.qml\nNotificationsSettingsPage 1.0 NotificationsSettingsPage.qml\n')
    shutil.copyfile(ROOT / 'shell/NotificationsSettingsPage.qml', base / 'shell/NotificationsSettingsPage.qml')
    fixture = (ROOT / 'tests/fixtures/NotificationsSettingsPageTest.qml').read_text()
    (base / 'shell/controls-test.qml').write_text(fixture)
    for name in ('home', 'config', 'data', 'cache', 'state', 'runtime'):
        (base / name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
        env.pop(key, None)
    source = (ROOT / 'shell/NotificationsSettingsPage.qml').read_text()
    mutations = {
        'disabled schedule times': ('enabled: page.schedule.enabled === true', 'enabled: true'),
        'schedule validation': ('&& !/^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(next)', '&& false'),
        'timer duration': ('Number(next) * 60000', 'Number(next) * 60'),
        'rule isolation': ('Object.assign({}, rules)', '{}'),
        'rule reset': ('delete updated[id]', 'updated[id] = "off"'),
        'rejected time edit': ('text = Qt.binding(() => String(timeRow.value));', 'text = text;'),
    }
    for name, replacement in [('baseline', None), *mutations.items()]:
        changed = source
        if replacement:
            before, after = replacement
            assert before in source, name
            changed = source.replace(before, after)
        (base / 'shell/NotificationsSettingsPage.qml').write_text(changed)
        env['NOTIFICATIONS_SCREENSHOT'] = str(screenshot) if args.screenshot and name == 'baseline' else ''
        result = subprocess.run(['qs', '-p', str(base / 'shell/controls-test.qml'), '--no-color'],
                                env=env, capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        assert 'Configuration Loaded' in output, output
        # The completed signal precedes QtTest's cleanupTestCase counter increment.
        if name == 'baseline':
            assert result.returncode == 0 and 'NOTIFICATIONS_SETTINGS_RESULT 2 0' in output, output
        else:
            assert 'NOTIFICATIONS_SETTINGS_RESULT 1 1' in output, name + ': ' + output
            assert 'COMPARE' in output or 'CHECK FAILED' in output, output
print(f'PASS: notification settings, keyboard validation, timers, rule isolation/reset; {len(mutations)} mutations rejected')
