#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise privacy state, fixed commands, revoked keys and unavailable helpers."""
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
with tempfile.TemporaryDirectory(prefix='settings-privacy-page-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    shutil.copyfile(ROOT / 'tests/fixtures/SettingsPrivacyPageTest.qml', base / 'shell/test.qml')
    definitions = base / 'shell/qmldir'
    if 'SettingsPrivacyPage 1.0 SettingsPrivacyPage.qml' not in definitions.read_text():
        with definitions.open('a') as stream:
            stream.write('\nSettingsPrivacyPage 1.0 SettingsPrivacyPage.qml\n')
    for part in ('home', 'config', 'data', 'cache', 'state', 'bin'):
        (base / part).mkdir(mode=0o700)
    helper = base / 'bin/emaki-settings-privacy'
    helper.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
base = Path(os.environ['FIXTURE_ROOT'])
with (base / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
mode = os.environ['PRIVACY_FIXTURE_MODE']
if mode == 'malformed':
    print('{broken')
    sys.exit(0)
if mode == 'error':
    print(json.dumps({'message': 'PRIVATE RAW ERROR'}))
    sys.exit(1)
revoked = sys.argv[1] == 'revoke-portal'
if revoked:
    assert sys.argv[2:] == ['screencast', 'private-token', 'org.example.App', '--json']
else:
    assert sys.argv[1:] == ['status', '--json']
print(json.dumps({'schema_version': 1, 'status': 'ready',
    'recovery': {'snapshot': {'state': 'ready', 'number': '42', 'date': '2026-10-11'},
                 'rollback': {'mode': 'normal'},
                 'menu': {'state': 'checked', 'latest_snapshot_present': True}},
    'encryption': {'state': 'encrypted', 'mounts': [{'mount': '/', 'state': 'encrypted'}]},
    'portals': {'state': 'ready', 'rows': [] if revoked and mode != 'revoke-failed' else [{'table': 'screencast',
        'id': 'private-token', 'app': 'org.example.App', 'permissions': ['yes'], 'can_revoke': True}],
        'unavailable': []},
    'lock': {'state': 'ready', 'delay': 300},
    'reports': {'saved': '/fixture/reports', 'runtime': None},
    'operation': ('failed' if mode == 'revoke-failed' else 'revoked') if revoked else None}))
''')
    helper.chmod(0o700)
    (base / 'bin/python3').symlink_to(shutil.which('python3'))
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
               PATH=str(base / 'bin'), FIXTURE_ROOT=str(base),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
        env.pop(key, None)
    for mode in ('ready', 'revoke-failed', 'malformed', 'error', 'missing'):
        if mode == 'missing':
            helper.rename(helper.with_suffix('.disabled'))
        result = subprocess.run([shutil.which('qs'), '-p', str(base / 'shell/test.qml'), '--no-color'],
                                env=dict(env, PRIVACY_FIXTURE_MODE=mode, PRIVACY_SETTINGS_SHOT=os.environ.get('PRIVACY_SETTINGS_SHOT', '') if mode == 'ready' else ''), capture_output=True,
                                text=True, timeout=30)
        output = result.stdout + result.stderr
        assert result.returncode == 0 and 'PRIVACY_PAGE_RESULT 2 0' in output, output
        for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign', 'PRIVATE RAW ERROR'):
            assert error not in output, output
        if mode == 'ready':
            calls = [json.loads(line) for line in (base / 'calls.jsonl').read_text().splitlines()]
            assert calls == [['status', '--json'],
                ['revoke-portal', 'screencast', 'private-token', 'org.example.App', '--json'],
                ['status', '--json']], calls
print('PASS: Privacy states, lock navigation, sudo revocation, portal refusal guards and command, retry and unavailable helpers')
