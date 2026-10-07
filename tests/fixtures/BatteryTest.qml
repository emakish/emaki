// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell

ShellRoot {
    id: root
    property int phase: 0
    property double deadline: 0
    NiriService {
        id: niri
        binary: ""
    }
    Window {
        visible: true
        width: 1280
        height: 800
        ShellScene {
            id: scene
            anchors.fill: parent
            niri: niri
            borderMode: "soft"
            reservedSpace: 52
            headless: true
            testWidth: 1280
            testHeight: 800
            skipIntro: true
        }
    }
    function check(ok: bool, message: string): void {
        if (!ok)
            throw new Error(message);
    }
    function publish(overview: bool): void {
        niri.receive(JSON.stringify({
            schema_version: 1,
            ipc_release: "26.04",
            generation: phase + 1,
            connection: {
                status: "connected",
                reason: "synchronized"
            },
            model: {
                focused_output: "Fixture",
                overview_open: overview,
                links_pending: false,
                keyboard_layouts: {
                    names: ["English (US)"],
                    current_idx: 0
                },
                outputs: {
                    Fixture: {
                        logical: {
                            width: 1280,
                            height: 800
                        }
                    }
                },
                workspaces: {
                    "1": {
                        id: 1,
                        idx: 1,
                        is_active: true,
                        is_focused: true,
                        output: "Fixture"
                    }
                },
                windows: {
                    "8": {
                        id: 8,
                        workspace_id: 1,
                        layout: {
                            tile_size: [1280, 800]
                        }
                    }
                },
                casts: {}
            }
        }));
    }
    function find(item: var, predicate: var): var {
        if (predicate(item))
            return item;
        for (const child of item.children ?? []) {
            const result = find(child, predicate);
            if (result)
                return result;
        }
        return null;
    }
    SystemBackend {
        id: batteryBackend
        batteryPower: true
        battery: QtObject {
            property real percentage: 0.11
        }
    }
    function checkRow(): void {
        check(scene.batteryPeekOpen && scene.clockPanel.visible, "battery preview missing");
        check(!scene.clockPanel.opened && !scene.clockPanel.drawerContent, "drawer covers warning");
        const id = scene.peekIds[0];
        const row = find(scene.clockBody, item => item.key === "peek-" + id && item.visible);
        check(row !== null, "visible battery row missing");
        const text = find(row, item => item.text === scene.notifications.entries.find(e => e.id === id).summary);
        check(text !== null && text.height >= text.contentHeight, "battery summary clipped");
        const position = row.mapToItem(scene.clockBody, 0, 0);
        check(position.y + row.height <= scene.clockPanel.targetHeight, "battery row clipped by panel");
    }
    function ordinaryQuiet(): void {
        scene.notifications.local("Fixture", "Fixture notice", "");
        check(!scene.peekOpen, "ordinary notice bypassed privacy gate");
    }
    Timer {
        interval: 250
        repeat: true
        running: true
        onTriggered: {
            try {
                switch (root.phase) {
                case 0:
                    root.publish(false);
                    scene.services.backend = batteryBackend;
                    scene.notifications.dnd = true;
                    break;
                case 1:
                    root.check(scene.notifications.count === 0, "warning above threshold");
                    root.ordinaryQuiet();
                    scene.notifications.entries = [];
                    batteryBackend.battery.percentage = .10;
                    break;
                case 2:
                    root.check(scene.notifications.count === 1, "missing 10% warning");
                    root.checkRow();
                    batteryBackend.battery.percentage = .09;
                    root.deadline = scene.peekUntil;
                    root.publish(true);
                    break;
                case 3:
                    root.check(scene.notifications.count === 1, "repeat at 9%");
                    root.checkRow();
                    root.check(scene.peekUntil === root.deadline, "overview restarted warning timer");
                    batteryBackend.battery.percentage = .05;
                    break;
                case 4:
                    root.check(scene.notifications.count === 2, "missing 5% warning in overview");
                    root.checkRow();
                    batteryBackend.battery.percentage = .04;
                    break;
                case 5:
                    root.check(scene.notifications.count === 2, "repeat at 4%");
                    batteryBackend.charging = true;
                    batteryBackend.battery.percentage = .15;
                    break;
                case 6:
                    batteryBackend.charging = false;
                    batteryBackend.battery.percentage = .10;
                    break;
                case 7:
                    root.check(scene.notifications.count === 3, "charging above threshold did not re-arm");
                    root.checkRow();
                    root.publish(false);
                    scene.closeAll();
                    scene.openLauncher();
                    root.ordinaryQuiet();
                    scene.notifications.systemBattery(10);
                    break;
                case 8:
                    root.check(scene.launcherOpen, "warning closed launcher");
                    root.checkRow();
                    scene.closeAll();
                    scene.openSystem("power");
                    root.ordinaryQuiet();
                    scene.notifications.systemBattery(5);
                    break;
                case 9:
                    root.check(scene.systemOpen, "warning closed settings");
                    root.checkRow();
                    scene.closeAll();
                    scene.privacyPresent = true;
                    scene.privacyOpen = true;
                    scene.privacyExpansion = 1;
                    root.ordinaryQuiet();
                    scene.notifications.systemBattery(5);
                    break;
                case 10:
                    root.check(scene.privacyOpen && scene.clockPanel.z > scene.privacyPopup.z, "privacy panel covers warning");
                    root.checkRow();
                    scene.closeAll();
                    scene.openDrawer();
                    root.ordinaryQuiet();
                    scene.notifications.systemBattery(5);
                    break;
                case 11:
                    root.check(scene.drawerOpen, "warning discarded drawer state");
                    root.checkRow();
                    scene.endPeek();
                    break;
                case 12:
                    root.check(scene.drawerOpen && scene.clockPanel.opened && scene.clockPanel.drawerContent, "drawer not restored");
                    scene.closeAll();
                    scene.notifications.systemBattery(5);
                    scene.openSystem("power");
                    break;
                case 13:
                    root.check(scene.systemOpen, "settings did not open during warning");
                    root.checkRow();
                    scene.closeAll();
                    root.check(!scene.peekOpen, "explicit close did not dismiss warning");
                    console.log("BATTERY LAYOUT PASS");
                    Qt.quit();
                }
                root.phase++;
            } catch (error) {
                console.error("BATTERY LAYOUT FAIL: " + error);
                Qt.quit();
            }
        }
    }
}
