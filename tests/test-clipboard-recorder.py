#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Count real recorder children through output hotplug and shared Pause/Resume."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time

from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
QML = r'''
import QtQuick
import Quickshell
ShellRoot {
    id: root
    property var a: ({name: "Laptop", width: 1280, height: 800, devicePixelRatio: 1})
    property var b: ({name: "External", width: 1440, height: 900, devicePixelRatio: 1})
    property int step: 0
    readonly property bool owned: Quickshell.env("EMAKI_SHELL_CLIPBOARD_RECORDER") === "1"
    function check(value, message) {
        if (!value) { console.error("RECORDER_FAILED " + message); Qt.exit(1); throw new Error(message); }
    }
    NiriService { id: niri; binary: "" }
    ShellOutputs {
        id: outputs
        niri: niri
        screens: [root.a, root.b]
        headless: true
        borderMode: "soft"
        reservedSpace: 0
        testWidth: 0
        testHeight: 0
    }
    function histories() { return outputs.instances.map(instance => instance.scene.input.clipboard); }
    Timer {
        interval: 1000
        running: true
        repeat: true
        onTriggered: {
            const list = root.histories();
            if (root.step === 0) {
                root.check(list.length === 2, "two launchers");
                root.check(list[0] !== list[1], "private history readers");
                root.check(list.every(clips => clips.recorderSource === outputs.shared.clipboard), "shared recorder owner");
                root.check(list.every(clips => clips.recorder === (root.owned ? "recording" : "external")), "initial status");
                console.log("RECORDER_CHECK initial");
                outputs.screens = [root.b];
            } else if (root.step === 1) {
                root.check(list.length === 1, "removed output");
                console.log("RECORDER_CHECK removed");
                root.check(list[0].setRecording(false) === root.owned, "pause authorization");
            } else if (root.step === 2) {
                root.check(list[0].recorder === (root.owned ? "paused" : "external"), "pause status");
                console.log("RECORDER_CHECK paused");
                outputs.screens = [root.a, root.b];
            } else if (root.step === 3) {
                root.check(list.length === 2, "recreated output");
                root.check(list.every(clips => clips.recorder === (root.owned ? "paused" : "external")), "new launcher inherits pause");
                console.log("RECORDER_CHECK recreated");
                root.check(list[0].setRecording(true) === root.owned, "resume authorization");
            } else if (root.step === 4) {
                root.check(list.every(clips => clips.recorder === (root.owned ? "recording" : "external")), "shared resume status");
                console.log("RECORDER_CHECK resumed");
                outputs.screens = [];
            } else if (root.step === 5) {
                root.check(list.length === 0, "all outputs removed");
                root.check(outputs.shared.clipboard.recorder === (root.owned ? "recording" : "external"), "session owner survives");
                console.log("RECORDER_CHECK empty");
                console.log("RECORDER_COMPLETE");
                running = false;
            }
            root.step++;
        }
    }
}
'''


def run(owned):
    with tempfile.TemporaryDirectory(prefix='clipboard-recorder-') as temporary:
        profile = Path(temporary)
        for part in ('cache', 'config', 'state', 'data', 'tmp'):
            (profile / part).mkdir(mode=0o700)
        qml = profile / 'qml'
        shutil.copytree(ROOT / 'shell', qml)
        (qml / 'shell.qml').write_text(QML)
        events = profile / 'events'
        paste = profile / 'paste'
        paste.write_text('''#!/usr/bin/python3
import json, os, signal, time
path = os.environ["RECORDER_EVENTS"]
def event(kind):
    with open(path, "a") as stream:
        stream.write(json.dumps([kind, os.getpid()]) + "\\n")
def stop(*_):
    event("stop")
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
event("start")
while True:
    time.sleep(1)
''')
        paste.chmod(0o700)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', QT_SCALE_FACTOR='1', HOME=str(profile),
                   EMAKI_SHELL_TRAY='0', EMAKI_TEST_SYSTEM='0', EMAKI_BIN='', EMAKI_SETTINGS_PROFILE='',
                   EMAKI_TEST_MPRIS='0', EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_GLASS_RENDERER='canvas',
                   EMAKI_FIXTURE_WALLPAPER='', EMAKI_SESSION_START='', EMAKI_SESSION_SKIP_INTRO='',
                   EMAKI_SHELL_CLIPBOARD_RECORDER=str(int(owned)), EMAKI_WL_PASTE=str(paste),
                   EMAKI_PYTHON='/usr/bin/python3', RECORDER_EVENTS=str(events),
                   XDG_RUNTIME_DIR=str(runtime_path(profile)), NIRI_SOCKET='',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'),
                   TMPDIR=str(profile / 'tmp'))
        for kind in ('CACHE', 'CONFIG', 'STATE', 'DATA'):
            env['XDG_' + kind + '_HOME'] = str(profile / kind.lower())
        env['XDG_DATA_DIRS'] = str(profile / 'data')
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_SCREEN_SCALE_FACTORS', 'QT_PLUGIN_PATH',
                     'QML_IMPORT_PATH', 'QML2_IMPORT_PATH', 'QT_LOGGING_RULES'):
            env.pop(name, None)
        expected = {'initial': (1, 0), 'removed': (1, 0), 'paused': (1, 1),
                    'recreated': (1, 1), 'resumed': (2, 1), 'empty': (2, 1)}
        seen = {}
        lines = []

        def counts():
            records = [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
            return tuple(sum(record[0] == kind for record in records) for kind in ('start', 'stop'))

        with subprocess.Popen(['qs', '-p', str(qml), '--no-color'], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
            timeout = threading.Timer(20, process.kill)
            timeout.start()
            try:
                for line in process.stdout:
                    lines.append(line)
                    if 'RECORDER_CHECK ' in line:
                        phase = line.split('RECORDER_CHECK ', 1)[1].strip()
                        seen[phase] = counts()
                    if 'RECORDER_COMPLETE' in line:
                        process.terminate()
                returncode = process.wait(timeout=5)
            finally:
                timeout.cancel()
                if process.poll() is None:
                    process.kill()
        log = ''.join(lines)
        assert returncode in (0, -15) and 'RECORDER_COMPLETE' in log, log
        assert seen == {phase: count if owned else (0, 0) for phase, count in expected.items()}, (seen, log)
        # Normal shell teardown may kill the whole child group directly. Verify
        # child liveness rather than requiring its signal handler to run.
        records = [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []
        started = [pid for kind, pid in records if kind == 'start']

        def alive(pid):
            status = Path(f'/proc/{pid}/stat')
            try:
                return status.read_text().rsplit(')', 1)[1].split()[0] != 'Z'
            except (FileNotFoundError, ProcessLookupError):
                return False

        deadline = time.monotonic() + 5
        while any(alive(pid) for pid in started) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not any(alive(pid) for pid in started), ('recorder shutdown', started)
        unexpected = [line for line in lines if any(word in line for word in ('ReferenceError', 'TypeError', 'Binding loop', 'ERROR'))
                      and 'quickshell.ipc: Failed to start IPC server' not in line]
        assert not unexpected, ''.join(unexpected)
        if 'quickshell.ipc: Failed to start IPC server' in log:
            print('Restricted environment: Unix IPC unavailable; self-driven recorder assertions ran.')
        print('PASS: shared clipboard recorder, hotplug, Pause/Resume and shutdown; opt-in=' + str(int(owned)))


if __name__ == '__main__':
    run(True)
    run(False)
