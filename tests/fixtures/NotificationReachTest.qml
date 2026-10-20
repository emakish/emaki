// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    NotificationStore {
        id: store
    }
    Window {
        id: window
        width: 560
        height: 850
        visible: true
        ClockPanel {
            id: panel
            width: window.width
            height: window.height
            viewportWidth: window.width
            viewportHeight: window.height
            expansion: 1
            head: 1
            opened: true
            serverState: "active"
            store: store
        }
        TestCase {
            name: "NotificationReach"
            property var body: panel.body
            when: window.visible
            function check(ok, message) {
                if (!ok)
                    console.error("NOTIFICATION_REACH_FAIL", message);
                verify(ok, message);
            }
            function equal(actual, expected, message) {
                check(actual === expected, String(actual) + " expected " + String(expected) + " " + (message || ""));
            }
            function init() {
                store.entries = [];
                panel.opened = false;
                panel.expansion = 0;
                body.keyboardMode = false;
                for (let i = 0; i < 9; i++)
                    store.local("Application " + i, "Notification " + i, "Message " + i);
                // Distinct ages keep the newest row stable across plan rebuilds.
                store.entries = store.entries.map((note, index) => Object.assign({}, note, {
                        time: Date.now() - index * 1000
                    }));
                panel.opened = true;
                panel.expansion = 1;
                wait(350);
            }
            function test_close_hover_and_keyboard() {
                try {
                    closeHoverAndKeyboard();
                } catch (error) {
                    console.error("NOTIFICATION_REACH_FAIL", String(error), error.stack);
                    throw error;
                }
            }
            function closeHoverAndKeyboard() {
                const newest = store.entries.slice().sort((a, b) => b.time - a.time)[0];
                const row = body.findTarget(body, "note-" + newest.id);
                const close = body.findTarget(body, "note-" + newest.id + ":x");
                mouseMove(window.contentItem, 1, 1);
                window.contentItem.forceActiveFocus();
                wait(30);
                check(!close.visible, "close rests hidden");
                mouseMove(row, 60, 20);
                // Clearing the animated drop must not hide a close under the pointer.
                panel.leave();
                try {
                    wait(150);
                    check(close.visible, "hover close is visible");
                } catch (error) {
                    console.error("NOTIFICATION_REACH_FAIL", "close must appear on pointer hover");
                    throw error;
                }
                mouseClick(close, 12, 12);
                wait(30);
                check(!store.entries.some(note => note.id === newest.id), "pointer close dismisses only its card");
                equal(store.entries.length, 8);
                body.keyboardMode = true;
                wait(30);
                for (const key of [Qt.Key_Return, Qt.Key_Space]) {
                    const next = body.plan.items.find(item => item.kind === "note").note;
                    const target = body.findTarget(body, "note-" + next.id);
                    const dismiss = body.findTarget(body, "note-" + next.id + ":x");
                    panel.focusNewest();
                    wait(150);
                    check(target.activeFocus, "newest card holds focus");
                    wait(50);
                    check(dismiss.visible, "keyboard close is visible");
                    keyClick(Qt.Key_Tab);
                    check(dismiss.activeFocus, "Tab reaches the card close");
                    keyClick(key);
                    wait(30);
                    check(!store.entries.some(note => note.id === next.id), "keyboard closes its card");
                }
                equal(store.entries.length, 6);
            }
            function test_pointer_scroll() {
                check(!body.keyboardMode);
                mouseWheel(body.scroller, 250, 80, 0, -240);
                wait(150);
                check(body.scroller.contentY > 0, "pointer wheel scrolls history");
                equal(body.plan.items.filter(item => item.kind === "note").length, 9, "older application groups enter the scrollable list");
                for (let i = 0; i < 8; i++)
                    mouseWheel(body.scroller, 250, 80, 0, -240);
                wait(150);
                const oldest = body.plan.items.filter(item => item.kind === "note").pop();
                check(oldest.y >= body.scroller.contentY && oldest.y < body.scroller.contentY + body.scroller.height, "oldest notification is visible after scrolling");
                check(!body.keyboardMode, "pointer use does not switch keyboard mode");
            }
            function test_overflow_action() {
                const more = body.findTarget(body, "history-more");
                check(more !== null, "overflow count is actionable");
                mouseClick(more, more.width / 2, more.height / 2);
                wait(150);
                equal(body.plan.items.filter(item => item.kind === "note").length, 9);
                check(body.scroller.contentHeight > body.scroller.height);
                check(body.scroller.contentY > 0, "overflow action scrolls to older groups");
                panel.opened = false;
                panel.expansion = 0;
                panel.opened = true;
                panel.expansion = 1;
                wait(350);
                check(body.plan.items.some(item => item.kind === "hidden"), "reopen restores compact history");
            }
            onCompletedChanged: if (completed)
                console.log("NOTIFICATION_REACH_RESULT", qtest_results.passCount, qtest_results.failCount)
        }
    }
}
