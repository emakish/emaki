#!/usr/bin/env python3
"""Actual QML mouse events + emaki watch + fake niri overview; never Wayland."""
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
assert len(sys.argv) == 2, 'Pass the freshly built emaki binary'
BINARY = Path(sys.argv[1]).resolve(strict=True)
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'tests/fixtures'))
from niri_server import Server

profile = Path(tempfile.mkdtemp(prefix='sc-', dir=ROOT / '.cache'))
for part in ('config', 'state', 'cache', 'data', 'share', 'runtime', 'tmp'):
    (profile / part).mkdir(mode=0o700)
# Two apps for the tile bubble: a short name and a long one (XDG_DATA_DIRS, not the data
# home the shell must leave empty).
(profile / 'share/applications').mkdir()
for app_id, name in (('fixture-anki', 'Anki'), ('fixture-qvidcap', 'Qt V4L2 video capture utility')):
    (profile / 'share/applications' / f'{app_id}.desktop').write_text(
        f'[Desktop Entry]\nType=Application\nName={name}\nExec=/usr/bin/true\nTerminal=false\n')
shutil.copytree(ROOT / 'shell', profile / 'qml')
shutil.copyfile(ROOT / 'tests/fixtures/ShellCloseTest.qml', profile / 'qml/shell.qml')
env = dict(os.environ, EMAKI_BIN=str(BINARY), NIRI_SOCKET=str(runtime_path(profile) / 'n.sock'),
           QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_SCALE_FACTOR='1',
           QML_DISABLE_DISK_CACHE='1', XDG_CONFIG_HOME=str(profile / 'config'),
           XDG_STATE_HOME=str(profile / 'state'), XDG_DATA_HOME=str(profile / 'data'),
           XDG_DATA_DIRS=str(profile / 'share'), XDG_CACHE_HOME=str(profile / 'cache'),
           XDG_RUNTIME_DIR=str(runtime_path(profile)), TMPDIR=str(profile / 'tmp'),
           DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session'),
           DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'))
for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_SCREEN_SCALE_FACTORS', 'QT_LOGGING_RULES'):
    env.pop(name, None)
server = Server(runtime_path(profile) / 'n.sock')
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
log_path = profile / 'qs.log'
try:
    with log_path.open('w') as log:
        proc = subprocess.Popen(['qs', '-p', str(profile / 'qml'), '--no-color'], env=env,
                                stdout=log, stderr=subprocess.STDOUT)
        def call(*args):
            result = subprocess.run(['qs', 'ipc', '--pid', str(proc.pid), 'call', 'test', *map(str, args)],
                                    env=env, capture_output=True, text=True, timeout=3)
            assert result.returncode == 0, (args, result.stdout, result.stderr, log_path.read_text())
            return result.stdout.strip()
        def wait(predicate):
            deadline = time.monotonic() + 6
            last = None
            while time.monotonic() < deadline:
                assert proc.poll() is None, log_path.read_text()
                reply = subprocess.run(['qs', 'ipc', '--pid', str(proc.pid), 'call', 'test', 'status'],
                                       env=env, capture_output=True, text=True, timeout=3)
                if reply.returncode == 0:
                    try:
                        last = json.loads(reply.stdout)
                    except json.JSONDecodeError as error:
                        raise AssertionError((reply.stdout, reply.stderr, log_path.read_text())) from error
                    if predicate(last):
                        return last
                time.sleep(.02)
            raise AssertionError((last, log_path.read_text()))
        def settled(index):
            """The selection bubble on result `index` once its springs are at rest."""
            deadline = time.monotonic() + 6
            last, same = None, 0
            while same < 5:
                value = json.loads(call('status'))
                rect = value['launcher_glass']['select']
                assert time.monotonic() < deadline, (index, value['search'], rect)
                same = same + 1 if value['search']['selected_index'] == index and rect['alpha'] == 1 and rect == last else 0
                last = rect
                time.sleep(.03)
            return last
        def opened():
            return wait(lambda s: s['launcher'] == 'open' and s['expansion'] == 1)
        def closed():
            value = wait(lambda s: s['launcher'] == 'closed')
            assert value['query_length'] == 0 and value['search']['mode'] == 'Apps', value
            assert not value['input_focused'] and not value['extra_popup_open'], value
            return value
        try:
            wait(lambda s: s['niri']['connection'] == 'connected')
            call('click', 28, 26)  # Compact launcher button, actual Qt hit testing.
            opened()
            call('prepare')
            before = json.loads(call('status'))['close_count']
            call('click', 180, 40)  # Search field, not the logo.
            assert json.loads(call('status'))['launcher'] == 'open'
            call('click', 400, 105)  # Empty body space, not an actionable result.
            value = json.loads(call('status'))
            assert value['launcher'] == 'open' and value['close_count'] == before
            assert value['query_length'] == len('fixture') and value['search']['mode'] == 'Files'
            call('click', 40, 38)  # The arrow after its morph into the launcher header.
            closed()
            call('click', 28, 26)
            opened()
            call('prepare')
            server.overview(True)  # Real EventStream -> real CLI -> production NiriService.
            value = closed()
            assert value['niri']['overview_open'] is True
            count = value['close_count']
            server.overview(False)
            value = wait(lambda s: s['niri']['overview_open'] is False)
            assert value['launcher'] == 'closed' and value['close_count'] == count
            server.overview(False)
            time.sleep(.15)
            assert json.loads(call('status'))['close_count'] == count
            call('click', 28, 26)
            opened()
            assert call('mode', 'Settings') == 'false'
            # Apps: the selection bubble is one size on every tile, short name or long
            # (27.09: the bubble of "Qt V4L2 video capture utility" grew over the neighbours).
            # Panel at (10, 8) on the screen; Frequent tiles 676/6 x 76 from (22, 92), grid
            # cells 96 x 86 from (24, 240); the pointer on each tile, then its bubble at rest.
            call('frequent', 'fixture-anki,fixture-qvidcap')
            call('mode', 'Apps')
            wait(lambda s: s['search']['result_count'] == 4 and s['launcher_glass']['select_shape'] == 'tile')
            bubbles = []
            for index, centre, top in ((0, 22 + 676 / 12, 92), (1, 22 + 676 / 12 * 3, 92), (2, 24 + 48, 240), (3, 24 + 48 + 96, 240)):
                call('move', 10 + centre, 8 + top + 38)
                bubble = settled(index)
                assert abs(bubble['x'] + bubble['width'] / 2 - centre) <= 1 and bubble['y'] == top - 2, (index, bubble)
                bubbles.append(bubble)
            assert len({(b['width'], b['height']) for b in bubbles}) == 1, bubbles
            assert bubbles[0]['width'] <= 96, bubbles  # inside its grid cell, off the neighbours
            call('prepare')
            call('keyEscape')
            closed()
            call('race')
            closed()
            time.sleep(.1)
            assert json.loads(call('status'))['launcher'] == 'closed'
            assert not server.actions and not server.errors, (server.actions, server.errors)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
    contents = log_path.read_text()
    assert not any(w in contents for w in ('WARN', 'ERROR', 'FAIL!', 'PRIVATE', 'ReferenceError', 'TypeError')), contents
    assert all(not list((profile / part).rglob('*')) for part in ('config', 'state', 'data'))
finally:
    server.stop.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
print(f'Offscreen Qt clicks, search/body containment, hover bubbles (one tile size for short and long names), Esc, overview via real core, close-all hook/reset/race: OK; {profile.relative_to(ROOT)}')
