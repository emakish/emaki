// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    id: root
    property var calls: []
    SystemBackend {
        id: fixtureBackend
        audioReady: true
        sink: ({
                audio: {
                    volume: 0.4,
                    muted: false
                }
            })
        networkReady: true
        wifiHardwareEnabled: true
        wifiEnabled: true
        wifiDevices: [
            {}
        ]
        adapter: ({
                enabled: true
            })
        battery: ({
                percentage: 0.6
            })
    }
    SystemService {
        id: fixtureService
        backend: fixtureBackend
        brightness: ({
                state: "ready",
                percent: 70
            })
        profiles: ({
                state: "ready",
                current: "balanced",
                profiles: ["balanced", "power-saver"]
            })
        function act(kind: string, value: var): bool {
            root.calls = root.calls.concat([
                {
                    kind: kind,
                    value: value
                }
            ]);
            return true;
        }
    }
    NiriService {
        id: fixtureNiri
        binary: ""
        connection: "connected"
        model: ({
                keyboard_layouts: {
                    names: ["English (US)", "English (UK)"],
                    current_idx: 0
                }
            })
        function switchLayout(index: int): bool {
            root.calls = root.calls.concat([
                {
                    kind: "layout",
                    value: index
                }
            ]);
            return true;
        }
    }
    FloatingWindow {
        id: window
        visible: true
        implicitWidth: 620
        implicitHeight: 650
        color: "#eee4db"
        SystemSettingsPage {
            id: page
            x: 20
            y: 20
            width: parent.width - 40
            service: fixtureService
            niri: fixtureNiri
        }
    }
    TestCase {
        name: "SystemSettingsPage"
        when: window.backingWindowVisible
        onCompletedChanged: if (completed)
            console.log("SYSTEM_SETTINGS_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function check(value, message) {
            if (!value)
                console.error("CHECK FAILED", message);
            verify(value, message || "");
        }
        function equal(actual, expected, message) {
            if (actual !== expected)
                console.error("COMPARE FAILED", actual, expected, message);
            compare(actual, expected, message || "");
        }
        function find(item, name) {
            if (item.objectName === name)
                return item;
            for (const child of item.children ?? []) {
                const result = find(child, name);
                if (result)
                    return result;
            }
            return null;
        }
        function row(name) {
            return find(page, "settings-" + name);
        }
        function test_controls() {
            fixtureNiri.model = {
                overview_open: false,
                workspaces: {},
                windows: {},
                keyboard_layouts: {
                    names: ["English (US)", "English (UK)"],
                    current_idx: 0
                }
            };
            fixtureNiri.connection = "connected";
            const cases = [["wifi", "wifi-power", false, "wifi-power", null], ["bluetooth", "bluetooth-power", false, "bt-power", null], ["sound", "sound-mute", true, "mute", null], ["sound", "sound-volume", 62, "volume", 62], ["displays", "display-brightness", 80, "brightness", 80], ["battery", "power-profile", "power-saver", "profile", "power-saver"], ["keyboard", "keyboard-layout", 1, "layout", 1]];
            equal(page.availablePages.length, 6);
            for (const entry of cases) {
                page.page = entry[0];
                const control = row(entry[1]);
                check(control.visible, entry[1]);
                check(control.editable, entry[1]);
                check(!control.changed, "Runtime controls have no fabricated default");
                check(!control.resetItem.visible);
                control.requestValue(entry[2]);
                equal(root.calls[root.calls.length - 1].kind, entry[3]);
                equal(root.calls[root.calls.length - 1].value, entry[4]);
                check(!fixtureBackend.wifiScan && !fixtureBackend.micMeter, "Pages do not start probes");
            }
            const before = root.calls.length;
            fixtureBackend.networkReady = false;
            page.page = "wifi";
            row("wifi-power").requestValue(false);
            equal(root.calls.length, before, "Unavailable controls cannot mutate");
            fixtureBackend.networkReady = true;
            fixtureService.actionState = "pending";
            row("wifi-power").requestValue(false);
            equal(root.calls.length, before, "Pending changes disable controls");
            fixtureService.actionState = "idle";
            page.page = "sound";
            fixtureService.actionState = "locked";
            equal(page.actionResult, "idle", "Panel results stay outside this page");
            fixtureService.actionState = "idle";
            fixtureBackend.sink = ({
                    audio: {
                        volume: 0.8,
                        muted: true
                    }
                });
            equal(row("sound-volume").value, 80);
            equal(row("sound-mute").value, true);
            check(!row("sound-volume").changed);
            page.page = "displays";
            check(row("night-light").unavailable);
            check(row("night-warmth").unavailable);
            wait(100);
            let saved = false;
            page.grabToImage(result => saved = result.saveToFile(Quickshell.env("SYSTEM_SETTINGS_SHOT")));
            tryVerify(() => saved, 5000);
        }
    }
}
