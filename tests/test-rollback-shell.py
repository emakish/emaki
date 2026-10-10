#!/usr/bin/env python3
"""Real keyboard/pointer prompt checks in an isolated Qt session, plus polkit wiring."""
from runtime_fixture import runtime_path
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
assert '["pkexec", Platform.binDir + "/emaki-rollback", "keep"]' in source
assert 'status.mode === "snapshot"' in source
assert 'status.automatic === true ? status.message : ""' in source
assert 'automaticMessage: recovery.automaticMessage' in source
with tempfile.TemporaryDirectory(prefix='emaki-rollback-ui-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    fixture = (ROOT / 'tests/fixtures/SnapshotPromptTest.qml').read_text()
    fixture = fixture.replace('import "../../shell" as Shell\n', '').replace('Shell.', '')
    # Exercise the installed component; keep this extension local to the fixture copy.
    fixture = fixture.replace('prompt.busy = false;', 'prompt.automaticMessage = ""; prompt.busy = false;', 1)
    fixture = fixture.replace('function test_keyboard() {', r'''function textContains(item, expected) {
                if (typeof item.text === "string" && item.text.indexOf(expected) >= 0)
                    return true;
                for (const child of item.children || [])
                    if (textContains(child, expected)) return true;
                return false;
            }
            function test_automatic_return() {
                const message = "The update did not start correctly, so Emaki returned to the system from before the update (2026-10-07). Your files are safe.";
                prompt.automaticMessage = message;
                wait(50);
                verify(textContains(prompt, message));
                verify(textContains(prompt, "Snapshot 7 is temporary until you keep it."));
                verify(textContains(prompt, "Your home files stay as they are."));
                mouseClick(findChild(prompt, "snapshotKeep"));
                compare(window.keeps, 1);
                prompt.busy = true;
                mouseClick(findChild(prompt, "snapshotKeep"));
                compare(window.keeps, 1);
                prompt.busy = false;
                prompt.succeeded = true;
                wait(50);
                verify(!textContains(prompt, message));
                verify(textContains(prompt, "Restart to use the restored system."));
                compare(findChild(prompt, "snapshotKeep").visible, false);
            }
            function test_manual_return() {
                verify(textContains(prompt, "Snapshot 7 is temporary until you keep it."));
                verify(!textContains(prompt, "The update did not start correctly"));
            }
            function test_keyboard() {''')
    (base / 'shell/rollback-test.qml').write_text(fixture)
    for name in ('home', 'config', 'data', 'cache', 'state', 'runtime'):
        (base / name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/rollback-test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'ROLLBACK_TEST_RESULT 6 0' in output, output
    print('PASS: snapshot return notice, keyboard, pointer, busy, error/retry, success and admin policy')
