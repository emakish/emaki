#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offscreen power refresh and scheduled quiet-mode override regressions."""
import argparse
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from runtime_fixture import runtime_path
import reaper
reaper.guard()

parser = argparse.ArgumentParser()
parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
parser.add_argument('--case', choices=('power', 'dnd', 'restore', 'settings'))
args = parser.parse_args()

COMMON = '''import QtQuick
import Quickshell
import Quickshell.Io
Scope {
    id: fixture
    property bool failed: false
    function check(ok, label) {
        if (!ok) { failed = true; console.error("REGRESSION_FAIL", label); }
    }
    function finish() { console.log(failed ? "REGRESSION_FAILED" : "REGRESSION_PASS"); Qt.quit(); }
'''
POWER = '''
    SystemService { id: service; helpersEnabled: true; panelOpen: true }
    FileView { id: profile; path: Quickshell.env("PROFILE_FILE"); blockLoading: true }
    property int step: 0
    Timer {
        interval: 2600; running: true; repeat: true
        onTriggered: {
            if (fixture.step === 0) {
                fixture.check(service.profiles.current === "balanced", "initial profile");
                profile.setText("power-saver");
            } else if (fixture.step === 1) {
                fixture.check(service.profiles.current === "power-saver", "external change while open");
                service.panelOpen = false;
                profile.setText("performance");
            } else if (fixture.step === 2) {
                fixture.check(service.profiles.current === "power-saver", "closed panel does not poll");
                service.panelOpen = true;
            } else {
                fixture.check(service.profiles.current === "performance", "reopening refreshes");
                fixture.finish();
            }
            fixture.step++;
        }
    }
}'''
DND = '''
    QtObject {
        id: catalog
        property var values: []
        function set(key, value) {
            values = values.map(row => row.key === key ? {key:key, value:key === "notifications.dnd" ? value === "true" : value, override_value: false} : row);
        }
    }
    NotificationStore { id: notes; catalog: catalog }
    ClockBody { id: panel; store: notes; opened: true; width: 560; serverState: "active" }
    function find(item, name) {
        if (item.objectName === name) return item;
        for (const child of item.children || []) { const found = find(child, name); if (found) return found; }
        return null;
    }
    function setup(start, end, hour) {
        notes.setDnd(true);
        notes.now = new Date(2026, 9, 15, hour, 30).getTime();
        catalog.values = [
            {key:"notifications.dnd", value:false, override_value:false},
            {key:"notifications.until", value:"0"},
            {key:"notifications.schedule", value:JSON.stringify({enabled:true,start:start,end:end})}
        ];
    }
    Timer { interval: 50; running: true; onTriggered: fixture.run() }
    Timer { id: saved; interval: 600; onTriggered: fixture.finish() }
    function run() {
        for (const scenario of [["09:00", "17:00", 12, 16], ["22:00", "07:00", 23, 16], ["22:00", "07:00", 2, 15], ["06:00", "06:00", 12, 16]]) {
            setup(scenario[0], scenario[1], scenario[2]);
            check(notes.effectiveDnd, "scheduled mode starts on");
            notes.setDnd(false);
            check(!notes.effectiveDnd, "off overrides active schedule");
            const expected = new Date(2026, 9, scenario[3], Number(scenario[0].slice(0,2)), 0).getTime();
            check(notes.scheduleSuppressedUntil === expected, "next local start deadline");
            const notice = find(panel, "dnd-schedule-resume");
            check(notice && notice.visible && notice.text.includes(Qt.formatDateTime(new Date(expected), "ddd HH:mm")), "panel shows next start");
            notes.now = expected - 1;
            check(!notes.effectiveDnd, "suppressed through next start");
            notes.now = expected;
            check(notes.effectiveDnd, "next start restores schedule");
        }
        setup("22:00", "07:00", 23);
        notes.setDnd(false);
        notes.setDnd(true);
        check(notes.effectiveDnd && !notes.scheduleSuppressed, "manual enable clears suppression");
        notes.setDnd(false);
        catalog.set("notifications.schedule", JSON.stringify({enabled:true,start:"00:00",end:"00:00"}));
        check(notes.effectiveDnd, "edited schedule takes effect");
        setup("22:00", "07:00", 23);
        notes.setDnd(false);
        saved.start();
    }
}'''
SETTINGS = DND.replace('    ClockBody { id: panel; store: notes; opened: true; width: 560; serverState: "active" }',
    '    NotificationsSettingsPage { id: panel; store: notes; catalog: catalog; width: 720 }').replace(
    '        property var values: []',
    '        property var values: []\n        property bool writing: false\n        function row(key) { return values.find(row => row.key === key) || null; }').replace(
    '            notes.setDnd(false);',
    '            const toggle = find(panel, "setting-notifications.dnd");\n            check(toggle.value === true, "settings switch shows active schedule");\n            toggle.requestValue(false);\n            check(toggle.value === false, "settings switch turns schedule off");').replace(
    'find(panel, "dnd-schedule-resume")', 'find(panel, "settings-notifications-status")')
RESTORE = '''
    QtObject {
        id: catalog
        property var values: [
            {key:"notifications.dnd",value:false,override_value:false},
            {key:"notifications.schedule",value:'{"enabled":true,"start":"22:00","end":"07:00"}'}
        ]
    }
    NotificationStore { id: notes; catalog: catalog; now: new Date(2026, 9, 15, 23, 30).getTime() }
    Timer { interval: 50; running: true; onTriggered: {
        fixture.check(!notes.effectiveDnd, "restart preserves schedule override");
        notes.now = new Date(2026, 9, 16, 22, 0).getTime();
        fixture.check(notes.effectiveDnd, "restored override expires");
        fixture.finish();
    } }
}'''
with tempfile.TemporaryDirectory(prefix='settings-live-policy-', dir='/tmp') as temporary:
    base = Path(temporary)
    shutil.copytree(args.source_root / 'shell', base / 'shell')
    for name in ('home', 'config', 'state', 'data', 'cache', 'runtime'):
        (base / name).mkdir(mode=0o700)
    (base / 'profile').write_text('balanced')
    (base / 'shell/helpers/system-tools.py').write_text('''import json, os, sys
from pathlib import Path
request = json.loads(sys.stdin.readline())
reply = {"schema_version": 1, "state": "unavailable"}
if request["op"] == "profiles-read":
    reply.update(state="ready", current=Path(os.environ["PROFILE_FILE"]).read_text(), profiles=["balanced","power-saver","performance"])
print(json.dumps(reply))
''')
    env = dict(os.environ, HOME=str(base/'home'), XDG_CONFIG_HOME=str(base/'config'),
               XDG_CONFIG_DIRS=str(base/'config'), XDG_STATE_HOME=str(base/'state'),
               XDG_DATA_HOME=str(base/'data'), XDG_DATA_DIRS=str(base/'data'),
               XDG_CACHE_HOME=str(base/'cache'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
               PROFILE_FILE=str(base/'profile'), EMAKI_UDEVADM='true', EMAKI_NMCLI='true',
               DBUS_SESSION_BUS_ADDRESS='unix:path='+str(base/'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path='+str(base/'no-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
        env.pop(key, None)
    for name, fixture in [('power', POWER), ('dnd', DND), ('restore', RESTORE), ('settings', SETTINGS)]:
        if args.case and args.case != name:
            continue
        if name == 'restore':
            state = base/'state/emaki/notifications.json'
            state.parent.mkdir(exist_ok=True)
            state.write_text(json.dumps(dict(version=1, dnd=False, dndMigrated=True,
                scheduleSuppressedUntil=datetime.datetime(2026,10,16,22).timestamp()*1000,
                suppressedSchedule='{"enabled":true,"start":"22:00","end":"07:00"}')))
        (base/'shell/live-test.qml').write_text(COMMON + fixture)
        result = subprocess.run(['qs', '-p', str(base/'shell/live-test.qml'), '--no-color'],
                                env=env, capture_output=True, text=True, timeout=20)
        output = result.stdout + result.stderr
        assert result.returncode == 0 and 'REGRESSION_PASS' in output and 'REGRESSION_FAIL' not in output, (name, output)
        if name in ('dnd', 'settings'):
            saved = json.loads((base/'state/emaki/notifications.json').read_text())
            assert saved['scheduleSuppressedUntil'] == datetime.datetime(2026,10,16,22).timestamp()*1000
            assert json.loads(saved['suppressedSchedule']) == dict(enabled=True,start='22:00',end='07:00')
        print('PASS:', name)
