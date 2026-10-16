#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The session's Secure Boot notices are seen: with Welcome open the preview waits and appears
when Welcome closes; it stays (no timer, back after the drawer, a battery warning or the end of
a preview) until Open Settings or its close; Do not disturb does not hide it; it is never saved
with the history, so the next login raises it again while the problem remains and a fixed
problem leaves no stale copy. The card shows the whole text in the preview and the drawer.
Ordinary notices: a body that fits keeps its one-line row, a longer one wraps to three lines.

SECUREBOOT_NOTICE_SHOTS=<directory> also saves the preview and the drawer, dark and light."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)

MISSING_OFF = {'schema_version': 1, 'secure_boot': False, 'key': True, 'enrollment': 'missing', 'signed': {}}
UNSIGNED = {'schema_version': 1, 'secure_boot': False, 'key': True, 'enrollment': 'enrolled',
            'signed': {'/boot/vmlinuz-linux': False, '/boot/vmlinuz-linux-lts': True}}
ENROLLED = {'schema_version': 1, 'secure_boot': True, 'key': True, 'enrollment': 'enrolled',
            'signed': {'/boot/vmlinuz-linux': True}}
HELPER = '''#!/usr/bin/env python3
import os, sys
from pathlib import Path
assert sys.argv[1:] == ['status', '--json'], sys.argv
print(Path(os.environ['FIXTURE_ROOT'], 'status.json').read_text(), flush=True)
'''

shots = os.environ.get('SECUREBOOT_NOTICE_SHOTS', '')
if shots:
    shots = str(Path(shots).resolve())
    Path(shots).mkdir(parents=True, exist_ok=True)

with tempfile.TemporaryDirectory(prefix='sb-notice-', dir=CACHE) as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'qml')
    shutil.copyfile(ROOT / 'tests/fixtures/SecureBootNoticeTest.qml', base / 'qml/shell.qml')
    for part in ('home', 'config', 'data', 'cache', 'state', 'bin', 'tmp'):
        (base / part).mkdir(mode=0o700)
    helper = base / 'bin/emaki-secureboot'
    helper.write_text(HELPER)
    helper.chmod(0o700)
    (base / 'bin/python3').symlink_to(shutil.which('python3'))
    # The real wallpaper behind the glass, for pictures a person can judge.
    wallpaper = ROOT / 'art/wallpaper/fallback.png'
    (base / 'config/wpaperd').mkdir()
    (base / 'config/wpaperd/config.toml').write_text(
        '[default]\npath = ' + json.dumps(str(wallpaper)) + '\nmode = "center"\n')
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_DATA_HOME=str(base / 'data'), XDG_DATA_DIRS=str(base / 'data'),
               XDG_CACHE_HOME=str(base / 'cache'), XDG_STATE_HOME=str(base / 'state'),
               XDG_RUNTIME_DIR=str(runtime_path(base)), TMPDIR=str(base / 'tmp'),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_SCALE_FACTOR='1',
               QML_DISABLE_DISK_CACHE='1', PYTHONDONTWRITEBYTECODE='1',
               PATH=str(base / 'bin'), FIXTURE_ROOT=str(base),
               EMAKI_BIN='', EMAKI_SETTINGS_PROFILE='', EMAKI_SHELL_NOTIFICATIONS='0',
               EMAKI_SHELL_TRAY='0', EMAKI_TEST_SYSTEM='0', EMAKI_TEST_MPRIS='0',
               EMAKI_SHELL_GLASS_RENDERER='canvas', NIRI_SOCKET='',
               NOTICE_FIXTURE_WALLPAPER=wallpaper.as_uri(), NOTICE_FIXTURE_SHOTS='',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_QPA_PLATFORMTHEME', 'QT_SCREEN_SCALE_FACTORS',
                'QT_LOGGING_RULES', 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH'):
        env.pop(key, None)
    saved = base / 'state/emaki/notifications.json'
    failures = []

    def run(mode, status, theme='dark', long_wait=False, pictures=False, keep=False):
        (base / 'status.json').write_text(json.dumps(status))
        if not keep:
            saved.unlink(missing_ok=True)
        process = subprocess.Popen([shutil.which('qs'), '-p', str(base / 'qml'), '--no-color'],
                                   env=dict(env, NOTICE_FIXTURE_MODE=mode, NOTICE_FIXTURE_THEME=theme,
                                            NOTICE_FIXTURE_LONG='1' if long_wait else '0',
                                            NOTICE_FIXTURE_SHOTS=shots if pictures else ''),
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   start_new_session=True)
        try:
            output, _ = process.communicate(timeout=60)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
        bad = [error for error in ('NOTICE FAIL', 'ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign',
                                   'Cannot assign', 'is not a type', 'non-existent property') if error in output]
        if process.returncode != 0 or f'NOTICE PASS {mode}' not in output or bad:
            # Every scenario runs; all failures are reported at the end.
            reason = [line.strip() for line in output.splitlines() if 'NOTICE FAIL' in line or 'ERROR' in line]
            failures.append(f'{mode} {theme}: ' + ('; '.join(reason) or output[-2000:]))
            print('FAIL', failures[-1])
            return
        print(mode, theme, ' / '.join(line.split('NOTICE ', 1)[1] for line in output.splitlines()
                                      if 'NOTICE CARD' in line or 'NOTICE WRAP' in line))

    def history():
        return json.loads(saved.read_text()) if saved.exists() else {'entries': []}

    # Welcome open at login; the preview stays past the 15 s preview timer (dark).
    run('welcome', MISSING_OFF, long_wait=True, pictures=True)
    run('welcome', MISSING_OFF, theme='light', pictures=True)
    run('unsigned', UNSIGNED, pictures=True)
    run('unsigned', UNSIGNED, theme='light', pictures=True)
    # Do not disturb on: still previewed; its close dismisses it for the session.
    run('dismiss', MISSING_OFF)
    # A cache written before the notices were held (version 1, as 0.8.1 and the first 0.9
    # builds wrote it): both old warnings and their application row, beside an unrelated entry.
    def legacy_cache():
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text(json.dumps({'version': 1, 'dnd': False, 'dndMigrated': False, 'applications': [
            {'id': 'name:Secure Boot', 'name': 'Secure Boot'}, {'id': 'name:Fixture', 'name': 'Fixture'}], 'entries': [
            {'app': 'Secure Boot', 'summary': "This computer's Secure Boot key is not confirmed.",
             'body': 'Open Settings to get a one-time password and confirm the key at the next restart.',
             'time': 1790200100000},
            {'app': 'Secure Boot', 'summary': 'Emaki will not start with Secure Boot on.',
             'body': "An update could not sign this computer's startup files. Free some disk space, then "
                     'open Settings and choose Sign again.', 'time': 1790200050000},
            {'app': 'Fixture', 'summary': 'Older notice', 'body': '', 'time': 1790200000000}]}))
    # Sign out without acting, sign in again: one notice again, previewed again; never saved; the
    # old copies are gone, the unrelated entry stays.
    legacy_cache()
    for _ in range(2):
        run('login', MISSING_OFF, keep=True)
        summaries = [entry['summary'] for entry in history()['entries']]
        if 'Older notice' not in summaries or any(entry['app'] == 'Secure Boot' for entry in history()['entries']):
            failures.append(f'history after login: {summaries}')
    # The key confirmed since: nothing at the next login, and no stale copy, also from an old cache.
    run('fixed', ENROLLED, keep=True)
    legacy_cache()
    run('fixed', ENROLLED, keep=True)
    # The other warning unresolved, from an old cache: one card, the new one.
    legacy_cache()
    run('login', UNSIGNED, keep=True)
    if any(entry['app'] == 'Secure Boot' for entry in history()['entries']):
        failures.append(f"history after login: {[entry['summary'] for entry in history()['entries']]}")
    # Ordinary notices: one-line rows unchanged, long bodies on three lines.
    run('wrap', MISSING_OFF)
    assert not failures, '\n'.join(failures)
print('PASS: Secure Boot notice previews after Welcome, stays until acted on or dismissed, returns at '
      'each login while the problem remains, whole text in the preview and the drawer; ordinary '
      'one-line rows unchanged, long bodies wrap to three lines')
