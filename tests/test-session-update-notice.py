#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Update notices wait for startup, ignore live media and survive observer failure."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
evidence = ROOT / '.cache/evidence/u6d'
evidence.mkdir(parents=True, exist_ok=True)


def run_case(name, watcher, checks, *, live=False, fallback=False, unmanaged=False, duration=800, setup=None):
    with tempfile.TemporaryDirectory(prefix='session-notice-', dir=evidence) as directory, \
            tempfile.TemporaryDirectory(prefix='emaki-notice-', dir='/tmp') as runtime:
        base = Path(directory)
        for folder in ('home', 'config', 'state', 'cache', 'qml', 'generation'):
            (base / folder).mkdir(mode=0o700)
        for component in ('NotificationStore.qml', 'SessionUpdateNotice.qml', 'Platform.qml'):
            shutil.copyfile(ROOT / 'shell' / component, base / 'qml' / component)
        (base / 'qml/qmldir').write_text('singleton Platform 1.0 Platform.qml\n'
                                              'NotificationStore 1.0 NotificationStore.qml\n'
                                              'SessionUpdateNotice 1.0 SessionUpdateNotice.qml\n')
        (base / 'generation/watch.py').write_text(watcher)
        (base / 'qml/shell.qml').write_text('''import QtQuick
import Quickshell
ShellRoot {
    NotificationStore { id: notes; dnd: true }
    SessionUpdateNotice { id: notice; store: notes }
''' + checks + '''
    Timer {
        interval: ''' + str(duration) + '''; running: true
        onTriggered: {
            console.log("UPDATE_RESULT=" + JSON.stringify(notes.entries));
            Qt.quit();
        }
    }
}
''')
        env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
                   XDG_STATE_HOME=str(base / 'state'), XDG_CACHE_HOME=str(base / 'cache'),
                   XDG_RUNTIME_DIR=runtime, QT_QPA_PLATFORM='offscreen',
                   QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                   EMAKI_SESSION_GENERATION='' if fallback or unmanaged else str(base / 'generation'),
                   EMAKI_SESSION_SOURCE=str(base / 'qml') if fallback else '',
                   EMAKI_SESSION_WATCHER=str(base / 'generation/watch.py') if fallback else '',
                   EMAKI_LIVE_SESSION='1' if live else '0',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + runtime + '/missing-bus',
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + runtime + '/missing-system-bus')
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_LOGGING_RULES'):
            env.pop(key, None)
        if setup:
            env.update(setup(base, runtime))
        result = subprocess.run(['qs', '-p', str(base / 'qml'), '--no-color'], env=env,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=duration / 1000 + 10)
        (evidence / ('notice-' + name + '.log')).write_text(result.stdout)
        assert result.returncode == 0, result.stdout
        line = next(line for line in result.stdout.splitlines() if 'UPDATE_RESULT=' in line)
        notes = json.loads(line.split('UPDATE_RESULT=', 1)[1])
        started = (base / 'generation/started').exists()
        attempts_file = base / 'generation/attempts'
        attempts = attempts_file.read_text() if attempts_file.exists() else ''
        return notes, started, attempts


notification = '{"schema":1,"restart":true}'
notes, _, _ = run_case('startup', 'print(' + repr(notification) + ', flush=True)\n', '''
    Timer {
        interval: 300; running: true
        onTriggered: {
            if (!notice.pending || notice.shown || notes.count !== 0) Qt.exit(2);
            notice.ready = true;
        }
    }
    Timer {
        interval: 500; running: true
        onTriggered: {
            notice.ready = false; notice.ready = true;
            let dismissed = false;
            notes.accept({appName: "Desktop update", summary: "Sign out and sign in again to finish updating the desktop.", dismiss: function() { dismissed = true; }});
            if (!dismissed) Qt.exit(3);
        }
    }
''')
assert len(notes) == 1 and notes[0]['critical'] and notes[0]['sessionUpdate'], notes
assert not notes[0]['batteryWarning'] and notes[0]['deadline'] == 0, notes
assert notes[0]['summary'] == 'Sign out and sign in again to finish updating the desktop.', notes

notes, started, _ = run_case('live',
    'from pathlib import Path\nPath(__file__).with_name("started").touch()\nprint(' + repr(notification) + ', flush=True)\n', '''
    Timer {
        interval: 300; running: true
        onTriggered: {
            notice.ready = true;
            if (notice.pending || notice.shown || notes.count !== 0) Qt.exit(4);
            // A marker indication must be ignored even if it arrives externally.
            notice.pending = true;
            let dismissed = false;
            notes.accept({appName: "Desktop update", summary: "Sign out and sign in again to finish updating the desktop.", dismiss: function() { dismissed = true; }});
            if (!dismissed || notice.shown || notes.count !== 0) Qt.exit(5);
        }
    }
''', live=True)
assert notes == [] and not started, (notes, started)

watcher = '''from pathlib import Path
import sys
attempts = Path(__file__).with_name("attempts")
previous = attempts.read_text() if attempts.exists() else ""
attempts.write_text(previous + "attempt\\n")
if not previous:
    print("incomplete output", flush=True)
    sys.exit(1)
''' + 'print(' + repr(notification) + ', flush=True)\n'
notes, _, attempts = run_case('retry', watcher, '''
    Component.onCompleted: notice.ready = true
    Timer {
        interval: 1000; running: true
        onTriggered: {
            if (notice.pending || notice.shown || notes.count !== 0) Qt.exit(6);
        }
    }
''', duration=6500)
assert len(attempts.splitlines()) == 2, attempts
assert len(notes) == 1 and notes[0]['sessionUpdate'], notes
print('PASS: startup gating, critical persistent notice, no duplicate, live suppression, failed observer retry')

notes, _, _ = run_case('installed', 'import sys\nassert sys.argv[1] == "watch-installed"\nprint(' + repr(notification) + ', flush=True)\n', '''
    Timer {
        interval: 300; running: true
        onTriggered: {
            if (!notice.pending || notice.shown || notes.count !== 0) Qt.exit(7);
            notice.ready = true;
            let dismissed = false;
            notes.accept({appName: "Desktop update", summary: "Sign out and sign in again to finish updating the desktop.", dismiss: function() { dismissed = true; }});
            if (!dismissed) Qt.exit(8);
        }
    }
''', fallback=True)
assert len(notes) == 1 and notes[0]['critical'] and notes[0]['sessionUpdate'], notes
assert notes[0]['deadline'] == 0, notes

notes, _, _ = run_case('unmanaged-hook', '', '''
    Component.onCompleted: {
        const hook = notes.snapshot({id: 21, appName: "Desktop update", appIcon: "", summary: "Sign out and sign in again to finish updating the desktop.", body: "Save your work first [Emaki].", actions: [], expireTimeout: 0}, Date.now());
        if (!hook.critical || hook.deadline !== 0) Qt.exit(9);
    }
''', unmanaged=True)
assert notes == [], notes
print('PASS: installed-file observer, preserved startup gating, critical hook fallback and duplicate suppression')

# The real observer against the desktop state command: the panel learns of an update only from
# `emaki-session-update state`, never from the package manager. A fixture command answers.
def backend_case(name, baseline, answer):
    def setup(base, runtime):
        shutil.copyfile(ROOT / 'scripts/emaki-session-files', base / 'generation/watch.py')
        shutil.copyfile(ROOT / 'scripts/emaki_session_state.py', base / 'generation/emaki_session_state.py')
        # Same identity as emaki-session-files for a session without a compositor socket.
        identity = hashlib.sha256(('offscreen\0' + str(base / 'qml')).encode()).hexdigest()[:24]
        session = Path(runtime) / 'emaki-session-files' / identity
        session.mkdir(parents=True)
        (session / 'session.json').write_text(json.dumps(baseline))
        tools = base / 'bin'
        tools.mkdir()
        (base / 'state.json').write_text(json.dumps(answer))
        command = tools / 'emaki-session-update'
        command.write_text('#!/bin/sh\n: > ' + str(base / 'generation/started') + '\n'
                           '[ "$1" = state ] && exec cat ' + str(base / 'state.json') + '\nexit 2\n')
        command.chmod(0o700)
        return dict(PATH=str(tools) + ':' + os.environ['PATH'])
    notes, asked, _ = run_case('backend-' + name, '', '''
    Component.onCompleted: notice.ready = true
''', fallback=True, duration=2500, setup=setup)
    assert asked, 'the observer never asked the desktop state command: ' + name
    return notes


update = dict(id='update-two', at='2026-10-09T12:00:00+00:00', components=['quickshell-emaki'], action='sign-out')
notes = backend_case('new-update', dict(desktop='desk-one'),
                     dict(schema=1, busy=False, desktop='desk-one', update=update))
assert len(notes) == 1 and notes[0]['sessionUpdate'] and notes[0]['critical'], notes
notes = backend_case('missing-record', dict(desktop='desk-one'),
                     dict(schema=1, busy=False, desktop='desk-one', update=None))
assert notes == [], notes
notes = backend_case('stale-record', dict(desktop='desk-one', transaction='update-two'),
                     dict(schema=1, busy=False, desktop='desk-one', update=update))
assert notes == [], notes
notes = backend_case('busy', dict(desktop='desk-one'),
                     dict(schema=1, busy=True, desktop='desk-two', update=update))
assert notes == [], notes
notes = backend_case('desktop-changed', dict(desktop='desk-one'),
                     dict(schema=1, busy=False, desktop='desk-two', update=None))
assert len(notes) == 1 and notes[0]['sessionUpdate'], notes
print('PASS: notice from the desktop state command: new update, missing and stale records, busy system, changed desktop')
