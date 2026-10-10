#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings lifecycle and real input events in a private, service-free session."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / '.cache/evidence/ls1b'
EVIDENCE.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='settings-launcher-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    # The full shell copy includes the page registry and definition components.
    for name in ('SettingsPageRegistry.qml', 'SettingsPageDefinition.qml'):
        assert (base / 'shell' / name).is_file(), name
    helpers = base / 'helpers'
    helpers.mkdir()
    # Opening every section must not invoke installed machine helpers.
    for name in ('emaki-settings-apps', 'emaki-machine-settings', 'emaki-settings-about',
                 'emaki-settings-power', 'emaki-update-channel'):
        helper = helpers / name
        helper.write_text('#!/bin/sh\nexit 1\n')
        helper.chmod(0o700)
    for name in ('SettingsLauncherTest.qml', 'SettingsIpcHarness.qml'):
        shutil.copyfile(ROOT / 'tests/fixtures' / name, base / 'shell' / name)
    with (base / 'shell/qmldir').open('a') as stream:
        stream.write('\nSettingsIpcHarness 1.0 SettingsIpcHarness.qml\n')
    for part in ('home', 'config', 'data', 'cache', 'state'):
        (base / part).mkdir(mode=0o700)
    runtime = runtime_path(base)
    env = dict(os.environ, HOME=str(base / 'home'), USER='Emaki',
               PATH=str(helpers) + os.pathsep + os.environ.get('PATH', ''),
               XDG_CONFIG_HOME=str(base / 'config'), XDG_CONFIG_DIRS=str(base / 'config'),
               XDG_DATA_HOME=str(base / 'data'), XDG_DATA_DIRS=str(base / 'data'),
               XDG_CACHE_HOME=str(base / 'cache'), XDG_STATE_HOME=str(base / 'state'),
               XDG_RUNTIME_DIR=str(runtime), QT_QPA_PLATFORM='offscreen',
               QT_QUICK_BACKEND='software', QT_SCALE_FACTOR='1',
               QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
               EMAKI_BIN='', EMAKI_SHELL_TRAY='0', SETTINGS_SHOT_DIR=str(EVIDENCE),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME',
                'QT_SCREEN_SCALE_FACTORS', 'QT_LOGGING_RULES'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/SettingsLauncherTest.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=45)
    output = result.stdout + result.stderr
    (EVIDENCE / 'settings-launcher.log').write_text(output)
    assert result.returncode == 0 and 'SETTINGS_LAUNCHER_RESULT 15 0' in output, output
    for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
        assert error not in output, output
    for name in ('settings-launcher.png', 'settings-search.png', 'settings-narrow.png', 'launcher-normal.png',
                 'launcher-output.png', 'settings-output.png', 'settings-grow-mid.png',
                 'settings-shrink-mid.png', 'settings-close-mid.png', 'settings-1280x800.png',
                 'settings-1024x640.png'):
        assert (EVIDENCE / name).stat().st_size > 1000, name
    print('PASS: settings lifecycle, all 17 pages, search anchors, dynamic reset, input, history and screenshots')

    # Environments without Unix sockets still run every in-process QML assertion.
    probe = socket.socket(socket.AF_UNIX)
    try:
        probe.bind(str(runtime / 'probe.sock'))
    except PermissionError as error:
        print('NOT PROVEN: settings IPC; Unix socket binding is blocked:', error)
        raise SystemExit(77)
    else:
        shutil.copyfile(base / 'shell/SettingsIpcHarness.qml', base / 'shell/shell.qml')
        env['EMAKI_SHELL_DIR'] = str(base / 'shell')
        log_path = EVIDENCE / 'settings-ipc.log'
        with log_path.open('w') as log:
            process = subprocess.Popen(['qs', '-p', str(base / 'shell'), '--no-color'],
                                       env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                def call(method, *args):
                    reply = subprocess.run([str(ROOT / 'scripts/emaki-shell'), 'call',
                                            'settings', method, *args], env=env,
                                           capture_output=True, text=True, timeout=3)
                    assert reply.returncode == 0, (reply.stdout, reply.stderr, log_path.read_text())
                    return json.loads(reply.stdout)

                deadline = time.monotonic() + 6
                while True:
                    assert process.poll() is None, log_path.read_text()
                    try:
                        status = call('status')
                        break
                    except (AssertionError, json.JSONDecodeError):
                        assert time.monotonic() < deadline, log_path.read_text()
                        time.sleep(.03)
                assert status == {'schema_version': 1, 'opened': False, 'page': 'panel'}, status
                assert call('open', '') == {'schema_version': 1, 'status': 'opened', 'page': 'panel'}
                assert call('status')['opened'] is True
                assert call('open', 'windows')['page'] == 'windows'
                assert call('open', 'appearance')['status'] == 'unavailable'
                assert call('status')['page'] == 'windows'
                assert call('close')['status'] == 'closed'
                assert call('status')['opened'] is False
                assert call('open', 'panel')['status'] == 'opened'
                assert call('close')['status'] == 'closed'
                print('PASS: real settings open/close/status IPC')
            finally:
                process.terminate()
                process.wait(timeout=5)
    finally:
        probe.close()
