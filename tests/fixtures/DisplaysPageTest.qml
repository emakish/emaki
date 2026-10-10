// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    id: root
    property var calls: []
    QtObject {
        id: backend
        property var outputs: [
            {
                name: "PANEL",
                description: "Built-in display",
                enabled: true,
                current_mode: 0,
                modes: [
                    {
                        width: 1920,
                        height: 1080,
                        refresh_rate: 60000,
                        is_preferred: true
                    },
                    {
                        width: 1920,
                        height: 1080,
                        refresh_rate: 120000
                    },
                    {
                        width: 1280,
                        height: 720,
                        refresh_rate: 60000
                    }
                ],
                logical: {
                    x: 0,
                    y: 0,
                    width: 1920,
                    height: 1080,
                    scale: 1,
                    transform: "normal"
                },
                overrides: {
                    scale: 1.25
                }
            }
        ]
        property string main: "PANEL"
        property bool pending: false
        property bool busy: false
        property bool ready: true
        property int seconds: 15
        property string message: ""
        function set(output, key, value) {
            root.calls = root.calls.concat([
                {
                    op: "set",
                    output,
                    key,
                    value
                }
            ]);
        }
        function reset(output, key) {
            root.calls = root.calls.concat([
                {
                    op: "reset",
                    output,
                    key
                }
            ]);
        }
        function keep() {
            root.calls = root.calls.concat([
                {
                    op: "keep"
                }
            ]);
        }
        function revert() {
            root.calls = root.calls.concat([
                {
                    op: "revert"
                }
            ]);
        }
    }
    QtObject {
        id: system
        property var brightness: ({
                state: "ready",
                percent: 70
            })
        property string actionState: "idle"
        property int actionSerial: 0
        property var night: ({
                on: false,
                warmth: 50,
                state: "off",
                error: "",
                schedule: "always",
                startTime: "20:00",
                endTime: "07:00",
                setOn: value => root.calls = root.calls.concat([
                        {
                            op: "night",
                            value
                        }
                    ]),
                setWarmth: value => root.calls = root.calls.concat([
                        {
                            op: "warmth",
                            value
                        }
                    ]),
                setSchedule: value => root.calls = root.calls.concat([
                        {
                            op: "schedule",
                            value
                        }
                    ])
            })
        function act(key, value) {
            actionSerial += 1;
            actionState = "pending";
            root.calls = root.calls.concat([
                {
                    op: key,
                    value
                }
            ]);
        }
    }
    Component {
        id: ownedPage
        DisplaysSettingsPage {
            service: system
            displays: adapter
            width: 720
            visible: false
        }
    }
    DisplaysService {
        id: adapter
        helper: Quickshell.env("DISPLAYS_HELPER")
    }
    FloatingWindow {
        id: window
        visible: true
        implicitWidth: 750
        implicitHeight: 1000
        Flickable {
            anchors.fill: parent
            contentHeight: page.implicitHeight
            DisplaysSettingsPage {
                id: page
                width: parent.width - 30
                x: 15
                service: system
                displays: backend
            }
        }
    }
    TestCase {
        name: "DisplaysPage"
        when: window.backingWindowVisible
        onCompletedChanged: if (completed)
            console.log("DISPLAYS_PAGE_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function equal(a, b) {
            if (a !== b)
                console.error("MISMATCH", JSON.stringify(a), JSON.stringify(b));
            compare(a, b);
        }
        function check(a) {
            if (!a)
                console.error("CHECK FAILED");
            verify(a);
        }
        function find(item, name) {
            if (item.objectName === name)
                return item;
            for (const child of item.children || []) {
                const found = find(child, name);
                if (found)
                    return found;
            }
            return null;
        }
        function row(name) {
            return find(page, "settings-" + name);
        }
        function test_adapter() {
            tryVerify(() => adapter.ready && !adapter.busy, 5000);
            equal(adapter.outputs.length, 1);
            check(adapter.set("PANEL", "scale", 1.25));
            check(!adapter.set("PANEL", "scale", 2));
            tryVerify(() => adapter.pending && !adapter.busy, 5000);
            equal(adapter.seconds, 15);
            check(adapter.keep());
            tryVerify(() => !adapter.pending && !adapter.busy, 5000);
            check(adapter.set("PANEL", "enabled", false));
            tryVerify(() => adapter.message === "The last display must stay on." && !adapter.busy, 5000);
            equal(adapter.outputs.length, 1);
            const owned = ownedPage.createObject(page.parent);
            check(owned !== null);
            tryVerify(() => owned.displays?.ready, 5000);
            owned.destroy();
            wait(20);
            check(adapter.ready);
            adapter.close();
        }
        function test_brightness_result() {
            system.actionSerial += 1;
            system.actionState = "wrong_password";
            check(!row("display-action-result").visible);
            row("display-brightness").requestValue(75);
            equal(root.calls[root.calls.length - 1].op, "brightness");
            equal(page.actionResult, "pending");
            system.actionState = "confirmation_timeout";
            check(row("display-action-result").visible);
            system.actionSerial += 1;
            system.actionState = "wrong_password";
            check(!row("display-action-result").visible);
            system.actionState = "idle";
        }
        function test_controls() {
            const initialOutput = backend.outputs[0];
            backend.main = "";
            equal(row("display-main").value, true);
            check(!row("display-main").changed);
            check(find(page, "display-main-marker-PANEL").visible);
            const beforeMain = root.calls.length;
            row("display-main").requestValue(false);
            row("display-main").requestReset();
            equal(root.calls.length, beforeMain);
            backend.outputs = [Object.assign({}, initialOutput, {
                    current_mode: 0,
                    modes: [
                        {
                            width: 2560,
                            height: 1600,
                            refresh_rate: 75000
                        }
                    ],
                    logical: {
                        x: 0,
                        y: 0,
                        width: 1706,
                        height: 1066,
                        scale: 1.5
                    }
                })];
            equal(row("display-resolution").value, "2560x1600");
            equal(find(page, "display-resolution-label-PANEL").text, "Built-in display\n2560 × 1600");
            const scaled = backend.outputs[0];
            backend.outputs = [scaled, Object.assign({}, initialOutput, {
                    name: "SECOND"
                })];
            equal(row("display-main").value, false);
            backend.main = "SECOND";
            equal(page.effectiveMain, "SECOND");
            page.selectedName = "SECOND";
            equal(row("display-main").value, true);
            check(row("display-main").changed);
            backend.outputs = [scaled, Object.assign({}, initialOutput, {
                    name: "SECOND",
                    enabled: false,
                    logical: null
                })];
            page.selectedName = "PANEL";
            equal(page.effectiveMain, "PANEL");
            equal(row("display-main").value, true);
            check(!row("display-main").changed);
            backend.outputs = [initialOutput];
            backend.main = "PANEL";
            equal(page.resolutions.length, 2);
            equal(page.rates.length, 2);
            row("display-resolution").requestValue("1280x720");
            equal(root.calls[root.calls.length - 1].value, "1280x720@60.000");
            row("display-refresh-rate").requestValue("1920x1080@120.000");
            equal(root.calls[root.calls.length - 1].value, "1920x1080@120.000");
            row("display-scale").requestReset();
            equal(root.calls[root.calls.length - 1].op, "reset");
            equal(root.calls[root.calls.length - 1].key, "scale");
            row("display-rotation").requestValue("90");
            equal(root.calls[root.calls.length - 1].value, "90");
            const managed = backend.outputs[0];
            backend.outputs = [Object.assign({}, managed, {
                    overrides: {
                        rotation: "normal"
                    }
                })];
            row("display-rotation").requestReset();
            equal(root.calls[root.calls.length - 1].op, "reset");
            equal(root.calls[root.calls.length - 1].key, "rotation");
            backend.outputs = [managed];
            const drag = find(page, "display-drag-PANEL");
            mousePress(drag, 20, 30);
            mouseMove(drag, 50, 45, 30);
            mouseMove(drag, 80, 60, 30);
            mouseRelease(drag, 80, 60);
            equal(root.calls[root.calls.length - 1].key, "position");
            check(root.calls[root.calls.length - 1].value.x !== 0);
            equal(drag.parent.x, (drag.parent.parent.width - drag.parent.width) / 2);
            const panel = backend.outputs[0];
            backend.outputs = backend.outputs.concat([
                {
                    name: "SECOND",
                    enabled: true,
                    logical: {
                        x: 1920,
                        y: 0,
                        width: 1920,
                        height: 1080
                    }
                }
            ]);
            const snapped = page.snappedPosition(panel, 1915, 9, .1);
            equal(snapped.x, 1920);
            equal(snapped.y, 0);
            backend.outputs = [panel];
            page.moveDisplay(-100, 20);
            equal(root.calls[root.calls.length - 1].value.x, -100);
            equal(root.calls[root.calls.length - 1].value.y, 20);
            row("display-enabled").requestValue(false);
            equal(root.calls[root.calls.length - 1].key, "enabled");
            const before = root.calls.length;
            backend.pending = true;
            check(row("display-confirmation").visible);
            row("display-scale").requestValue(2);
            equal(root.calls.length, before);
            backend.pending = false;
            row("night-schedule").requestValue("manual");
            equal(root.calls[root.calls.length - 1].op, "schedule");
            equal(root.calls[root.calls.length - 1].value, "manual");
            row("night-light").requestValue(true);
            equal(root.calls[root.calls.length - 1].op, "night");
            equal(row("night-warmth").value, 4000);
            row("night-warmth").requestValue(2500);
            equal(root.calls[root.calls.length - 1].op, "warmth");
            equal(root.calls[root.calls.length - 1].value, 80);
            let saved = false;
            page.grabToImage(result => saved = result.saveToFile(Quickshell.env("DISPLAYS_SHOT")));
            tryVerify(() => saved, 5000);
            backend.outputs = [];
            check(row("display-resolution").unavailable);
            check(row("display-enabled").unavailable);
        }
    }
    Timer {
        interval: 15000
        running: true
        onTriggered: Qt.quit()
    }
}
