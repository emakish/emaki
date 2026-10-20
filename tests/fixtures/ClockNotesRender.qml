// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick

Rectangle {
    id: root
    property bool dark: false
    property bool notes: false
    property bool scrolled: false
    width: 560
    height: 650
    color: dark ? "#29262c" : "#fffaf8"
    QtObject {
        id: colors
        property color ink: root.dark ? "#f7f0ed" : "#322b2e"
        property color dim: root.dark ? "#bfb0b8" : "#786a70"
        property color faint: root.dark ? "#544a50" : "#d3c5cb"
        property color accent: root.dark ? "#f3a4bf" : "#ad4169"
    }
    NotificationStore {
        id: notifications
        entries: [
            {
                id: -1,
                app: "Calendar",
                summary: "A walk by the river",
                body: "A little time outside this afternoon.",
                time: Date.now(),
                actions: [],
                deadline: 0
            }
        ]
    }
    ClockBody {
        id: shade
        width: parent.width
        height: drawerHeight
        opened: true
        store: notifications
        glass: colors
        today: new Date(2026, 9, 19)
        time: "09:41"
        longDate: "Monday, 19 October"
        serverState: "active"
    }
    Timer {
        interval: 100
        running: true
        onTriggered: {
            notifications.addLocal("Calendar", "A walk by the river", "A little time outside this afternoon.", false, false, []);
            notifications.addLocal("Files", "Travel plans", "Your notes are ready for the weekend.", false, false, []);
            if (root.scrolled)
                shade.scroller.contentY = Math.max(0, shade.scroller.contentHeight - shade.scroller.height);
        }
    }
    Component.onCompleted: {
        SettingsBridge.values = [
            {
                key: "appearance.theme",
                value: dark ? "dark" : "light"
            }
        ];
        NotesService.active = false;
        NotesService.ready = true;
        NotesService.notes = [
            {
                id: "note-1",
                title: "A quieter week",
                body: "A quieter week\n- [ ] Walk by the river\n- [x] Make time to read",
                pinned: true,
                revision: "v1",
                updated: 1792400400
            },
            {
                id: "note-2",
                title: "Ideas for the weekend",
                body: "Ideas for the weekend\nVisit the bookshop and bring a notebook.",
                pinned: false,
                revision: "v1",
                updated: 1792400400
            }
        ];
        NotesService.events = [
            {
                id: "event-1",
                title: "Morning walk",
                date: "2026-10-19",
                start: "09:00",
                duration: 45,
                reminder: 10,
                revision: "v1"
            }
        ];
        notifications.addLocal("Calendar", "A walk by the river", "A little time outside this afternoon.", false, false, []);
        notifications.addLocal("Files", "Travel plans", "Your notes are ready for the weekend.", false, false, []);
        shade.showingNotes = notes;
    }
}
