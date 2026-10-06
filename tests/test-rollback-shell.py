#!/usr/bin/env python3
"""Real keyboard/pointer prompt checks in an isolated Qt session, plus polkit wiring."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parents[1]
policy = ET.parse(ROOT / 'polkit/org.emaki.rollback.policy').getroot().find('action')
assert policy.attrib['id'] == 'org.emaki.rollback'
assert policy.find('defaults/allow_active').text == 'auth_admin'
assert policy.find('defaults/allow_inactive').text == 'no'
assert policy.find('defaults/allow_any').text == 'no'
assert policy.find('annotate').text == '/usr/bin/emaki-rollback'
source = (ROOT / 'shell/SnapshotRecovery.qml').read_text()
assert '["/usr/bin/pkexec", "/usr/bin/emaki-rollback", "keep"]' in source
assert 'status.mode === "snapshot"' in source
with tempfile.TemporaryDirectory(prefix='emaki-rollback-ui-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    fixture = (ROOT / 'tests/fixtures/SnapshotPromptTest.qml').read_text()
    fixture = fixture.replace('import "../../shell" as Shell\n', '').replace('Shell.', '')
    (base / 'shell/rollback-test.qml').write_text(fixture)
    for name in ('home', 'config', 'data', 'cache', 'state', 'runtime'):
        (base / name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(base / 'runtime'),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/rollback-test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'ROLLBACK_TEST_RESULT 4 0' in output, output
    print('PASS: snapshot prompt keyboard, pointer, busy, error/retry, success and admin policy')
