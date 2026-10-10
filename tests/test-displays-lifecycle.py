#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real display rollback survives settings page and launcher closure."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / '.cache/evidence/p2c'
EVIDENCE.mkdir(parents=True, exist_ok=True)
QML = r'''// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

SettingsIpcHarness {
    id: harness
    TestCase {
        name: "DisplaysLifecycle"
        when: true
        onCompletedChanged: if (completed)
            console.log("DISPLAYS_LIFECYCLE_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function nodes(item) {
            let result = [item];
            for (const child of item.children || [])
                result = result.concat(nodes(child));
            return result;
        }
        function displayPage() {
            if (!harness.controller.view)
                return null;
            const loader = nodes(harness.controller.view).find(item =>
                item.sourceComponent !== undefined && item.item &&
                String(item.item).includes("DisplaysSettingsPage_"));
            return loader ? loader.item : null;
        }
        function test_timeout_data() {
            return [
                {tag: "navigation", key: "scale", value: 1.5},
                {tag: "search", key: "mode", value: "1280x720@59.940"},
                {tag: "history", key: "scale", value: 1.5},
                {tag: "launcher", key: "mode", value: "1280x720@59.940"}
            ];
        }
        function test_timeout(data) {
            compare(JSON.parse(harness.controller.open("displays")).status, "opened");
            tryVerify(() => displayPage() !== null);
            const displays = harness.controller.displays;
            tryVerify(() => displays.ready && !displays.busy);
            compare(displayPage().displays, displays);
            displayPage().selectedName = "DP-1";
            const started = Date.now();
            displayPage().set(data.key, data.value);
            tryCompare(displays, "pending", true);
            verify(displays.seconds > 0);
            const changed = displays.outputs.find(output => output.name === "DP-1");
            if (data.key === "scale")
                compare(changed.logical.scale, 1.5);
            else
                compare(changed.current_mode, 1);
            const view = harness.controller.view;
            if (data.tag === "navigation")
                verify(view.navigate("panel"));
            else if (data.tag === "search")
                view.search.text = "window gaps";
            else if (data.tag === "history")
                view.historyShown = true;
            else
                harness.controller.dismiss();
            wait(100);
            if (data.tag === "launcher") {
                compare(harness.controller.opened, false);
                compare(harness.scene.launcherOpen, false);
                compare(harness.controller.view, view);
                verify(displayPage() !== null);
                tryCompare(harness.scene, "launcherPresent", false);
                compare(harness.controller.view, null);
            }
            compare(displayPage(), null);
            compare(harness.controller.displays, displays);
            verify(displays.worker.running);
            verify(displays.pending);
            tryCompare(displays, "pending", false, 19000);
            const elapsed = Date.now() - started;
            verify(elapsed >= 14000 && elapsed < 19000, "Rollback elapsed: " + elapsed);
            compare(displayPage(), null);
            compare(harness.controller.displays, displays);
            verify(displays.ready && displays.worker.running);
            const restored = displays.outputs.find(output => output.name === "DP-1");
            compare(restored.logical.scale, 1.0);
            compare(restored.current_mode, 0);
            if (data.tag === "launcher")
                compare(harness.controller.view, null);
        }
        function cleanup() {
            harness.controller.dismiss();
            wait(30);
        }
    }
}
'''

with tempfile.TemporaryDirectory(prefix='displays-lifecycle-') as temporary:
    base = Path(temporary)
    shutil.copytree(ROOT / 'shell', base / 'shell')
    shutil.copyfile(ROOT / 'tests/fixtures/SettingsIpcHarness.qml',
                    base / 'shell/SettingsIpcHarness.qml')
    with (base / 'shell/qmldir').open('a') as stream:
        stream.write('\nSettingsIpcHarness 1.0 SettingsIpcHarness.qml\n')
    (base / 'shell/DisplaysLifecycleTest.qml').write_text(QML)
    helpers = base / 'bin'
    helpers.mkdir()
    shutil.copyfile(ROOT / 'tests/fixtures/displays-niri.py', helpers / 'niri')
    (helpers / 'niri').chmod(0o700)
    for name in ('emaki-settings-apps', 'emaki-machine-settings', 'emaki-settings-about',
                 'emaki-settings-power', 'emaki-update-channel'):
        helper = helpers / name
        helper.write_text('#!/bin/sh\nexit 1\n')
        helper.chmod(0o700)
    for part in ('home', 'config', 'data', 'cache', 'state'):
        (base / part).mkdir(mode=0o700)
    initial = {name: dict(make='Example', model=name,
                         modes=[dict(width=1920, height=1080, refresh_rate=60000,
                                     is_preferred=True),
                                dict(width=1280, height=720, refresh_rate=59940,
                                     is_preferred=False)],
                         current_mode=0,
                         logical=dict(x=x, y=0, width=1920, height=1080,
                                      scale=1.0, transform='Normal'))
               for name, x in [('DP-1', 0), ('DP-2', 1920)]}
    (base / 'outputs').write_text(json.dumps(initial))
    (base / 'flags').write_text('{}')
    (base / 'focus').write_text('DP-1')
    validator = shutil.which('niri')
    assert validator, 'niri is required for real configuration validation'
    env = dict(os.environ, HOME=str(base / 'home'), USER='Emaki',
               PATH=str(helpers) + os.pathsep + os.environ.get('PATH', ''),
               XDG_CONFIG_HOME=str(base / 'config'), XDG_CONFIG_DIRS=str(base / 'config'),
               XDG_DATA_HOME=str(base / 'data'), XDG_DATA_DIRS=str(base / 'data'),
               XDG_CACHE_HOME=str(base / 'cache'), XDG_STATE_HOME=str(base / 'state'),
               XDG_RUNTIME_DIR=str(runtime_path(base)), QT_QPA_PLATFORM='offscreen',
               QT_QUICK_BACKEND='software', QT_SCALE_FACTOR='1',
               QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
               EMAKI_BIN='', EMAKI_SHELL_TRAY='0', DISPLAY_TEST_ROOT=str(base),
               DISPLAY_TEST_VALIDATOR=validator,
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME',
                'QT_SCREEN_SCALE_FACTORS', 'QT_LOGGING_RULES'):
        env.pop(key, None)
    result = subprocess.run(['qs', '-p', str(base / 'shell/DisplaysLifecycleTest.qml'),
                             '--no-color'], env=env, capture_output=True, text=True, timeout=100)
    output = result.stdout + result.stderr
    (EVIDENCE / 'displays-lifecycle.log').write_text(output)
    assert result.returncode == 0 and 'DISPLAYS_LIFECYCLE_RESULT 5 0' in output, output
    for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
        assert error not in output, output
    assert json.loads((base / 'outputs').read_text()) == initial
    print('PASS: display timeout survives navigation, search, history and launcher closure')
