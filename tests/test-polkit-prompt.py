#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the production prompt controller with no polkit registration or profile access."""
import json
import os
from pathlib import Path
import resource
import re
import shutil
import socket
import subprocess
import tempfile
import time

from runtime_fixture import short_runtime
from polkit_service_fixture import exercise_headless, exercise_offscreen_service, exercise_service
import reaper
reaper.guard()

ROOT = Path(__file__).resolve().parent.parent
# Ten real 1050 ms input-guard waits plus lock/focus checks need over 12 seconds.
# Both transports run the same fixture; keep a bounded budget with runtime slack.
EXERCISE_TIMEOUT = 30


def no_core():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def scan_logs(qs, runtime, profile, env):
    """Detailed logs have their own filtering and encoding; raw bytes are insufficient."""
    secrets = ('PRIVATE_AUTH_FIXTURE_91', 'PRIVATE_COOKIE_FIXTURE_91')
    logs = list((Path(runtime) / 'quickshell' / 'by-id').glob('*/log.qslog'))
    assert logs, 'No detailed Quickshell logs were produced; secrecy check did not run'
    for path in logs:
        decoded = subprocess.run([qs, 'log', '--no-color', '-r', '*=true', str(path)],
                                 env=env, text=True, capture_output=True, timeout=10)
        assert decoded.returncode == 0, f'Could not decode detailed log: {path.name}'
        text = decoded.stdout + decoded.stderr
        assert not any(secret in text for secret in secrets), 'Credential appeared in decoded detailed log'
        assert not re.search(r'quickshell\.service\.polkit[^\n]*\bcookie\s*:', text, re.IGNORECASE), \
            'Native polkit cookie logging remains enabled; merge the native removal before shipping'
    for base in (Path(runtime), profile / 'cache', profile / 'state'):
        for path in base.rglob('*'):
            if path.is_file():
                assert not any(secret.encode() in path.read_bytes() for secret in secrets), \
                    f'Credential persisted in {path.name}'
    print(f'Credential scan: decoded {len(logs)} detailed Quickshell logs and checked runtime/cache/state')


def main():
    qs = shutil.which('qs')
    assert qs, 'qs is required'
    for source in (ROOT / 'shell').glob('Polkit*.qml'):
        text = source.read_text()
        if source.name == 'PolkitPromptService.qml':
            assert re.findall(r'function\s+(\w+)\s*\(', text) == ['lock'], 'Only lock cancellation may use authentication IPC'
        else:
            assert 'IpcHandler' not in text, f'Production authentication IPC in {source.name}'
    launcher = (ROOT / 'scripts/emaki-shell').read_text()
    assert "('WAYLAND_DEBUG', 'QML_IMPORT_TRACE', 'QV4_SHOW_BYTECODE', 'QV4_FORCE_INTERPRETER')" in launcher
    assert "env['QT_LOGGING_RULES'] = '*.debug=false'" in launcher
    assert 'resource.setrlimit(resource.RLIMIT_CORE, (0, 0))' in launcher
    with tempfile.TemporaryDirectory(prefix='polkit-test-') as temporary, short_runtime() as runtime:
        profile = Path(temporary)
        for name in ('cache', 'config', 'state', 'data', 'tmp', 'qml'):
            (profile / name).mkdir(mode=0o700)
        shutil.copytree(ROOT / 'shell', profile / 'qml', dirs_exist_ok=True)
        shutil.copyfile(ROOT / 'tests/fixtures/polkit-prompt.qml', profile / 'qml/shell.qml')
        platform = profile / 'qml/Platform.qml'
        marker = profile / 'sudo-key.installed'
        platform.write_text(re.sub(r'(readonly property string sudoKeyMarker:) [^\n]+',
                                   lambda match: match[1] + ' ' + json.dumps(str(marker)),
                                   platform.read_text()))
        env = dict(os.environ, HOME=str(profile), QT_QPA_PLATFORM='offscreen',
                   QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                   EMAKI_POLKIT_SELFTEST='1', XDG_RUNTIME_DIR=runtime,
                   XDG_CACHE_HOME=str(profile / 'cache'), XDG_CONFIG_HOME=str(profile / 'config'),
                   XDG_STATE_HOME=str(profile / 'state'), XDG_DATA_HOME=str(profile / 'data'),
                   XDG_DATA_DIRS=str(profile / 'data'), TMPDIR=str(profile / 'tmp'),
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session.sock'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system.sock'))
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH', 'QT_LOGGING_RULES'):
            env.pop(name, None)
        env['QT_LOGGING_RULES'] = '*.debug=false'
        sockets = True
        try:
            with socket.socket(socket.AF_UNIX) as probe:
                probe.bind(str(Path(runtime) / 'probe.sock'))
        except PermissionError:
            sockets = False
        if sockets:
            env['EMAKI_POLKIT_SELFTEST'] = '0'
            with (profile / 'output.log').open('w') as log:
                process = subprocess.Popen([qs, '-p', str(profile / 'qml'), '--no-color'],
                                           env=env, text=True, stdout=log, stderr=subprocess.STDOUT, preexec_fn=no_core)
                try:
                    deadline = time.monotonic() + 20
                    while True:
                        reply = subprocess.run([qs, 'ipc', '--pid', str(process.pid), 'call', 'tests', 'exercise'],
                                               env=env, text=True, capture_output=True, timeout=EXERCISE_TIMEOUT)
                        if reply.returncode == 0:
                            assert reply.stdout.strip() == 'POLKIT_PROMPT_OK', reply.stdout + reply.stderr
                            break
                        assert process.poll() is None and time.monotonic() < deadline, (profile / 'output.log').read_text()
                        time.sleep(.1)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
            output = (profile / 'output.log').read_text()
        else:
            result = subprocess.run([qs, '-p', str(profile / 'qml'), '--no-color'],
                                    env=env, text=True, capture_output=True, timeout=EXERCISE_TIMEOUT, preexec_fn=no_core)
            output = result.stdout + result.stderr
            # Only this exact optional listener failure is allowed in the sandbox.
            output = '\n'.join(line for line in output.splitlines()
                               if 'quickshell.ipc: Failed to start IPC server on path' not in line)
            print('BLOCKED: IPC sockets unavailable; in-process QML assertions executed')
            assert result.returncode == 0 and 'POLKIT_PROMPT_OK' in output, (result.returncode, output)
        assert 'PRIVATE_AUTH_FIXTURE_91' not in output, 'password appeared in process output'
        assert not any(word in output for word in ('ERROR', 'WARN', 'ReferenceError', 'TypeError')), output
        # Exercise the actual FileView singleton, including an absent marker.
        (profile / 'qml/shell.qml').write_text('''import QtQuick
import Quickshell
ShellRoot {
    property bool markerPresent: SudoKeySupport.installed
    SettingsPageRegistry { id: registry }
    Timer {
        interval: 250
        running: true
        onTriggered: {
            const expected = Quickshell.env("MARKER_EXPECTED") === "1";
            if (markerPresent !== expected || !registry.availablePages.includes("assistants") || !registry.definition("assistants")) {
                console.error("Marker visibility mismatch");
                Qt.exit(1);
            } else {
                console.info("MARKER_OK");
                Qt.quit();
            }
        }
    }
}
''')
        for installed in (False, True):
            if installed:
                marker.write_text('')
            marker_result = subprocess.run([qs, '-p', str(profile / 'qml'), '--no-color'],
                                           env=dict(env, MARKER_EXPECTED=str(int(installed))),
                                           text=True, capture_output=True, timeout=10, preexec_fn=no_core)
            assert marker_result.returncode == 0 and 'MARKER_OK' in marker_result.stdout + marker_result.stderr, marker_result.stdout + marker_result.stderr
        exercise_headless(ROOT, profile, env, qs)
        exercise_offscreen_service(ROOT, profile, env, qs)
        wayland = exercise_service(ROOT, profile, runtime, env, qs)
        scan_logs(qs, runtime, profile, env)
        print('Polkit controller: retry, timeout, dismissal, lock, replacement, input clearing and redaction passed')
        # The cookie scan can only find a cookie line after a real native request (a VM's pkexec).
        print('NOT PROVEN here: no native polkit request ran, so the cookie log check had nothing to find')
        if not wayland:
            print('BLOCKED: the production surfaces were not loaded on Wayland (see the BLOCKED line above); '
                  'the offscreen load covers the service, adapter and controller only')


if __name__ == '__main__':
    main()
