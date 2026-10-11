#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise storage preview, privilege boundaries, cancellation and failed replies."""
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
registry = (ROOT / 'shell/SettingsPageRegistry.qml').read_text()
assert 'pageId: "storage"' in registry and 'SettingsStoragePage {}' in registry
with tempfile.TemporaryDirectory(prefix='settings-storage-page-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    shutil.copyfile(ROOT / 'tests/fixtures/SettingsStoragePageTest.qml', base / 'shell/test.qml')
    definitions = base / 'shell/qmldir'
    if 'SettingsStoragePage 1.0 SettingsStoragePage.qml' not in definitions.read_text():
        with definitions.open('a') as stream:
            stream.write('\nSettingsStoragePage 1.0 SettingsStoragePage.qml\n')
    for part in ('home', 'config', 'data', 'cache', 'state', 'bin'):
        (base / part).mkdir(mode=0o700)
    helper_source = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
base = Path(os.environ['FIXTURE_ROOT'])
name = Path(sys.argv[0]).name
args = sys.argv[2:] if name == 'pkexec' else sys.argv[1:]
with (base / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps([name, *sys.argv[1:]]) + '\n')
def reply(status, code=0, **kwargs):
    print(json.dumps(dict(schema_version=1, status=status, **kwargs)))
    sys.exit(code)
if args == ['status', '--json']:
    cleaned = len(list(base.glob('cleaned-*')))
    reply('ready', filesystems=[dict(path=path,filesystem='btrfs',pool='fixture',total_bytes=1000,used_bytes=500,available_bytes=500,shared_with=shared) for path,shared in [('/',None),('/home','/')]], categories={kind:dict(bytes=(None if kind == 'trash' else 200) if name == 'pkexec' else 100-cleaned,reason='') for kind in ['snapshots','cache','logs','trash']})
action, kind = args[:2]
if action == 'preview':
    marker=base / ('seen-'+kind)
    previous=int(marker.read_text()) if marker.exists() else 0
    marker.write_text(str(previous+1))
    if kind == 'logs' and previous >= 3:
        reply('ready',1,message='[PLACEHOLDER] Invalid failed status.')
    if kind == 'cache' and previous:
        sys.exit(126)
    if kind == 'trash' and previous == 1:
        print('{bad json')
        sys.exit(0)
    reply('preview', kind=kind,token='a'*64,entries=[dict(path='/fixture/'+kind+'/first',bytes=50),dict(path='/fixture/'+kind+'/line\nbreak<literal>',bytes=50)] + ([dict(path='/fixture/trash/'+str(index),bytes=0) for index in range(2,20000)] if kind == 'trash' and previous == 0 else []),allocated_bytes=100,reclaimable_bytes=None,reclaimable_lower_bound_bytes=50,reclaimable_unknown_files=1,reason='[PLACEHOLDER] Fixture shared extents.')
if action == 'cleanup':
    assert args[2:] == ['a'*64,'--json']
    if kind == 'logs':
        marker = base / 'logs-cleanup-seen'
        if marker.exists():
            reply('error',1,message='[PLACEHOLDER] Cleanup stopped after removing 1 entries. Preview again.',removed_count=1)
        marker.touch()
        reply('error',1,message='[PLACEHOLDER] Fixture stale preview.')
    (base / ('cleaned-'+kind)).touch()
    reply('cleaned',kind=kind,removed_count=2,reclaimed_bytes=None,message='[PLACEHOLDER] Fixture cleaned.')
raise AssertionError(args)
'''
    for command in ('emaki-settings-storage', 'pkexec'):
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
    executable = shutil.which('qs')
    for mode in ('normal', 'missing'):
        run_env = dict(env, STORAGE_FIXTURE_MODE=mode)
        if mode == 'missing':
            (base / 'bin/emaki-settings-storage').rename(base / 'bin/disabled')
            (base / 'bin/python3').symlink_to(shutil.which('python3'))
            run_env['PATH'] = str(base / 'bin')
        result = subprocess.run([executable, '-p', str(base / 'shell/test.qml'), '--no-color'],
                                env=run_env, capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        assert result.returncode == 0 and 'STORAGE_PAGE_RESULT 2 0' in output, output
        for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
            assert error not in output, output
    calls = [json.loads(line) for line in (base / 'calls.jsonl').read_text().splitlines()]
    privileged = [call for call in calls if call[0] == 'pkexec']
    target = privileged[0][1]
    assert Path(target).is_absolute() and Path(target).name == 'emaki-settings-storage', calls
    assert privileged == [['pkexec', target, *args] for args in [
        ['status','--json'], ['preview','cache','--json'], ['cleanup','cache','a'*64,'--json'],
        ['preview','logs','--json'], ['preview','logs','--json'], ['cleanup','logs','a'*64,'--json'],
        ['preview','logs','--json'], ['cleanup','logs','a'*64,'--json'], ['preview','cache','--json'],
        ['preview','logs','--json']]], calls
    assert [call[1:] for call in calls if call[0] == 'emaki-settings-storage'] == [
        ['status','--json'], ['preview','trash','--json'], ['status','--json'], ['status','--json'], ['preview','trash','--json'],
        ['preview','trash','--json'], ['cleanup','trash','a'*64,'--json'], ['status','--json']], calls
print('PASS: Storage preview, exact entries, cancel, confirm, privilege routing, stale replies, malformed JSON, auth cancellation and missing helper')
