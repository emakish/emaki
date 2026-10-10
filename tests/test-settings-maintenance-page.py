#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise channel navigation, fixed commands, reset and visible failure feedback."""
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
with tempfile.TemporaryDirectory(prefix='settings-maintenance-page-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    shutil.copyfile(ROOT / 'tests/fixtures/SettingsMaintenancePageTest.qml', base / 'shell/test.qml')
    definitions = base / 'shell/qmldir'
    for name in ('SettingsMaintenancePage', 'SettingsPowerPage'):
        if f'{name} 1.0 {name}.qml' not in definitions.read_text():
            with definitions.open('a') as stream:
                stream.write(f'\n{name} 1.0 {name}.qml\n')
    for part in ('home', 'config', 'data', 'cache', 'state', 'bin'):
        (base / part).mkdir(mode=0o700)
    # All executable entry points are stubs, including the privilege broker:
    # it records the fixed target but never executes it.
    helper_source = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
name = Path(sys.argv[0]).name
base = Path(os.environ['FIXTURE_ROOT'])
with (base / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps([name, *sys.argv[1:]]) + '\\n')
if name == 'emaki-terminal':
    assert sys.argv[1:] == ['--change-password', '--json']
    marker = base / 'password-attempts'
    attempt = int(marker.read_text()) if marker.exists() else 0
    marker.write_text(str(attempt + 1))
    outcomes = [('changed', 'Password changed.'), ('unchanged', 'Password not changed.'),
                ('unconfirmed', 'The password change could not be confirmed. Check the password window.')]
    state, message = outcomes[min(attempt, 2)]
    print(json.dumps({'schema_version': 1, 'status': state, 'message': message}))
    sys.exit(0)
if name == 'emaki-settings-power':
    print(json.dumps({'state': 'ready', 'values': {}, 'defaults': {}, 'batteries': []}))
    sys.exit(0)
if name == 'emaki-update-channel':
    assert sys.argv[1:] == ['status', '--json']
    print(json.dumps({'schema_version': 1, 'status': 'read', 'editable': True, 'channel': 'stable'}))
    sys.exit(0)
assert name == 'pkexec'
assert Path(sys.argv[1]).name == 'emaki-update-channel'
assert sys.argv[2] == 'set' and sys.argv[4:] == ['--json']
value = sys.argv[3]
marker = base / 'testing-seen'
if value == 'testing' and marker.exists():
    print(json.dumps({'schema_version': 1, 'status': 'error', 'message': 'Fixture refused the channel change.'}))
    sys.exit(1)
if value == 'testing':
    marker.touch()
print(json.dumps({'schema_version': 1, 'status': 'applied', 'editable': True, 'channel': value}))
'''
    for command in ('emaki-update-channel', 'pkexec', 'emaki-terminal', 'emaki-settings-power'):
        helper = base / 'bin' / command
        helper.write_text(helper_source)
        helper.chmod(0o700)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
               PATH=str(base / 'bin') + os.pathsep + os.environ['PATH'],
               FIXTURE_ROOT=str(base),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'MAINTENANCE_PAGE_RESULT 2 0' in output, output
    for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
        assert error not in output, output
    calls = [json.loads(line) for line in (base / 'calls.jsonl').read_text().splitlines()]
    assert [call for call in calls if call[0] == 'emaki-terminal'] == [
        ['emaki-terminal', '--change-password', '--json']] * 3, calls
    assert [call for call in calls if call[0] == 'emaki-update-channel'] == [
        ['emaki-update-channel', 'status', '--json']], calls
    privileged = [call for call in calls if call[0] == 'pkexec']
    assert len(privileged) == 3, calls
    target = privileged[0][1]
    assert Path(target).is_absolute() and Path(target).name == 'emaki-update-channel', calls
    assert privileged == [['pkexec', target, 'set', value, '--json']
                          for value in ('testing', 'stable', 'testing')], calls
    assert [call for call in calls if call[0] == 'emaki-settings-power'] == [
        ['emaki-settings-power', 'status', '--json']], calls
    # Restrict lookup to the fixture executables for missing-command cases.
    # The interpreter symlink is the only additional executable they need.
    (base / 'bin/python3').symlink_to(shutil.which('python3'))
    for mode, command in (('missing-channel', 'emaki-update-channel'),
                          ('missing-terminal', 'emaki-terminal')):
        helper = base / 'bin' / command
        helper.rename(helper.with_suffix('.disabled'))
        missing_env = dict(env, PATH=str(base / 'bin'), MAINTENANCE_FIXTURE_MODE=mode)
        result = subprocess.run([shutil.which('qs'), '-p', str(base / 'shell/test.qml'), '--no-color'],
                                env=missing_env, capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        assert result.returncode == 0 and 'MAINTENANCE_PAGE_RESULT 2 0' in output, output
        for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
            assert error not in output, output
        helper.with_suffix('.disabled').rename(helper)
print('PASS: Maintenance channel, reset, rejection, password, navigation and missing helpers')
