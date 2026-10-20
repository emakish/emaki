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
            store: store
            today: new Date(2026, 9, 19, 23, 59)
        }
        TestCase {
            name: "CalendarReset"
            property var body: panel.body
            when: window.visible
            function test_reopen_and_midnight() {
                panel.opened = true;
                wait(30);
                compare(body.calendar.year, 2026);
                compare(body.calendar.month, 9);
                body.calendar.shift(4);
                body.calendar.picked = 12;
                compare(body.calendar.year, 2027);
                compare(body.calendar.month, 1);
                panel.today = new Date(2026, 9, 20, 0, 0);
                compare(body.calendar.month, 1, "clock updates preserve open navigation");
                compare(body.calendar.today.getDate(), 20, "today follows the date source");
                panel.opened = false;
                panel.opened = true;
                wait(30);
                compare(body.calendar.year, 2026);
                compare(body.calendar.month, 9);
                compare(body.calendar.picked, 0);
                verify(body.calendar.todayTarget !== null);
                compare(body.calendar.todayTarget.number, 20);
                panel.today = new Date(2026, 9, 21, 0, 0);
                compare(body.calendar.todayTarget.number, 21, "midnight moves the today marker");
                panel.opened = false;
                panel.today = new Date(2027, 0, 1, 0, 0);
                panel.opened = true;
                wait(30);
                compare(body.calendar.year, 2027);
                compare(body.calendar.month, 0);
                compare(body.calendar.todayTarget.number, 1);
            }
            onCompletedChanged: if (completed)
                console.log("CALENDAR_RESET_RESULT", qtest_results.passCount, qtest_results.failCount)
        }
    }
}
