#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Alt+Tab window switcher: real core + fake niri with focus times, offscreen Qt keys and clicks.

A quick tap switches to the previous window with no panel; a held chord shows the panel, Tab and
the arrows move, releasing switches (across workspaces), Esc and a lost keyboard cancel, a click
picks; pictures are cut out of the screen for windows on it and kept for later. Never Wayland.
`--shots DIR` also saves the panel in light and dark with every accent into DIR."""
from runtime_fixture import runtime_path
import argparse
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
parser = argparse.ArgumentParser()
parser.add_argument('binary', help='the freshly built emaki binary')
parser.add_argument('--shots', help='save pictures of the panel into this directory')
options = parser.parse_args()
BINARY = Path(options.binary).resolve(strict=True)
SHOTS = Path(options.shots).resolve() if options.shots else None
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'tests/fixtures'))
from niri_server import Server, Handler, window, workspace

ALT = 0x08000000
(ROOT / '.cache').mkdir(exist_ok=True)
holder = tempfile.TemporaryDirectory(prefix='ws-', dir=ROOT / '.cache')
profile = Path(holder.name)
for part in ('home', 'config', 'state', 'cache', 'data', 'runtime', 'tmp'):
    (profile / part).mkdir(mode=0o700)
shutil.copytree(ROOT / 'shell', profile / 'qml')
shutil.copyfile(ROOT / 'tests/fixtures/WindowSwitcherTest.qml', profile / 'qml/shell.qml')
apps = profile / 'data/applications'
apps.mkdir()
for desktop_id, name, icon in (('fixture-browser', 'Browser', 'firefox'), ('fixture-editor', 'Editor', 'libreoffice-writer'),
                               ('fixture-files', 'Files', 'org.gnome.Calculator')):
    (apps / f'{desktop_id}.desktop').write_text(
        f'[Desktop Entry]\nType=Application\nName={name}\nIcon={icon}\nExec=true\nTerminal=false\n')
# Real application icons for the pictures, when this machine has them (read only).
system_icons = Path('/', 'usr', 'share', 'icons')
if system_icons.is_dir():
    (profile / 'data/icons').symlink_to(system_icons)
env = dict(os.environ, EMAKI_BIN=str(BINARY), NIRI_SOCKET=str(runtime_path(profile) / 'n.sock'),
           EMAKI_SHELL_DIR=str(profile / 'qml'), QS_ICON_THEME='hicolor',
           QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_SCALE_FACTOR='1',
           QML_DISABLE_DISK_CACHE='1', PYTHONDONTWRITEBYTECODE='1', EMAKI_SETTINGS_PROFILE='',
           EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_TRAY='0', EMAKI_TEST_MPRIS='0',
           HOME=str(profile / 'home'),
           XDG_CONFIG_HOME=str(profile / 'config'), XDG_STATE_HOME=str(profile / 'state'),
           XDG_DATA_HOME=str(profile / 'data'), XDG_DATA_DIRS=str(profile / 'data'),
           XDG_CACHE_HOME=str(profile / 'cache'), XDG_RUNTIME_DIR=str(runtime_path(profile)),
           TMPDIR=str(profile / 'tmp'),
           DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session'),
           DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'))
for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_SCREEN_SCALE_FACTORS', 'QT_LOGGING_RULES'):
    env.pop(name, None)


def place(row, pos):
    """Where Emaki's niri says a tile is in its workspace view (stock niri says nothing for a
    tiled window, the floating ones carry tile_pos_in_workspace_view)."""
    key = 'tile_pos_in_workspace_view' if row['is_floating'] else 'emaki_tile_pos_in_view'
    row['layout'][key] = list(pos) if pos else None
    row['layout']['emaki_capture_safe'] = True


def at(row, pos, size=(736, 876), seconds=None, floating=False):
    """A window niri shows at `pos` in its workspace view (None: off this screen)."""
    row['is_floating'] = floating
    row['layout'].update(tile_size=[size[0] + 4., size[1] + 4.], window_size=list(size))
    place(row, pos)
    if seconds is not None:
        row['focus_timestamp'] = dict(secs=seconds, nanos=0)
    return row


# Focus history: 1 now, then 3, 2, 4, 6; 5 never had the focus.
WINDOWS = [
    at(window(1, 101, True, 'fixture-browser', 'Emaki — news'), (16, 64), (736, 876), 1000),
    at(window(3, 101, False, 'fixture-editor', 'notes.md — Editor'), (768, 64), (752, 876), 900),
    at(window(4, 101, False, 'fixture-editor', 'todo.md — Editor'), (1552, 64), (736, 876), 500),
    at(window(2, 202, False, 'PRIVATE_UNKNOWN_APP', 'Terminal'), None, (736, 876), 800),
    at(window(6, 202, False, 'fixture-browser', 'Emaki — downloads'), None, (736, 876), 300),
    at(window(5, 202, False, 'fixture-files', 'Pictures — Files'), None, (736, 876)),
]
class RecordingHandler(Handler):
    def handle(self):
        original = self.rfile
        connection = id(self)
        accepted = time.monotonic()
        records = self.server.requests

        class Reader:
            def readline(self):
                line = original.readline()
                if line:
                    records.append((json.loads(line), connection, accepted))
                return line

            def close(self):
                original.close()

        self.rfile = Reader()
        super().handle()


server = Server(runtime_path(profile) / 'n.sock')
server.requests = []
server.RequestHandlerClass = RecordingHandler
server.logical = dict(x=0, y=0, width=1536, height=960, scale=1.0, transform='Normal')
with server.lock:
    server.windows = [dict(w, layout=dict(w['layout'])) for w in WINDOWS]
    server.workspaces = [workspace(101, 1, True, 1), workspace(202, 2, False, 2)]
threading.Thread(target=server.serve_forever, daemon=True).start()
log_path = profile / 'qs.log'


def run(check):
    with log_path.open('a') as log:
        proc = subprocess.Popen(['qs', '-p', str(profile / 'qml'), '--no-color'], env=env,
                                stdout=log, stderr=subprocess.STDOUT)

        def call(*args):
            result = subprocess.run(['qs', 'ipc', '--pid', str(proc.pid), 'call', *map(str, args)],
                                    env=env, capture_output=True, text=True, timeout=5)
            assert result.returncode == 0, (args, result.stdout, result.stderr, log_path.read_text())
            return result.stdout.strip()

        def wait(predicate, seconds=8):
            deadline = time.monotonic() + seconds
            last = None
            while time.monotonic() < deadline:
                assert proc.poll() is None, log_path.read_text()
                reply = subprocess.run(['qs', 'ipc', '--pid', str(proc.pid), 'call', 'test', 'status'],
                                       env=env, capture_output=True, text=True, timeout=5)
                if reply.returncode == 0:
                    last = json.loads(reply.stdout)
                    if predicate(last):
                        return last
                time.sleep(.02)
            raise AssertionError((last, log_path.read_text()[-4000:]))
        try:
            check(call, wait)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)


def focus_actions():
    with server.lock:
        return [a['FocusWindow']['id'] for a in server.actions if 'FocusWindow' in a]


def reset_focus(ident=1):
    """niri state between cases: window `ident` focused again, no actions recorded."""
    with server.lock:
        server.actions.clear()
        for row in server.windows:
            row['is_focused'] = row['id'] == ident
        server.broadcast({'WindowsChanged': {'windows': server.windows}})


def closed(s):
    # Most scenarios start after the previous focus acknowledgement/timeout has settled.
    # Release itself is asserted separately, while pending still tracks the request.
    return (not s['switcher']['open'] and not s['modal'] and s['keyboard_surface'] == ''
            and s['switcher']['pending'] is None)


def checks(call, wait):
    s = wait(lambda s: s['niri'] == 'connected' and s['labels'] == 'ready' and len(s['times']) == 5 and s['immediate_ready'])
    assert s['times'] == {'1': 1e6, '3': 9e5, '4': 5e5, '2': 8e5, '6': 3e5}, s['times']
    # The first focus write uses a connection established before the action; the next one
    # uses that same connection even while the ordinary helper waits for layout confirmation.
    server.set_mode('ignore')
    started = time.monotonic()
    assert int(call('test', 'immediateFocus', 3)) > 0
    wait(lambda s: focus_actions() == [3])
    assert call('test', 'queuedLayout', 1) == 'true'
    wait(lambda s: s['action_state'] == 'pending' and any('SwitchLayout' in a for a in server.actions))
    assert int(call('test', 'immediateFocus', 2)) > 0
    s = wait(lambda s: focus_actions() == [3, 2])
    assert s['action_state'] == 'pending', 'focus waited for the ordinary action helper'
    requests = [(conn, accepted) for request, conn, accepted in server.requests
                if isinstance(request, dict) and 'FocusWindow' in request.get('Action', {})]
    assert len(requests) == 2 and requests[0][0] == requests[1][0], requests
    assert requests[0][1] < started, 'focus opened a new connection at release'
    wait(lambda s: s['action_state'] != 'pending', 4)
    server.set_mode('apply')
    reset_focus()
    call('test', 'timeouts', 40, 400, 150)
    if SHOTS:
        call('test', 'appearance', 'dark', 'rose')
        wait(lambda s: s['dark'] and s['accent'] == 'rose')

    def step(delta=1, same_app=False, sent=None):
        stamp = call('test', 'now') if sent is None else sent
        return call('test', 'step', delta, 'true' if same_app else 'false', stamp)

    # A quick tap: released before the keyboard arrived. The previous window, no panel, no
    # cards built, nothing else closed (notification previews stay).
    call('test', 'modifiers', 0)
    assert step() == 'true'
    s = wait(lambda s: s['switcher']['armed'])
    assert s['switcher']['ids'] == [1, 3, 2, 4, 6, 5], s['switcher']
    assert s['switcher']['selected'] == 3 and s['modal'] and s['keyboard_surface'] == 'switcher', s
    assert not s['bar_open_ui'], 'the switcher must not bring out a hidden bar'
    assert not s['presenting'], 'a hidden quick tap requested a desktop capture'
    call('test', 'arrive')
    s = wait(lambda s: closed(s) and s['switcher']['outcome'] == 'switched')
    assert s['shown_count'] == 0 and s['opacity'] == 0 and not s['switcher']['populated'] and s['cards'] == [], s
    assert s['close_count'] == 0, 'a tap closed other panels and previews'
    wait(lambda s: focus_actions() == [3], 3)
    reset_focus()

    # Held: the panel shows; Tab (the binding again), the arrows; releasing Alt switches to a
    # window on another workspace. The keyboard stays with the switcher until niri reports the
    # chosen window focused (the fake niri never does: the hand-off ends at its time limit).
    call('test', 'modifiers', ALT)
    step()
    call('test', 'arrive')
    s = wait(lambda s: s['switcher']['shown'] and s['opacity'] == 1)
    assert s['shown_count'] == 1 and s['focus'] == 'window-switcher', s
    assert s['presenting'], 'the visible panel needs its desktop capture'
    assert [c['id'] for c in s['cards']] == [1, 3, 2, 4, 6, 5], s['cards']
    titles = [c['title'] for c in s['cards']]
    assert titles == ['Emaki — news', 'notes.md — Editor', 'Terminal', 'todo.md — Editor',
                      'Emaki — downloads', 'Pictures — Files'], titles
    # Live pictures: the two tiled windows wholly on the screen (positions from Emaki's niri);
    # 4 is beyond its right edge.
    assert sorted(s['switcher']['live']) == [1, 3], s['switcher']
    assert [c['id'] for c in s['cards'] if c['live']] == [1, 3], s['cards']
    panel = s['switcher']['panel']
    assert abs(panel['x'] + panel['width'] / 2 - 768) <= 1 and abs(panel['y'] + panel['height'] / 2 - 480) <= 1, panel
    step()
    assert wait(lambda s: s['switcher']['selected'] == 2)['switcher']['index'] == 2
    call('test', 'key', 'right')
    wait(lambda s: s['switcher']['selected'] == 4)
    call('test', 'key', 'left')
    wait(lambda s: s['switcher']['selected'] == 2)
    # The pointer selects only when it really moves over a card.
    s = wait(lambda s: True)
    card = next(c for c in s['cards'] if c['id'] == 5)
    x, y = card['rect']['x'] + card['rect']['width'] / 2, card['rect']['y'] + 60
    call('test', 'move', x, y)
    call('test', 'move', x + 8, y + 4)
    wait(lambda s: s['switcher']['selected'] == 5)
    call('test', 'key', 'left')
    wait(lambda s: s['switcher']['selected'] == 6)
    call('test', 'key', 'left')
    wait(lambda s: s['switcher']['selected'] == 4)
    call('test', 'key', 'left')
    wait(lambda s: s['switcher']['selected'] == 2)
    if SHOTS:
        save(call, wait, 'held-dark-rose.png')
    late = call('test', 'now')
    call('test', 'modifiers', 0)
    call('test', 'release', 'alt')
    s = wait(lambda s: s['switcher']['outcome'] == 'switched')
    assert not s['switcher']['armed'], s
    if s['switcher']['pending'] is not None:
        assert s['keyboard_surface'] == 'switcher' and s['modal'], s
    wait(lambda s: focus_actions() == [2], 3)
    s = wait(closed)
    assert s['switcher']['pending'] is None, s
    # A Tab pressed just before the release, arriving after it: dropped, no second switcher.
    assert step(sent=late) == 'true'
    time.sleep(.2)
    s = wait(lambda s: True)
    assert closed(s) and not s['switcher']['armed'] and focus_actions() == [2], (s, focus_actions())
    # The live pictures of this opening are kept for later.
    assert sorted(s['switcher']['recent']) == [1, 3], s['switcher']
    reset_focus()

    # Two quick taps: the second comes while the first still waits for niri to report its
    # window. It counts that window as focused and goes back to the one just left.
    call('test', 'timeouts', 40, 400, 3000)
    step()
    call('test', 'arrive')
    wait(lambda s: s['switcher']['outcome'] == 'switched' and s['switcher']['pending'] == 3)
    step()
    # The pending request belongs to the old chord; the new chord needs activation.
    call('test', 'arrive')
    # The cached focus cannot confirm a new request; the fake sends no focus event.
    s = wait(lambda s: closed(s) and s['switcher']['outcome'] == 'switched' and s['switcher']['selected'] == 1)
    assert s['switcher']['ids'][:2] == [3, 1] and s['shown_count'] == 1, s['switcher']
    wait(lambda s: focus_actions() == [3, 1], 3)
    call('test', 'timeouts', 40, 400, 150)
    wait(closed, 5)
    reset_focus()

    # A real focus event, rather than the old cached focused flag, ends the retained layer.
    call('test', 'timeouts', 40, 400, 3000)
    call('test', 'modifiers', 0)
    step()
    call('test', 'arrive')
    s = wait(lambda s: s['switcher']['pending'] == 3)
    assert s['switcher']['open'] and s['modal'] and s['keyboard_surface'] == 'switcher', s
    with server.lock:
        for row in server.windows:
            row['is_focused'] = row['id'] == 3
        server.broadcast({'WindowsChanged': {'windows': server.windows}})
    wait(closed)
    reset_focus()

    # A rejected immediate request closes promptly, without waiting for the long deadline.
    server.set_mode('reject')
    step()
    call('test', 'arrive')
    wait(closed, 1)
    assert focus_actions() == [3], focus_actions()
    server.set_mode('apply')
    reset_focus()
    call('test', 'timeouts', 40, 400, 150)

    # Shift+Tab starts at the far end; Esc closes without switching.
    call('test', 'modifiers', ALT)
    step(-1)
    call('test', 'arrive')
    s = wait(lambda s: s['switcher']['shown'])
    assert s['switcher']['selected'] == 5, s['switcher']
    call('test', 'key', 'escape')
    s = wait(lambda s: closed(s) and s['switcher']['outcome'] == 'cancelled')
    time.sleep(.2)
    assert focus_actions() == [], focus_actions()

    # A click on a card picks its window.
    step()
    call('test', 'arrive')
    s = wait(lambda s: s['switcher']['shown'] and s['opacity'] == 1 and len(s['cards']) == 6)
    card = next(c for c in s['cards'] if c['id'] == 6)
    call('test', 'click', card['rect']['x'] + card['rect']['width'] / 2, card['rect']['y'] + 60)
    wait(lambda s: closed(s) and s['switcher']['outcome'] == 'switched')
    wait(lambda s: focus_actions() == [6], 3)
    reset_focus()

    # A click outside the panel closes it without a switch; so does the keyboard leaving (the
    # lock screen takes it), and closing every panel.
    for close in (lambda: call('test', 'click', 20, 940), lambda: call('test', 'lost'),
                  lambda: call('test', 'closeAll')):
        step()
        call('test', 'arrive')
        wait(lambda s: s['switcher']['shown'] and s['opacity'] == 1)
        close()
        wait(lambda s: closed(s) and s['switcher']['outcome'] == 'cancelled')
    time.sleep(.2)
    assert focus_actions() == [], focus_actions()

    # Nothing held any more and no release came: the keyboard went elsewhere. No switch.
    step()
    call('test', 'arrive')
    wait(lambda s: s['switcher']['shown'])
    call('test', 'modifiers', 0)
    wait(lambda s: closed(s) and s['switcher']['outcome'] == 'cancelled')
    time.sleep(.2)
    assert focus_actions() == [], focus_actions()

    # The keyboard never arrives (another surface holds it): it switches as a tap would.
    shown = wait(closed)['shown_count']
    call('test', 'modifiers', ALT)
    step()
    s = wait(lambda s: closed(s) and s['switcher']['outcome'] == 'switched', 3)
    assert s['shown_count'] == shown, s
    wait(lambda s: focus_actions() == [3], 3)
    reset_focus()

    # A Quickshell without the modifier query: Alt+Tab focuses the previous window at once.
    call('test', 'modifiers', -1)
    step()
    call('test', 'arrive')
    s = wait(lambda s: closed(s) and s['switcher']['outcome'] == 'switched')
    assert s['shown_count'] == shown, s
    wait(lambda s: focus_actions() == [3], 3)
    reset_focus()

    # Alt+`: the windows of the focused application only (two browser windows).
    call('test', 'modifiers', ALT)
    step(same_app=True)
    call('test', 'arrive')
    s = wait(lambda s: s['switcher']['shown'])
    assert s['switcher']['ids'] == [1, 6] and s['switcher']['same_app'] and s['switcher']['selected'] == 6, s['switcher']
    call('test', 'key', 'escape')
    wait(closed)

    # The real query of quickshell-emaki 0009, fed by Qt key events.
    call('test', 'realProbe', 'true')
    call('test', 'press', 'alt')
    step()
    call('test', 'arrive')
    wait(lambda s: s['switcher']['shown'])
    call('test', 'release', 'alt')
    wait(lambda s: closed(s) and s['switcher']['outcome'] == 'switched')
    wait(lambda s: focus_actions() == [3], 3)
    call('test', 'realProbe', 'false')
    reset_focus()

    # Another workspace: the editor is off screen now and shows the picture kept before.
    with server.lock:
        for row in server.workspaces:
            row['is_active'] = row['is_focused'] = row['id'] == 202
        for row in server.windows:
            if row['workspace_id'] == 101:
                place(row, None)
            row['is_focused'] = row['id'] == 2
        place(server.windows[3], (16, 64))
        server.broadcast({'WorkspacesChanged': {'workspaces': server.workspaces}})
        server.broadcast({'WindowsChanged': {'windows': server.windows}})
    wait(lambda s: s['switcher']['open'] is False and s['times'].get('2') == 8e5)
    call('test', 'modifiers', ALT)
    step()
    call('test', 'arrive')
    s = wait(lambda s: s['switcher']['shown'] and s['opacity'] == 1)
    cards = {c['id']: c for c in s['cards']}
    assert s['switcher']['live'] == [2], s['switcher']
    assert cards[2]['live'] and cards[3]['kept'] and cards[1]['kept'] and not cards[4]['kept'], s['cards']
    assert cards[1]['icon'] == cards[3]['icon'], 'icons come from the desktop entries'
    if SHOTS:
        save(call, wait, 'kept-pictures-dark-rose.png')
        for theme in ('light', 'dark'):
            for accent in ('orange', 'coral', 'rose', 'amber', 'green', 'teal', 'blue', 'violet'):
                call('test', 'appearance', theme, accent)
                wait(lambda s: s['dark'] == (theme == 'dark') and s['accent'] == accent)
                save(call, wait, f'panel-{theme}-{accent}.png')
    # A window moving under an open panel invalidates its original screen crop. Keep the
    # earlier thumbnail rather than continuing to label arbitrary desktop pixels as it.
    with server.lock:
        place(server.windows[3], (400, 64))
        server.broadcast({'WindowLayoutsChanged': {'changes': [[2, server.windows[3]['layout']]]}})
    s = wait(lambda s: s['switcher']['live'] == [])
    assert not any(c['live'] for c in s['cards']), s['cards']
    call('test', 'key', 'escape')
    wait(closed)
    with server.lock:
        assert not server.errors, server.errors


def save(call, wait, name):
    time.sleep(.25)  # animations settle
    path = SHOTS / name
    assert call('test', 'shot', str(path)) == 'true'
    wait(lambda s: str(path) in s['shots'] and path.is_file(), 5)


if SHOTS:
    SHOTS.mkdir(parents=True, exist_ok=True)
try:
    run(checks)
except BaseException:
    print(log_path.read_text()[-6000:] if log_path.exists() else "no shell log", file=sys.stderr)
    raise
finally:
    server.stop.set()
    server.shutdown()
    holder.cleanup()
print('window switcher: OK')
