// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    NiriService {
        id: niri
        binary: ""
    }
    FloatingWindow {
        id: window
        visible: true
        implicitWidth: 1200
        implicitHeight: 800
        ShellScene {
            id: scene
            anchors.fill: parent
            niri: niri
            headless: true
            testWidth: 1200
            testHeight: 800
        }
        ClockCompactRow {
            id: compact
            time: scene.bar.clockTime
            date: scene.bar.clockDate
            visible: false
        }
    }
    TestCase {
        name: "ClockSeconds"
        when: window.backingWindowVisible
        onCompletedChanged: if (completed)
            console.log("CLOCK_SECONDS_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function initTestCase() {
            Quickshell.watchFiles = false;
            NotesService.active = false;
            scene.openDrawer();
            wait(500);
        }
        function test_formats_data() {
            let rows = [];
            for (const twelve of [false, true])
                for (const closed of [false, true])
                    for (const opened of [false, true])
                        rows.push({
                            tag: String(twelve) + closed + opened,
                            twelve: twelve,
                            closed: closed,
                            opened: opened
                        });
            return rows;
        }
        function test_formats(data) {
            SettingsBridge.values = [
                {
                    key: "bar.clock_24_hour",
                    value: !data.twelve
                },
                {
                    key: "bar.clock_seconds_closed",
                    value: data.closed
                },
                {
                    key: "bar.clock_seconds_open",
                    value: data.opened
                }
            ];
            wait(20);
            compare(scene.bar.clockTime.split(":").length, data.closed ? 3 : 2);
            compare(scene.bar.clockOpenTime.split(":").length, data.opened ? 3 : 2);
            compare(scene.clockPanel.time, scene.bar.clockTime);
            compare(scene.clockBody.time, scene.bar.clockOpenTime);
            compare(compact.showSeconds, data.closed);
            const beforeWidth = compact.naturalWidth;
            const beforeDateX = findChild(scene.clockBody, "clock-open-date").x;
            const beforeMinute = scene.bar.dateTime.getTime();
            const initial = scene.bar.clockOpenTime;
            const initialClosed = scene.bar.clockTime;
            const dateLabel = findChild(scene.clockBody, "clock-open-date");
            const timeLabel = findChild(scene.clockBody, "clock-open-time");
            verify(dateLabel.x >= timeLabel.x + timeLabel.implicitWidth + 13);
            wait(1150);
            compare(compact.naturalWidth, beforeWidth);
            compare(findChild(scene.clockBody, "clock-open-date").x, beforeDateX);
            // A minute boundary may update both clocks, but only the enabled seconds
            // clock must tick within a minute.
            if (scene.bar.dateTime.getTime() === beforeMinute) {
                compare(scene.bar.clockOpenTime !== initial, data.opened);
                compare(scene.bar.clockTime !== initialClosed, data.closed);
            }
        }
        function test_defaults() {
            SettingsBridge.values = [];
            wait(20);
            compare(scene.bar.secondsClosed, false);
            compare(scene.bar.secondsOpen, false);
            compare(scene.bar.clockTime.split(":").length, 2);
            compare(scene.bar.clockOpenTime.split(":").length, 2);
        }
    }
}
