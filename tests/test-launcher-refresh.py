#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The launcher lists a program installed while the shell runs and drops a removed one.

Every directory kind a program can arrive in: the system directory (repository and AUR
packages), the system and user flatpak exports (created by the first flatpak install) and
~/.local/share/applications (missing on a new account)."""
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
ENTRY = '[Desktop Entry]\nType=Application\nName={}\nExec=true\n'
# Seconds a change may take to reach the list; the watch reacts in well under one.
LIMIT = 5

# Quickshell before 0.3.1-12 rescanned on any change in an ancestor of a watched directory, so
# a busy one (a shared TMPDIR) makes the missing watch pass by accident: keep the tree in a
# directory of its own.
QUIET = ROOT / '.cache/launcher-refresh'
QUIET.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='emaki-launcher-refresh-', dir=QUIET) as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    (base / 'shell/launcher-refresh-test.qml').write_text(
        (ROOT / 'tests/fixtures/LauncherRefreshTest.qml').read_text())
    home = base / 'home'
    system = base / 'usr/share'
    flatpak = base / 'var/lib/flatpak'
    user_flatpak = home / '.local/share/flatpak'
    (system / 'applications').mkdir(parents=True)
    (system / 'applications/org.example.Present.desktop').write_text(ENTRY.format('Present'))
    flatpak.mkdir(parents=True)
    user_flatpak.mkdir(parents=True)
    for name in ('config', 'cache', 'state', 'runtime'):
        (base / name).mkdir(mode=0o700)
    env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(home / '.local/share'),
               XDG_DATA_DIRS=':'.join([str(user_flatpak / 'exports/share'),
                                       str(flatpak / 'exports/share'), str(system)]),
               XDG_CACHE_HOME=str(base / 'cache'), XDG_STATE_HOME=str(base / 'state'),
               XDG_RUNTIME_DIR=str(runtime_path(base)), XDG_CURRENT_DESKTOP='niri',
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
        env.pop(key, None)
    shell = subprocess.Popen(['qs', '-p', str(base / 'shell/launcher-refresh-test.qml'), '--no-color'],
                             env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lists = queue.Queue()
    log = []

    def read():
        for line in shell.stdout:
            log.append(line)
            if 'CATALOG [' in line:
                lists.put(line.split('CATALOG [', 1)[1].rsplit(']', 1)[0].split())

    threading.Thread(target=read, daemon=True).start()
    shown = []

    def expect(ids, why):
        global shown
        until = time.monotonic() + LIMIT
        while sorted(ids) != shown:
            try:
                shown = lists.get(timeout=max(.01, until - time.monotonic()))
            except queue.Empty:
                raise AssertionError(f'{why}: expected {sorted(ids)}, the launcher lists {shown}\n'
                                     + ''.join(log[-40:])) from None

    def install(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ENTRY.format(path.stem))

    try:
        apps = ['org.example.Present']
        expect(apps, 'startup')
        for why, path in (
                ('repository or AUR package', system / 'applications/org.example.Repo.desktop'),
                ('first system flatpak', flatpak / 'exports/share/applications/org.example.Flat.desktop'),
                ('second system flatpak', flatpak / 'exports/share/applications/org.example.Flat2.desktop'),
                ('first user flatpak', user_flatpak / 'exports/share/applications/org.example.User.desktop'),
                ('first ~/.local/share/applications entry', home / '.local/share/applications/org.example.Local.desktop')):
            install(path)
            apps.append(path.stem)
            expect(apps, why)
            time.sleep(.3)
        for path in (system / 'applications/org.example.Repo.desktop',
                     flatpak / 'exports/share/applications/org.example.Flat.desktop',
                     home / '.local/share/applications/org.example.Local.desktop'):
            path.unlink()
            apps.remove(path.stem)
            expect(apps, 'removed ' + path.stem)
    finally:
        shell.kill()
        shell.wait()
print('PASS: launcher lists programs installed in every application directory kind and drops removed ones')
