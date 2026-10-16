// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtQuick.Window
import QtTest
import Quickshell

// Shell ordering only: keep the layer requested until a fresh compositor confirmation.
// Native headless tests separately prove keyboard delivery during the retained-layer gap.
ShellRoot {
    id: root
    property int modifiers: Qt.AltModifier
    property int received: 0
    property int finishes: 0
    property int requested: -1
    property bool releasedBeforeRequest: false
    function check(ok: bool, message: string): void {
        if (!ok)
            throw new Error(message);
    }
    NiriService {
        id: service
    }
    WindowLabels {
        id: labels
    }
    AppIdentity {
        id: identity
    }
    Window {
        id: window
        width: 1280
        height: 800
        visible: true
        Item {
            id: target
            focus: true
            Keys.onPressed: event => {
                if (event.key === Qt.Key_A)
                    ++root.received;
            }
        }
        WindowSwitcher {
            id: panel
            niri: service
            labels: labels
            identity: identity
            modifierProbe: () => root.modifiers
            onFocusRequested: id => {
                root.releasedBeforeRequest = target.activeFocus;
                root.requested = id;
            }
            onFinished: {
                ++root.finishes;
                target.forceActiveFocus();
            }
        }
        TestCase {
            id: keys
            when: false
        }
    }
    function open(): void {
        service.windows = [
            {
                id: 1,
                is_focused: true,
                workspace_id: 1
            },
            {
                id: 2,
                is_focused: false,
                workspace_id: 1
            }
        ];
        modifiers = Qt.AltModifier;
        requested = -1;
        panel.step(1, false);
        check(panel.armed && !panel.presenting, "hidden chord started capture");
        panel.focusArrived();
        check(panel.activeFocus, "switcher did not take keyboard");
    }
    function release(): void {
        const closed = finishes;
        modifiers = 0;
        keys.keyRelease(Qt.Key_Alt, Qt.NoModifier, 0);
        check(!releasedBeforeRequest && requested === 2, "keyboard released before focus request");
        check(panel.activeFocus && !panel.armed && finishes === closed, "released keyboard before confirmation");
        check(panel.pendingFocus === 2, "focus confirmation did not remain pending");
    }
    function confirm(): void {
        service.windows = [
            {
                id: 1,
                is_focused: false,
                workspace_id: 1
            },
            {
                id: 2,
                is_focused: true,
                workspace_id: 1
            }
        ];
        keys.tryCompare(panel, "pendingFocus", -1, 1000);
        check(panel.pendingFocus === -1 && target.activeFocus, "acknowledged handoff did not release keyboard");
    }
    Timer {
        interval: 100
        running: true
        onTriggered: {
            try {
                window.requestActivate();
                for (const delay of [0, 1, 10]) {
                    panel.handoffTimeout = 150;
                    root.open();
                    root.release();
                    const before = root.received, closed = root.finishes;
                    root.confirm();
                    keys.keyClick(Qt.Key_A, Qt.NoModifier, delay);
                    root.check(root.received === before + 1 && root.finishes === closed + 1, "confirmed handoff did not release exactly once");
                }
                panel.handoffTimeout = 20;
                root.open();
                root.release();
                keys.tryCompare(panel, "pendingFocus", -1, 1000);
                root.check(panel.pendingFocus === -1 && target.activeFocus, "lost handoff did not time out");
                const finished = root.finishes;
                root.confirm();
                root.check(root.finishes === finished, "late confirmation closed twice");

                panel.handoffTimeout = 1000;
                root.open();
                root.release();
                root.modifiers = Qt.AltModifier;
                panel.step(1, false);
                root.check(!panel.keyboardReady && panel.pendingFocus === -1, "new chord reused pending handoff");
                service.windows = [
                    {
                        id: 1,
                        is_focused: false,
                        workspace_id: 1
                    },
                    {
                        id: 2,
                        is_focused: true,
                        workspace_id: 1
                    }
                ];
                keys.wait(20);
                root.check(panel.armed && panel.activeFocus, "old confirmation closed new chord");
                panel.cancel();
                root.check(panel.pendingFocus === -1 && target.activeFocus, "new chord did not cancel");

                // Closed windows release memory even while idle, without reopening the panel.
                const kept = {
                    result: {},
                    width: 50,
                    height: 50
                };
                panel.recent = {
                    1: kept,
                    2: {
                        result: {},
                        width: 50,
                        height: 50
                    }
                };
                const rect = {
                    x: 0,
                    y: 0,
                    width: 50,
                    height: 50
                };
                panel.live = {
                    2: rect
                };
                service.windows = [
                    {
                        id: 1,
                        is_focused: true,
                        workspace_id: 1
                    }
                ];
                root.check(Object.keys(panel.recent).join() === "1" && panel.recent[1] === kept, "closed-window picture retained");
                panel.storeRecent(2, {}, 50, 50, rect);
                root.check(Object.keys(panel.recent).join() === "1", "late grab restored closed picture");

                root.open();
                panel.cancel();
                root.check(!panel.armed && panel.pendingFocus === -1 && target.activeFocus && root.requested === -1, "lost keyboard did not cancel cleanly");
                console.log("PASS switcher lifecycle: request before release, confirmation, timeout, new chord, close pruning");
            } catch (error) {
                console.log("FAIL " + error.message);
            }
            Qt.quit();
        }
    }
}
