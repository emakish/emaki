// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    Window {
        id: window
        width: 560
        height: 620
        visible: true
        color: "#29262c"
        QtObject {
            id: mock
            property var events: [
                {
                    id: "event-1",
                    title: "Morning walk",
                    date: "2026-10-19",
                    start: "09:07",
                    duration: 45,
                    reminder: 10,
                    revision: "v1"
                }
            ]
            property var last: null
            signal completed(var result)
            function request(action, fields) {
                last = {
                    action: action,
                    fields: fields
                };
                return "request-1";
            }
        }
        CalendarMonth {
            id: month
            x: 22
            y: 20
            width: 516
            today: new Date(2026, 9, 19)
            events: mock.events
        }
        CalendarEvents {
            id: view
            x: 22
            y: month.y + month.height + 12
            width: 516
            service: mock
            selectedDate: month.selectedDate
            pickedDate: month.picked ? month.selectedDate : ""
            onSelectDate: date => month.selectDate(date)
        }
        NotificationStore {
            id: notifications
        }
        ClockBody {
            id: shade
            width: 560
            height: 650
            visible: false
            store: notifications
        }
        TestCase {
            name: "CalendarEvents"
            when: window.visible
            function test_edit_and_conflict() {
                compare(view.dayEvents.length, 1);
                view.edit(mock.events[0]);
                compare(findChild(view, "event-duration").currentIndex, 5);
                wait(30);
                const minutes = findChild(view, "event-minute");
                mouseClick(minutes, minutes.width - 15, minutes.height / 2);
                compare(minutes.value, 8);
                mouseClick(minutes, 15, minutes.height / 2);
                compare(minutes.value, 7);
                findChild(view, "event-title").text = "Walk by the river";
                view.save();
                compare(mock.last.action, "calendar.save");
                compare(mock.last.fields.revision, "v1");
                compare(mock.last.fields.duration, 45);
                compare(mock.last.fields.start, "09:07");
                compare(findChild(view, "event-hour").value, 9);
                compare(findChild(view, "event-minute").value, 7);
                mock.completed({
                    id: "request-1",
                    ok: false,
                    error: "Event changed in another window"
                });
                verify(view.editing);
                compare(findChild(view, "event-title").text, "Walk by the river");
                verify(view.error.length > 0);
                view.save();
                mock.completed({
                    id: "request-1",
                    ok: true
                });
                verify(!view.editing);
            }
            function test_new_and_delete() {
                month.picked = 21;
                compare(view.selectedDate, "2026-10-21");
                compare(view.dayEvents.length, 0);
                view.edit(null);
                month.picked = 22;
                compare(view.dateHeading, "Thursday, 22 October");
                // Browsing months while the editor is open does not move the draft.
                month.shift(1);
                compare(view.dateHeading, "Thursday, 22 October");
                month.shift(-1);
                findChild(view, "event-title").text = "A day outside";
                findChild(view, "event-duration").currentIndex = 4;
                findChild(view, "event-reminder").currentIndex = 6;
                view.save();
                compare(mock.last.fields.duration, "all-day");
                compare(mock.last.fields.start, "00:00");
                compare(mock.last.fields.reminder, 1440);
                compare(mock.last.fields.date, "2026-10-22");
                mock.completed({
                    id: "request-1",
                    ok: true
                });
                view.edit(mock.events[0]);
                view.remove();
                compare(mock.last.action, "calendar.delete");
                compare(mock.last.fields.eventId, "event-1");
                compare(mock.last.fields.revision, "v1");
                mock.completed({
                    id: "request-1",
                    ok: true
                });
            }
            function test_shade_dimensions_and_event_draft() {
                shade.opened = false;
                shade.opened = true;
                // The plate fits the events, the Notes row and the notifications below them.
                compare(shade.drawerHeight, shade.fullHeight);
                verify(shade.listTop < shade.drawerHeight);
                const calendar = findChild(shade, "clock-calendar-events");
                // On a short screen the editor scrolls inside the plate instead of growing it.
                shade.maxHeight = 520;
                shade.calendar.picked = 21;
                const draftDate = calendar.selectedDate;
                calendar.edit(null);
                findChild(calendar, "event-title").text = "An unfinished event";
                wait(20);
                compare(shade.drawerHeight, 520);
                verify(shade.scroller.contentHeight > shade.scroller.height);
                shade.maxHeight = 10000;
                shade.opened = false;
                shade.opened = true;
                verify(calendar.editing);
                compare(calendar.selectedDate, draftDate);
                compare(findChild(calendar, "event-title").text, "An unfinished event");
            }
            function test_notes_draft_survives_close() {
                NotesService.active = false;
                shade.opened = true;
                shade.showingNotes = true;
                wait(30);
                const notes = findChild(shade, "clock-notes-view");
                verify(notes !== null);
                notes.draft = "An unfinished thought";
                shade.showingNotes = false;
                shade.opened = false;
                shade.opened = true;
                shade.showingNotes = true;
                wait(30);
                compare(findChild(shade, "clock-notes-view"), notes);
                compare(notes.draft, "An unfinished thought");
            }
            function test_validation() {
                view.edit(null);
                view.save();
                verify(view.error.length > 0);
                compare(view.pending, "");
            }
            onCompletedChanged: if (completed)
                console.log("CALENDAR_EVENTS_RESULT", qtest_results.passCount, qtest_results.failCount)
        }
    }
}
