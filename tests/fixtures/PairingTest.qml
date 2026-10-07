// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell

ShellRoot {
    id: root
    property int phase: 0
    property string request: "Pairing request for:\n" + "W".repeat(248) + " (00:11:22:33:44:55)\nConfirm value for authentication: 001234"
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
    function publish(cast: bool): void {
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
                overview_open: false,
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
                casts: cast ? {
                    "1": {
                        id: 1,
                        by_parent: false
                    }
                } : {}
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
    function checkRow(peek: bool, actionable: bool): void {
        const key = (peek ? "peek-" : "note-") + 7;
        const row = find(scene.clockBody, item => item.key === key && item.visible);
        check(row !== null, "visible pairing row missing: " + key + ", phase " + phase);
        const text = find(row, item => item.text === request);
        check(text !== null && text.wrapMode === Text.Wrap, "full pairing body must wrap");
        check(text.height >= text.contentHeight && text.lineCount > 3, "pairing body clipped");
        check(text.text.endsWith("001234"), "confirm code lost");
        const action = find(row, item => item.label === "Confirm" && item.visible);
        if (actionable) {
            check(action !== null, "confirmation action missing");
            const position = action.mapToItem(row, 0, 0);
            check(position.y >= text.y + text.height, "actions overlap pairing body");
            check(position.y + action.height <= row.height, "actions outside row");
        } else {
            check(row.actions.length === 0, "expired request still actionable");
        }
        const rowPosition = row.mapToItem(scene.clockBody, 0, 0);
        check(rowPosition.y + row.height <= scene.clockPanel.targetHeight, "pairing row clipped by panel");
    }
    Timer {
        interval: 400
        repeat: true
        running: true
        onTriggered: {
            try {
                if (root.phase === 0) {
                    root.publish(false);
                    scene.notifications.entries = [scene.notifications.snapshot({
                            id: 7,
                            appName: "blueman",
                            appIcon: "blueman",
                            summary: "Bluetooth",
                            body: root.request,
                            expireTimeout: 0,
                            actions: [
                                {
                                    identifier: "confirm",
                                    text: "Confirm"
                                },
                                {
                                    identifier: "deny",
                                    text: "Deny"
                                }
                            ]
                        }, Date.now())];
                    scene.openSystem("bt");
                    scene.showNotification(7);
                } else if (root.phase === 1) {
                    root.check(scene.systemOpen && scene.peekOpen && scene.clockPanel.visible, "system panel hides pairing preview");
                    root.check(scene.clockPanel.z > scene.systemPanel.z, "pairing preview below system panel");
                    root.check(scene.clockPanel.shownX + scene.clockPanel.shownWidth > scene.systemPanel.shownX, "fixture must exercise overlapping panels");
                    root.checkRow(true, true);
                    scene.closeSystem();
                    scene.endPeek();
                    scene.showNotification(7);
                } else if (root.phase === 2) {
                    root.check(!scene.systemOpen && scene.peekOpen, "closed-panel pairing preview missing");
                    root.checkRow(true, true);
                    root.publish(true);
                } else if (root.phase === 3) {
                    root.check(scene.privacyCast && scene.clockPanel.hidePreviewBodies, "cast not propagated to preview");
                    root.check(scene.clockBody.peekNotes[0].body === "" && scene.clockBody.peekNotes[0].actions.length === 0, "shared preview exposes body or actions");
                    root.check(scene.notifications.entries[0].body === root.request, "redaction changed history");
                    root.publish(false);
                } else if (root.phase === 4) {
                    root.check(!scene.clockPanel.hidePreviewBodies, "preview stays redacted after cast ends");
                    root.checkRow(true, true);
                    root.publish(true);
                    scene.openDrawer();
                } else if (root.phase === 5) {
                    root.check(scene.drawerOpen && scene.pairingPeekOpen && !scene.clockBody.opened, "drawer displaced pending pairing preview");
                    const row = root.find(scene.clockBody, item => item.key === "peek-7" && item.visible);
                    root.check(row !== null && row.note.body === "" && row.actions.length === 0, "drawer exposes shared pairing preview");
                    root.publish(false);
                } else if (root.phase === 6) {
                    root.checkRow(true, true);
                    // Match NotificationStore's expired entry: history remains, live actions end.
                    scene.notifications.entries = scene.notifications.entries.map(note => Object.assign({}, note, {
                            object: null,
                            actions: [],
                            deadline: 0
                        }));
                    root.publish(true);
                } else if (root.phase === 7) {
                    root.check(!scene.pairingPeekOpen && scene.clockBody.opened, "expired request blocks drawer");
                    root.checkRow(false, false);
                    console.log("PAIRING LAYOUT PASS");
                    Qt.quit();
                }
                root.phase++;
            } catch (error) {
                console.error("PAIRING LAYOUT FAIL: " + error);
                Qt.quit();
            }
        }
    }
}
