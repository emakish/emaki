#!/usr/bin/env python3
"""Mic sample/lifecycle and glass policy in isolated qs, without a mic or session."""
from runtime_fixture import runtime_path
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
(ROOT / '.cache').mkdir(exist_ok=True)
with tempfile.TemporaryDirectory(prefix='meter-', dir=ROOT / '.cache') as temporary:
    profile = Path(temporary)
    for name in ('runtime', 'cache', 'config', 'state', 'data', 'tmp'):
        (profile / name).mkdir(mode=0o700)
    qml = profile / 'qml'
    qml.mkdir()
    shutil.copyfile(ROOT / 'shell/MicMeter.qml', qml / 'MicMeter.qml')
    shutil.copyfile(ROOT / 'shell/SystemMeterPolicy.qml', qml / 'SystemMeterPolicy.qml')
    shutil.copyfile(ROOT / 'tests/fixtures/SystemMeterPolicyTest.qml', qml / 'SystemMeterPolicyTest.qml')
    shutil.copyfile(ROOT / 'tests/fixtures/MicMeterTest.qml', qml / 'shell.qml')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_SCALE_FACTOR='1',
               QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               XDG_RUNTIME_DIR=str(runtime_path(profile)), XDG_CACHE_HOME=str(profile / 'cache'),
               XDG_CONFIG_HOME=str(profile / 'config'), XDG_STATE_HOME=str(profile / 'state'),
               XDG_DATA_HOME=str(profile / 'data'), XDG_DATA_DIRS=str(profile / 'data'),
               TMPDIR=str(profile / 'tmp'), NIRI_SOCKET='',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'))
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_PLUGIN_PATH', 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH',
                 'QT_SCREEN_SCALE_FACTORS', 'QT_AUTO_SCREEN_SCALE_FACTOR'):
        env.pop(name, None)
    result = subprocess.run(['qs', '-p', str(qml), '--no-color'], env=env,
                            text=True, capture_output=True, timeout=5)
    log = result.stdout + result.stderr
    assert result.returncode == 0 and 'MIC_METER_OK' in log and 'SYSTEM_METER_POLICY_OK' in log, log
    assert 'Failed to start IPC server' not in log, log
    unexpected = [line for line in log.splitlines()
                  if any(word in line for word in ('ERROR', 'WARN', 'Error:'))]
    assert not unexpected, '\n'.join(unexpected)
print('PASS: every mic peak immediate, unchanged width, close/reopen, resize; sound cache and clipping transitions')
