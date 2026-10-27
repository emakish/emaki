#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""A launcher tile shows its program's icon when the icon is installed after the tile appeared.

Quickshell reloads the icon themes on a miss at most every two seconds. Two tiles look up their
icons at start (the first lookup reloads, the second falls in the same two seconds), then both
icons are installed inside that window. Nothing looks them up again: the tiles must change by
themselves once the window ends."""
from runtime_fixture import runtime_path
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
PNG = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                    '1f15c4890000000d49444154789c6360f8cf00000301010018dd8db00000'
                    '000049454e44ae426082')
THEME = '[Icon Theme]\nName=Hicolor\nDirectories=48x48/apps\n\n[48x48/apps]\nSize=48\nType=Fixed\n'
# The reload window is two seconds; allow for a slow machine.
LIMIT = 6
BOTH = 'image://icon/org.example.First image://icon/org.example.Second'

QUIET = ROOT / '.cache/launcher-icon-refresh'
QUIET.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='emaki-launcher-icon-', dir=QUIET) as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    (base / 'shell/launcher-icon-refresh-test.qml').write_text(
        (ROOT / 'tests/fixtures/LauncherIconRefreshTest.qml').read_text())
    data = base / 'usr/share'
    apps = data / 'icons/hicolor/48x48/apps'
    apps.mkdir(parents=True)
    (data / 'icons/hicolor/index.theme').write_text(THEME)
    (apps / 'org.example.Present.png').write_bytes(PNG)
    for name in ('home', 'config/qt6ct', 'cache', 'state'):
        (base / name).mkdir(parents=True, mode=0o700)
    # The offscreen platform has no icon theme; qt6ct with an empty configuration gives the
    # generic hicolor lookup.
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'home/.local/share'),
               XDG_DATA_DIRS=str(data), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               XDG_CURRENT_DESKTOP='niri', QT_QPA_PLATFORM='offscreen', QT_QPA_PLATFORMTHEME='qt6ct',
               QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QS_ICON_THEME'):
        env.pop(key, None)
    shell = subprocess.Popen(['qs', '-p', str(base / 'shell/launcher-icon-refresh-test.qml'), '--no-color'],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tiles = queue.Queue()
    log = []
    revision = queue.Queue()

    def read():
        for line in shell.stdout:
            log.append(line)
            if 'ICON_REVISION ' in line:
                revision.put(line.split('ICON_REVISION ', 1)[1].strip())
            if 'TILES ' in line:
                tiles.put(line.split('TILES ', 1)[1].strip())

    threading.Thread(target=read, daemon=True).start()
    try:
        try:
            shown = tiles.get(timeout=15)
        except queue.Empty:
            raise AssertionError('the tiles never reported their icons\n' + ''.join(log[-40:])) from None
        # A Quickshell without iconThemeRevision cannot pass whatever LauncherItem does: say
        # what is missing instead of failing.
        try:
            probe = revision.get(timeout=15)
        except queue.Empty:
            raise AssertionError('the fixture never reported its Quickshell\n' + ''.join(log[-40:])) from None
        if probe != 'number':
            version = subprocess.run(['qs', '--version'], capture_output=True, text=True).stdout.strip()
            print('SKIP: needs quickshell-emaki 0.3.1-12 or later (Quickshell.iconThemeRevision, patch '
                  f'0008); the qs in PATH is {version!r} and has none (typeof is {probe!r}). Install '
                  'the packaged fork (packaging/quickshell-emaki, Qt 6.12) or put its qs first in PATH.')
            raise SystemExit(0)
        assert shown == '- -', f'both icons are missing at start, the tiles show {shown!r}\n' + ''.join(log[-40:])
        started = time.monotonic()
        # Both icons arrive inside the two-second window that the tiles' lookups opened.
        (apps / 'org.example.First.png').write_bytes(PNG)
        (apps / 'org.example.Second.png').write_bytes(PNG)
        written = time.monotonic() - started
        until = time.monotonic() + LIMIT
        while shown != BOTH:
            try:
                shown = tiles.get(timeout=max(.01, until - time.monotonic()))
            except queue.Empty:
                raise AssertionError(f'icons installed after the tiles appeared stay missing: the tiles show '
                                     f'{shown!r} {LIMIT} s later\n' + ''.join(log[-40:])) from None
        assert written < 1, f'writing the icons took {written:.2f} s, outside the window the test needs'
    finally:
        shell.kill()
        shell.wait()
print('PASS: both launcher tiles show icons installed after they appeared, with no new lookup')
