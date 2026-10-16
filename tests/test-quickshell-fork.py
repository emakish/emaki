#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The qs in PATH carries the packaged quickshell-emaki patches the shell tests can observe.

The shell tests run whatever qs comes first in PATH. A development host can have an older
quickshell-emaki than the one the packages pin (0.3.1-1 against 0.3.1-12 on 2026-10-15), and
then a test passes or fails for a watcher or an API the shipped system does not have
(test-launch-handoff waited for a rescan that the packaged watcher never performs). This
check fails first, names each missing patch and says how to get the right qs.

Probed offscreen: 0005 (no WAYLAND_SOCKET in children), 0006 (OnlyShowIn/NotShowIn),
0007 (the desktop-entry watcher), 0008 (Quickshell.iconThemeRevision), 0009
(Quickshell.queryKeyboardModifiers). Not observable here:
0001 (needs a compositor's screencopy), 0002 (needs NetworkManager), 0003 (a build fix),
0004 (needs polkitd)."""
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
ENTRY = '[Desktop Entry]\nType=Application\nName={}\nExec=true\n'
PROBED = {'0005', '0006', '0007', '0008', '0009'}
# The patches this check was reviewed against: what the recipe applies, in order, with the
# sha256 the PKGBUILD pins. Any change (a patch added, removed, renamed, reordered, applied
# twice or edited) fails here, so that someone decides whether the probe can observe it and
# updates PROBED, the docstring and this list.
REVIEWED = (
    ('0001-wlr-screencopy-raw-wl_output-for-transform.patch',
     'b32f0be7920e0dd10cf89bb0fecdd0a7c0ac73f0f113fc93e3b235ad36e894c7'),
    ('0002-network-nm-name-the-activation-a-connection-failure.patch',
     'a01afe049a5eec79752ecbb57b705fb672a34bb7b5eff20c43e68c544f70ea65'),
    ('0003-qt-6.12-moc-includes.patch',
     '4cb3b5acc94e0c9546568a782540ab01c1ad06946c8c7b7e290e2b9ffc21b7a2'),
    ('0004-services-polkit-one-request-at-a-time.patch',
     '7115569ed25736ba4f5ae1cc3da37f4dfbcfc046f813dc0ffb89268232827b4b'),
    ('0005-launch-drop-private-wayland-socket.patch',
     '3fa5679080c8c18fb8a131f6a3634a1cc4b36c805cae9d55eb93eae8a5d95753'),
    ('0006-core-desktopentry-honour-onlyshowin-notshowin.patch',
     '7b54d7516aede6d4eb17138ae27259d0eaf4ff106df2db9eab3564cc526482a6'),
    ('0007-core-desktopentry-watch-directories-created-later.patch',
     '5786bb33ebd625d181b6f51f85c8fab828a3da0305f065b070d2cc782416b599'),
    ('0008-core-find-theme-icons-installed-after-startup.patch',
     '979342b98e21c517843e8b6e957cf9cf6f8d6a0c46bc347f0ed0d7d55462fcf1'),
    ('0009-core-query-keyboard-modifiers.patch',
     'd7227add293f3e69752d7c8a8f903b1f2caf14f110473695bdaa5446e8464e5e'),
)
# tests/build-quickshell-fork.sh installs qs into <directory>/bin.
REMEDY = 'tests/build-quickshell-fork.sh <directory>, then PATH=<directory>/bin:$PATH'
# The remedy printed on failure must name the directory the build script installs qs into.
BUILT_BIN = re.search(r'Run the shell tests with PATH=\$OUT/(\S+):',
                      (ROOT / 'tests/build-quickshell-fork.sh').read_text())[1]
assert f'PATH=<directory>/{BUILT_BIN}:' in REMEDY, f'the remedy names another directory than {BUILT_BIN}: {REMEDY}'


def recipe_patches(recipe):
    """(name, sha256) of each patch the PKGBUILD text applies, in order. ValueError when that
    is not REVIEWED."""
    def array(name):
        found = re.search(rf'^{name}=\((.*?)\)', recipe, re.M | re.S)
        return [item.strip('\'"').split('::')[0] for item in found[1].split()] if found else []
    sources, sums = array('source'), array('sha256sums')
    if len(sources) != len(sums):
        raise ValueError(f'{len(sources)} sources against {len(sums)} sha256sums')
    pinned = dict(zip(sources, sums))
    applied = [(name, pinned.get(name))
               for name in re.findall(r'^\s+patch -Np1 -i "\$srcdir/(\S+)"$', recipe, re.M)]
    if tuple(applied) != REVIEWED:
        raise ValueError('the recipe applies another patch list than the one this check was '
                         'reviewed against; decide whether the probe can observe the change and '
                         f'update PROBED, the docstring and REVIEWED: {applied}')
    return applied


def main():
    from runtime_fixture import runtime_path
    import reaper
    reaper.guard()  # nothing this test starts outlives it
    recipe = (ROOT / 'packaging/quickshell-emaki/PKGBUILD').read_text()
    pinned = '{}-{}'.format(*(re.search(rf'^{key}=(\S+)$', recipe, re.M)[1] for key in ('pkgver', 'pkgrel')))
    recipe_patches(recipe)
    # A tree of its own: a busy ancestor would hide or fake the watcher's answers.
    QUIET = ROOT / '.cache/quickshell-fork'
    QUIET.mkdir(parents=True, exist_ok=True)
    qs = shutil.which('qs')
    assert qs, 'no qs in PATH'
    version = subprocess.run([qs, '--version'], capture_output=True, text=True).stdout.strip()

    with tempfile.TemporaryDirectory(prefix='qs-fork-', dir=QUIET) as temporary:
        base = Path(temporary)
        home = base / 'home'
        system = base / 'usr/share'
        (system / 'applications').mkdir(parents=True)
        (system / 'applications/present.desktop').write_text(ENTRY.format('Present'))
        (system / 'applications/elsewhere.desktop').write_text(ENTRY.format('Elsewhere') + 'OnlyShowIn=GNOME;\n')
        for name in ('home', 'config', 'cache', 'state', 'shell'):
            (base / name).mkdir(mode=0o700)
        (base / 'shell/shell.qml').write_text((ROOT / 'tests/fixtures/QuickshellForkProbe.qml').read_text())
        env = dict(os.environ, HOME=str(home), XDG_CONFIG_HOME=str(base / 'config'),
                   XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(home / '.local/share'),
                   XDG_DATA_DIRS=str(system), XDG_CACHE_HOME=str(base / 'cache'),
                   XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
                   XDG_CURRENT_DESKTOP='niri', WAYLAND_SOCKET='198',
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
            env.pop(key, None)
        shell = subprocess.Popen([qs, '-p', str(base / 'shell/shell.qml'), '--no-color'], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        lines = queue.Queue()
        log = []

        def read():
            for line in shell.stdout:
                log.append(line)
                lines.put(line)

        threading.Thread(target=read, daemon=True).start()
        seen = {'ids': None, 'scans': 0, 'revision': None, 'socket': None, 'modifiers': None}

        def drain(seconds, until=lambda: False):
            end = time.monotonic() + seconds
            while not until():
                try:
                    line = lines.get(timeout=max(.01, end - time.monotonic()))
                except queue.Empty:
                    return
                for key, marker in (('ids', 'FORK_IDS ['), ('scans', 'FORK_SCANS '),
                                    ('revision', 'FORK_REVISION '), ('socket', 'FORK_CHILD_SOCKET '),
                                    ('modifiers', 'FORK_MODIFIERS ')):
                    if marker in line:
                        value = line.split(marker, 1)[1].strip()
                        seen[key] = value.rstrip(']').split() if key == 'ids' else int(value) if key == 'scans' else value

        missing = []
        try:
            drain(15, lambda: seen['ids'] and seen['revision'] and seen['socket'])
            assert seen['ids'] and seen['revision'] and seen['socket'], \
                'the probe never reported its first state\n' + ''.join(log[-40:])
            if seen['socket'] != 'unset':
                missing.append(('0005', f'a child inherits WAYLAND_SOCKET={seen["socket"]}'))
            if seen['ids'] != ['present']:
                missing.append(('0006', f'an OnlyShowIn=GNOME entry is listed on niri: {seen["ids"]}'))
            if seen['revision'] != 'number':
                missing.append(('0008', f'typeof Quickshell.iconThemeRevision is {seen["revision"]!r}'))
            if seen['modifiers'] != 'function 0':
                missing.append(('0009', f'Quickshell.queryKeyboardModifiers is {seen["modifiers"]!r}'))
            drain(.6)
            before = seen['scans']
            # A file saved into a parent of a desktop-entry directory (the home folder, /usr/share).
            (system / 'saved.txt').write_text('')
            (home / 'saved.txt').write_text('')
            drain(1.2)
            if seen['scans'] != before:
                missing.append(('0007', f'a file saved into a parent directory rescanned the entries '
                                        f'({seen["scans"] - before} scans)'))
            # update-desktop-database after a package transaction: tests rely on this rescan.
            before = seen['scans']
            (system / 'applications/mimeinfo.cache').write_text('[MIME Cache]\n')
            drain(5, lambda: seen['scans'] > before)
            assert seen['scans'] > before, ('a change inside an applications directory did not rescan; '
                                            'every quickshell-emaki does\n' + ''.join(log[-40:]))
            # The first entry of a new account: ~/.local/share/applications did not exist at startup.
            (home / '.local/share/applications').mkdir(parents=True)
            (home / '.local/share/applications/local.desktop').write_text(ENTRY.format('Local'))
            drain(5, lambda: 'local' in (seen['ids'] or []))
            if 'local' not in (seen['ids'] or []):
                missing.append(('0007', f'a directory created later is not watched: {seen["ids"]}'))
        finally:
            shell.kill()
            shell.wait()

    if missing:
        raise SystemExit(
            f'FAIL: the qs in PATH ({qs}, {version!r}) is not the packaged quickshell-emaki {pinned}:\n'
            + ''.join(f'  missing {patch}: {why}\n' for patch, why in missing)
            + 'The shell tests would check behaviour the shipped system does not have. Put the packaged '
              'quickshell-emaki first in PATH, or build its patches on this host: '
              f'{REMEDY} (GOTCHAS 2026-10-15 "The shell tests\' qs").')
    print(f'PASS: {qs} ({version}) carries the observable packaged patches '
          f'{", ".join(sorted(PROBED))} of quickshell-emaki {pinned}')


if __name__ == '__main__':
    main()
