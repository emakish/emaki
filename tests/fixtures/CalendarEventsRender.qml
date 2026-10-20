// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick

Rectangle {
    id: root
    property bool dark: false
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
    QtObject {
        id: store
        property var events: [
            {
                id: "event-1",
                title: "Morning walk",
                date: "2026-10-19",
                start: "09:00",
                duration: 45,
                reminder: 10,
                revision: "v1"
            }
        ]
        signal completed(var result)
        function request(action, fields) {
            return "1";
        }
    }
    CalendarMonth {
        id: month
        x: 22
        y: 20
        width: 516
        today: new Date(2026, 9, 19)
        glass: colors
        events: store.events
    }
    CalendarEvents {
        id: events
        x: 22
        y: month.y + month.height + 12
        width: 516
        glass: colors
        service: store
        selectedDate: month.selectedDate
        onSelectDate: date => month.selectDate(date)
        Component.onCompleted: edit(store.events[0])
    }
}
