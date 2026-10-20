// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    NotificationStore { id: store }
    FloatingWindow {
        id: window
        visible: true
        implicitWidth: 800
        implicitHeight: 720
        Rectangle {
            anchors.fill: parent
            color: LiquidPalette.ink.r > .5 ? "#29262c" : "#fffaf8"
        }
        ClockPanel {
            id: panel
            store: store
            viewportWidth: 800
            viewportHeight: 720
            opened: true
            expansion: 1
            head: 1
            today: new Date(2026, 9, 19)
            time: "09:41"
            longDate: "Monday, 19 October"
            serverState: "active"
        }
    }
    TestCase {
        name: "ClockScroll"
        when: window.backingWindowVisible
        onCompletedChanged: if (completed)
            console.log("CLOCK_SCROLL_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function initTestCase() {
            NotesService.active = false;
            NotesService.ready = true;
            NotesService.events = [];
            NotesService.notes = [];
            for (let i = 0; i < 12; ++i)
                store.addLocal("Calendar", "Reminder " + i, "A walk by the river", false, false, []);
            panel.body.historyExpanded = true;
            wait(500);
        }
        function test_scroll_data() {
            return [{tag: "light"}, {tag: "dark"}];
        }
        function test_scroll(data) {
            SettingsBridge.values = [{key: "appearance.theme", value: data.tag}];
            const body = panel.body;
            const scroll = body.scroller;
            const events = findChild(body, "clock-calendar-events");
            events.editing = false;
            scroll.contentY = 0;
            wait(400);
            const today = body.calendar.todayTarget;
            verify(today !== null);
            verify(panel.targetVisible(today));
            verify(panel.todayBubble.alpha.x > .9);
            const initialY = panel.todayBubble.y.x;
            panel.hover(today);
            wait(350);
            verify(panel.hoverBubble.alpha.x > .9);
            scroll.contentY = 24;
            compare(panel.hoverKey, "");
            compare(panel.hoverBubble.alpha.x, 0);
            wait(100);
            fuzzyCompare(panel.todayBubble.y.x, initialY - 24, 1);
            fuzzyCompare(panel.todayBubble.y.x, panel.screenRect(today).y, 1);
            panel.hover(today);
            wait(350);
            verify(panel.hoverBubble.alpha.x > .9);
            const previousTop = body.notesTop;
            events.edit(null);
            wait(100);
            verify(body.notesTop > previousTop);
            compare(panel.hoverKey, "");
            compare(panel.hoverBubble.alpha.x, 0);
            scroll.contentY = 0;
            wait(300);
            verify(panel.todayBubble.alpha.x > .9);
            const position = today.mapToItem(body, 0, 0);
            scroll.contentY = position.y - body.header + 1;
            compare(panel.todayBubble.alpha.x, 0);
            verify(!panel.targetVisible(today));
            wait(100);
            compare(panel.todayBubble.alpha.x, 0);
            grabImage(window.contentItem).save(Quickshell.env("EMAKI_CLOCK_SHOTS") + "/clock-scrolled-" + data.tag + ".png");
            scroll.contentY = 0;
            wait(350);
            verify(panel.todayBubble.alpha.x > .9);
            const image = grabImage(window.contentItem);
            image.save(Quickshell.env("EMAKI_CLOCK_SHOTS") + "/clock-scroll-" + data.tag + ".png");
        }
    }
}
