#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real clock worker lifecycle and local actions in an isolated, silent shell."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from unittest.mock import patch

from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
with tempfile.TemporaryDirectory(prefix='clock-service-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    shutil.copyfile(ROOT / 'tests/fixtures/ClockToolsServiceTest.qml', base / 'shell/test.qml')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', HOME=str(base / 'home'),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'),
               PIPEWIRE_REMOTE='clock-test-no-audio')
    for name in ('home', 'config', 'data', 'state', 'cache'):
        (base / name).mkdir(mode=0o700)
        if name != 'home':
            env['XDG_' + name.upper() + '_HOME'] = str(base / name)
    env['XDG_RUNTIME_DIR'] = str(runtime_path(base))
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_QPA_PLATFORMTHEME', 'NIRI_SOCKET', 'EMAKI_NOTES_HELPER'):
        env.pop(name, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=45)
    output = result.stdout + result.stderr
    evidence = ROOT / '.cache/clock-tools'
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / 'service.log').write_text(output)
    assert result.returncode == 0 and 'CLOCK_SERVICE_RESULT 3 0' in output, output
    assert not any(error in output for error in ('TypeError:', 'ReferenceError:', 'Binding loop')), output
    assert (base / 'state/emaki/clock-tools.json').stat().st_mode & 0o777 == 0o600
    history = json.loads((base / 'state/emaki/notifications.json').read_text())['entries']
    assert any(entry['summary'] == 'Keep me' for entry in history), history
    assert not any(entry['app'] == 'Clocks' for entry in history), history
    # A new shell instance must restore the pending card without an arrival or sound.
    pending = json.loads((base / 'state/emaki/clock-tools.json').read_text())['notices'][0]
    restart_qml = """// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell
ShellRoot {
    id: fixture
    property int arrivals: 0
    property int sounds: 0
    NotificationStore { id: notes; onArrived: fixture.arrivals++ }
    ClockToolsService {
        id: service; active: true; store: notes; soundCommand: ["true"]
        onSoundRunningChanged: if (soundRunning) fixture.sounds++
    }
    Timer {
        interval: 1500; running: true
        onTriggered: {
            const restored = notes.entries.filter(n => n.app === "Clocks");
            console.log("RESTORE_RESULT", service.state.ready, fixture.arrivals, fixture.sounds,
                        restored.length, restored.length ? restored[0].time : 0);
            Qt.quit();
        }
    }
}
"""
    (base / 'shell/restart.qml').write_text(restart_qml)
    restarted = subprocess.run(['qs', '-p', str(base / 'shell/restart.qml'), '--no-color'],
                               env=env, capture_output=True, text=True, timeout=10)
    restart_output = restarted.stdout + restarted.stderr
    assert restarted.returncode == 0, restart_output
    assert 'RESTORE_RESULT true 0 0 1 ' in restart_output, restart_output
    restored_time = float(restart_output.split('RESTORE_RESULT true 0 0 1 ')[1].split()[0])
    assert abs(restored_time - pending['created'] * 1000) < 1, restart_output

    # The first worker check can create fresh notices alongside saved ones.
    # Use a due snooze so this check cannot race a wall-clock minute boundary.
    state_path = base / 'state/emaki/clock-tools.json'
    startup_state = json.loads(state_path.read_text())
    due = time.time() - 1
    startup_state['timer'].update(status='running', deadline=due, pomodoro=False)
    startup_state['alarms'] = [{
        'id': 'startup-alarm', 'hour': 0, 'minute': 0, 'days': [],
        'enabled': False, 'lastDate': '', 'snooze': due, 'label': 'Startup alarm',
    }]
    # Also retain an older-schema notice with no issued timestamp.
    legacy = dict(pending, id='legacy-notice')
    legacy.pop('issued', None)
    startup_state['notices'].append(legacy)
    state_path.write_text(json.dumps(startup_state))
    startup_qml = restart_qml.replace(
        'restored.length, restored.length ? restored[0].time : 0',
        'restored.length, Object.keys(service.ringing).length')
    (base / 'shell/startup.qml').write_text(startup_qml)
    started = subprocess.run(['qs', '-p', str(base / 'shell/startup.qml'), '--no-color'],
                             env=env, capture_output=True, text=True, timeout=10)
    startup_output = started.stdout + started.stderr
    assert started.returncode == 0, startup_output
    assert 'RESTORE_RESULT true 2 1 4 2' in startup_output, startup_output
    startup_notices = json.loads(state_path.read_text())['notices']
    fresh = [notice for notice in startup_notices if notice['id'] not in (pending['id'], legacy['id'])]
    assert {notice['kind'] for notice in fresh} == {'timer', 'alarm'}, startup_notices
    assert all(notice['created'] == due for notice in fresh), startup_notices

    # A saved notice issued "after now" means the system clock went back: it is old, so it is
    # restored without an arrival, a sound or a ringing chime.
    future_state = json.loads(state_path.read_text())
    ahead = time.time() + 3600
    future_state['timer'].update(status='idle', deadline=None)
    future_state['alarms'] = []
    future_state['notices'] = [dict(pending, id='future-notice', created=ahead, issued=ahead)]
    state_path.write_text(json.dumps(future_state))
    clock_back = subprocess.run(['qs', '-p', str(base / 'shell/startup.qml'), '--no-color'],
                                env=env, capture_output=True, text=True, timeout=10)
    clock_back_output = clock_back.stdout + clock_back.stderr
    assert clock_back.returncode == 0, clock_back_output
    assert 'RESTORE_RESULT true 0 0 1 0' in clock_back_output, clock_back_output

spec = importlib.util.spec_from_file_location('clock_chime', ROOT / 'shell/helpers/clock-chime.py')
chime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chime)
with patch.object(chime.subprocess, 'run') as play:
    play.return_value.returncode = 0
    assert chime.main() == 0
    assert play.call_args.args[0][0] == 'pw-play'
    assert len(play.call_args.kwargs['input']) == 60000
    assert play.call_args.kwargs['timeout'] == 8
with patch.object(chime.subprocess, 'run', side_effect=FileNotFoundError):
    assert chime.main() == 1
print('PASS: clock worker restart, timer/alarm actions, dismissal, world zones and sound transport')
