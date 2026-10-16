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
        ClockBody {
            id: body
            width: window.width
            height: desiredHeight
            maxHeight: window.height
            opened: true
            serverState: "active"
            store: store
        }
        TestCase {
            name: "NotificationReach"
            when: window.visible
            function check(ok, message) {
                if (!ok)
                    console.error("NOTIFICATION_REACH_FAIL", message);
                verify(ok, message);
            }
            function init() {
                store.entries = [];
                body.opened = false;
                body.keyboardMode = false;
                for (let i = 0; i < 9; i++)
                    store.local("Application " + i, "Notification " + i, "Message " + i);
                body.opened = true;
                wait(100);
            }
            function test_pointer_scroll() {
                check(!body.keyboardMode);
                mouseWheel(body.scroller, 250, 80, 0, -240);
                wait(150);
                check(body.scroller.contentY > 0, "pointer wheel scrolls history");
                compare(body.plan.items.filter(item => item.kind === "note").length, 9, "older application groups enter the scrollable list");
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
                compare(body.plan.items.filter(item => item.kind === "note").length, 9);
                check(body.scroller.contentHeight > body.scroller.height);
                check(body.scroller.contentY > 0, "overflow action scrolls to older groups");
                body.opened = false;
                body.opened = true;
                wait(100);
                check(body.plan.items.some(item => item.kind === "hidden"), "reopen restores compact history");
            }
            onCompletedChanged: if (completed)
                console.log("NOTIFICATION_REACH_RESULT", qtest_results.passCount, qtest_results.failCount)
        }
    }
}
