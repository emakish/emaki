#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Recovery notices run only once, after startup, in an isolated offscreen shell."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

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
case "$1" in
    started) mkdir -p "$XDG_RUNTIME_DIR/emaki-shell-health"; exit 0 ;;
    clean) exit 0 ;;
esac
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
    Connections {
        target: notes
        function onLocalActionRequested(action) { console.log("LOCAL_ACTION=" + action); }
    }
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
            if (notes.count > 0) {
                const id = notes.entries[0].id;
                // The notice's body is not the report: only its named action opens anything.
                const plain = notes.activate(id, "default");
                const chosen = notes.activate(id, "report:shell");
                console.log("ACTIVATED=" + JSON.stringify({default: plain, chosen: chosen,
                    left: notes.entries.some(e => e.id === id), state: notes.actionState}));
            }
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
    def value(output, key):
        lines = [line.split(key + '=', 1)[1] for line in output.splitlines() if key + '=' in line]
        return lines

    (base / 'runtime/pending').write_text(json.dumps({'backup': 'components/Panel.qml.broken-123'}))
    # 0: a set-aside customisation; 1: a restart with nothing pending; 2: a crash without a backup.
    for attempt in range(3):
        if attempt == 2:
            (base / 'runtime/pending').write_text(json.dumps({'crashed': True}))
        # Separate history storage proves the second start receives no new notice.
        env['XDG_STATE_HOME'] = str(base / f'state-{attempt}')
        result = subprocess.run(['qs', '-p', str(base / 'qml'), '--no-color'], env=env,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=10, check=True)
        output = result.stdout
        for error in ('ReferenceError', 'TypeError', 'Unable to assign', 'Cannot assign'):
            assert error not in output, output
        notes = json.loads(value(output, 'NOTICE_RESULT')[0])
        assert len(notes) == (0 if attempt == 1 else 1), output
        if attempt == 0:
            assert notes[0]['summary'] == 'Your panel customisation failed to load.'
            assert '(components/Panel.qml.broken-123)' in notes[0]['body']
            assert 'until you sign in again.' in notes[0]['body']
        if attempt == 2:
            assert notes[0]['summary'].endswith('The panel stopped and was restarted.'), notes
        if notes:
            # One action, the report for the shell; choosing it closes the notice and names it.
            actions = notes[0]['actions']
            assert len(actions) == 1 and actions[0]['id'] == 'report:shell', actions
            assert 'Report' in actions[0]['text'], actions
            activated = json.loads(value(output, 'ACTIVATED')[0])
            assert activated == {'default': False, 'chosen': True, 'left': False, 'state': 'requested'}, activated
            assert value(output, 'LOCAL_ACTION') == ['report:shell'], output
        else:
            assert value(output, 'ACTIVATED') == [] and value(output, 'LOCAL_ACTION') == [], output
        assert (base / 'runtime/calls').read_text().splitlines() == ['called'] * (attempt + 1)
        assert not (base / 'runtime/pending').exists()
print('PASS: startup readiness, one notice with backup name, no duplicate after restart, crash notice; '
      'each offers one Report action that closes the notice and asks for the shell report')

# Exercise the real helper and shell lifecycle, including a process killed without
# QML teardown. Separate history directories keep old inert notices out of counts.
with tempfile.TemporaryDirectory(prefix='recovery-life-', dir=evidence) as directory:
    base = Path(directory)
    for name in ('home', 'config', 'state', 'cache', 'runtime', 'bin', 'qml'):
        (base / name).mkdir(mode=0o700)
    for name in ('NotificationStore.qml', 'ShellRecoveryNotice.qml'):
        shutil.copyfile(ROOT / 'shell' / name, base / 'qml' / name)
    for name in ('emaki-shell-health', 'emaki_paths.py'):
        shutil.copyfile(ROOT / 'scripts' / name, base / 'bin' / name)
    real_helper = base / 'bin/emaki-shell-health-real'
    (base / 'bin/emaki-shell-health').rename(real_helper)
    real_helper.chmod(0o700)
    # Force cleanup to finish after the immediate restart. The synchronous QML
    # receipt must prevent a false crash while the detached helper is delayed.
    (base / 'bin/emaki-shell-health').write_text(
        '#!/bin/sh\n[ "$1" != clean ] || sleep 1.2\n'
        + 'exec ' + str(real_helper) + ' "$@"\n')
    (base / 'bin/emaki-shell-health').chmod(0o700)
    (base / 'qml/shell.qml').write_text('''import QtQuick
import Quickshell
ShellRoot {
    NotificationStore { id: notes }
    ShellRecoveryNotice { store: notes; ready: true }
    Timer {
        interval: 700; running: true
        onTriggered: {
            console.log("LIFECYCLE_NOTICES=" + JSON.stringify(notes.entries));
            if (Quickshell.env("LIFECYCLE_HOLD") !== "1") Qt.quit();
        }
    }
}
''')
    env = dict(os.environ, HOME=str(base / 'home'),
               XDG_CONFIG_HOME=str(base / 'config'), XDG_STATE_HOME=str(base / 'state'),
               XDG_CACHE_HOME=str(base / 'cache'), XDG_RUNTIME_DIR=str(base / 'runtime'),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'missing-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'missing-system-bus'),
               PATH=str(base / 'bin') + os.pathsep + os.environ['PATH'])
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_LOGGING_RULES'):
        env.pop(key, None)
    marker = base / 'runtime/emaki-shell-health/running.json'
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()

    def poll(predicate, description):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(0.02)
        raise AssertionError(description)

    def lifecycle(attempt, hold=False, terminate=False, reload=False, requested=False, legacy=False):
        # Notification history is independent from health's persistent boot budget.
        history = Path(env['XDG_STATE_HOME']) / 'emaki/notifications.json'
        history.unlink(missing_ok=True)
        runenv = dict(env, LIFECYCLE_HOLD='1' if hold else '0')
        log = base / ('launch-' + str(attempt) + '.log')
        with log.open('w') as output:
            process = subprocess.Popen(['qs', '-n', '-p', str(base / 'qml'), '--no-color'],
                                       env=runenv, text=True, stdout=output,
                                       stderr=subprocess.STDOUT)
        try:
            poll(lambda: 'LIFECYCLE_NOTICES=' in log.read_text(),
                 'Shell lifecycle fixture did not finish: ' + log.read_text())
            output = log.read_text()
            for error in ('ReferenceError', 'TypeError', 'Unable to assign', 'Cannot assign'):
                assert error not in output, output
            notes = json.loads(value(output, 'LIFECYCLE_NOTICES')[0])
            if hold:
                current = json.loads(marker.read_text())
                assert current['pid'] == process.pid and current['boot_id'] == boot, current
                if reload:
                    fixture_path = base / 'qml/shell.qml'
                    original = fixture_path.read_text()
                    fixture_path.write_text(original.replace(
                        'Quickshell.env("LIFECYCLE_HOLD") !== "1"', 'true'))
                    def reloaded():
                        try:
                            return json.loads(marker.read_text())['token'] != current['token']
                        except (FileNotFoundError, ValueError):
                            return False
                    poll(reloaded, 'Reload did not register a new shell generation')
                    assert json.loads(marker.read_text())['pid'] == process.pid
                    assert process.wait(timeout=5) == 0
                    fixture_path.write_text(original)
                    rows = value(log.read_text(), 'LIFECYCLE_NOTICES')
                    assert len(rows) == 2 and json.loads(rows[1]) == [], rows
                elif terminate:
                    if requested:
                        subprocess.run([str(real_helper), 'restart-begin'], env=runenv,
                                       capture_output=True, check=True, timeout=5)
                    process.terminate()
                    assert process.wait(timeout=5) in (0, -signal.SIGTERM), output
                    if legacy:
                        subprocess.run([str(real_helper), 'stopped'],
                                       env=dict(runenv, SERVICE_RESULT='success'),
                                       capture_output=True, check=True, timeout=5)
                else:
                    process.kill()
                    process.wait(timeout=5)
                    assert marker.exists(), 'An abrupt process death must leave its marker'
            else:
                assert process.wait(timeout=5) == 0, output
            return notes
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    assert lifecycle('clean-first') == []
    # Restart immediately; polling for cleanup here would conceal a teardown race.
    assert lifecycle('clean-restart') == []
    poll(lambda: not marker.exists(), 'A clean shell exit left its running marker')
    assert lifecycle('clean-reload', hold=True, reload=True) == []
    assert lifecycle('after-clean-reload') == []
    poll(lambda: not marker.exists(), 'Clean reload left a running marker')
    assert lifecycle('requested-signal', hold=True, terminate=True, requested=True) == []
    assert lifecycle('after-requested-signal') == []
    poll(lambda: not marker.exists(), 'Requested restart left a running marker')
    assert lifecycle('legacy-stop', hold=True, terminate=True, legacy=True) == []
    assert lifecycle('after-legacy-stop') == []
    poll(lambda: not marker.exists(), 'Legacy clean stop left a running marker')
    assert lifecycle('first-crash', hold=True) == []
    notes = lifecycle('after-crash', hold=True)
    assert len(notes) == 1, notes
    assert notes[0]['summary'] == 'The panel stopped and was restarted.', notes
    assert [action['id'] for action in notes[0]['actions']] == ['report:shell'], notes
    assert lifecycle('after-second-crash') == []
    poll(lambda: not marker.exists(), 'Clean exit after crashes left a marker')
    # Keep the runtime directory to prove the boot id, rather than tmpfs lifetime,
    # excludes a previous boot's running marker and an unconsumed crash notice.
    marker.write_text(json.dumps({'boot_id': 'another-boot', 'pid': 2147483647,
                                  'token': 'a' * 32}))
    (marker.parent / 'repair-notice.json').write_text(
        json.dumps({'crashed': True, 'boot_id': 'another-boot'}))
    assert lifecycle('after-reboot') == []
    poll(lambda: not marker.exists(), 'Clean exit after reboot left a marker')
    # An unsolicited signal is an abrupt termination: no clean QML exit runs.
    env['XDG_STATE_HOME'] = str(base / 'state-signal')
    assert lifecycle('raw-signal', hold=True, terminate=True) == []
    notes = lifecycle('after-raw-signal')
    assert len(notes) == 1 and notes[0]['summary'] == 'The panel stopped and was restarted.', notes
    poll(lambda: not marker.exists(), 'Clean exit after the signal left a marker')
print('PASS: real shell start, Qt.quit with delayed cleanup, same-process reload, '
      'requested and legacy stop handling, killed shell report, second crash suppression, '
      'stale boot exclusion, and abrupt SIGTERM notice')
