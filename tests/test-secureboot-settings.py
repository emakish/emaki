#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings' Secure Boot card and the session notice: hidden without the key, the new
confirmation through pkexec with its one-time password, cancellation and broken answers, and
startup files an update could not sign: their notice first, and Sign again through pkexec.
A session that starts before root has written its copy asks again until the copy appears."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parents[1]

STATUS = {
    'no-key': {'schema_version': 1, 'secure_boot': False, 'key': False, 'enrollment': None, 'signed': {}},
    'unknown': {'schema_version': 1, 'secure_boot': False, 'key': None, 'enrollment': None, 'signed': {}},
    'enrolled-on': {'schema_version': 1, 'secure_boot': True, 'key': True, 'enrollment': 'enrolled', 'signed': {}},
    'missing-on': {'schema_version': 1, 'secure_boot': True, 'key': True, 'enrollment': 'missing', 'signed': {}},
}
MISSING_OFF = {'schema_version': 1, 'secure_boot': False, 'key': True, 'enrollment': 'missing', 'signed': {}}
SIGNED = {'/boot/vmlinuz-linux': True, '/boot/vmlinuz-linux-lts': True}
UNSIGNED = {'/boot/vmlinuz-linux': False, '/boot/vmlinuz-linux-lts': True}
# The kernel is not signed: with the key unconfirmed and Secure Boot off (both notices apply, the
# unsigned one wins), and with Secure Boot on; signing again works, fails or is cancelled.
for mode, secure_boot, enrollment in (('unsigned-off', False, 'missing'), ('unsigned-on', True, 'enrolled'),
                                      ('unsigned-fail', None, 'enrolled'), ('unsigned-cancel', False, 'enrolled')):
    STATUS[mode] = {'schema_version': 1, 'secure_boot': secure_boot, 'key': True, 'enrollment': enrollment,
                    'signed': UNSIGNED}
STATUS['signed-on'] = {'schema_version': 1, 'secure_boot': True, 'key': True, 'enrollment': 'enrolled',
                       'signed': SIGNED}

HELPER = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
name = Path(sys.argv[0]).name
base = Path(os.environ['FIXTURE_ROOT'])
def log():
    with (base / 'calls.jsonl').open('a') as stream:
        stream.write(json.dumps([name, *sys.argv[1:]]) + '\n')
mode = os.environ['SECUREBOOT_FIXTURE_MODE']
if name == 'emaki-secureboot':
    assert sys.argv[1:] == ['status', '--json'], sys.argv
    calls = (base / 'calls.jsonl').read_text() if (base / 'calls.jsonl').exists() else ''
    with (base / 'times.txt').open('a') as stream:
        stream.write(f'{time.monotonic()}\n')
    reads = calls.count('"status"')
    if mode == 'flaky' and reads < 3:
        # A failed read, then a malformed answer, then one of an unknown format (Settings' read
        # and the notice's share them).
        log()
        if reads == 0:
            sys.exit(1)
        print('{' if reads == 1 else json.dumps({'schema_version': 2}), flush=True)
        sys.exit(0)
    if mode == 'slow' and reads < 6:
        # Root's copy is late: unknown for the first six reads, past the notice's quick tries.
        print(json.dumps({'schema_version': 1, 'secure_boot': False, 'key': None, 'enrollment': None,
                          'signed': {}}), flush=True)
        log()
        sys.exit(0)
    if mode == 'late' and calls.count('"status"') < 2:
        # Root's copy is not written yet: the non-root answer knows no key (Settings' read and
        # the notice's first read both get it).
        answer = json.dumps({'schema_version': 1, 'secure_boot': False, 'key': None, 'enrollment': None,
                             'signed': {}})
    else:
        answer = (base / 'status.json').read_text()
    # The read after a privileged attempt is slow, and a read is logged only once it has
    # answered: a case that ends before that read finishes misses it in the log.
    if '"pkexec"' in calls:
        time.sleep(0.5)
    print(answer, flush=True)
    log()
    sys.exit(0)
log()
assert name == 'pkexec'
assert Path(sys.argv[1]).name == 'emaki-secureboot' and Path(sys.argv[1]).is_absolute(), sys.argv
if sys.argv[2:] == ['sign']:
    if mode == 'unsigned-cancel':
        sys.exit(126)
    if mode == 'unsigned-fail':
        print('emaki-secureboot: Could not sign /boot/vmlinuz-linux: No space left on device', file=sys.stderr)
        sys.exit(1)
    status = json.loads((base / 'status.json').read_text())
    status['signed'] = {name: True for name in status['signed']}
    (base / 'status.json').write_text(json.dumps(status))
    sys.exit(0)
assert sys.argv[2:] == ['enroll', '--json'], sys.argv
if mode == 'cancel':
    sys.exit(126)
if mode == 'broken':
    print(json.dumps({'schema_version': 1, 'status': 'queued', 'password': '12ab'}))
    sys.exit(0)
status = json.loads((base / 'status.json').read_text())
status['enrollment'] = 'pending'
(base / 'status.json').write_text(json.dumps(status))
print(json.dumps({'schema_version': 1, 'status': 'queued', 'enrollment': 'pending', 'password': '40213377'}))
'''

with tempfile.TemporaryDirectory(prefix='secureboot-settings-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    shutil.copyfile(ROOT / 'tests/fixtures/SecureBootSettingsTest.qml', base / 'shell/test.qml')
    for part in ('home', 'config', 'data', 'cache', 'state', 'bin'):
        (base / part).mkdir(mode=0o700)
    for command in ('emaki-secureboot', 'pkexec'):
        helper = base / 'bin' / command
        helper.write_text(HELPER)
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
    for mode in ('no-key', 'unknown', 'enrolled-on', 'signed-on', 'missing-on', 'missing-off', 'cancel', 'broken',
                 'unsigned-off', 'unsigned-on', 'unsigned-fail', 'unsigned-cancel', 'late', 'flaky', 'slow',
                 'appears'):
        # SECUREBOOT_SETTINGS_MODES=a,b runs only those cases.
        if os.environ.get('SECUREBOOT_SETTINGS_MODES') and mode not in os.environ['SECUREBOOT_SETTINGS_MODES'].split(','):
            continue
        (base / 'status.json').write_text(json.dumps(STATUS.get(mode, MISSING_OFF)))
        (base / 'calls.jsonl').unlink(missing_ok=True)
        (base / 'times.txt').unlink(missing_ok=True)
        shot = os.environ.get('SECUREBOOT_SETTINGS_SHOT', '') if mode == 'missing-off' else ''
        status_command = base / 'bin/emaki-secureboot'
        if mode == 'appears':
            # The command is not there when the session starts (it does not even start).
            status_command.unlink()
        process = subprocess.Popen([shutil.which('qs'), '-p', str(base / 'shell/test.qml'), '--no-color'],
                                   env=dict(env, SECUREBOOT_FIXTURE_MODE=mode, SECUREBOOT_SETTINGS_SHOT=shot),
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if mode == 'appears':
            time.sleep(1.5)
            status_command.write_text(HELPER)
            status_command.chmod(0o700)
        try:
            output, _ = process.communicate(timeout=60)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        result = subprocess.CompletedProcess(process.args, process.returncode, output, '')
        output = result.stdout + result.stderr
        assert result.returncode == 0 and re.search(r'SECUREBOOT_RESULT [1-9][0-9]* 0\b', output), (mode, output)
        for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
            assert error not in output, (mode, output)
        # The one-time password is shown on the page and nowhere else.
        assert '40213377' not in output, (mode, output)
        calls = [json.loads(line) for line in (base / 'calls.jsonl').read_text().splitlines()]
        privileged = [call for call in calls if call[0] == 'pkexec']
        if mode.startswith('unsigned-'):
            assert [call[2:] for call in privileged] == [['sign']], (mode, calls)
            assert calls[-1] == ['emaki-secureboot', 'status', '--json'], (mode, calls)
        elif mode in ('missing-off', 'cancel', 'broken'):
            assert len(privileged) == 1, (mode, calls)
            assert privileged[0][2:] == ['enroll', '--json'], (mode, calls)
            # The page reads the status again after every attempt.
            assert calls[-1] == ['emaki-secureboot', 'status', '--json'], (mode, calls)
        elif mode == 'late':
            # Both the card and notice retry the unknown answer once the copy is written.
            assert calls == [['emaki-secureboot', 'status', '--json']] * 4, (mode, calls)
        else:
            assert privileged == [], (mode, calls)
            assert all(call == ['emaki-secureboot', 'status', '--json'] for call in calls), (mode, calls)
        if mode == 'slow':
            # Two quick tries (100 ms), then 200, 400, 400 ms: past the quick tries the notice
            # keeps asking, more slowly.
            times = [float(line) for line in (base / 'times.txt').read_text().split()]
            gaps = [later - earlier for earlier, later in zip(times, times[1:])]
            assert len(times) == 7 and times[-1] - times[0] >= 1.0 and max(gaps) >= 0.35, (mode, gaps)
print('PASS: Secure Boot card and notice: hidden without the key, new confirmation, cancel and broken answers, '
      'unsigned startup files signed again, a status copy written after the session started, failed, '
      'malformed and missing reads asked again, a late copy asked for past the quick tries')
