#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise network pages and process failures without accessing session services."""
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile

from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / '.cache/evidence/s1'
EVIDENCE.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='network-settings-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    shutil.copyfile(ROOT / 'tests/fixtures/SettingsNetworkPageTest.qml', base / 'shell/test.qml')
    definitions = base / 'shell/qmldir'
    if 'SettingsNetworkPage 1.0 SettingsNetworkPage.qml' not in definitions.read_text():
        with definitions.open('a') as stream:
            stream.write('\nSettingsNetworkPage 1.0 SettingsNetworkPage.qml\n')
    for part in ('home', 'config', 'data', 'cache', 'state'):
        (base / part).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
               EMAKI_BIN='', EMAKI_SHELL_TRAY='0',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
        env.pop(key, None)
    commands = base / 'bin'
    commands.mkdir()
    helper = commands / 'emaki-settings-network'
    env['PATH'] = str(commands) + os.pathsep + env['PATH']
    helper_source = '''#!/usr/bin/env python3
import json
import pathlib
import sys
if sys.argv[1:] == ["status", "bad-version"]:
    print(json.dumps({"ok": True, "schema_version": 2, "connections": [], "wired": [], "wifi_profiles": []}))
elif sys.argv[1:] == ["status", "bad-shape"]:
    print(json.dumps({"ok": True, "schema_version": 1, "connections": {}, "wired": [], "wifi_profiles": []}))
elif sys.argv[1:] == ["status", "bad-entry"]:
    print(json.dumps({"ok": True, "schema_version": 1, "connections": [None], "wired": [], "wifi_profiles": []}))
elif sys.argv[1] == "status":
    print(json.dumps({"ok": True, "schema_version": 1, "connections": [{"uuid": "fixture-uuid", "name": "Fixture", "dns": [], "dnsAutomatic": True, "proxyMode": "none", "proxyValue": ""}], "wired": [], "wifi_profiles": []}))
elif sys.argv[1] == "failure":
    print(json.dumps({"ok": False, "schema_version": 1, "error": "Fixture permission denied."}))
    sys.exit(1)
elif sys.argv[1] == "malformed":
    print("broken reply")
else:
    if sys.argv[1] == "remove-self":
        pathlib.Path(__file__).unlink()
    print(json.dumps({"ok": True, "schema_version": 1, "message": "Fixture imported."}))
'''
    def run(label, should_pass):
        env['NETWORK_SETTINGS_SHOT'] = str(EVIDENCE / 'network-page') if label == 'normal' else ''
        helper.write_text(helper_source)
        helper.chmod(0o755)
        result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                                env=env, capture_output=True, text=True, timeout=40)
        output = result.stdout + result.stderr
        (EVIDENCE / ('network-settings-' + label + '.log')).write_text(output)
        passed = result.returncode == 0 and 'NETWORK_SETTINGS_RESULT 3 0' in output
        assert passed == should_pass, output
        for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
            assert error not in output, output
        if not should_pass:
            marker = re.search(r'NETWORK_SETTINGS_RESULT \d+ (\d+)', output)
            assert marker and int(marker.group(1)) > 0, output

    run('normal', True)
    page_file = base / 'shell/SettingsNetworkPage.qml'
    original = page_file.read_text()
    mutants = [
        ('dns-uuid', '["dns", page.connectionId, text.trim() || "reset"]', '["dns", page.connection.name, text.trim() || "reset"]'),
    ]
    for label, before, after in mutants:
        assert original.count(before) == 1, before
        page_file.write_text(original.replace(before, after))
        run(label, False)
    page_file.write_text(original)
    service_file = base / 'shell/SettingsNetworkService.qml'
    service_file.write_text(service_file.read_text().replace('root.arguments = args;', 'arguments = args;'))
    run('process-arguments', False)
print('PASS: network DNS, UUID routing and errors, VPN file URLs, process failures; 2 mutants rejected')
