#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lists in helper replies reach the shell on the shipped Qt.

A PrivateJob hands its reply to the handlers through `signal completed(var)`. With
quickshell-emaki 0.3.1-12 on Qt 6.12 every list inside that argument arrives as a QVariantList
sequence, for which Array.isArray is false (Qt 6.11 gives a plain Array). Handlers that checked
Array.isArray dropped the list: the launcher's Recent stayed empty and a failed lock before
sleep was never told. Runs the production RecentLog and SystemService with their real helpers
on the qs in PATH (tests/test-quickshell-fork.py checks that it is the packaged one)."""
from runtime_fixture import runtime_path
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
LIMIT = 15
qs = shutil.which('qs')
assert qs, 'no qs in PATH'

with tempfile.TemporaryDirectory(prefix='emaki-private-job-lists-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    (base / 'shell/private-job-lists-test.qml').write_text(
        (ROOT / 'tests/fixtures/PrivateJobListsTest.qml').read_text())
    for name in ('home', 'config', 'cache', 'state', 'data', 'bin'):
        (base / name).mkdir(mode=0o700)
    (base / 'state/emaki').mkdir(mode=0o700)
    (base / 'state/emaki/recent.json').write_text(json.dumps(
        [dict(kind='app', ref='org.example.First', t=2), dict(kind='file', ref='/fixture/note.txt', t=1)]))
    runtime = runtime_path(base)
    # What scripts/emaki-sleep-guard leaves when the lock before sleep failed in this session.
    (Path(runtime) / 'emaki-sleep-lock-failed').write_text('sleep-relock\n')
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime),
               XDG_CURRENT_DESKTOP='niri', EMAKI_PYTHON=sys.executable,
               # The helpers find no brightnessctl, nmcli or other host tool: read-only answers.
               PATH=str(base / 'bin'),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
        env.pop(key, None)
    shell = subprocess.Popen([qs, '-p', str(base / 'shell/private-job-lists-test.qml'), '--no-color'],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = queue.Queue()
    log = []

    def read():
        for line in shell.stdout:
            log.append(line)
            lines.put(line)

    threading.Thread(target=read, daemon=True).start()
    seen = {'recent': None, 'sleep': []}
    try:
        end = time.monotonic() + LIMIT
        while time.monotonic() < end and not (seen['recent'] and seen['sleep']):
            try:
                line = lines.get(timeout=max(.01, end - time.monotonic()))
            except queue.Empty:
                break
            if 'RECENT ' in line:
                seen['recent'] = line.split('RECENT ', 1)[1].split()
            if 'SLEEP_LOCK ' in line:
                seen['sleep'].append(line.split('SLEEP_LOCK ', 1)[1].strip())
        time.sleep(.5)  # a second notice would arrive now
        while not lines.empty():
            line = lines.get()
            if 'SLEEP_LOCK ' in line:
                seen['sleep'].append(line.split('SLEEP_LOCK ', 1)[1].strip())
    finally:
        shell.kill()
        shell.wait()
    version = subprocess.run([qs, '--version'], capture_output=True, text=True).stdout.strip()
    tail = ''.join(log[-40:])
    failures = []
    if seen['recent'] != ['ready', '2']:
        failures.append(f'Recent lost the helper\'s list: {seen["recent"]} (want ready 2)')
    if seen['sleep'] != ['sleep-relock']:
        failures.append(f'the sleep-lock notice lost the helper\'s list: {seen["sleep"]} '
                        '(want exactly one sleep-relock)')
    assert not failures, f'on {qs} ({version}):\n' + '\n'.join(failures) + '\n' + tail
print(f'PASS: helper lists reach Recent and the sleep-lock notice on {qs} ({version})')
