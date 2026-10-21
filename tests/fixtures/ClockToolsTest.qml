// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtTest
import Quickshell
import "Keyboard.js" as Keyboard

ShellRoot {
    NiriService {
        id: niri
        binary: ""
    }
    NotificationStore {
        id: notifications
    }
    QtObject {
        id: fakeService
        property var state: ({
                ready: true,
                timer: {
                    status: "idle",
                    remaining: 0,
                    duration: 0,
                    pomodoro: false,
                    cycle: 0
                },
                stopwatch: {
                    running: false,
                    elapsed: 0,
                    laps: []
                },
                alarms: [
                    {
                        id: "morning",
                        hour: 7,
                        minute: 30,
                        days: [0, 2, 4],
                        label: "Morning walk",
                        enabled: true
                    }
                ],
                world: [
                    {
                        zone: "Europe/London",
                        time: "15:41",
                        date: "2026-10-21",
                        offset: "+06:00"
                    }
                ],
                search: ["Asia/Tokyo"]
            })
        property int recoveries: 0
        function recover() {
            recoveries += 1;
        }
        property var commands: []
        function send(command) {
            commands = commands.concat([command]);
        }
        function update(key, value) {
            const next = Object.assign({}, state);
            next[key] = value;
            state = next;
        }
    }
    Window {
        id: window
        width: 560
        height: 720
        visible: true
        ShellScene {
            id: scene
            visible: false
            niri: niri
            borderMode: "soft"
            reservedSpace: 52
            headless: true
            testWidth: 560
            testHeight: 720
        }
        Item {
            id: boundary
            anchors.fill: parent
            property bool keyboardBoundary: true
            property bool integration: false
            ClockTools {
                id: tools
                visible: !boundary.integration
                anchors.fill: parent
                anchors.margins: 24
                service: fakeService
            }
            ClockBody {
                id: shade
                visible: boundary.integration
                width: parent.width
                height: parent.height
                opened: boundary.integration
                store: notifications
                clockTools: fakeService
                time: "09:41"
                longDate: "Wednesday, 21 October"
                today: new Date(2026, 9, 21)
            }
        }
        ClockPanel {
            id: panel
            visible: false
            store: notifications
            clockTools: fakeService
            viewportWidth: 560
            viewportHeight: 720
            expansion: 1
            head: 1
        }
        TestCase {
            name: "ClockTools"
            when: window.visible
            function control(name) {
                const item = findChild(tools, name);
                verify(item !== null, name);
                return item;
            }
            function press(name) {
                const item = control(name);
                verify(item.visible && item.enabled, name + " available");
                verify(Keyboard.targets(boundary).includes(item), name + " keyboard reachable");
                item.forceActiveFocus();
                verify(item.activeFocus, name + " focused");
                keyClick(Qt.Key_Return);
                wait(10);
            }
            function command(expected) {
                compare(JSON.stringify(fakeService.commands[fakeService.commands.length - 1]), JSON.stringify(expected));
            }
            function test_01_timer() {
                press("clock-preset-60");
                command({
                    op: "timer_start",
                    seconds: 60,
                    pomodoro: false
                });
                press("clock-pomodoro");
                command({
                    op: "timer_start",
                    seconds: 1500,
                    pomodoro: true
                });
                control("clock-duration-minutes").text = "2";
                control("clock-duration-seconds").text = "30";
                press("clock-timer-start");
                command({
                    op: "timer_start",
                    seconds: 150,
                    pomodoro: false
                });
                for (const field of ["clock-duration-minutes", "clock-duration-seconds"]) {
                    control(field).forceActiveFocus();
                    const before = fakeService.commands.length;
                    keyClick(Qt.Key_Return);
                    compare(fakeService.commands.length, before + 1);
                    command({
                        op: "timer_start",
                        seconds: 150,
                        pomodoro: false
                    });
                }
                const count = fakeService.commands.length;
                control("clock-duration-minutes").text = "0";
                control("clock-duration-seconds").text = "00";
                press("clock-timer-start");
                compare(fakeService.commands.length, count);
                verify(tools.error.length > 0);
                fakeService.update("timer", {
                    status: "running",
                    remaining: 125,
                    duration: 150,
                    pomodoro: false
                });
                compare(control("clock-timer-display").text, "00:02:05");
                press("clock-timer-pause");
                command({
                    op: "timer_pause"
                });
                fakeService.update("timer", {
                    status: "paused",
                    remaining: 125,
                    duration: 150,
                    pomodoro: false
                });
                press("clock-timer-resume");
                command({
                    op: "timer_resume"
                });
                fakeService.update("timer", {
                    status: "finished",
                    remaining: 0,
                    duration: 150,
                    pomodoro: false
                });
                press("clock-timer-restart");
                command({
                    op: "timer_restart"
                });
                press("clock-timer-reset");
                command({
                    op: "timer_reset"
                });
            }
            function test_02_stopwatch() {
                press("clock-tab-1");
                compare(tools.page, 1);
                press("clock-stopwatch-toggle");
                command({
                    op: "stopwatch_start"
                });
                fakeService.update("stopwatch", {
                    running: true,
                    elapsed: 65,
                    laps: [20, 42]
                });
                compare(control("clock-stopwatch-display").text, "00:01:05");
                press("clock-stopwatch-lap");
                command({
                    op: "stopwatch_lap"
                });
                press("clock-stopwatch-toggle");
                command({
                    op: "stopwatch_stop"
                });
                press("clock-stopwatch-reset");
                command({
                    op: "stopwatch_reset"
                });
            }
            function test_03_alarm() {
                press("clock-tab-2");
                compare(tools.page, 2);
                press("clock-alarm-add");
                verify(tools.editing);
                control("clock-alarm-hour").text = "8";
                control("clock-alarm-minute").text = "45";
                control("clock-alarm-label").text = "Tea";
                press("clock-alarm-day-0");
                press("clock-alarm-day-4");
                press("clock-alarm-save");
                command({
                    op: "alarm_add",
                    hour: 8,
                    minute: 45,
                    days: [0, 4],
                    label: "Tea"
                });
                verify(!tools.editing);
                press("clock-alarm-toggle-morning");
                command({
                    op: "alarm_update",
                    id: "morning",
                    enabled: false
                });
                press("clock-alarm-edit-morning");
                compare(control("clock-alarm-label").text, "Morning walk");
                compare(JSON.stringify(tools.repeatDays), "[0,2,4]");
                press("clock-alarm-cancel");
                verify(!tools.editing);
                press("clock-alarm-remove-morning");
                command({
                    op: "alarm_remove",
                    id: "morning"
                });
            }
            function test_04_world() {
                press("clock-tab-3");
                compare(tools.page, 3);
                const search = control("clock-world-search");
                search.forceActiveFocus();
                keyClick(Qt.Key_T);
                command({
                    op: "world_search",
                    query: "t"
                });
                press("clock-world-add-Asia/Tokyo");
                command({
                    op: "world_add",
                    zone: "Asia/Tokyo"
                });
                press("clock-world-remove-Europe/London");
                command({
                    op: "world_remove",
                    zone: "Europe/London"
                });
            }
            function test_05_tab_navigation() {
                press("clock-tab-0");
                control("clock-tab-0").forceActiveFocus();
                keyClick(Qt.Key_Tab);
                verify(control("clock-tab-1").activeFocus);
                keyClick(Qt.Key_Backtab, Qt.ShiftModifier);
                verify(control("clock-tab-0").activeFocus);
            }
            function test_06_shade_navigation() {
                NotesService.active = false;
                NotesService.ready = true;
                boundary.integration = true;
                wait(30);
                compare(findChild(shade, "clock-tools-view"), null);
                const year = shade.calendar.year;
                const month = shade.calendar.month;
                function activate(key) {
                    const item = shade.findTarget(shade, key);
                    verify(item !== null, key);
                    verify(Keyboard.targets(boundary).includes(item), key + " keyboard reachable");
                    item.forceActiveFocus();
                    keyClick(Qt.Key_Return);
                    wait(20);
                }
                activate("shade-clocks");
                verify(shade.showingClocks);
                verify(findChild(shade, "clock-tools-view").visible);
                verify(!findChild(shade, "clock-calendar-events").visible);
                activate("notes-back");
                verify(!shade.showingClocks);
                verify(findChild(shade, "clock-tools-view") !== null);
                compare(shade.calendar.year, year);
                compare(shade.calendar.month, month);
                activate("shade-notes");
                verify(shade.showingNotes);
                verify(findChild(shade, "clock-notes-view").visible);
                activate("notes-back");
                verify(!shade.showingNotes);
                verify(findChild(shade, "clock-calendar-events").visible);
            }
            function test_07_alarm_enter_and_reenable() {
                boundary.integration = false;
                tools.page = 2;
                const once = {
                    id: "once",
                    hour: 7,
                    minute: 30,
                    days: [],
                    label: "Tea",
                    enabled: false
                };
                for (const field of ["clock-alarm-hour", "clock-alarm-minute", "clock-alarm-label"]) {
                    tools.editAlarm(once);
                    control("clock-alarm-minute").text = "31";
                    control(field).forceActiveFocus();
                    keyClick(Qt.Key_Return);
                    verify(!tools.editing);
                    command({
                        op: "alarm_update",
                        hour: 7,
                        minute: 31,
                        days: [],
                        label: "Tea",
                        id: "once",
                        enabled: true
                    });
                }
                tools.editAlarm(once);
                control("clock-alarm-label").text = "Coffee";
                press("clock-alarm-save");
                command({
                    op: "alarm_update",
                    hour: 7,
                    minute: 30,
                    days: [],
                    label: "Coffee",
                    id: "once"
                });
            }
            function test_08_recovery() {
                fakeService.update("ready", false);
                fakeService.update("recoverable", true);
                fakeService.update("error", "Saved clock data cannot be read.");
                press("clock-start-over");
                compare(fakeService.recoveries, 1);
                fakeService.update("ready", true);
                fakeService.update("recoverable", false);
                fakeService.update("error", "");
                verify(!control("clock-start-over").visible);
            }
            function test_09_clipped_highlight() {
                boundary.visible = false;
                panel.visible = true;
                panel.opened = true;
                panel.body.showingClocks = true;
                wait(100);
                const view = findChild(panel.body, "clock-tools-view");
                const scroll = findChild(view, "clock-tools-scroll");
                const target = findChild(view, "clock-preset-60");
                verify(panel.targetVisible(target));
                const position = target.mapToItem(scroll, 0, 0);
                scroll.contentY = position.y + 1;
                verify(target.mapToItem(panel.body, 0, 0).y > panel.body.header);
                verify(!panel.targetVisible(target));
                verify(panel.targetVisible(findChild(view, "clock-tab-0")));
                scroll.contentY = 0;
                verify(panel.targetVisible(target));
            }
            function test_10_alarm_popup_survives_entry_change() {
                const alarm = scene.notes.transientLocal("Clock", "Alarm", "Morning walk", [], {
                    clockAlert: true
                });
                verify(scene.peekOpen);
                verify(scene.peekIds.includes(alarm));
                const other = scene.notes.local("Mail", "Message", "New message");
                verify(scene.notes.entries.some(entry => entry.id === other));
                verify(scene.peekOpen);
                compare(JSON.stringify(scene.peekIds), JSON.stringify([alarm]));
                scene.notes.dismiss([other]);
                compare(JSON.stringify(scene.peekIds), JSON.stringify([alarm]));
                scene.notes.dismiss([alarm]);
                verify(!scene.peekOpen);
            }
            onCompletedChanged: if (completed)
                console.log("CLOCK_TOOLS_RESULT", qtest_results.passCount, qtest_results.failCount)
        }
    }
}
