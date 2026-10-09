#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run the production process controller against an isolated offline backend."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

UI = Path(__file__).resolve().parents[1]
BACKEND = '''#!{python}
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
import json, os, sys, time
from pathlib import Path
operation = sys.argv[1]
with Path(os.environ['TEST_CALLS']).open('a') as stream:
    stream.write(operation + '\\n')
def emit(event):
    print(json.dumps(event), flush=True)
if operation in ('--check', '--refresh'):
    emit(dict(type='result', data=dict(updates=[dict(name='linux', aur=False), dict(name='foreign', aur=True)], groups=[], news=[], warnings=[], downloadSize=123, cached=operation == '--check')))
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


def run(scenario):
    with tempfile.TemporaryDirectory(prefix='emaki-update-controller-') as directory:
        root = Path(directory)
        backend = root / 'backend'
        backend.write_text(BACKEND.format(python=sys.executable))
        backend.chmod(0o700)
        controller = (UI / 'UpdateController.qml').read_text()
        command = '"/usr/libexec/emaki/update-manager-backend"'
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
        expected = ['--check', '--refresh', '--apply'] + (['--restart'] if scenario == 'success' else [])
        assert (root / 'calls').read_text().splitlines() == expected
        print('PASS controller', scenario)


if __name__ == '__main__':
    for scenario in ('success', 'failure', 'empty', 'malformed', 'nonzero', 'cancelled'):
        run(scenario)
