#!/usr/bin/env python3
"""Dock: real core + fake niri with app IDs, actual Qt clicks/drags, dock.json persistence; never Wayland."""
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
from app_scope_fixture import install, launches
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
assert len(sys.argv) == 2, 'Pass the freshly built emaki binary'
BINARY = Path(sys.argv[1]).resolve(strict=True)
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'tests/fixtures'))
from niri_server import Server, window, workspace

profile = Path(tempfile.mkdtemp(prefix='dk-', dir=ROOT / '.cache'))
for part in ('config', 'state', 'cache', 'data', 'runtime', 'tmp', 'bin'):
    (profile / part).mkdir(mode=0o700)
shutil.copytree(ROOT / 'shell', profile / 'qml')
shutil.copyfile(ROOT / 'tests/fixtures/DockTest.qml', profile / 'qml/shell.qml')
apps = profile / 'data/applications'
apps.mkdir()
for name in ('Browser', 'Editor', 'Files'):
    (apps / f'fixture-{name.lower()}.desktop').write_text(
        f'[Desktop Entry]\nType=Application\nName={name}\nExec=/usr/bin/true\nTerminal=false\n')
# An ID that itself ends in ".desktop" (org.telegram.desktop): gtk-launch must get the full file name.
(apps / 'org.fixture.desktop.desktop').write_text(
    '[Desktop Entry]\nType=Application\nName=Dotted\nExec=/usr/bin/true\nTerminal=false\n')
# Windows whose app_id names no desktop file: a Steam game (steam_app_<id>) and a tool whose
# Exec file name is the app_id, as Electron and wine programs report themselves.
(apps / 'fixture-game.desktop').write_text(
    '[Desktop Entry]\nType=Application\nName=Game\nExec=steam steam://rungameid/42\nTerminal=false\nCategories=Game;\n')
(apps / 'fixture-suite.desktop').write_text(
    '[Desktop Entry]\nType=Application\nName=Suite\nExec=/opt/suite/bin/suite-tool %U\nTerminal=false\n')
# Fake gtk-launch records the desktop ID only; the real program is a live check.
launcher = profile / 'bin/gtk-launch'
# "slow" takes a moment (the icon pulses meanwhile); "fail" exits 2 with a stderr line like gtk-launch.
launcher.write_text("#!/usr/bin/env python3\nimport json,os,sys,time\nfrom pathlib import Path\n"
                    "Path(os.environ['EMAKI_SHELL_FIXTURE'], 'launch.json').write_text(json.dumps(sys.argv[1:]))\n"
                    "if 'slow' in sys.argv[-1]: time.sleep(0.8)\n"
                    "if 'fail' in sys.argv[-1]:\n    print('gtk-launch: no such application ' + sys.argv[-1], file=sys.stderr); sys.exit(2)\n")
for name in ('slow', 'fail'):
    (apps / f'fixture-{name}.desktop').write_text(
        f'[Desktop Entry]\nType=Application\nName={name.title()}\nExec=/usr/bin/true\nTerminal=false\n')
launcher.chmod(0o700)
env = dict(os.environ, EMAKI_BIN=str(BINARY), NIRI_SOCKET=str(runtime_path(profile) / 'n.sock'),
           EMAKI_GTK_LAUNCH=str(launcher), EMAKI_SHELL_FIXTURE=str(profile),
           EMAKI_SHELL_DIR=str(profile / "qml"), PATH=str(ROOT / "scripts") + os.pathsep + os.environ["PATH"],
           QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_SCALE_FACTOR='1',
           QML_DISABLE_DISK_CACHE='1', PYTHONDONTWRITEBYTECODE='1', EMAKI_SETTINGS_PROFILE='',
           EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_TRAY='0', EMAKI_TEST_MPRIS='0',
           XDG_CONFIG_HOME=str(profile / 'config'), XDG_STATE_HOME=str(profile / 'state'),
           XDG_DATA_HOME=str(profile / 'data'), XDG_DATA_DIRS=str(profile / 'data'),
           XDG_CACHE_HOME=str(profile / 'cache'), XDG_RUNTIME_DIR=str(runtime_path(profile)),
           TMPDIR=str(profile / 'tmp'),
           DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session'),
           DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'))
for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_SCREEN_SCALE_FACTORS', 'QT_LOGGING_RULES'):
    env.pop(name, None)
install(profile, env)
# niri 26.04 sends no tile_pos_in_workspace_view for tiled windows; a fullscreen tile is output-sized.
TILE = dict(tile_size=(700., 600.))
def windows(editor=True, cover=False):
    rows = [window(1, 101, True, 'fixture-browser', 'PRIVATE_BROWSER', **TILE),
            window(2, 202, False, 'PRIVATE_UNKNOWN_APP', 'PRIVATE_TITLE_2', **TILE)]
    if editor:
        rows += [window(3, 101, False, 'fixture-editor', 'PRIVATE_EDITOR_3', **TILE),
                 window(4, 101, False, 'fixture-editor', 'PRIVATE_EDITOR_4', **TILE)]
    if cover:
        rows.append(window(9, 101, False, 'fixture-browser', 'PRIVATE_FULL', tile_size=(1536., 960.)))
    return rows
server = Server(runtime_path(profile) / 'n.sock')
server.logical = dict(x=0, y=0, width=1536, height=960, scale=1.0, transform='Normal')
with server.lock:
    server.windows = windows()
    server.workspaces = [workspace(101, 1, True, 1), workspace(202, 2, False, 2)]
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
log_path = profile / 'qs.log'
DOCK_H, EDGE_Y, PLATE_Y = 66, 959, 960 - 77  # reveal strip: the 2 px at the screen edge (958..960)


def run(check):
    with log_path.open('a') as log:
        proc = subprocess.Popen(['qs', '-p', str(profile / 'qml'), '--no-color'], env=env,
                                stdout=log, stderr=subprocess.STDOUT)
        def call(*args):
            result = subprocess.run(['qs', 'ipc', '--pid', str(proc.pid), 'call', *map(str, args)],
                                    env=env, capture_output=True, text=True, timeout=3)
            assert result.returncode == 0, (args, result.stdout, result.stderr, log_path.read_text())
            return result.stdout.strip()
        def state():
            return json.loads(call('test', 'status'))
        def wait(predicate, seconds=6):
            deadline = time.monotonic() + seconds
            last = None
            while time.monotonic() < deadline:
                assert proc.poll() is None, log_path.read_text()
                reply = subprocess.run(['qs', 'ipc', '--pid', str(proc.pid), 'call', 'test', 'status'],
                                       env=env, capture_output=True, text=True, timeout=3)
                if reply.returncode == 0:
                    last = json.loads(reply.stdout)
                    if predicate(last):
                        return last
                time.sleep(.02)
            raise AssertionError((last, log_path.read_text()))
        def center(s, key):
            rect = s['item_rects'][[k for k in s['dock_keys'] if k != 'sep'].index(key)]
            return rect['x'] + rect['width'] / 2, rect['y'] + rect['height'] / 2
        try:
            check(call, state, wait, center)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)


def first(call, state, wait, center):
    s = wait(lambda s: s['niri']['connection'] == 'connected' and s['dock']['labels'] == 'ready'
             and len(s['dock']['entries']) == 3)
    assert call("test", "timing") == "ok"
    d = s['dock']
    assert d['on'] and d['auto_hide'] and d['pinned'] == []
    assert not d['visible'] and d['reserve'] == 0 and d['edge_enabled'], d
    # Running unpinned apps in first-window order; unresolved app IDs stay anonymous in status.
    assert s['dock_keys'] == ['fixture-browser', 'app:PRIVATE_UNKNOWN_APP', 'fixture-editor'], s['dock_keys']
    assert d['entries'] == [dict(id=None, pinned=False, source='exact', windows=1), dict(id=None, pinned=False, source='', windows=1),
                            dict(id=None, pinned=False, source='exact', windows=2)], d['entries']
    assert d['learned'] == 0
    assert s['dock_windows'] == [[1], [2], [3, 4]]
    assert 'PRIVATE' not in call('dock', 'status')
    assert call('dock', 'pin', 'fixture-files') == 'true'
    assert call('dock', 'pin', 'fixture-files') == 'false'
    assert call('dock', 'pin', 'bad/id') == 'false'
    s = wait(lambda s: s['dock_keys'] == ['fixture-files', 'sep', 'fixture-browser', 'app:PRIVATE_UNKNOWN_APP', 'fixture-editor'])
    assert s['dock']['entries'][0] == dict(id='fixture-files', pinned=True, source='', windows=0)
    assert s['dock']['pinned'] == ['fixture-files']
    wait(lambda s: (profile / 'state/emaki/dock.json').exists(), 3)
    saved = json.loads((profile / 'state/emaki/dock.json').read_text())
    assert saved == dict(version=1, on=True, auto_hide=True, pinned=['fixture-files']), saved
    # Pinned dock: reserve 77, centered, 11 px above the bottom edge (the 84 px bubble clears it by 2 px).
    call('dock', 'autoHide', 'false')
    s = wait(lambda s: s['dock']['visible'] and s['dock']['rect']['y'] == PLATE_Y)
    assert s['dock']['reserve'] == 77 and not s['dock']['edge_enabled']
    width = 2 * 8 + 4 * 54 - 4 + 13  # pitch 54 (4 px gaps), separator gap + 9
    assert s['dock']['rect'] == dict(x=(1536 - width) / 2, y=PLATE_Y, width=width, height=DOCK_H), s['dock']['rect']
    # The surface reaches 480 px above the 77 px band: the menu and tooltip grow inside it.
    assert s['dock']['origin'] == dict(x=0, y=960 - 77 - 480)
    # Hover: the hint appears above the dock; an unpinned app with a desktop entry says how to pin it.
    call('test', 'hover', *center(s, 'fixture-browser'))
    s = wait(lambda s: s['dock_tip'] == 'Browser · Right-click to pin')
    assert s['tip_rect']['y'] + s['tip_rect']['height'] <= PLATE_Y, s['tip_rect']  # dock.js: 62 px above the icon centre
    call('test', 'hover', 700, 400)
    # The tip hides on the next event-loop turn, not synchronously: under a loaded
    # check-shell an immediate read raced it (27.09).
    wait(lambda s: s['dock_tip'] == '')
    # No windows → launch through the catalog (fake gtk-launch), one window → focus via the core.
    call('test', 'click', *center(s, 'fixture-files'))
    s = wait(lambda s: s['search']['launch'] == 'requested' and not s['search']['launch_busy'])
    assert json.loads((profile / 'launch.json').read_text()) == ['--', 'fixture-files.desktop']
    assert s['dock']['popup'] == 'closed'
    # A dotted ID (org.fixture.desktop) reaches gtk-launch with its full file name.
    assert call('dock', 'pin', 'org.fixture.desktop') == 'true'
    s = wait(lambda s: s['dock_keys'][:2] == ['fixture-files', 'org.fixture.desktop'])
    call('test', 'click', *center(s, 'org.fixture.desktop'))
    s = wait(lambda s: json.loads((profile / 'launch.json').read_text()) == ['--', 'org.fixture.desktop.desktop'] and not s['search']['launch_busy'])
    assert call('dock', 'unpin', 'org.fixture.desktop') == 'true'
    s = wait(lambda s: 'org.fixture.desktop' not in s['dock_keys'])
    # Launch feedback: the icon pulses while gtk-launch runs; a failure becomes a notification
    # in the drawer with gtk-launch's exit code, and a click during the pulse is not lost
    # but reported as busy (the catalog runs one launch at a time).
    notes_before = s['notifications']['count']
    assert call('dock', 'pin', 'fixture-slow') == 'true' and call('dock', 'pin', 'fixture-fail') == 'true'
    s = wait(lambda s: s['dock_keys'][:3] == ['fixture-files', 'fixture-slow', 'fixture-fail'])
    call('test', 'click', *center(s, 'fixture-slow'))
    s = wait(lambda s: s['dock']['launching'] == 'fixture-slow' and s['search']['launch'] == 'pending')
    s = wait(lambda s: s['dock']['launching'] == '' and s['search']['launch'] == 'requested')
    assert s['notifications']['count'] == notes_before
    call('test', 'click', *center(s, 'fixture-fail'))
    s = wait(lambda s: s['search']['launch'] == 'failed' and s['dock']['launching'] == '')
    s = wait(lambda s: s['notifications']['count'] == notes_before + 1)
    assert json.loads((profile / 'launch.json').read_text()) == ['--', 'fixture-fail.desktop']
    # The next launch works again after a failure (no stuck busy flag).
    call('test', 'click', *center(s, 'fixture-slow'))
    s = wait(lambda s: s['search']['launch'] == 'requested' and not s['search']['launch_busy'])
    assert call('dock', 'unpin', 'fixture-slow') == 'true' and call('dock', 'unpin', 'fixture-fail') == 'true'
    s = wait(lambda s: s['dock_keys'][:2] == ['fixture-files', 'sep'])
    call('test', 'click', *center(s, 'fixture-browser'))
    s = wait(lambda s: s['niri']['action'] == 'confirmed')
    assert server.actions[-1] == {'FocusWindow': {'id': 1}}, server.actions
    # Several windows → list; a row focuses that window.
    call('test', 'click', *center(s, 'fixture-editor'))
    s = wait(lambda s: s['dock']['popup'] == 'windows' and s['dock']['popup_ready'])
    assert s['dock_rows'] == ['window', 'window', 'line', 'new'] and s['dock']['popup_rows'] == 4
    ex, _ = center(s, 'fixture-editor')
    # The menu is centred on the bubble, which lands on the icon a moment later (spring).
    s = wait(lambda s: abs(s['dock']['popup_rect']['x'] + s['dock']['popup_rect']['width'] / 2 - ex) < 1)
    pop = s['dock']['popup_rect']
    assert pop['y'] + pop['height'] == PLATE_Y - 12, pop
    assert 'PRIVATE' not in call('dock', 'status')
    call('test', 'click', *center(s, 'fixture-editor'))  # Same icon again closes the list.
    assert state()['dock']['popup'] == 'closed'
    call('test', 'click', *center(s, 'fixture-editor'))
    wait(lambda s: s['dock']['popup'] == 'windows' and s['dock']['popup_ready'])
    call('test', 'click', pop['x'] + 40, pop['y'] + 8 + 24 + 36 + 18)  # Second window row.
    s = wait(lambda s: s['dock']['popup'] == 'closed' and server.actions[-1] == {'FocusWindow': {'id': 4}})
    # Outside press closes; the dock's own icon under the overlay still acts.
    call('test', 'click', *center(s, 'fixture-editor'))
    wait(lambda s: s['dock']['popup'] == 'windows' and s['dock']['popup_ready'])
    call('test', 'click', 700, 300)
    assert state()['dock']['popup'] == 'closed'
    # Context menu: New window, Pin, Close all N windows.
    call('test', 'rightClick', *center(s, 'fixture-editor'))
    s = wait(lambda s: s['dock']['popup'] == 'menu' and s['dock']['popup_ready'])
    assert s['dock_rows'] == ['new', 'pin', 'line', 'closeall'], s['dock_rows']
    pop = s['dock']['popup_rect']
    call('test', 'click', pop['x'] + 40, pop['y'] + 8 + 24 + 36 + 18)  # Pin to Dock
    s = wait(lambda s: s['dock']['pinned'] == ['fixture-files', 'fixture-editor'])
    assert s['dock_keys'] == ['fixture-files', 'fixture-editor', 'sep', 'fixture-browser', 'app:PRIVATE_UNKNOWN_APP']
    # Unresolved app: no desktop entry, so no New window / Pin; Choose app… and Close.
    call('test', 'rightClick', *center(s, 'app:PRIVATE_UNKNOWN_APP'))
    s = wait(lambda s: s['dock']['popup'] == 'menu' and s['dock']['popup_ready'])
    assert s['dock_rows'] == ['assign', 'line', 'closeall'], s['dock_rows']
    call('test', 'click', 700, 300)
    # Drag along the dock reorders pinned apps; the order is saved.
    call('test', 'drag', *center(s, 'fixture-files'), center(s, 'fixture-editor')[0] + 20, center(s, 'fixture-files')[1])
    s = wait(lambda s: s['dock']['pinned'] == ['fixture-editor', 'fixture-files'] and not s['dock']['dragging'])
    assert s['dock_keys'][:3] == ['fixture-editor', 'fixture-files', 'sep']
    # Drag away from the dock unpins.
    fx, fy = center(s, 'fixture-files')
    call('test', 'drag', fx, fy, fx, fy - 90)
    s = wait(lambda s: s['dock']['pinned'] == ['fixture-editor'] and not s['dock']['dragging'])
    assert s['dock_keys'] == ['fixture-editor', 'sep', 'fixture-browser', 'app:PRIVATE_UNKNOWN_APP']
    # Drag a running app across the separator pins it.
    bx, by = center(s, 'fixture-browser')
    call('test', 'drag', bx, by, center(s, 'fixture-editor')[0] - 20, by)
    s = wait(lambda s: s['dock']['pinned'] == ['fixture-browser', 'fixture-editor'] and not s['dock']['dragging'])
    assert s['dock_keys'] == ['fixture-browser', 'fixture-editor', 'sep', 'app:PRIVATE_UNKNOWN_APP']
    # Auto-hide: hidden until the 2 px screen edge is touched for 100 ms; leaves after 100 ms.
    call('dock', 'autoHide', 'true')
    s = wait(lambda s: not s['dock']['visible'] and s['dock']['reserve'] == 0 and s['dock']['edge_enabled'])
    call('test', 'hover', 700, EDGE_Y)
    time.sleep(.05)
    assert not state()['dock']['revealed']
    s = wait(lambda s: s['dock']['revealed'] and s['dock']['visible'] and s['dock']['rect']['y'] == PLATE_Y)  # slide-in finished
    call('test', 'hover', *center(s, 'fixture-editor'))
    time.sleep(.6)
    assert state()['dock']['visible']
    call('test', 'hover', 700, 300)
    wait(lambda s: not s['dock']['visible'])
    # An open popup keeps the dock revealed; closing it starts the timer.
    call('test', 'hover', 700, EDGE_Y)
    s = wait(lambda s: s['dock']['revealed'] and s['dock']['rect']['y'] == PLATE_Y)
    call('test', 'click', *center(s, 'fixture-editor'))
    wait(lambda s: s['dock']['popup'] == 'windows' and s['dock']['popup_ready'])
    call('test', 'hover', 700, 300)
    time.sleep(.6)
    assert state()['dock']['visible']
    call('test', 'click', 700, 300)
    wait(lambda s: not s['dock']['visible'] and s['dock']['popup'] == 'closed')
    # Overview closes everything and hides the dock; leaving it restores only a pinned dock.
    call('test', 'hover', 700, EDGE_Y)
    wait(lambda s: s['dock']['revealed'])
    before = state()['close_count']
    server.overview(True)
    s = wait(lambda s: s['niri']['overview_open'] is True)
    assert not s['dock']['visible'] and not s['dock']['edge_enabled'] and s['close_count'] == before + 1
    server.overview(False)
    s = wait(lambda s: s['niri']['overview_open'] is False)
    assert not s['dock']['visible'] and s['dock']['edge_enabled']
    call('dock', 'autoHide', 'false')
    wait(lambda s: s['dock']['visible'])
    server.overview(True)
    wait(lambda s: not s['dock']['visible'])
    server.overview(False)
    wait(lambda s: s['dock']['visible'])
    # Fullscreen-like coverage: the dock slides away and the edge still reveals it.
    server.set_windows(windows(cover=True))
    s = wait(lambda s: s['notifications']['presentation'] == 'covered')
    assert not s['dock']['visible']
    call('dock', 'autoHide', 'true')
    # Dock IPC goes through a settings transaction; wait for it instead of a fixed delay.
    wait(lambda s: s['dock']['auto_hide'])
    call('test', 'hover', 700, EDGE_Y)
    s = wait(lambda s: s['dock']['edge_enabled'] and s['dock']['visible'] and s['dock']['revealed'])
    call('test', 'hover', 700, 300)
    wait(lambda s: not s['dock']['visible'])
    server.set_windows(windows())
    wait(lambda s: s['notifications']['presentation'] == 'clear' and s['dock']['edge_enabled'])
    call('dock', 'autoHide', 'false')
    wait(lambda s: s['dock']['visible'] and s['dock']['rect']['y'] == PLATE_Y)  # slide-in finished before the click
    # Close all windows of one app: queued close-window actions through the core.
    call('test', 'rightClick', *center(state(), 'fixture-editor'))
    s = wait(lambda s: s['dock']['popup'] == 'menu' and s['dock']['popup_ready'])
    assert s['dock_rows'] == ['new', 'unpin', 'line', 'closeall']
    pop = s['dock']['popup_rect']
    call('test', 'click', pop['x'] + 40, pop['y'] + pop['height'] - 8 - 18)
    s = wait(lambda s: s['dock']['popup'] == 'closed' and s['dock_windows'] == [[1], [], [2]])
    assert [a for a in server.actions if 'CloseWindow' in a] == [{'CloseWindow': {'id': 3}}, {'CloseWindow': {'id': 4}}], server.actions
    # Dock IPC remains available without launcher settings pages.
    call('dock', 'autoHide', 'true')
    call('dock', 'toggle')
    s = wait(lambda s: not s['dock']['on'])
    assert not s['dock']['visible'] and s['dock']['reserve'] == 0 and not s['dock']['edge_enabled']
    assert call('dock', 'toggle') == 'true'
    wait(lambda s: s['dock']['on'])
    call('dock', 'autoHide', 'false')
    wait(lambda s: s['dock']['visible'])
    wait(lambda s: json.loads((profile / 'state/emaki/dock.json').read_text()) == dict(version=1, on=True, auto_hide=False, pinned=['fixture-browser', 'fixture-editor']), 3)


def second(call, state, wait, center):
    # Pinned apps and auto-hide survive a restart.
    s = wait(lambda s: s['niri']['connection'] == 'connected' and s['dock']['labels'] == 'ready' and len(s['dock']['entries']) == 3)
    assert s['dock']['pinned'] == ['fixture-browser', 'fixture-editor'] and not s['dock']['auto_hide'] and s['dock']['visible']
    assert s['dock_keys'] == ['fixture-browser', 'fixture-editor', 'sep', 'app:PRIVATE_UNKNOWN_APP']
    assert call('dock', 'unpin', 'fixture-editor') == 'true' and call('dock', 'unpin', 'fixture-editor') == 'false'
    wait(lambda s: s['dock_keys'] == ['fixture-browser', 'sep', 'app:PRIVATE_UNKNOWN_APP'])


def identity_windows():
    return [window(1, 101, True, 'fixture-browser', 'PRIVATE_BROWSER', **TILE),
            window(2, 202, False, 'PRIVATE_UNKNOWN_APP', 'PRIVATE_TITLE_2', **TILE),
            window(5, 101, False, 'steam_app_42', 'PRIVATE_GAME', **TILE),
            window(6, 101, False, 'Suite-Tool', 'PRIVATE_SUITE', **TILE)]


def third(call, state, wait, center):
    # Guessed identity: steam_app_<id> ↔ steam://rungameid/<id>, Exec file name ↔ app_id (any case).
    s = wait(lambda s: s['niri']['connection'] == 'connected' and s['dock']['labels'] == 'ready' and s['niri']['window_count'] == 4)
    for pinned in s['dock']['pinned']:  # The earlier runs' pins are not this run's subject.
        call('dock', 'unpin', pinned)
    s = wait(lambda s: s['dock_keys'] == ['fixture-browser', 'app:PRIVATE_UNKNOWN_APP', 'fixture-game', 'fixture-suite'])
    assert [e['source'] for e in s['dock']['entries']] == ['exact', '', 'guess', 'guess'], s['dock']['entries']
    assert s['dock']['learned'] == 0 and not (profile / 'state/emaki/apps.json').exists()
    # A guessed app can be pinned like any other; a guess offers "Not this app…".
    call('test', 'rightClick', *center(s, 'fixture-game'))
    s = wait(lambda s: s['dock']['popup'] == 'menu' and s['dock']['popup_ready'])
    assert s['dock_rows'] == ['new', 'pin', 'assign', 'line', 'closeall'], s['dock_rows']
    call('test', 'click', 700, 300)
    # Unknown window: "Choose app…" opens the launcher on Apps; the picked app is learned.
    call('test', 'rightClick', *center(s, 'app:PRIVATE_UNKNOWN_APP'))
    s = wait(lambda s: s['dock']['popup'] == 'menu' and s['dock']['popup_ready'])
    assert s['dock_rows'] == ['assign', 'line', 'closeall'], s['dock_rows']
    pop = s['dock']['popup_rect']
    call('test', 'click', pop['x'] + 40, pop['y'] + 8 + 24 + 24)
    s = wait(lambda s: s['launcher'] == 'open' and s['search']['assigning'] and s['search']['mode'] == 'Apps')
    call('test', 'query', 'edit')
    s = wait(lambda s: s['search']['result_count'] == 1 and s['search']['selected_kind'] == 'app')
    call('test', 'activate')
    s = wait(lambda s: s['launcher'] == 'closed' and s['dock_keys'] == ['fixture-browser', 'fixture-editor', 'fixture-game', 'fixture-suite'])
    assert s['dock']['entries'][1]['source'] == 'learned' and s['dock']['learned'] == 1 and not s['search']['assigning']
    assert json.loads((profile / 'launch.json').read_text()) == ['--', 'fixture-slow.desktop'], 'assigning must not launch'
    wait(lambda s: (profile / 'state/emaki/apps.json').exists(), 3)
    assert json.loads((profile / 'state/emaki/apps.json').read_text()) == dict(version=1, map={'PRIVATE_UNKNOWN_APP': 'fixture-editor'})
    assert json.loads(call('dock', 'apps')) == {'PRIVATE_UNKNOWN_APP': 'fixture-editor'}
    assert 'PRIVATE' not in call('dock', 'status')
    # A learned match offers "Not this app…" and can be forgotten from the terminal.
    call('test', 'rightClick', *center(s, 'fixture-editor'))
    s = wait(lambda s: s['dock']['popup'] == 'menu' and s['dock']['popup_ready'])
    assert s['dock_rows'] == ['new', 'pin', 'assign', 'line', 'closeall'], s['dock_rows']
    call('test', 'click', 700, 300)
    assert call('dock', 'forget', 'PRIVATE_UNKNOWN_APP') == 'true' and call('dock', 'forget', 'PRIVATE_UNKNOWN_APP') == 'false'
    s = wait(lambda s: s['dock_keys'] == ['fixture-browser', 'app:PRIVATE_UNKNOWN_APP', 'fixture-game', 'fixture-suite'])
    assert call('dock', 'assign', 'PRIVATE_UNKNOWN_APP', 'no-such-app') == 'false'
    assert call('dock', 'assign', 'PRIVATE_UNKNOWN_APP', 'fixture-editor') == 'true'
    wait(lambda s: s['dock_keys'][1] == 'fixture-editor')
    # Launch learning: an app started from Emaki whose first window names nothing is that app.
    assert call('dock', 'pin', 'fixture-files') == 'true'
    s = wait(lambda s: s['dock_keys'][0] == 'fixture-files')
    call('test', 'click', *center(s, 'fixture-files'))
    wait(lambda s: s['search']['launch'] == 'requested' and not s['search']['launch_busy'])
    server.open_window(window(7, 101, False, 'PRIVATE_NEW_APP', 'PRIVATE_TITLE_7', **TILE))
    s = wait(lambda s: s['dock_windows'][0] == [7])
    assert s['dock']['entries'][0]['source'] == 'learned' and s['dock']['learned'] == 2
    assert json.loads(call('dock', 'apps')) == {'PRIVATE_UNKNOWN_APP': 'fixture-editor', 'PRIVATE_NEW_APP': 'fixture-files'}
    # A second unknown window later is not the launched app any more.
    server.open_window(window(8, 101, False, 'PRIVATE_LATE_APP', 'PRIVATE_TITLE_8', **TILE))
    s = wait(lambda s: 'app:PRIVATE_LATE_APP' in s['dock_keys'])
    assert s['dock']['learned'] == 2
    # Esc leaves assignment without learning anything.
    call('test', 'rightClick', *center(s, 'app:PRIVATE_LATE_APP'))
    s = wait(lambda s: s['dock']['popup'] == 'menu' and s['dock']['popup_ready'])
    pop = s['dock']['popup_rect']
    call('test', 'click', pop['x'] + 40, pop['y'] + 8 + 24 + 24)
    wait(lambda s: s['launcher'] == 'open' and s['search']['assigning'])
    call('test', 'close')
    s = wait(lambda s: s['launcher'] == 'closed')
    assert not s['search']['assigning'] and s['dock']['learned'] == 2
    wait(lambda s: json.loads((profile / 'state/emaki/apps.json').read_text()).get('map', {}).get('PRIVATE_NEW_APP') == 'fixture-files', 3)
    wait(lambda s: json.loads((profile / 'state/emaki/dock.json').read_text()).get('pinned') == ['fixture-files'], 3)


def fourth(call, state, wait, center):
    # Learned matches survive a restart.
    s = wait(lambda s: s['niri']['connection'] == 'connected' and s['dock']['labels'] == 'ready' and s['dock']['learned'] == 2)
    assert s['dock_keys'] == ['fixture-files', 'sep', 'fixture-browser', 'fixture-editor', 'fixture-game', 'fixture-suite', 'app:PRIVATE_LATE_APP'], s['dock_keys']


# Files written by a newer shell (kept in /home across a system rollback): not read, not overwritten.
NEWER_DOCK = json.dumps(dict(version=2, on=False, auto_hide=False, pinned=['fixture-browser'], placement='future'))
NEWER_APPS = json.dumps(dict(version=2, map={'PRIVATE_UNKNOWN_APP': 'fixture-editor'}))


def newer(call, state, wait, center):
    s = wait(lambda s: s['niri']['connection'] == 'connected' and s['dock']['labels'] == 'ready')
    assert s['dock']['on'] and not s['dock']['auto_hide'] and s['dock']['pinned'] == [] and s['dock']['learned'] == 0, s['dock']
    assert call('dock', 'pin', 'fixture-files') == 'true' and call('dock', 'assign', 'PRIVATE_UNKNOWN_APP', 'fixture-editor') == 'true'
    wait(lambda s: s['dock']['pinned'] == ['fixture-files'] and s['dock']['learned'] == 1)
    time.sleep(.9)  # past the 500 ms save timers
    assert (profile / 'state/emaki/dock.json').read_text() == NEWER_DOCK
    assert (profile / 'state/emaki/apps.json').read_text() == NEWER_APPS


try:
    run(first)
    run(second)
    with server.lock:
        server.windows = identity_windows()
    run(third)
    run(fourth)
    (profile / 'state/emaki/dock.json').write_text('{broken')
    run(lambda call, state, wait, center: wait(lambda s: s['dock']['labels'] == 'ready' and s['dock']['pinned'] == [] and not s['dock']['auto_hide']))
    (profile / 'state/emaki/dock.json').write_text(NEWER_DOCK)
    (profile / 'state/emaki/apps.json').write_text(NEWER_APPS)
    run(newer)
    contents = log_path.read_text()
    assert not any(w in contents for w in ('WARN', 'ERROR', 'FAIL!', 'PRIVATE', 'ReferenceError', 'TypeError')), contents
    assert not server.errors, server.errors
    written = sorted(p.name for p in (profile / 'state/emaki').iterdir() if p.is_file())
    # notifications.json: the failed-launch notice from fixture-fail lives in the drawer history.
    assert written == ['apps.json', 'dock.json', 'frequent.json', 'frequent.lock', 'helper.lock', 'notifications.json', 'settings.lock'], written
finally:
    server.stop.set()
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
launches(profile, ['fixture-files', 'org.fixture.desktop', 'fixture-slow', 'fixture-fail'])
print(f'Dock: pinned/running order, dots, launch/focus/list, menu pin/close-all, drag reorder/unpin/pin, auto-hide timers, overview, coverage, dock IPC, dock.json restart, identity guess/choose/learn/forget, apps.json restart: OK; {profile.relative_to(ROOT)}')
