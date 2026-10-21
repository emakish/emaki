#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Emaki's update reminder: waits until the shell is ready, speaks once per new batch, opens the app."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

CHECKS = '''
    property int opened: 0
    property var log: []
    Connections { target: notice; function onOpenRequested(): void { root.opened += 1; } }
    function snapshot(label: string): void {
        log.push({label: label, notes: notes.entries.map(e => ({summary: e.summary, body: e.body, app: e.app,
                                                                 actions: e.actions, transient: e.transient === true}))});
    }
    Timer {
        interval: 100; running: true
        onTriggered: {
            notice.pending = 3;          // not ready yet: nothing
            root.snapshot("not-ready");
            notice.ready = true;         // ready: one notice for 3
            root.snapshot("ready");
            notice.pending = 2;          // some installed: no news
            root.snapshot("fewer");
            notice.pending = 5;          // more arrived: a new notice
            root.snapshot("more");
            notice.pending = 0;          // all installed
            notice.pending = 1;          // a new batch later: a new notice, singular
            root.snapshot("again");
            notice.pending = 3;          // two more arrived
            notice.pending = 2;          // one installed: no news
            notice.pending = 3;          // one more arrived: news again (the mark followed the decrease)
            root.snapshot("partial");
            notice.pending = 3;          // the same count checked again: no news
            notice.ready = false;
            notice.ready = true;         // the shell came back: nothing new, no repeat
            root.snapshot("repeat");
            notice.ready = false;
            notice.pending = 4;          // arrived while not ready: told once it is ready
            root.snapshot("hidden");
            notice.ready = true;
            root.snapshot("shown");
            notes.activate(notes.entries[0].id, notice.action);
            root.snapshot("activated");
            console.log("NOTICE_RESULT=" + JSON.stringify({log: root.log, opened: root.opened}));
            Qt.quit();
        }
    }
'''

with tempfile.TemporaryDirectory(prefix='emaki-updates-notice-') as directory, \
        tempfile.TemporaryDirectory(prefix='emaki-notice-', dir='/tmp') as runtime:
    base = Path(directory)
    for folder in ('home', 'config', 'state', 'cache', 'qml'):
        (base / folder).mkdir(mode=0o700)
    for component in ('NotificationStore.qml', 'UpdatesNotice.qml', 'Platform.qml', 'Translation.qml'):
        shutil.copyfile(ROOT / 'shell' / component, base / 'qml' / component)
    (base / 'qml/qmldir').write_text('singleton Platform 1.0 Platform.qml\n'
                                     'singleton Translation 1.0 Translation.qml\n'
                                     'NotificationStore 1.0 NotificationStore.qml\n'
                                     'UpdatesNotice 1.0 UpdatesNotice.qml\n')
    (base / 'qml/helpers').mkdir()
    for helper in ('private-state.py', 'clipboard_store.py'):
        shutil.copyfile(ROOT / 'shell/helpers' / helper, base / 'qml/helpers' / helper)
    (base / 'qml/shell.qml').write_text('''import QtQuick
import Quickshell
import Emaki.I18n
ShellRoot {
    id: root
    NotificationStore { id: notes; dnd: true }
    UpdatesNotice { id: notice; store: notes }
''' + CHECKS + '''
}
''')
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_STATE_HOME=str(base / 'state'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_RUNTIME_DIR=runtime, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', EMAKI_LANGUAGE='en',
               EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'),
               QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + runtime + '/missing-bus',
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + runtime + '/missing-system-bus')
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_LOGGING_RULES', 'QT_QPA_PLATFORMTHEME'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(base / 'qml'), '--no-color'], env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
    assert result.returncode == 0, result.stdout
    line = next((l for l in result.stdout.splitlines() if 'NOTICE_RESULT=' in l), None)
    assert line, result.stdout
    data = json.loads(line.split('NOTICE_RESULT=', 1)[1])

steps = {entry['label']: entry['notes'] for entry in data['log']}
assert steps['not-ready'] == [], steps
assert [n['body'] for n in steps['ready']] == ['3 updates are ready to install.'], steps
assert steps['ready'][0]['summary'] == 'Updates available' and steps['ready'][0]['app'] == 'Programs & Updates', steps
assert steps['ready'][0]['actions'] == [{'id': 'emaki-updates-open', 'text': 'Open'}], steps
# Never saved with the history: each login raises one fresh card while updates wait.
assert steps['ready'][0]['transient'] is True, steps
assert len(steps['fewer']) == 1, steps
# One card at a time: a newer count replaces the unread card instead of stacking a second one.
assert [n['body'] for n in steps['more']] == ['5 updates are ready to install.'], steps
assert [n['body'] for n in steps['again']] == ['1 update is ready to install.'], steps
assert [n['body'] for n in steps['partial']] == ['3 updates are ready to install.'], steps
assert steps['repeat'] == steps['partial'], steps
assert steps['hidden'] == steps['repeat'], steps
assert [n['body'] for n in steps['shown']] == ['4 updates are ready to install.'], steps
assert steps['activated'] == [] and data['opened'] == 1, data
print('PASS: updates notice — waits for ready, one card with the latest count, Open opens Programs & Updates')
