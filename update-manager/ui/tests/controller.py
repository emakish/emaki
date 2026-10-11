#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the production process controller against an isolated offline backend."""
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
UI = Path(__file__).resolve().parents[1]
BACKEND = '''#!{python}
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
import json, os, sys, time
from pathlib import Path
operation = sys.argv[1]
with Path(os.environ['TEST_CALLS']).open('a') as stream:
    stream.write(' '.join(sys.argv[1:]) + '\\n')
def emit(event):
    print(json.dumps(event), flush=True)
scenario = os.environ['TEST_SCENARIO']
if operation in ('--check', '--refresh'):
    if scenario == 'flatpak':
        emit(dict(type='result', data=dict(updates=[dict(name='App', kind='flatpak', aur=False)])))
        sys.exit(0)
    if scenario == 'aur':
        emit(dict(type='result', data=dict(updates=[dict(name='foreign', aur=True)])))
        sys.exit(0)
    if scenario == 'reminder':
        emit(dict(type='result', data=dict(updates=[], restart=True, reminder=not Path(os.environ['TEST_CALLS'] + '.dismissed').exists())))
        sys.exit(0)
    emit(dict(type='result', data=dict(updates=[dict(name='linux', aur=False), dict(name='foreign', aur=True)], groups=[], news=[], warnings=[], downloadSize=123, cached=operation == '--check')))
elif operation == '--dismiss-restart':
    Path(os.environ['TEST_CALLS'] + '.dismissed').touch()
elif operation == '--restore-points':
    emit(dict(type='restorePoints', points=[dict(id=42, date='2026-10-10T12:00:00Z', description='System update')]))
elif operation == '--restore':
    time.sleep(0.15)
    ok = scenario == 'restore-success'
    emit(dict(type='restoreFinished', ok=ok, restart=ok or scenario == 'restore-partial', message='Restore prepared.' if ok else 'Restore refused.'))
elif operation == '--apply':
    emit(dict(type='progress', message='Preparing the update.'))
    print('Download detail.', file=sys.stderr, flush=True)
    time.sleep(0.15)
    scenario = os.environ['TEST_SCENARIO']
    if scenario == 'cancelled':
        emit(dict(type='finished', ok=False, notStarted=True, message='The update was not started.', restart=False, signOut=False))
        sys.exit(0)
    if scenario == 'empty':
        sys.exit(0)
    if scenario == 'malformed':
        print('not json', flush=True)
        sys.exit(0)
    ok = scenario != 'failure'
    emit(dict(type='finished', ok=ok, message='Update complete.' if ok else 'Update refused.', restart=ok, signOut=ok))
    sys.exit(1 if scenario in ('failure', 'nonzero') else 0)
elif operation == '--restart':
    emit(dict(type='finished', ok=False, message='Restart was refused.', restart=True, signOut=False))
    sys.exit(1)
else:
    sys.exit(99)
'''


def rendered_window():
    """Load the shipped entry point without rewriting imports or QML behavior."""
    renderer = runpy.run_path(str(ROOT / 'scripts/render-paths'))
    with tempfile.TemporaryDirectory(prefix='update-window-') as directory:
        root = Path(directory)
        values = renderer['paths'](str(root / 'install'))
        shell = Path(values['DATADIR']) / 'shell'
        shutil.copytree(ROOT / 'shell', shell)
        shell.joinpath('Platform.qml').write_text(renderer['modules'](values)[2])
        ui = Path(values['PREFIX']) / 'share/emaki-update-manager/ui'
        ui.mkdir(parents=True)
        for source in UI.glob('*.qml'):
            (ui / source.name).write_text(renderer['substitute'](source.read_text(), values))
        backend = Path(values['LIBEXECDIR']) / 'update-manager-backend'
        backend.parent.mkdir(parents=True)
        backend.write_text(BACKEND.format(python=sys.executable))
        backend.chmod(0o700)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
                   QS_DISABLE_CRASH_HANDLER='1', TEST_SCENARIO='success',
                   TEST_CALLS=str(root / 'calls'))
        for name in ('cache', 'config', 'data', 'state'):
            (root / name).mkdir(mode=0o700)
            env['XDG_' + name.upper() + '_HOME'] = str(root / name)
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'NIRI_SOCKET'):
            env.pop(name, None)
        # Keep Unix socket names short while retaining all scratch in the worktree.
        with tempfile.TemporaryDirectory(prefix='', dir=ROOT / '.cache') as runtime:
            env['XDG_RUNTIME_DIR'] = runtime
            entry = str(ui / 'shell.qml')
            with (root / 'window.log').open('w+') as stream:
                process = subprocess.Popen(['qs', '-p', entry, '--no-color'], env=env,
                                           stdout=stream, stderr=subprocess.STDOUT)
                try:
                    presented = False
                    deadline = time.monotonic() + 15
                    while process.poll() is None and time.monotonic() < deadline:
                        calls = root / 'calls'
                        if calls.exists() and '--check' in calls.read_text().splitlines():
                            result = subprocess.run(['qs', 'ipc', '-p', entry, 'call', 'updates', 'present'],
                                                    env=env, capture_output=True, text=True, timeout=3)
                            if result.returncode == 0 and result.stdout.strip() == 'presented':
                                presented = True
                                break
                        time.sleep(0.05)
                    stream.flush()
                    log = (root / 'window.log').read_text()
                    assert presented and process.poll() is None, log
                    assert not any(word in log for word in ('ERROR', 'ReferenceError', 'TypeError',
                                                           'Unable to assign', 'Binding loop')), log
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
        print('PASS rendered production window')


def run(scenario):
    with tempfile.TemporaryDirectory(prefix='emaki-update-controller-') as directory:
        root = Path(directory)
        backend = root / 'backend'
        backend.write_text(BACKEND.format(python=sys.executable))
        backend.chmod(0o700)
        controller = (UI / 'UpdateController.qml').read_text().replace('@EMAKI_DATADIR@', str(ROOT))
        command = 'Shell.Platform.libexecDir + "/update-manager-backend"'
        assert controller.count(command) == 1
        (root / 'UpdateController.qml').write_text(controller.replace(command, json.dumps(str(backend))))
        shutil.copyfile(UI / 'tests/ControllerTest.qml', root / 'ControllerTest.qml')
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1', TEST_SCENARIO=scenario, TEST_CALLS=str(root / 'calls'))
        for name in ('runtime', 'cache', 'config', 'data', 'state'):
            (root / name).mkdir(mode=0o700)
            env['XDG_' + name.upper() + ('_DIR' if name == 'runtime' else '_HOME')] = str(root / name)
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'NIRI_SOCKET'):
            env.pop(name, None)
        result = subprocess.run(['qs', '-p', str(root / 'ControllerTest.qml'), '--no-color'], env=env, capture_output=True, text=True, timeout=20)
        log = result.stdout + result.stderr
        assert result.returncode == 0 and 'CONTROLLER_OK' in log, log
        assert not any(word in log for word in ('ASSERTION_FAILED', 'ReferenceError', 'TypeError', 'Unable to assign', 'Binding loop')), log
        expected = ['--check', '--refresh', '--apply'] + (['--dismiss-restart', '--restart'] if scenario == 'success' else [])
        if scenario == 'flatpak':
            expected = ['--check', '--apply']
        elif scenario == 'aur':
            expected = ['--check']
        elif scenario == 'reminder':
            expected = ['--check', '--dismiss-restart', '--refresh']
        elif scenario.startswith('restore-'):
            expected = ['--check', '--restore-points', '--restore 42']
        assert (root / 'calls').read_text().splitlines() == expected
        print('PASS controller', scenario)


if __name__ == '__main__':
    rendered_window()
    for scenario in ('success', 'failure', 'empty', 'malformed', 'nonzero', 'cancelled', 'flatpak', 'aur', 'reminder', 'restore-success', 'restore-refused', 'restore-partial'):
        run(scenario)
