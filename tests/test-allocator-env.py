#!/usr/bin/env python3
"""Allocator defaults stay with shell/lock; scoped and fallback apps keep session env."""
from runtime_fixture import runtime_path
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import reaper
reaper.guard()  # nothing this test starts outlives it


ROOT = Path(__file__).resolve().parent.parent
MARKER = 'EMAKI_SHELL_ORIGINAL_MALLOC_CONF'


def run():
    (ROOT / '.cache').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='allocator-', dir=ROOT / '.cache') as directory:
        root = Path(directory)
        binary = root / 'bin'
        binary.mkdir()
        qt_check = binary / 'emaki-qt-check'
        qt_check.write_text('#!/bin/sh\nexit 0\n')
        qt_check.chmod(0o700)
        empty = root / 'empty'
        empty.mkdir()
        qml = root / 'qml'
        qml.mkdir()
        (qml / 'shell.qml').write_text('''import QtQuick
import Quickshell
ShellRoot {
    Component.onCompleted: {
        console.log("ALLOCATOR_ENV=" + JSON.stringify({
            "MALLOC_CONF": Quickshell.env("MALLOC_CONF"),
            "EMAKI_SHELL_ORIGINAL_MALLOC_CONF": Quickshell.env("EMAKI_SHELL_ORIGINAL_MALLOC_CONF"),
            "QS_DISABLE_CRASH_HANDLER": Quickshell.env("QS_DISABLE_CRASH_HANDLER")
        }));
    }
    Timer { interval: 50; running: true; onTriggered: Qt.quit() }
}
''')
        # Only our stubs run through this PATH. Missing session-start.py is an
        # explicitly supported shell-launch case and cannot touch a live session.
        capture = root / 'launch.json'
        fake_qs = binary / 'qs'
        fake_qs.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
from pathlib import Path
keys = ('MALLOC_CONF', 'EMAKI_SHELL_ORIGINAL_MALLOC_CONF')
Path(os.environ['EMAKI_ALLOCATOR_RESULT']).write_text(json.dumps({
    'env': {key: os.environ[key] for key in keys if key in os.environ},
    'crash_handler_disabled': os.environ.get('QS_DISABLE_CRASH_HANDLER'),
    'argv': sys.argv[1:]
}))
''')
        fake_qs.chmod(0o700)
        fake_manager = binary / 'systemd-run'
        fake_manager.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
from pathlib import Path
keys = ('MALLOC_CONF', 'EMAKI_SHELL_ORIGINAL_MALLOC_CONF')
Path(os.environ['EMAKI_ALLOCATOR_MANAGER_RESULT']).write_text(json.dumps(
    {key: os.environ[key] for key in keys if key in os.environ}))
if os.environ['EMAKI_ALLOCATOR_SCOPE_MODE'] == 'fail':
    sys.exit(1)
command = sys.argv[sys.argv.index('--') + 1:]
os.execv(command[0], command)
''')
        fake_manager.chmod(0o700)
        app = root / 'app.py'
        app.write_text('''import json, os
from pathlib import Path
keys = ('MALLOC_CONF', 'EMAKI_SHELL_ORIGINAL_MALLOC_CONF')
Path(os.environ['EMAKI_ALLOCATOR_RESULT']).write_text(json.dumps(
    {key: os.environ[key] for key in keys if key in os.environ}))
''')
        # Exercise system-tools -> locker supervisor -> actual production child
        # launch arguments without creating a locker or binding a socket.
        fake_lock = binary / 'emaki-lock'
        fake_lock.write_text(f'#!{sys.executable}\n' + '''import importlib.machinery, importlib.util, json, os, sys
from pathlib import Path
assert sys.argv[1:] == [os.environ['EMAKI_ALLOCATOR_LOCK_FLAG']]
keys = ('MALLOC_CONF', 'EMAKI_SHELL_ORIGINAL_MALLOC_CONF')
result = {'supervisor': {key: os.environ[key] for key in keys if key in os.environ}, 'children': {}}
loader = importlib.machinery.SourceFileLoader('lock_fixture', os.environ['EMAKI_ALLOCATOR_LOCK_SOURCE'])
spec = importlib.util.spec_from_loader(loader.name, loader)
lock = importlib.util.module_from_spec(spec)
loader.exec_module(lock)
class Captured(Exception): pass
def capture(argv, **kwargs):
    name = 'hyprlock' if 'hyprlock' in argv else 'qs'
    result['children'][name] = {key: kwargs['env'][key] for key in keys if key in kwargs['env']}
    raise Captured()
lock.subprocess.Popen = capture
for fallback in (False, True):
    supervisor = lock.Supervisor(Path(os.environ['TMPDIR']))
    try:
        supervisor.spawn(fallback)
    except Captured:
        pass
    finally:
        supervisor.selector.close()
Path(os.environ['EMAKI_ALLOCATOR_RESULT']).write_text(json.dumps(result))
''')
        fake_lock.chmod(0o700)
        fake_systemctl = binary / 'systemctl'
        fake_systemctl.write_text(f'#!{sys.executable}\nimport sys\nassert sys.argv[1:] == ["suspend"]\n')
        fake_systemctl.chmod(0o700)
        env = dict(PATH=str(binary), LC_ALL='C', TMPDIR=str(root),
                   EMAKI_SHELL_DIR=str(qml), EMAKI_ALLOCATOR_RESULT=str(capture),
                   EMAKI_ALLOCATOR_MANAGER_RESULT=str(root / 'manager.json'))
        for original in (None, '', 'thp:always,dirty_decay_ms:123', '1$literal\n0:unset'):
            original_env = dict(env, **{MARKER: '1stale inherited value'})
            if original is not None:
                original_env['MALLOC_CONF'] = original
            subprocess.run([str(ROOT / 'scripts/emaki-shell')], env=original_env,
                           check=True, capture_output=True, timeout=3)
            launch = json.loads(capture.read_text())
            assert launch['argv'] == ['-n', '-p', str(qml)], launch
            # A crash ends the process so systemd restarts it (no in-place re-exec).
            assert launch['crash_handler_disabled'] == '1', launch
            shell_env = launch['env']
            assert shell_env['MALLOC_CONF'] == (original or 'thp:never'), shell_env
            assert shell_env[MARKER] == ('0' if original is None else '1' + original), shell_env
            expected = {} if original is None else {'MALLOC_CONF': original}
            for mode in ('ok', 'fail', 'missing'):
                capture.unlink()
                manager_result = root / 'manager.json'
                manager_result.unlink(missing_ok=True)
                environment = dict(env, **shell_env, EMAKI_ALLOCATOR_SCOPE_MODE=mode)
                if mode == 'missing':
                    environment['PATH'] = str(empty)
                completed = subprocess.run(
                    [sys.executable, '-B', str(ROOT / 'shell/helpers/app_scope.py'), '--exec',
                     'allocator-fixture', sys.executable, '-B', str(app)],
                    env=environment, check=True, capture_output=True, text=True, timeout=3)
                assert json.loads(capture.read_text()) == expected, (original, mode)
                if mode != 'missing':
                    assert json.loads(manager_result.read_text()) == expected, (original, mode)
                assert ('without cgroup isolation' in completed.stderr) == (mode != 'ok')
            for request, flag, state in (({'op': 'lock'}, '--confirm', 'locked'),
                                         ({'op': 'session', 'value': 'suspend', 'confirmed': True},
                                          '--wait', 'requested')):
                capture.unlink(missing_ok=True)
                completed = subprocess.run(
                    [sys.executable, '-B', str(ROOT / 'shell/helpers/system-tools.py')],
                    input=json.dumps(request), text=True, capture_output=True, check=True, timeout=3,
                    env=dict(env, **shell_env, EMAKI_LOCK=str(fake_lock),
                             EMAKI_ALLOCATOR_LOCK_FLAG=flag,
                             EMAKI_ALLOCATOR_LOCK_SOURCE=str(ROOT / 'scripts/emaki-lock')))
                assert json.loads(completed.stdout)['state'] == state, completed
                observed_lock = json.loads(capture.read_text())
                assert observed_lock == {'supervisor': expected, 'children': {
                    'qs': {'MALLOC_CONF': original or 'thp:never'}, 'hyprlock': expected}}, observed_lock
        # A direct app_scope caller has no shell marker; preserve its own allocator
        # environment too, and never pass invalid/stale private markers into apps.
        for original, marker in ((None, None), ('', None), ('thp:never', 'invalid')):
            environment = dict(env, EMAKI_ALLOCATOR_SCOPE_MODE='ok')
            if original is not None:
                environment['MALLOC_CONF'] = original
            if marker is not None:
                environment[MARKER] = marker
            subprocess.run([sys.executable, '-B', str(ROOT / 'shell/helpers/app_scope.py'),
                            '--exec', 'allocator-direct', sys.executable, '-B', str(app)],
                           env=environment, check=True, capture_output=True, timeout=3)
            assert json.loads(capture.read_text()) == ({} if original is None else {'MALLOC_CONF': original})
        # Verify the real qs process sees the same launch environment offscreen.
        real_qs = shutil.which('qs')
        assert real_qs, 'qs is required by check-shell'
        fake_qs.unlink()
        fake_qs.symlink_to(real_qs)
        for name in ('runtime', 'config', 'state', 'cache', 'data'):
            (root / name).mkdir(mode=0o700)
        offscreen = dict(env, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                         QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(runtime_path(root)),
                         XDG_CONFIG_HOME=str(root / 'config'), XDG_STATE_HOME=str(root / 'state'),
                         XDG_CACHE_HOME=str(root / 'cache'), XDG_DATA_HOME=str(root / 'data'),
                         DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(root / 'no-bus'),
                         DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(root / 'no-system'))
        for original in (None, '', 'thp:always,dirty_decay_ms:123'):
            environment = dict(offscreen)
            if original is not None:
                environment['MALLOC_CONF'] = original
            completed = subprocess.run([str(ROOT / 'scripts/emaki-shell')], env=environment,
                                       check=True, capture_output=True, text=True, timeout=5)
            line = next(line for line in (completed.stdout + completed.stderr).splitlines()
                        if 'ALLOCATOR_ENV=' in line)
            observed = json.loads(line.split('ALLOCATOR_ENV=', 1)[1])
            # The variable itself reaches qs; qs prints "Crash handling disabled." only when it
            # was built with CRASH_HANDLER, which is a packaging choice, not this launch path's.
            assert observed == {'MALLOC_CONF': original or 'thp:never',
                                MARKER: '0' if original is None else '1' + original,
                                'QS_DISABLE_CRASH_HANDLER': '1'}, observed
        assert 'Environment=MALLOC_CONF=' not in (ROOT / 'systemd/emaki-shell.service').read_text()
    print('PASS allocator defaults: real offscreen qs, unset/empty/custom session values, '
          'scoped/failed/missing-manager app launches, system lock and hyprlock fallback, '
          'stripped markers, direct callers')


if __name__ == '__main__':
    run()
