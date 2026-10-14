#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the shipped program controller through its real JSON process transport."""
import json
import os
import re
import runpy
import signal
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

UI = Path(__file__).resolve().parents[1]
ROOT = UI.parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
import reaper
from app_scope_fixture import install, launches
reaper.guard()

LAUNCHER = r'''#!PYTHON
import json, os, signal, sys, time
from pathlib import Path
root = Path(os.environ['EMAKI_SCOPE_FIXTURE'])
(root / 'launch-args').write_text(json.dumps(sys.argv[1:]))
if os.environ['TEST_SCENARIO'] == 'launch-nonzero': sys.exit(7)
parent = os.getpid()
if os.fork(): os._exit(0)
# Write only after the launcher has exited and its caller has reaped it.
while Path('/proc/' + str(parent)).exists(): time.sleep(.01)
signal.signal(signal.SIGPIPE, signal.SIG_DFL)
os.write(2, b'Late application warning\n')
os.write(1, b'Late application output\n')
(root / 'application').write_text(json.dumps(dict(pid=os.getpid(),
    scope=os.environ.get('EMAKI_TEST_SCOPE'),
    fds=[os.readlink('/proc/self/fd/' + str(fd)) for fd in (1, 2)])))
time.sleep(30)
'''
BACKEND = r'''#!PYTHON
import json, os, sys, time
from pathlib import Path
installed = False
package_installed = False
scenario = os.environ['TEST_SCENARIO']
marker = Path(os.environ['TEST_CALLS'] + '.installed')
removed = Path(os.environ['TEST_CALLS'] + '.removed')
external = Path(os.environ['TEST_CALLS'] + '.external')
if sys.argv[1] == 'terminal-install': external.touch(); sys.exit(0)
if sys.argv[1] == 'terminal-remove': external.unlink(); sys.exit(0)
if sys.argv[1] == 'list':
    inventory_failed = Path(os.environ['TEST_CALLS'] + '.inventory-failed')
    with open(os.environ['TEST_CALLS'] + '.lists', 'a') as log: log.write('list\n')
    if scenario == 'inventory-empty': print(json.dumps(dict(ok=True, programs=[]))); sys.exit(0)
    if scenario.startswith('inventory-') and marker.exists() and (not inventory_failed.exists() or scenario == 'inventory-permanent'):
        inventory_failed.touch()
        if scenario == 'inventory-malformed': print('broken'); sys.exit(0)
        print(json.dumps(dict(ok=False, message='Inventory unavailable.'))); sys.exit(1)
    programs = [] if removed.exists() else [dict(package='local-editor', ids=['missing-entry.desktop', 'local-editor.desktop'], names=['Local editor'], version='1')]
    if marker.exists(): programs.append(dict(package='editor', ids=['editor-launch.desktop'], names=['Editor'], version='1', preinstalled=True))
    print(json.dumps(dict(ok=True, programs=programs))); sys.exit(0)
if sys.argv[1] == 'preview':
    if scenario == 'missing-remove': Path(sys.argv[0]).unlink()
    print(json.dumps(dict(ok=True, packages=['local-editor', 'dependency'], token='b'*64))); sys.exit(0)
if sys.argv[1] == 'remove':
    assert sys.argv[2] == 'local-editor' and sys.argv[3] == 'b'*64
    removed.touch()
    print(json.dumps(dict(ok=True))); sys.exit(0)
aur_enabled = False
if scenario == 'aur': installed = True
def row(kind, identity='org.example.Editor', key=None):
    value = dict(key=key or kind + ':editor', id=identity, name='Editor', kind=kind, package='editor',
                 installed=installed, actions=['remove'] if installed else ['install'],
                 categories=['Utility'], summary='', icons=[], screenshots=[])
    if scenario == 'aur' and value['key'] == 'arch:editor' and installed:
        value.update(actions=['remove', 'undo'], undo=dict(id='20261012T120000123456Z-' + 'b' * 32, kind='arch',
                     name='editor', time='2026-10-12T12:00:00+00:00', target='editor', added=['editor', 'libeditor'], snapshot='17'))
    return value
def package_row(i):
    present = i == 0 or i == 2 and package_installed
    return dict(key='package:extra/pkg-' + str(i), id='pkg-' + str(i), package='pkg-' + str(i),
                name='pkg-' + str(i), kind='arch', repository='extra', version='1.2',
                summary='Compression helper' if i == 2 else 'Repository package',
                installed=present, packageOnly=True, actions=['remove'] if present else ['install'],
                programKey='arch:editor' if i == 1 else '')
for line in sys.stdin:
    request = json.loads(line)
    with open(os.environ['TEST_CALLS'], 'a') as log:
        log.write(json.dumps(request) + '\n')
    method, params = request['method'], request['params']
    value = dict(version=1, id=request['id'], type='result', ok=True)
    if method == 'catalog' and params.get('scope') == 'packages':
        query = params.get('query', '')
        if query == 'old': time.sleep(.4)
        rows = [package_row(i) for i in range(505)]
        if query == 'old': rows = [dict(rows[3], name='Old response')]
        elif query == 'new': rows = [dict(rows[4], name='New response')]
        elif query: rows = [row for row in rows if query.lower() in (row['name'] + ' ' + row['summary']).lower()]
        offset = params.get('offset', 0)
        value.update(programs=rows[offset:offset + 500], total=len(rows))
    elif method == 'aur':
        if 'accept' in params: aur_enabled = params['accept'] is True
        value.update(enabled=aur_enabled, showWarning=params.get('showWarning', True), accepted='', snapshots=True)
    elif method == 'catalog' and params.get('source') == 'aur':
        if not aur_enabled:
            value.update(ok=False, code='aur-closed', message='The AUR section is off.')
        else:
            found = [dict(row('aur', 'editor-tools', 'aur:editor-tools'), name='editor-tools', installed=False,
                          actions=['install'], votes=3, orphan=True)]
            value.update(programs=found, total=1)
    elif method == 'popular':
        if not aur_enabled:
            value.update(ok=False, code='aur-closed', message='The AUR section is off.')
        else:
            value.update(programs=[dict(row('aur', 'yay', 'aur:yay'), name='yay', installed=False, actions=['install'], votes=2649, popularity=20.7),
                                   dict(row('aur', 'editor-tools', 'aur:editor-tools'), name='editor-tools', installed=False, actions=['install'], votes=3, popularity=0.1)])
    elif method == 'related':
        value.update(programs=[dict(row('aur', 'org.example.Editor.desktop', 'aur:editor-bin'), installed=False,
                                    actions=['install'], sameAs=params['key'])] if aur_enabled else [])
    elif method == 'details':
        index = int(params['key'].removeprefix('package:extra/pkg-'))
        value.update(program=package_row(index))
    elif method == 'catalog':
        if scenario == 'presentation' and params.get('refresh'):
            package_installed = external.exists()
        protected = row('arch', 'org.example.Protected', 'arch:protected')
        protected.update(installed=True, package='emaki-config')
        rows = [row('flatpak'), row('arch', 'org.example.Editor.desktop'), row('arch', 'org.example.Other', 'arch:other'), protected]
        offset = params.get('offset', 0)
        value.update(programs=rows[offset:offset + 2], total=4, warnings=[],
                     storefront=dict(featured=dict(title='Worth trying', note='Picked by Emaki', programs=['org.example.editor']), rows=[], names={}))
    elif method == 'search':
        query = params['query']
        if query == 'old': time.sleep(.2)
        if query == 'needle':
            rows = [dict(row('arch', 'app-' + str(i), 'arch:match-' + str(i)), name='Match ' + str(i), keywords=['needle']) for i in range(505)]
        elif query == 'new': rows = [dict(row('arch', 'new-result', 'arch:new'), name='Different title', categories=['new'])]
        else: rows = [row('aur', 'editor-aur')]
        offset = params.get('offset', 0)
        value.update(programs=rows[offset:offset + 500], total=len(rows))
    elif method == 'plan' and params['action'] == 'undo':
        value.update(key=params['key'], action='undo', token='c'*64,
                     transaction=dict(receipt='20261012T120000123456Z-' + 'b' * 32, target='editor',
                                      remove=['editor', 'libeditor'], packages=['editor', 'libeditor'],
                                      keep={'shared-lib': 'explicit'}, upgraded=['linux'], snapshot='17', token='d'*64))
    elif method == 'plan' and scenario.startswith('plan-'):
        if scenario == 'plan-disconnect': sys.exit(1)
        if scenario == 'plan-malformed':
            print('broken', flush=True)
            continue
        value.update(ok=False, code='recipe-unavailable', message='The build instructions could not be read.')
    elif method == 'plan':
        value.update(key=params['key'], action=params['action'], token='a'*64,
                     transaction=dict(packages=['editor', 'dependency']))
    elif method == 'execute' and params['action'] == 'undo':
        assert params['key'] == 'arch:editor' and params['token'] == 'c'*64
        installed = False
        print(json.dumps(dict(version=1, id=request['id'], type='progress', message='Removing')), flush=True)
        time.sleep(.1)
        value.update(code='undone', message='The install was taken back.')
    elif method == 'execute':
        if scenario == 'packages':
            assert params['key'] == 'package:extra/pkg-2' and params['token'] == 'a'*64
            assert params['action'] in ('install', 'remove')
            package_installed = params['action'] == 'install'
            value.update(message='Finished')
            print(json.dumps(value), flush=True)
            continue
        assert params['key'] == 'arch:editor' and params['token'] == 'a'*64
        print(json.dumps(dict(version=1, id=request['id'] - 1, type='result', ok=False)), flush=True)
        print(json.dumps(dict(version=1, id=request['id'], type='progress', message='Working')), flush=True)
        time.sleep(.1)
        if scenario == 'disconnect': sys.exit(1)
        if scenario == 'malformed':
            print('broken', flush=True)
            continue
        installed = scenario == 'success' or scenario.startswith('inventory-')
        if installed: marker.touch()
        failure = scenario if scenario in ('failed', 'not-authorized', 'metadata-unavailable') else 'changed'
        value.update(ok=installed, code='completed' if installed else failure, message='Finished' if installed else 'Review the changed plan.')
    print(json.dumps(value), flush=True)
'''
# Stands in for the shell's IPC so no test reaches a real running shell.
REPORT_SHELL = r'''#!PYTHON
import os, sys
with open(os.environ['TEST_REPORTS'], 'a') as log: log.write(' '.join(sys.argv[1:]) + '\n')
'''
# Only a failed install offers a report; refusals, stale plans and successes do not.
# 'aur' injects four installs that failed after their pre-install snapshot (partial, failed,
# unknown, cancelled) and one refusal (refused, nothing installed); only the four offer.
REPORT_OFFERS = {'failed': 1, 'disconnect': 1, 'malformed': 1, 'aur': 4}


def run(scenario):
    with tempfile.TemporaryDirectory(prefix='programs-', dir=os.environ.get('TMPDIR')) as directory:
        root = Path(directory)
        backend = root / 'programs'
        backend.write_text(BACKEND.replace('PYTHON', sys.executable, 1))
        backend.chmod(0o700)
        controller = (UI / 'ProgramsController.qml').read_text()
        renderer = runpy.run_path(str(ROOT / 'scripts/render-paths'))
        values = renderer['paths'](str(root / 'install'))
        shell = Path(values['DATADIR']) / 'shell'
        shutil.copytree(ROOT / 'shell', shell)
        (shell / 'Platform.qml').write_text(renderer['modules'](values)[2])
        controller = renderer['substitute'](controller, values)
        controller = controller.replace('"emaki-programs"', json.dumps(str(backend)))
        if scenario == 'inventory-missing':
            controller = controller.replace('command: [' + json.dumps(str(backend)) + ', "list"', 'command: ["/missing-inventory-service", "list"')
        if scenario == 'missing-preview':
            controller = controller.replace('? [' + json.dumps(str(backend)) + ', "preview"', '? ["/missing-program-service", "preview"')
        (root / 'ProgramsController.qml').write_text(controller)
        shutil.copyfile(UI / 'ManagerLogic.js', root / 'ManagerLogic.js')
        fixture = (UI / 'tests/ProgramsControllerTest.qml').read_text()
        present = re.search(r'function present\(\): void \{([^}]+)\}', (UI / 'ManagerView.qml').read_text())
        assert present, 'window presentation hook exists'
        fixture = fixture.replace('/* PRESENT_BODY */', present.group(1))
        (root / 'test.qml').write_text(fixture)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1',
                   TEST_SCENARIO=scenario, TEST_CALLS=str(root / 'calls'), TEST_BACKEND=str(backend))
        for name in ('cache', 'config', 'data', 'state'):
            (root / name).mkdir()
            env['XDG_' + name.upper() + '_HOME'] = str(root / name)
        applications = root / 'data/applications'
        applications.mkdir()
        (applications / 'local-editor.desktop').write_text('[Desktop Entry]\nType=Application\nName=Local editor\nIcon=editor-icon\nExec=true\n')
        (applications / 'hidden-editor.desktop').write_text('[Desktop Entry]\nType=Application\nName=Hidden editor\nNoDisplay=true\nExec=true\n')
        env['XDG_DATA_DIRS'] = str(root / 'data')
        env['EMAKI_GTK_LAUNCH'] = str(root / 'missing-launcher')
        env['EMAKI_PYTHON'] = sys.executable
        install(root, env)
        (root / 'report-bin').mkdir()
        report_shell = root / 'report-bin/emaki-shell'
        report_shell.write_text(REPORT_SHELL.replace('PYTHON', sys.executable, 1))
        report_shell.chmod(0o700)
        env.update(PATH=str(root / 'report-bin') + os.pathsep + env['PATH'], TEST_REPORTS=str(root / 'reports'))
        if scenario.startswith('launch-'):
            launcher = root / 'gtk-launch'
            launcher.write_text(LAUNCHER.replace('PYTHON', sys.executable, 1))
            launcher.chmod(0o700)
            env['EMAKI_GTK_LAUNCH'] = str(launcher)
        if scenario == 'launch-fallback':
            (root / 'scope-mode').write_text('fail')
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'NIRI_SOCKET',
                     'EMAKI_SHELL_SESSION_ENV', 'EMAKI_SHELL_ORIGINAL_MALLOC_CONF', 'EMAKI_SHELL_PROCESS_ID'):
            env.pop(name, None)
        with tempfile.TemporaryDirectory(prefix='', dir=ROOT / '.cache') as runtime:
            env['XDG_RUNTIME_DIR'] = runtime
            result = subprocess.run(['qs', '-p', str(root / 'test.qml'), '--no-color'], env=env,
                                    capture_output=True, text=True, timeout=25)
        log = result.stdout + result.stderr
        assert result.returncode == 0 and 'PROGRAMS_OK' in log, log
        assert not any(word in log for word in ('ASSERTION_FAILED', 'ReferenceError', 'TypeError', 'Unable to assign', 'Binding loop')), log
        requests = [json.loads(line) for line in (root / 'calls').read_text().splitlines()]
        offers = (root / 'reports').read_text().splitlines() if (root / 'reports').exists() else []
        assert offers == ['call report offer install'] * REPORT_OFFERS.get(scenario, 0), (scenario, offers)
        if scenario.startswith('launch-'):
            assert json.loads((root / 'launch-args').read_text()) == ['--', 'local-editor.desktop']
            if scenario != 'launch-nonzero':
                assert (root / 'application').exists(), 'application died writing output after its launcher exited'
                application = json.loads((root / 'application').read_text())
                pid = application['pid']
                assert Path(f'/proc/{pid}/stat').read_text().split(')')[1].split()[0] != 'Z', 'application remains running'
                descriptor = os.pidfd_open(pid)
                try:
                    signal.pidfd_send_signal(descriptor, signal.SIGTERM)
                finally:
                    os.close(descriptor)
                assert application['fds'] == [os.devnull, os.devnull], application
                assert bool(application['scope']) == (scenario == 'launch-output'), application
            launches(root, ['local-editor'])
            print('PASS programs controller', scenario)
            return
        if scenario.startswith('plan-'):
            assert any(item['method'] == 'plan' for item in requests)
            assert not any(item['method'] == 'execute' for item in requests), 'failed plans cannot execute'
            print('PASS programs controller', scenario)
            return
        if scenario == 'presentation':
            refreshes = [item for item in requests if item['method'] == 'catalog' and item['params'].get('refresh')]
            assert len(refreshes) >= 3, 'persistent controller refreshes on each presentation'
            assert not any(item['method'] == 'execute' for item in requests), 'refresh never executes a stale preview'
            print('PASS programs controller', scenario)
            return
        if scenario == 'packages':
            package_requests = [item for item in requests if item['method'] == 'catalog' and item['params'].get('scope') == 'packages']
            assert any(item['params'].get('offset') == 500 and not item['params']['query'] for item in package_requests)
            assert any(item['params']['query'] == 'compression' for item in package_requests)
            mutations = [item for item in requests if item['method'] in ('plan', 'execute')]
            assert [(item['method'], item['params']['action']) for item in mutations] == [
                ('plan', 'install'), ('plan', 'install'), ('execute', 'install'), ('plan', 'remove'), ('execute', 'remove')]
            assert mutations[0]['params']['key'] == 'package:extra/pkg-504'
            assert all(item['params']['key'] == 'package:extra/pkg-2' for item in mutations[1:])
            assert all(item['params']['token'] == 'a' * 64 for item in mutations if item['method'] == 'execute')
            details = [item for item in requests if item['method'] == 'details']
            assert len(details) == 2 and all(item['params']['key'] == 'package:extra/pkg-2' for item in details)
            assert all(item['method'] != 'search' or item['params']['includeAur'] is False for item in requests)
            print('PASS programs controller', scenario)
            return
        if scenario == 'aur':
            methods = [(item['method'], item['params']) for item in requests]
            consent = [index for index, (method, params) in enumerate(methods) if method == 'aur' and params.get('accept') is True]
            assert len(consent) == 1 and methods[consent[0]][1]['showWarning'] is True, methods
            assert [params for method, params in methods if method == 'aur' and 'accept' not in params] == [{}], 'state is read once'
            searches = [index for index, (method, params) in enumerate(methods) if method == 'catalog' and params.get('source') == 'aur']
            assert searches and all(index > consent[0] for index in searches), 'no AUR search before consent'
            assert ('related', {'key': 'arch:editor'}) in methods
            undo = [params for method, params in methods if method == 'execute']
            assert undo == [{'action': 'undo', 'key': 'arch:editor', 'token': 'c' * 64}], undo
            assert methods[-2:] == [('aur', {'accept': False}), ('popular', {})], methods[-2:]
            popular = [index for index, (method, _params) in enumerate(methods) if method == 'popular']
            assert len(popular) >= 2 and all(index > consent[0] for index in popular), 'the popular list is read only after consent'
            print('PASS programs controller', scenario)
            return
        if scenario == 'inventory-permanent':
            assert len((root / 'calls.lists').read_text().splitlines()) == 5, 'initial inventory plus refresh and three retries'
        if scenario.startswith(('missing-', 'inventory-')):
            print('PASS programs controller', scenario)
            return
        executions = [item for item in requests if item['method'] == 'execute']
        assert len(executions) == 1
        assert executions[0]['params']['token'] == 'a' * 64
        assert any(item['method'] == 'search' and item['params']['includeAur'] is False for item in requests)
        assert any(item['method'] == 'search' and item['params'].get('offset') == 500 for item in requests)
        print('PASS programs controller', scenario)


if __name__ == '__main__':
    for scenario in ('plan-refused', 'plan-refresh', 'plan-disconnect', 'plan-malformed', 'launch-output', 'launch-fallback', 'launch-nonzero', 'presentation', 'packages', 'aur', 'success', 'changed', 'disconnect', 'malformed', 'missing-preview', 'missing-remove', 'missing-launcher', 'inventory-failed', 'inventory-malformed', 'inventory-missing', 'inventory-empty', 'inventory-permanent', 'failed', 'not-authorized', 'metadata-unavailable'):
        run(scenario)
