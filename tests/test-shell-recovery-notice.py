#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recovery notices run only once, after startup, in an isolated offscreen shell."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
evidence = ROOT / '.cache/evidence'
evidence.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='recovery-notice-', dir=evidence) as directory:
    base = Path(directory)
    for name in ('home', 'config', 'state', 'cache', 'runtime', 'bin', 'qml'):
        (base / name).mkdir(mode=0o700)
    for name in ('NotificationStore.qml', 'ShellRecoveryNotice.qml'):
        shutil.copyfile(ROOT / 'shell' / name, base / 'qml' / name)
    helper = base / 'bin/emaki-shell-health'
    helper.write_text('''#!/bin/sh
[ "$1" = notice ] || exit 1
printf 'called\\n' >> "$XDG_RUNTIME_DIR/calls"
pending="$XDG_RUNTIME_DIR/pending"
if [ -f "$pending" ]; then
    cat "$pending"
    rm "$pending"
fi
''')
    helper.chmod(0o700)
    fixture = '''import QtQuick
import Quickshell
ShellRoot {
    NotificationStore { id: notes }
    ShellRecoveryNotice { id: recovery; store: notes }
    Timer {
        interval: 100; running: true
        onTriggered: {
            if (recovery.checked || notes.count !== 0) Qt.exit(2);
            recovery.ready = true;
        }
    }
    Timer {
        interval: 300; running: true
        onTriggered: { recovery.ready = false; recovery.ready = true; }
    }
    Timer {
        interval: 700; running: true
        onTriggered: {
            console.log("NOTICE_RESULT=" + JSON.stringify(notes.entries));
            Qt.quit();
        }
    }
}
'''
    (base / 'qml/shell.qml').write_text(fixture)
    env = dict(os.environ, HOME=str(base / 'home'),
               XDG_CONFIG_HOME=str(base / 'config'), XDG_STATE_HOME=str(base / 'state'),
               XDG_CACHE_HOME=str(base / 'cache'), XDG_RUNTIME_DIR=str(base / 'runtime'),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'missing-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'missing-system-bus'),
               PATH=str(base / 'bin') + os.pathsep + os.environ['PATH'])
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_LOGGING_RULES'):
        env.pop(key, None)
    (base / 'runtime/pending').write_text(json.dumps({'backup': 'components/Panel.qml.broken-123'}))
    for attempt in range(2):
        # Separate history storage proves the second start receives no new notice.
        env['XDG_STATE_HOME'] = str(base / f'state-{attempt}')
        result = subprocess.run(['qs', '-p', str(base / 'qml'), '--no-color'], env=env,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=10, check=True)
        line = next(line for line in result.stdout.splitlines() if 'NOTICE_RESULT=' in line)
        notes = json.loads(line.split('NOTICE_RESULT=', 1)[1])
        assert len(notes) == (1 if attempt == 0 else 0), result.stdout
        if notes:
            assert notes[0]['summary'] == 'Your panel customisation failed to load.'
            assert '(components/Panel.qml.broken-123)' in notes[0]['body']
            assert 'until you sign in again.' in notes[0]['body']
        assert (base / 'runtime/calls').read_text().splitlines() == ['called'] * (attempt + 1)
        assert not (base / 'runtime/pending').exists()
print('PASS: startup readiness, one notice with backup name, no duplicate after restart')
