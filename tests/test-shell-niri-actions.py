#!/usr/bin/env python3
"""Regression: real CLI serialization -> production QML parser, fake niri only."""
from runtime_fixture import runtime_path
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
assert len(sys.argv) == 2, 'Pass the freshly built emaki binary (make check-shell builds it)'
BINARY = Path(sys.argv[1]).resolve(strict=True)
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)
PROFILE = Path(tempfile.mkdtemp(prefix='na-', dir=CACHE))
for part in ('config', 'state', 'data', 'cache', 'runtime', 'tmp', 'qml'):
    (PROFILE / part).mkdir(mode=0o700)
# The whole production shell: the harness also holds the system panel's keyboard page.
shutil.copytree(ROOT / 'shell', PROFILE / 'qml', dirs_exist_ok=True)
shutil.copy(ROOT / 'tests/fixtures/NiriActionsTest.qml', PROFILE / 'qml/shell.qml')
ENV = dict(os.environ, EMAKI_BIN=str(BINARY), NIRI_SOCKET=str(runtime_path(PROFILE) / 'n.sock'),
           QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
           XDG_CONFIG_HOME=str(PROFILE / 'config'), XDG_STATE_HOME=str(PROFILE / 'state'),
           XDG_DATA_HOME=str(PROFILE / 'data'), XDG_DATA_DIRS=str(PROFILE / 'data'),
           XDG_RUNTIME_DIR=str(runtime_path(PROFILE)), XDG_CACHE_HOME=str(PROFILE / 'cache'),
           TMPDIR=str(PROFILE / 'tmp'))
for key in ('WAYLAND_DISPLAY', 'DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'DBUS_SYSTEM_BUS_ADDRESS'):
    ENV.pop(key, None)


sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'tests/fixtures'))
from niri_server import Server, window


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
            # Requests in one tick: the second waits for the first action and still returns true.
            def arrivals(before, count, timeout=6):
                times = []
                deadline = time.monotonic() + timeout
                while len(times) < count and time.monotonic() < deadline:
                    while len(server.actions) > before + len(times):
                        times.append(time.monotonic())
                    time.sleep(.01)
                return times
            workspace_202 = {'FocusWorkspace': {'reference': {'Id': 202}}}
            before = len(server.actions)
            reply = ipc('burst', '202', '2', '0')
            assert reply.returncode == 0 and json.loads(reply.stdout) == [True, True], reply
            assert len(arrivals(before, 2)) == 2, server.actions[before:]
            wait_status(lambda value: value['action'] == 'confirmed')
            assert server.actions[before:] == [workspace_202, {'FocusWindow': {'id': 2}}], server.actions[before:]
            assert server.workspaces[1]['is_focused'] and server.windows[1]['is_focused']
            # Unanswered first action (confirmation timeout 1.5 s): the pending request starts only
            # after it ends, and a newer request replaces the pending one.
            server.set_mode('ignore')
            before = len(server.actions)
            reply = ipc('burst', '101', '2', '1')
            assert reply.returncode == 0 and json.loads(reply.stdout) == [True, True, True], reply
            times = arrivals(before, 3, timeout=5)
            assert len(times) == 2 and times[1] - times[0] > 1.3, (times, server.actions[before:])
            assert server.actions[before:] == [{'FocusWorkspace': {'reference': {'Id': 101}}},
                                               {'FocusWindow': {'id': 1}}], server.actions[before:]
            server.set_mode('apply')
            # The keyboard page's note speaks of the layout switch only: its own rejection shows,
            # a later action's (a close from "Close all windows") does not.
            wait_status(lambda value: value['action'] != 'pending')
            unconfirmed = 'The layout switch wasn’t confirmed.'
            server.set_mode('reject')
            assert ipc('keyboardLayout', '0').stdout.strip() == 'requested'
            wait_status(lambda value: value['action'] == 'rejected' and value['keyboard_note'] == unconfirmed)
            server.set_mode('apply')
            assert ipc('keyboardLayout', '0').stdout.strip() == 'requested'
            wait_status(lambda value: value['action'] == 'confirmed' and value['keyboard_note'] == '')
            assert server.layouts['current_idx'] == 0
            server.set_mode('reject')
            assert ipc('close', '2').stdout.strip() == 'true'
            last = wait_status(lambda value: value['action'] == 'rejected')
            assert last['keyboard_note'] == '', last
            server.set_mode('apply')
            # Dock "Close all windows" on windows that stay open (each close waits 1.5 s for niri),
            # then a click on a third window: the click waits for the close in flight only, not
            # for every close queued behind it; the remaining close still runs.
            wait_status(lambda value: value['action'] != 'pending')
            server.open_window(window(3, 101, False))
            wait_status(lambda value: value['windows'] == 3)
            server.set_mode('ignore')
            before = len(server.actions)
            reply = ipc('closeAllThenFocus', '1', '2', '3')
            assert reply.returncode == 0 and json.loads(reply.stdout) == [True, True, True], reply
            assert len(arrivals(before, 3, timeout=7)) == 3, server.actions[before:]
            assert server.actions[before:] == [{'CloseWindow': {'id': 1}}, {'FocusWindow': {'id': 3}},
                                               {'CloseWindow': {'id': 2}}], server.actions[before:]
            wait_status(lambda value: value['action'] == 'unconfirmed')
            server.set_mode('apply')
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
    contents = (PROFILE / 'qs.log').read_text()
    assert all(marker not in contents for marker in ('WARN', 'ERROR', 'PRIVATE')), contents


server = Server(runtime_path(PROFILE) / 'n.sock')
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
