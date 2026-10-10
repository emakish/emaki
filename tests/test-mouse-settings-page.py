#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise input controls through an isolated catalog, with no device changes."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / '.cache/evidence/s4'
EVIDENCE.mkdir(parents=True, exist_ok=True)
FIXTURE = r'''// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    id: root
    property var calls: []
    QtObject {
        id: catalog
        property bool busy: false
            readonly property bool writing: busy
        property bool reject: false
        property string missing: ""
        property string disconnected: ""
        property string lastStatus: ""
        property var values: ({})
        property var defaults: ({
            "mouse.speed": 0, "mouse.natural_scroll": false,
            "touchpad.speed": 0, "touchpad.natural_scroll": true,
            "touchpad.tap": true, "touchpad.two_finger_right_click": null,
            "touchpad.disable_while_typing": true,
            "gestures.dnd_edge_view_scroll": true,
            "gestures.dnd_edge_workspace_switch": true
        })
        function row(key) {
            if (missing === key) return null;
            return { value: key in values ? values[key] : defaults[key], default: defaults[key], editable: !disconnected || !key.startsWith(disconnected), explanation: "No pointing device is connected." };
        }
        function set(key, value) {
            root.calls = root.calls.concat([{kind: "set", key: key, value: value}]);
            if (reject) { lastStatus = "rejected"; return; }
            const next = Object.assign({}, values);
            next[key] = JSON.parse(value);
            values = next;
            lastStatus = "committed";
        }
        function reset(key) {
            root.calls = root.calls.concat([{kind: "reset", key: key}]);
            const next = Object.assign({}, values);
            delete next[key];
            values = next;
        }
    }
    FloatingWindow {
        id: window
        visible: true
        implicitWidth: 760
        implicitHeight: 1060
        color: "#eee4db"
        MouseSettingsPage { id: page; x: 20; y: 20; width: parent.width - 40; catalog: catalog }
    }
    TestCase {
        name: "MouseSettingsPage"
        when: window.backingWindowVisible
        onCompletedChanged: if (completed)
            console.log("MOUSE_SETTINGS_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function find(item, name) {
            if (item.objectName === name) return item;
            for (const child of item.children ?? []) {
                const result = find(child, name);
                if (result) return result;
            }
            return null;
        }
        function test_controls() {
            for (const key of Object.keys(catalog.defaults)) {
                const row = find(page, "setting-" + key);
                verify(row !== null, key);
                verify(row.visible && row.editable, key);
                verify(row.title.length > 0 && row.explanation.length > 0, key);
                compare(row.value, catalog.defaults[key]);
                verify(!row.changed);
                const next = key.endsWith(".speed") ? 0.35 : !catalog.defaults[key];
                row.requestValue(next);
                const call = root.calls[root.calls.length - 1];
                compare(call.kind, "set");
                compare(call.key, key);
                compare(call.value, String(next));
                compare(row.value, next);
                verify(row.changed && row.resetItem.visible);
                row.requestReset();
                compare(root.calls[root.calls.length - 1].kind, "reset");
                compare(root.calls[root.calls.length - 1].key, key);
                compare(row.value, catalog.defaults[key]);
                verify(!row.changed);
                catalog.busy = true;
                const before = root.calls.length;
                row.requestValue(next);
                row.requestReset();
                compare(root.calls.length, before);
                catalog.busy = false;
                catalog.missing = key;
                verify(!row.editable);
                row.requestValue(next);
                compare(root.calls.length, before);
                catalog.missing = "";
                catalog.reject = true;
                row.requestValue(next);
                compare(catalog.lastStatus, "rejected");
                compare(row.value, catalog.defaults[key]);
                catalog.reject = false;
                if (key.endsWith(".speed")) {
                    compare(row.formatValue(row.defaultValue), "50%");
                    for (const spec of [[-1, "0%"], [0, "50%"], [1, "100%"]]) {
                        row.requestValue(spec[0]);
                        compare(row.formatValue(row.value), spec[1]);
                        verify(row.explanation.includes(spec[1]));
                        verify(row.controlItem.children.some(child => child.text === spec[1]), "Slider prints the percentage");
                    }
                    row.requestReset();
                    compare(row.from, -1);
                    compare(row.to, 1);
                    compare(row.stepSize, 0.05);
                    row.requestValue(-0.75000000001);
                    compare(root.calls[root.calls.length - 1].value, "-0.75");
                    row.requestReset();
                }
            }
            const rightClick = find(page, "setting-touchpad.two_finger_right_click");
            compare(rightClick.options.length, 3);
            compare(rightClick.options[0].value, null);
            compare(rightClick.options[1].value, true);
            compare(rightClick.options[2].value, false);
            rightClick.requestValue(false);
            compare(root.calls[root.calls.length - 1].value, "false");
            compare(rightClick.value, false);
            verify(rightClick.explanation.includes("three fingers"));
            rightClick.requestValue(null);
            compare(root.calls[root.calls.length - 1].kind, "reset");
            compare(rightClick.value, null);
            verify(!rightClick.changed);
            catalog.set("touchpad.tap", "false");
            rightClick.requestValue(true);
            compare(rightClick.explanation, "Click with two fingers to right-click.");
            rightClick.requestValue(false);
            compare(rightClick.explanation, "Press the lower-right corner to right-click.");
            rightClick.requestValue(null);
            catalog.reset("touchpad.tap");
            catalog.disconnected = "touchpad.";
            verify(!find(page, "setting-touchpad.speed").visible);
            verify(find(page, "setting-mouse.speed").visible);
            catalog.disconnected = "mouse.";
            verify(!find(page, "setting-mouse.speed").visible);
            verify(find(page, "setting-touchpad.speed").visible);
            catalog.disconnected = "";
            wait(100);
            let saved = false;
            page.grabToImage(result => saved = result.saveToFile(Quickshell.env("MOUSE_SETTINGS_SHOT")));
            tryVerify(() => saved, 5000);
        }
    }
}
'''


def run(mutation=None):
    with tempfile.TemporaryDirectory(prefix='mouse-settings-') as temporary:
        base = Path(temporary)
        shutil.copytree(ROOT / 'shell', base / 'shell')
        (base / 'shell/test.qml').write_text(FIXTURE)
        definitions = base / 'shell/qmldir'
        if 'MouseSettingsPage 1.0 MouseSettingsPage.qml' not in definitions.read_text():
            with definitions.open('a') as stream:
                stream.write('\nMouseSettingsPage 1.0 MouseSettingsPage.qml\n')
        if mutation:
            page = base / 'shell/MouseSettingsPage.qml'
            before, after = mutation
            assert before in page.read_text()
            page.write_text(page.read_text().replace(before, after))
        for part in ('home', 'config', 'data', 'cache', 'state'):
            (base / part).mkdir(mode=0o700)
        env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
                   XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
                   XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
                   XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
                   EMAKI_BIN='', EMAKI_SHELL_TRAY='0',
                   MOUSE_SETTINGS_SHOT=str(EVIDENCE / 'mouse-settings.png'),
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
            env.pop(key, None)
        result = subprocess.run(['qs', '-p', str(base / 'shell/test.qml'), '--no-color'],
                                env=env, capture_output=True, text=True, timeout=40)
        output = result.stdout + result.stderr
        if not mutation:
            (EVIDENCE / 'mouse-settings.log').write_text(output)
            assert result.returncode == 0 and 'MOUSE_SETTINGS_RESULT 2 0' in output, output
            for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign', 'Cannot assign'):
                assert error not in output, output
        else:
            assert 'MOUSE_SETTINGS_RESULT 1 1' in output, output


run()
for mutation in (
    ('busy: page.catalog.writing', 'busy: false'),
    ('onResetRequested: page.catalog.reset("touchpad.tap")',
     'onResetRequested: page.catalog.reset("touchpad.natural_scroll")'),
    ('page.catalog.set("mouse.speed",', 'page.catalog.set("touchpad.speed",'),
):
    run(mutation)
run(('value === null ? page.catalog.reset("touchpad.two_finger_right_click")',
     'value === null ? page.catalog.set("touchpad.two_finger_right_click", "false")'))
print('PASS: mouse and trackpad changes, reset, rejection, pending guards, values and four mutants')
