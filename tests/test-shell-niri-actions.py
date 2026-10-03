#!/usr/bin/env python3
"""Regression: real CLI serialization -> production QML parser, fake niri only."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
assert len(sys.argv) == 2, 'Pass the freshly built emaki binary (make check-shell builds it)'
BINARY = Path(sys.argv[1]).resolve(strict=True)
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)
PROFILE = Path(tempfile.mkdtemp(prefix='na-', dir=CACHE))
for part in ('config', 'state', 'data', 'cache', 'runtime', 'tmp', 'qml'):
    (PROFILE / part).mkdir(mode=0o700)
shutil.copy(ROOT / 'shell/NiriService.qml', PROFILE / 'qml/NiriService.qml')
shutil.copy(ROOT / 'tests/fixtures/NiriActionsTest.qml', PROFILE / 'qml/shell.qml')
(PROFILE / 'qml/qmldir').write_text('NiriService 1.0 NiriService.qml\n')
ENV = dict(os.environ, EMAKI_BIN=str(BINARY), NIRI_SOCKET=str(PROFILE / 'n.sock'),
           QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
           XDG_CONFIG_HOME=str(PROFILE / 'config'), XDG_STATE_HOME=str(PROFILE / 'state'),
           XDG_DATA_HOME=str(PROFILE / 'data'), XDG_DATA_DIRS=str(PROFILE / 'data'),
           XDG_RUNTIME_DIR=str(PROFILE / 'runtime'), XDG_CACHE_HOME=str(PROFILE / 'cache'),
           TMPDIR=str(PROFILE / 'tmp'))
for key in ('WAYLAND_DISPLAY', 'DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'DBUS_SYSTEM_BUS_ADDRESS'):
    ENV.pop(key, None)


sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'tests/fixtures'))
from niri_server import Server


def command(server, args, code, outcome):
    result = subprocess.run([str(BINARY), 'niri', *args, '--json', '--timeout-ms', '100'],
                            env=ENV, capture_output=True, text=True, timeout=3)
    assert not server.errors, server.errors
    assert result.returncode == code, (args, result.returncode, result.stderr)
    assert result.stderr == '', result.stderr
    # This fails on the old pretty JSON, before any substitute QML/helper can mask it.
    assert len(result.stdout.splitlines()) == 1, ('action JSON must be one line', args, len(result.stdout.splitlines()))
    assert result.stdout.endswith('\n')
    value = json.loads(result.stdout)
    assert value['schema_version'] == 1 and value['outcome'] == outcome, value
    assert 'PRIVATE' not in result.stdout
    return value


def qml_check(server):
    qs = shutil.which('qs')
    assert qs, 'qs required'
    with (PROFILE / 'qs.log').open('w') as log:
        proc = subprocess.Popen([qs, '-p', str(PROFILE / 'qml'), '--no-color'], env=ENV,
                                stdout=log, stderr=subprocess.STDOUT)
        def ipc(*args):
            return subprocess.run([qs, 'ipc', '--pid', str(proc.pid), 'call', 'test', *args],
                                  env=ENV, capture_output=True, text=True, timeout=3)
        def wait_status(predicate):
            deadline = time.monotonic() + 5
            last = None
            while time.monotonic() < deadline:
                assert proc.poll() is None, (PROFILE / 'qs.log').read_text()
                reply = ipc('status')
                if reply.returncode == 0:
                    last = json.loads(reply.stdout)
                    if predicate(last):
                        return last
                time.sleep(.02)
            raise AssertionError(('QML action result did not arrive', last, (PROFILE / 'qs.log').read_text()))
        def action(kind, ident, expected, reason):
            before = len(server.actions)
            reply = ipc(kind, str(ident))
            assert reply.returncode == 0 and reply.stdout.strip() == 'true', reply
            wait_status(lambda value: len(server.actions) == before + 1 and value['action'] == expected and value['reason'] == reason)
        try:
            wait_status(lambda value: value['connection'] == 'connected')
            action('window', 2, 'confirmed', 'postcondition_observed')
            assert server.windows[1]['is_focused']
            action('workspace', 202, 'confirmed', 'postcondition_observed')
            assert server.workspaces[1]['is_focused']
            server.set_mode('reject')
            action('window', 1, 'rejected', 'request_rejected')
            server.set_mode('ignore')
            action('workspace', 101, 'unconfirmed', 'confirmation_timeout')
            server.set_mode('apply')
            action('window', 1, 'confirmed', 'postcondition_observed')
            action('workspace', 101, 'confirmed', 'postcondition_observed')
            action('layout', 1, 'confirmed', 'postcondition_observed')
            wait_status(lambda value: value['layout'] == 'RU')
            # An index outside the current model never reaches the CLI.
            before_layouts = len(server.actions)
            reply = ipc('layout', '7')
            assert reply.stdout.strip() == 'false' and len(server.actions) == before_layouts
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
    contents = (PROFILE / 'qs.log').read_text()
    assert all(marker not in contents for marker in ('WARN', 'ERROR', 'PRIVATE')), contents


server = Server(PROFILE / 'n.sock')
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    command(server, ['focus-window', '--id', '2'], 0, 'confirmed')
    command(server, ['focus-workspace', '--id', '202'], 0, 'confirmed')
    command(server, ['move-window-to-workspace', '--id', '1', '--workspace-id', '202'], 0, 'confirmed')
    command(server, ['close-window', '--id', '2'], 0, 'confirmed')
    command(server, ['switch-layout', '--index', '1'], 0, 'confirmed')
    assert server.layouts['current_idx'] == 1 and server.actions[-1] == {'SwitchLayout': {'layout': {'Index': 1}}}
    absent = command(server, ['switch-layout', '--index', '2'], 1, 'rejected')
    assert absent['delivery'] == 'not_sent' and absent['reason'] == 'layout_not_found' and absent['before']['layout_exists'] is False
    rejected = command(server, ['close-window', '--id', '2'], 1, 'rejected')
    assert rejected['delivery'] == 'not_sent' and rejected['reason'] == 'window_not_found'
    server.set_mode('ignore')
    command(server, ['focus-window', '--id', '1'], 3, 'unconfirmed')
    server.reset()
    qml_check(server)
    assert not server.errors, server.errors
    assert all(not list((PROFILE / part).rglob('*')) for part in ('config', 'state', 'data'))
finally:
    server.stop.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
print(f'Real emaki + fake niri + production QML: compact JSON, confirmed/rejected/unconfirmed OK; {PROFILE.relative_to(ROOT)}')
