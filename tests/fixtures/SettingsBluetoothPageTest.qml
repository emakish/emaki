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
        id: backend
        adapter: ({
                enabled: true,
                discovering: false
            })
        devices: [
            {
                key: "headphones",
                name: "Headphones",
                paired: true,
                connected: true,
                battery: 0
            },
            {
                key: "mouse",
                name: "Mouse",
                paired: true,
                connected: false,
                battery: -1
            },
            {
                key: "keyboard",
                name: "Keyboard",
                paired: false,
                pairing: false,
                battery: 80
            }
        ]
    }
    SystemService {
        id: service
        backend: backend
        function act(kind: string, value: var): bool {
            actionSerial += 1;
            root.calls = root.calls.concat([
                {
                    kind: kind,
                    value: value
                }
            ]);
            if (kind === "bt-scan")
                backend.adapter.discovering = !backend.adapter.discovering;
            return true;
        }
    }
    FloatingWindow {
        id: window
        visible: true
        implicitWidth: 700
        implicitHeight: 700
        color: "#eee4db"
        SettingsBluetoothPage {
            id: page
            width: parent.width - 40
            x: 20
            y: 20
            service: service
        }
    }
    TestCase {
        name: "SettingsBluetoothPage"
        when: window.backingWindowVisible
        onCompletedChanged: if (completed)
            console.log("BLUETOOTH_SETTINGS_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function find(item, name) {
            if (item.objectName === name)
                return item;
            for (const child of item.children ?? []) {
                const match = find(child, name);
                if (match)
                    return match;
            }
            return null;
        }
        function last(kind, key) {
            compare(root.calls[root.calls.length - 1].kind, kind);
            compare(root.calls[root.calls.length - 1].value, key);
        }
        function test_controls() {
            wait(80);
            const headphones = find(page, "settings-bluetooth-device-headphones");
            const mouse = find(page, "settings-bluetooth-device-mouse");
            const keyboard = find(page, "settings-bluetooth-nearby-keyboard");
            verify(headphones && mouse && keyboard);
            compare(headphones.explanation, "Connected · Battery 0%");
            compare(mouse.explanation, "Not connected");
            compare(keyboard.explanation, "Ready to pair · Battery 80%");
            find(headphones, "connect").clicked();
            last("bt-disconnect", "headphones");
            find(mouse, "connect").clicked();
            last("bt-connect", "mouse");
            find(mouse, "forget").clicked();
            last("bt-forget", "mouse");
            find(keyboard, "pair").clicked();
            last("bt-pair", "keyboard");
            find(page, "settings-bluetooth-search").clicked();
            last("bt-scan", null);
            verify(backend.adapter.discovering);
            service.actionState = "pending";
            page.visible = false;
            verify(!backend.adapter.discovering, "Hide stops this page's discovery even during another action");
            service.actionState = "idle";
            page.visible = true;
            backend.adapter.discovering = true;
            page.visible = false;
            verify(backend.adapter.discovering, "Hide preserves discovery started elsewhere");
            page.visible = true;
            backend.adapter.discovering = false;
            page.act("bt-scan", null);
            window.visible = false;
            wait(20);
            verify(!backend.adapter.discovering, "Window close stops discovery");
            window.visible = true;
            const power = find(page, "settings-bluetooth-power");
            power.requestValue(false);
            last("bt-power", null);
            verify(!power.resetItem.visible, "Runtime radio state has no fabricated default");
            const before = root.calls.length;
            service.actionState = "pending";
            find(mouse, "connect").clicked();
            compare(root.calls.length, before);
            service.pendingKind = "bt-pair";
            service.pendingValue = "keyboard";
            compare(find(keyboard, "pair").text, "Cancel");
            find(keyboard, "pair").clicked();
            last("bt-cancel-pair", "keyboard");
            service.actionState = "pairing_failed";
            verify(find(page, "settings-bluetooth-status").visible);
            verify(find(page, "settings-bluetooth-status").text.includes("Couldn’t pair"));
            service.actionState = "unavailable";
            verify(find(page, "settings-bluetooth-status").text.includes("Couldn’t pair"), "Unrelated result cannot replace a completed page result");
            page.visible = false;
            page.visible = true;
            service.actionState = "target_gone";
            verify(!find(page, "settings-bluetooth-status").visible, "Unrelated action must not create a page error");
            service.actionState = "idle";
            backend.adapter = ({
                    enabled: false,
                    discovering: false
                });
            const disabledBefore = root.calls.length;
            page.act("bt-pair", "keyboard");
            compare(root.calls.length, disabledBefore);
            compare(power.explanation, "Off. Turn Bluetooth on to connect devices.");
            backend.adapter = null;
            compare(power.explanation, "No Bluetooth adapter");
            verify(power.unavailable);
            verify(!find(page, "settings-bluetooth-search").enabled);
            verify(!find(page, "settings-bluetooth-devices").visible);
            power.requestValue(true);
            compare(root.calls.length, disabledBefore);
            backend.adapter = ({
                    enabled: true,
                    discovering: false
                });
            verify(!power.unavailable);
            verify(find(page, "settings-bluetooth-search").enabled);
            compare(power.explanation, "On. Paired devices can connect.");
        }
    }
}
