#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The shell's long-running helpers end with the shell, whether it is stopped or killed.

Quickshell does not stop its children when it exits. The backlight and network monitors and
the session-update watcher write only on an event, so a closed pipe does not end them either:
before the fix every shell restart left one more set running until logout. Each helper here
is a fixture that records its PID and sleeps; the shell is then ended with SIGTERM (a stop or
restart) and SIGKILL (a crash), and every recorded helper must be gone within a few seconds.
A mutant without the parent-death signal must leave them running, so the check can fail.

The signal is set only after the fork, by setpriv. A shell that dies before that leaves the
helper with a new parent, and setting the signal then delivers nothing. The race case slows a
fixture setpriv down, kills the shell while all three wait in it, and requires that no helper
then runs: each checks its parent is still the shell. A mutant without that check must leak.
"""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reaper

ROOT = Path(__file__).resolve().parent.parent
SET = '"setpriv", "--pdeathsig", "TERM", "--"'
GUARD = SET + ', "sh", "-c", \'[ "$PPID" = "$0" ] || exit 0; exec "$@"\', String(Quickshell.processId)'
HELPERS = ('udevadm', 'nmcli', 'watch')
QML = '''import QtQuick
import Quickshell
ShellRoot {
    SystemBackend { id: fx }
    SystemService { backend: fx; helpersEnabled: true }
    NotificationStore { id: notes; dnd: true }
    SessionUpdateNotice { store: notes }
}
'''


def alive(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    except OSError:
        return False
    return fields[0] not in ('Z', 'X')


def run(sig, mutant=None, race=False):
    """mutant: None, 'unguarded' (no parent-death signal) or 'unchecked' (signal, no parent check)."""
    with tempfile.TemporaryDirectory(prefix='helpers-exit-', dir='/tmp') as directory:
        base = Path(directory)
        shell = base / 'shell'
        shutil.copytree(ROOT / 'shell', shell)
        for name in ('runtime', 'config', 'cache', 'state', 'data', 'pids', 'generation'):
            (base / name).mkdir(mode=0o700)
        if mutant:
            for name in ('SystemService.qml', 'SessionUpdateNotice.qml'):
                path = shell / name
                source = path.read_text()
                assert GUARD in source, name + ': helper guard changed'
                if mutant == 'unguarded':
                    source = source.replace(GUARD + ', ', '').replace(GUARD, '')
                path.write_text(source.replace(GUARD, SET))
        pids = base / 'pids'
        path = os.environ['PATH']
        if race:
            # Every helper waits 3 s in setpriv before the signal is set; the shell dies meanwhile.
            (base / 'bin').mkdir()
            slow = base / 'bin/setpriv'
            slow.write_text(f'#!/bin/sh\necho $$ >> {pids}/setpriv\nsleep 3\nexec {shutil.which("setpriv")} "$@"\n')
            slow.chmod(0o700)
            path = f'{base / "bin"}:{path}'
        for name in ('udevadm', 'nmcli'):
            fake = base / name
            fake.write_text(f'#!/bin/sh\necho $$ > {pids}/{name}\nexec sleep 600\n')
            fake.chmod(0o700)
        (base / 'generation/watch.py').write_text(
            f'import os, time\nopen({str(pids / "watch")!r}, "w").write(str(os.getpid()))\ntime.sleep(600)\n')
        # Other probes of the service are harmless fixtures; no host service is contacted.
        (shell / 'helpers/system-tools.py').write_text('import json\nprint(json.dumps({"state":"unavailable"}))\n')
        (shell / 'shell.qml').write_text(QML)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                   XDG_RUNTIME_DIR=str(base / 'runtime'), XDG_CONFIG_HOME=str(base / 'config'),
                   XDG_CACHE_HOME=str(base / 'cache'), XDG_STATE_HOME=str(base / 'state'),
                   XDG_DATA_HOME=str(base / 'data'), XDG_DATA_DIRS=str(base / 'data'),
                   EMAKI_UDEVADM=str(base / 'udevadm'), EMAKI_NMCLI=str(base / 'nmcli'),
                   EMAKI_SESSION_GENERATION=str(base / 'generation'), EMAKI_LIVE_SESSION='0',
                   EMAKI_SHELL_TRAY='0', EMAKI_SHELL_NOTIFICATIONS='0',
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'missing'),
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'missing-session'),
                   PIPEWIRE_REMOTE='missing', NIRI_SOCKET='', EMAKI_SETTINGS_PROFILE='', PATH=path)
        for key in ('DISPLAY', 'WAYLAND_DISPLAY'):
            env.pop(key, None)
        log = (base / 'qs.log').open('w')
        shell_process = subprocess.Popen(['qs', '-p', str(shell), '--no-color'], env=env,
                                         stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 20
            while not (len((pids / 'setpriv').read_text().split()) >= len(HELPERS) if race and (pids / 'setpriv').exists()
                       else not race and all((pids / name).exists() and (pids / name).read_text().strip()
                                             for name in HELPERS)):
                assert shell_process.poll() is None, (base / 'qs.log').read_text()
                assert time.monotonic() < deadline, 'helpers did not start: ' + str(sorted(os.listdir(pids)))
                time.sleep(0.05)
            if not race:
                started = {name: int((pids / name).read_text()) for name in HELPERS}
                assert all(alive(pid) for pid in started.values()), started
        finally:
            shell_process.send_signal(sig)
            try:
                shell_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                shell_process.kill()
                shell_process.wait()
            log.close()
        if race:
            # Past the fixture's 3 s wait: a helper that was going to start has started by now.
            time.sleep(4.5)
            started = {name: int((pids / name).read_text()) for name in HELPERS
                       if (pids / name).exists() and (pids / name).read_text().strip()}
        deadline = time.monotonic() + 3
        while any(alive(pid) for pid in started.values()) and time.monotonic() < deadline:
            time.sleep(0.05)
        left = sorted(name for name, pid in started.items() if alive(pid))
        for name in left:
            os.kill(started[name], signal.SIGKILL)
        if mutant:
            assert left == sorted(HELPERS), f'{mutant} mutant: {left} survived, expected all'
        else:
            assert not left, f'{signal.Signals(sig).name}{" before the signal was set" if race else ""}: {left} outlived the shell'


if __name__ == '__main__':
    reaper.guard()
    run(signal.SIGTERM)
    run(signal.SIGKILL)
    run(signal.SIGKILL, mutant='unguarded')
    run(signal.SIGKILL, race=True)
    run(signal.SIGKILL, mutant='unchecked', race=True)
    print('Shell helpers exit: PASS (monitors and update watcher end on shell stop and crash, also when the shell dies'
          ' before the signal is set; mutants without the signal or the parent check leak)')
