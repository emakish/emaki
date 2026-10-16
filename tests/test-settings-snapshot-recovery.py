#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep recovery reachable after dismissal and bounded on a scaled small screen."""
import argparse
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
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix='snapshot-settings-') as temporary:
    base = Path(temporary)
    shutil.copytree(args.source_root / 'shell', base / 'shell')
    source = base / 'shell/SnapshotRecovery.qml'
    instrumented = source.read_text()
    if 'objectName: "snapshotRecoveryWindow"' not in instrumented:
        instrumented = instrumented.replace('FloatingWindow {', 'FloatingWindow { objectName: "snapshotRecoveryWindow"')
    if 'objectName: "snapshotRecoveryPrompt"' not in instrumented:
        instrumented = instrumented.replace('id: prompt', 'id: prompt; objectName: "snapshotRecoveryPrompt"')
    instrumented = instrumented.replace('id: recovery', 'id: recovery; property alias testWindow: testLoader.item')
    instrumented = instrumented.replace('LazyLoader {', 'LazyLoader { id: testLoader')
    source.write_text(instrumented)
    for name in ('home', 'config', 'data', 'cache', 'state', 'bin'):
        (base / name).mkdir(mode=0o700)
    replies = {
        'emaki-rollback': {'mode': 'snapshot', 'snapshot': '7'},
        'emaki-update-channel': {'schema_version': 1, 'status': 'read', 'editable': True, 'channel': 'stable'},
    }
    for name, reply in replies.items():
        helper = base / 'bin' / name
        helper.write_text('#!/usr/bin/env python3\nprint(' + repr(json.dumps(reply)) + ')\n')
        helper.chmod(0o700)
    helper = base / 'bin/pkexec'
    helper.write_text('#!/bin/sh\nexit 126\n')
    helper.chmod(0o700)
    (base / 'shell/test.qml').write_text('''// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtTest
import Quickshell
ShellRoot {
 id: root
 SnapshotRecovery { id: recovery }
 SystemService {
  id: service
  Component.onCompleted: if ("recovery" in service) service.recovery = recovery
 }
 Window {
  id: window
  visible: true
  width: 600; height: 500
  SettingsMaintenancePage { id: page; width: 560; page: "updates"; service: service }
  TestCase {
   name: "SnapshotSettingsRecovery"
   when: window.visible
   onCompletedChanged: if (completed) console.log("SNAPSHOT_SETTINGS_RESULT", qtest_results.passCount, qtest_results.failCount)
   function check(value, message) { if (!value) console.error(message); verify(value, message); }
   function test_a_scaled_bounds() { try {
    tryCompare(recovery, "opened", true);
    tryVerify(() => recovery.testWindow !== null && findChild(recovery.testWindow.contentItem, "snapshotRecoveryPrompt") !== null);
    const prompt = findChild(recovery.testWindow.contentItem, "snapshotRecoveryPrompt");
    const recoveryWindow = recovery.testWindow;
    check(recoveryWindow.width <= recoveryWindow.screen.width - 32, "Recovery must fit the scaled screen width");
    check(recoveryWindow.height <= recoveryWindow.screen.height - 32, "Recovery must fit the scaled screen height");
    const scroll = findChild(prompt, "snapshotScroll");
    check(scroll, "Long text must scroll");
    prompt.automaticMessage = "Recovery details. ".repeat(100);
    wait(100);
    check(scroll.contentHeight > scroll.height);
    const keep = findChild(prompt, "snapshotKeep");
    const later = findChild(prompt, "snapshotLater");
    for (const button of [keep, later]) {
     const point = button.mapToItem(prompt, 0, 0);
     check(point.y >= 0 && point.y + button.height <= prompt.height);
     check(point.x >= 0 && point.x + button.width <= prompt.width);
    }
    recoveryWindow.contentItem.forceActiveFocus();
    prompt.takeFocus();
    keyClick(Qt.Key_Tab);
    tryCompare(keep, "activeFocus", true);
    keyClick(Qt.Key_Return);
    tryCompare(recovery, "error", "Authentication was cancelled. Nothing changed.");
    recovery.automaticMessage = "";
   } catch (error) { console.error(String(error), error.stack); throw error; } }
   function test_b_reopen_after_not_now() { try {
    recovery.opened = true;
    tryVerify(() => recovery.testWindow !== null && findChild(recovery.testWindow.contentItem, "snapshotLater") !== null);
    const later = findChild(recovery.testWindow.contentItem, "snapshotLater");
    later.clicked();
    tryCompare(recovery, "opened", false);
    const button = findChild(page, "settings-review-snapshot");
    check(button && button.visible, "Settings must retain the recovery action after Not now");
    button.clicked();
    tryCompare(recovery, "opened", true);
    tryVerify(() => recovery.testWindow !== null && findChild(recovery.testWindow.contentItem, "snapshotKeep") !== null && findChild(recovery.testWindow.contentItem, "snapshotKeep").visible);
    recovery.opened = false;
   } catch (error) { console.error(String(error), error.stack); throw error; } }
  }
 }
}''')
    env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
               XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
               XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_SCALE_FACTOR='2',
               QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
               PATH=str(base / 'bin') + os.pathsep + os.environ['PATH'],
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
        env.pop(name, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                            env=env, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    assert result.returncode == 0 and 'SNAPSHOT_SETTINGS_RESULT 3 0' in output, output
    assert not any(error in output for error in ('TypeError', 'ReferenceError', 'Binding loop')), output
    print('PASS: recovery reopens after Not now; prompt scrolls within a small screen at 2× with visible keyboard actions')
