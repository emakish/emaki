// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick

Rectangle {
    id: root
    property bool dark: false
    property bool integrated: false
    property string view: "timer"
    width: 560
    height: 720
    color: dark ? "#29262c" : "#fffaf8"
    QtObject {
        id: colors
        property color ink: root.dark ? "#f7f0ed" : "#322b2e"
        property color dim: root.dark ? "#bfb0b8" : "#786a70"
        property color faint: root.dark ? "#544a50" : "#d3c5cb"
        property color accent: root.dark ? "#f3a4bf" : "#ad4169"
    }
    QtObject {
        id: fakeService
        property bool ready: true
        property string error: ""
        property var state: ({
                ready: true,
                timer: {
                    status: "running",
                    remaining: 1425,
                    duration: 1500,
                    pomodoro: true,
                    phase: "work",
                    cycle: 2
                },
                stopwatch: {
                    running: true,
                    elapsed: 163,
                    laps: [61, 123]
                },
                alarms: [
                    {
                        id: "morning",
                        hour: 7,
                        minute: 30,
                        days: [0, 1, 2, 3, 4],
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
                    },
                    {
                        zone: "Asia/Tokyo",
                        time: "23:41",
                        date: "2026-10-21",
                        offset: "+14:00"
                    }
                ],
                search: []
            })
        function send(command) {
            return true;
        }
    }
    ClockTools {
        id: tools
        visible: !root.integrated
        x: 24
        y: 24
        width: parent.width - 48
        height: parent.height - 48
        page: ["timer", "stopwatch", "alarms", "world"].indexOf(root.view)
        service: fakeService
        glass: colors
    }
    NotificationStore {
        id: notifications
    }
    ClockBody {
        id: shade
        visible: root.integrated
        width: parent.width
        height: drawerHeight
        opened: root.integrated
        clockTools: fakeService
        store: notifications
        glass: colors
        today: new Date(2026, 9, 21)
        time: "09:41"
        longDate: "Wednesday, 21 October"
        serverState: "active"
    }
    Timer {
        interval: 50
        running: root.integrated
        onTriggered: shade.showingClocks = root.view !== "calendar"
    }
    Component.onCompleted: {
        NotesService.active = false;
        NotesService.ready = true;
        SettingsBridge.values = [
            {
                key: "appearance.theme",
                value: dark ? "dark" : "light"
            }
        ];
    }
}
