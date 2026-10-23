// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import "Keyboard.js" as Keyboard

Item {
    id: tools
    property var service: null
    property var glass: null
    property int page: 0
    onPageChanged: {
        scroll.contentY = 0;
        if (glass && glass.repositionDrops)
            glass.repositionDrops();
    }
    readonly property var words: ({
            tabs: [qsTranslate("ClockTools", "Timer"), qsTranslate("ClockTools", "Stopwatch"), qsTranslate("ClockTools", "Alarms"), qsTranslate("ClockTools", "World clocks")],
            start: qsTranslate("ClockTools", "Start"),
            pause: qsTranslate("ClockTools", "Pause"),
            resume: qsTranslate("ClockTools", "Resume"),
            reset: qsTranslate("ClockTools", "Reset"),
            restart: qsTranslate("ClockTools", "Restart"),
            stop: qsTranslate("ClockTools", "Stop"),
            lap: qsTranslate("ClockTools", "Lap"),
            minutes: qsTranslate("ClockTools", "Minutes"),
            seconds: qsTranslate("ClockTools", "Seconds"),
            presets: [qsTranslate("ClockTools", "1 min"), qsTranslate("ClockTools", "5 min"), qsTranslate("ClockTools", "15 min"), qsTranslate("ClockTools", "25 min")],
            pomodoro: qsTranslate("ClockTools", "Pomodoro · 25 / 5"),
            work: qsTranslate("ClockTools", "Work"),
            rest: qsTranslate("ClockTools", "Break"),
            cycle: qsTranslate("ClockTools", "Cycle"),
            finished: qsTranslate("ClockTools", "Finished"),
            addAlarm: qsTranslate("ClockTools", "Add alarm"),
            edit: qsTranslate("ClockTools", "Edit"),
            save: qsTranslate("ClockTools", "Save"),
            cancel: qsTranslate("ClockTools", "Cancel"),
            remove: qsTranslate("ClockTools", "Remove"),
            on: qsTranslate("ClockTools", "On"),
            off: qsTranslate("ClockTools", "Off"),
            hour: qsTranslate("ClockTools", "Hour (0–23)"),
            minute: qsTranslate("ClockTools", "Minute (0–59)"),
            label: qsTranslate("ClockTools", "Alarm label"),
            once: qsTranslate("ClockTools", "Once"),
            days: [qsTranslate("ClockTools", "Mon"), qsTranslate("ClockTools", "Tue"), qsTranslate("ClockTools", "Wed"), qsTranslate("ClockTools", "Thu"), qsTranslate("ClockTools", "Fri"), qsTranslate("ClockTools", "Sat"), qsTranslate("ClockTools", "Sun")],
            awake: qsTranslate("ClockTools", "Alarms ring only while the computer is awake. Closing the lid may put it to sleep."),
            noAlarms: qsTranslate("ClockTools", "No alarms yet"),
            noLaps: qsTranslate("ClockTools", "Laps appear here"),
            noWorld: qsTranslate("ClockTools", "Add a city or time zone"),
            search: qsTranslate("ClockTools", "Search cities or time zones"),
            add: qsTranslate("ClockTools", "Add"),
            offset: qsTranslate("ClockTools", "from local time"),
            unavailable: qsTranslate("ClockTools", "Clock tools are connecting…"),
            invalid: qsTranslate("ClockTools", "Enter a valid time or duration."),
            startOver: qsTranslate("ClockTools", "Start over")
        })
    readonly property var snapshot: service ? service.state : ({})
    readonly property var timer: snapshot.timer || ({
            status: "idle",
            remaining: 0,
            duration: 0,
            pomodoro: false,
            cycle: 0
        })
    readonly property var stopwatch: snapshot.stopwatch || ({
            running: false,
            elapsed: 0,
            laps: []
        })
    readonly property color ink: glass ? glass.ink : LiquidPalette.ink
    readonly property color dim: glass ? glass.dim : LiquidPalette.dim
    readonly property color accent: glass ? glass.accent : LiquidPalette.accent
    property string worldQuery: ""
    readonly property var localizedSearch: Translation.searchTimeZones(worldQuery, Qt.uiLanguage)
    readonly property var worldSearch: localizedSearch.concat(snapshot.search || []).filter((zone, index, zones) => zones.indexOf(zone) === index).slice(0, 40)
    property string error: ""
    property bool editing: false
    property var alarmId: null
    property var originalAlarm: null
    property var repeatDays: []
    function send(command): void {
        if (service)
            service.send(command);
    }
    function duration(seconds): string {
        const value = Math.max(0, Math.floor(seconds || 0));
        return String(Math.floor(value / 3600)).padStart(2, "0") + ":" + String(Math.floor(value / 60) % 60).padStart(2, "0") + ":" + String(value % 60).padStart(2, "0");
    }
    function editAlarm(alarm): void {
        alarmId = alarm ? alarm.id : null;
        originalAlarm = alarm;
        hours.text = String(alarm ? alarm.hour : 7);
        minutes.text = String(alarm ? alarm.minute : 0).padStart(2, "0");
        alarmLabel.text = alarm ? alarm.label : "";
        repeatDays = alarm ? alarm.days.slice() : [];
        error = "";
        editing = true;
        hours.forceActiveFocus();
    }
    function saveAlarm(): void {
        if (!hours.acceptableInput || !minutes.acceptableInput || !hours.text || !minutes.text) {
            error = words.invalid;
            return;
        }
        const command = {
            op: alarmId === null ? "alarm_add" : "alarm_update",
            hour: Number(hours.text),
            minute: Number(minutes.text),
            days: repeatDays,
            label: alarmLabel.text
        };
        if (alarmId !== null)
            command.id = alarmId;
        if (originalAlarm && originalAlarm.days.length === 0 && repeatDays.length === 0 && (command.hour !== originalAlarm.hour || command.minute !== originalAlarm.minute))
            command.enabled = true;
        send(command);
        editing = false;
        error = "";
    }
    function startTimer(): void {
        const seconds = Number(durationMinutes.text) * 60 + Number(durationSeconds.text);
        if (!durationMinutes.acceptableInput || !durationSeconds.acceptableInput || seconds <= 0 || seconds > 604800) {
            error = words.invalid;
            return;
        }
        error = "";
        send({
            op: "timer_start",
            seconds: seconds,
            pomodoro: false
        });
    }
    component Caption: Text {
        color: tools.ink
        font.family: ShellPalette.uiFont
        font.pixelSize: 13
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
    }
    component Action: GlassTarget {
        id: action
        objectName: key
        glass: tools.glass
        width: Math.max(48, caption.implicitWidth + 22)
        height: 34
        Rectangle {
            anchors.fill: parent
            radius: 10
            color: Qt.alpha(tools.accent, action.accent ? .20 : .07)
        }
        Caption {
            id: caption
            anchors.centerIn: parent
            text: action.label
            color: !action.enabled ? tools.dim : action.accent ? tools.accent : tools.ink
        }
    }
    component Field: TextField {
        color: tools.ink
        font.family: ShellPalette.uiFont
        font.pixelSize: 13
        height: 36
        selectByMouse: true
        leftPadding: 10
        placeholderTextColor: tools.dim
        Accessible.name: placeholderText
        onActiveFocusChanged: if (activeFocus)
            Keyboard.reveal(this)
        Keys.onPressed: event => {
            if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab)
                Keyboard.handle(Keyboard.boundary(tools), event);
        }
        background: Rectangle {
            radius: 10
            color: Qt.alpha(tools.ink, .05)
            border.width: 1
            border.color: parent.activeFocus ? tools.accent : Qt.alpha(tools.dim, .2)
        }
    }
    Column {
        id: header
        width: parent.width
        spacing: 8
        Flow {
            width: parent.width
            spacing: 5
            Repeater {
                model: tools.words.tabs
                Action {
                    required property int index
                    required property string modelData
                    key: "clock-tab-" + index
                    label: modelData
                    accent: tools.page === index
                    onClicked: {
                        tools.page = index;
                        tools.error = "";
                    }
                }
            }
        }
        Caption {
            width: parent.width
            visible: !!text
            text: tools.error || tools.snapshot.error || (!tools.snapshot.ready ? tools.words.unavailable : "")
        }
        Action {
            key: "clock-start-over"
            label: tools.words.startOver
            visible: tools.snapshot.recoverable === true
            onClicked: if (tools.service)
                tools.service.recover()
        }
    }
    Flickable {
        id: scroll
        objectName: "clock-tools-scroll"
        y: header.height + 18
        width: parent.width
        height: Math.max(1, parent.height - y)
        contentWidth: width
        contentHeight: pages.height + 16
        clip: true
        onContentYChanged: if (tools.glass && tools.glass.repositionDrops)
            tools.glass.repositionDrops()
        ScrollBar.vertical: ScrollBar {}
        Column {
            id: pages
            width: scroll.width
            enabled: tools.snapshot.ready === true
            Column {
                width: parent.width
                spacing: 14
                visible: tools.page === 0
                Caption {
                    objectName: "clock-timer-display"
                    text: tools.duration(Math.ceil(tools.timer.remaining))
                    font.pixelSize: 42
                    font.weight: Font.Medium
                }
                Caption {
                    visible: !!text
                    text: tools.timer.pomodoro ? (tools.timer.phase === "break" ? qsTranslate("ClockTools", "Break · Cycle %1") : qsTranslate("ClockTools", "Work · Cycle %1")).arg(tools.timer.cycle) : tools.timer.status === "finished" ? tools.words.finished : ""
                    color: tools.accent
                }
                Flow {
                    width: parent.width
                    spacing: 6
                    Repeater {
                        model: [60, 300, 900, 1500]
                        Action {
                            required property int index
                            required property int modelData
                            key: "clock-preset-" + modelData
                            label: tools.words.presets[index]
                            onClicked: tools.send({
                                op: "timer_start",
                                seconds: modelData,
                                pomodoro: false
                            })
                        }
                    }
                    Action {
                        key: "clock-pomodoro"
                        label: tools.words.pomodoro
                        onClicked: tools.send({
                            op: "timer_start",
                            seconds: 1500,
                            pomodoro: true
                        })
                    }
                }
                Row {
                    spacing: 8
                    Column {
                        spacing: 4
                        Caption {
                            text: tools.words.minutes
                            color: tools.dim
                        }
                        Field {
                            id: durationMinutes
                            onAccepted: tools.startTimer()
                            objectName: "clock-duration-minutes"
                            width: 110
                            text: "5"
                            placeholderText: tools.words.minutes
                            validator: IntValidator {
                                bottom: 0
                                top: 10080
                            }
                        }
                    }
                    Column {
                        spacing: 4
                        Caption {
                            text: tools.words.seconds
                            color: tools.dim
                        }
                        Field {
                            id: durationSeconds
                            onAccepted: tools.startTimer()
                            objectName: "clock-duration-seconds"
                            width: 110
                            text: "00"
                            placeholderText: tools.words.seconds
                            validator: IntValidator {
                                bottom: 0
                                top: 59
                            }
                        }
                    }
                    Action {
                        key: "clock-timer-start"
                        label: tools.words.start
                        anchors.bottom: parent.bottom
                        onClicked: tools.startTimer()
                    }
                }
                Flow {
                    width: parent.width
                    spacing: 8
                    Action {
                        key: "clock-timer-pause"
                        label: tools.words.pause
                        visible: tools.timer.status === "running"
                        onClicked: tools.send({
                            op: "timer_pause"
                        })
                    }
                    Action {
                        key: "clock-timer-resume"
                        label: tools.words.resume
                        visible: tools.timer.status === "paused"
                        onClicked: tools.send({
                            op: "timer_resume"
                        })
                    }
                    Action {
                        key: "clock-timer-restart"
                        label: tools.words.restart
                        visible: tools.timer.status === "finished"
                        onClicked: tools.send({
                            op: "timer_restart"
                        })
                    }
                    Action {
                        key: "clock-timer-reset"
                        label: tools.words.reset
                        onClicked: tools.send({
                            op: "timer_reset"
                        })
                    }
                }
            }
            Column {
                width: parent.width
                spacing: 14
                visible: tools.page === 1
                Caption {
                    objectName: "clock-stopwatch-display"
                    text: tools.duration(tools.stopwatch.elapsed)
                    font.pixelSize: 42
                    font.weight: Font.Medium
                }
                Row {
                    spacing: 8
                    Action {
                        key: "clock-stopwatch-toggle"
                        label: tools.stopwatch.running ? tools.words.stop : tools.words.start
                        onClicked: tools.send({
                            op: tools.stopwatch.running ? "stopwatch_stop" : "stopwatch_start"
                        })
                    }
                    Action {
                        key: "clock-stopwatch-lap"
                        label: tools.words.lap
                        enabled: tools.stopwatch.running
                        onClicked: tools.send({
                            op: "stopwatch_lap"
                        })
                    }
                    Action {
                        key: "clock-stopwatch-reset"
                        label: tools.words.reset
                        onClicked: tools.send({
                            op: "stopwatch_reset"
                        })
                    }
                }
                Caption {
                    visible: !tools.stopwatch.laps.length
                    text: tools.words.noLaps
                    color: tools.dim
                }
                Repeater {
                    model: tools.stopwatch.laps
                    Caption {
                        required property int index
                        required property real modelData
                        width: pages.width
                        text: qsTranslate("ClockTools", "Lap %1     %2").arg(index + 1).arg(tools.duration(modelData))
                    }
                }
            }
            Column {
                width: parent.width
                spacing: 12
                visible: tools.page === 2
                Caption {
                    width: parent.width
                    text: tools.words.awake
                    color: tools.dim
                }
                Action {
                    key: "clock-alarm-add"
                    label: tools.words.addAlarm
                    onClicked: tools.editAlarm(null)
                }
                Column {
                    width: parent.width
                    spacing: 10
                    visible: tools.editing
                    Row {
                        spacing: 8
                        Column {
                            spacing: 4
                            Caption {
                                text: tools.words.hour
                                color: tools.dim
                            }
                            Field {
                                id: hours
                                onAccepted: tools.saveAlarm()
                                objectName: "clock-alarm-hour"
                                width: 120
                                placeholderText: tools.words.hour
                                validator: IntValidator {
                                    bottom: 0
                                    top: 23
                                }
                            }
                        }
                        Column {
                            spacing: 4
                            Caption {
                                text: tools.words.minute
                                color: tools.dim
                            }
                            Field {
                                id: minutes
                                onAccepted: tools.saveAlarm()
                                objectName: "clock-alarm-minute"
                                width: 130
                                placeholderText: tools.words.minute
                                validator: IntValidator {
                                    bottom: 0
                                    top: 59
                                }
                            }
                        }
                    }
                    Field {
                        id: alarmLabel
                        onAccepted: tools.saveAlarm()
                        objectName: "clock-alarm-label"
                        width: parent.width
                        placeholderText: tools.words.label
                        maximumLength: 120
                    }
                    Flow {
                        width: parent.width
                        spacing: 4
                        Repeater {
                            model: tools.words.days
                            Action {
                                required property int index
                                required property string modelData
                                key: "clock-alarm-day-" + index
                                label: modelData
                                width: 49
                                accent: tools.repeatDays.includes(index)
                                onClicked: tools.repeatDays = tools.repeatDays.includes(index) ? tools.repeatDays.filter(d => d !== index) : tools.repeatDays.concat([index]).sort()
                            }
                        }
                    }
                    Row {
                        spacing: 8
                        Action {
                            key: "clock-alarm-save"
                            label: tools.words.save
                            onClicked: tools.saveAlarm()
                        }
                        Action {
                            key: "clock-alarm-cancel"
                            label: tools.words.cancel
                            onClicked: tools.editing = false
                        }
                    }
                }
                Caption {
                    visible: !(tools.snapshot.alarms || []).length
                    text: tools.words.noAlarms
                    color: tools.dim
                }
                Repeater {
                    model: tools.snapshot.alarms || []
                    Column {
                        id: alarmRow
                        required property var modelData
                        width: pages.width
                        spacing: 4
                        Caption {
                            width: parent.width
                            text: String(alarmRow.modelData.hour).padStart(2, "0") + ":" + String(alarmRow.modelData.minute).padStart(2, "0") + "  " + alarmRow.modelData.label
                            font.pixelSize: 18
                        }
                        Caption {
                            width: parent.width
                            color: tools.dim
                            text: alarmRow.modelData.days.length ? alarmRow.modelData.days.map(d => tools.words.days[d]).join(" · ") : tools.words.once
                        }
                        Row {
                            spacing: 6
                            Action {
                                key: "clock-alarm-toggle-" + alarmRow.modelData.id
                                label: alarmRow.modelData.enabled ? tools.words.on : tools.words.off
                                accent: alarmRow.modelData.enabled
                                onClicked: tools.send({
                                    op: "alarm_update",
                                    id: alarmRow.modelData.id,
                                    enabled: !alarmRow.modelData.enabled
                                })
                            }
                            Action {
                                key: "clock-alarm-edit-" + alarmRow.modelData.id
                                label: tools.words.edit
                                onClicked: tools.editAlarm(alarmRow.modelData)
                            }
                            Action {
                                key: "clock-alarm-remove-" + alarmRow.modelData.id
                                label: tools.words.remove
                                onClicked: tools.send({
                                    op: "alarm_remove",
                                    id: alarmRow.modelData.id
                                })
                            }
                        }
                    }
                }
            }
            Column {
                width: parent.width
                spacing: 12
                visible: tools.page === 3
                Field {
                    objectName: "clock-world-search"
                    width: parent.width
                    placeholderText: tools.words.search
                    onTextEdited: {
                        tools.worldQuery = text;
                        tools.send({op: "world_search", query: text});
                    }
                }
                Repeater {
                    model: tools.worldSearch
                    Row {
                        id: resultRow
                        required property string modelData
                        width: pages.width
                        spacing: 8
                        Caption {
                            width: parent.width - add.width - 8
                            height: 34
                            verticalAlignment: Text.AlignVCenter
                            text: Translation.timeZoneLabel(resultRow.modelData, Qt.uiLanguage)
                            elide: Text.ElideRight
                            wrapMode: Text.NoWrap
                        }
                        Action {
                            id: add
                            key: "clock-world-add-" + resultRow.modelData
                            label: tools.words.add
                            onClicked: tools.send({
                                op: "world_add",
                                zone: resultRow.modelData
                            })
                        }
                    }
                }
                Caption {
                    visible: !(tools.snapshot.world || []).length
                    text: tools.words.noWorld
                    color: tools.dim
                }
                Repeater {
                    model: tools.snapshot.world || []
                    Column {
                        id: worldRow
                        required property var modelData
                        width: pages.width
                        spacing: 4
                        Row {
                            width: parent.width
                            spacing: 8
                            Caption {
                                width: parent.width - remove.width - 8
                                height: 34
                                verticalAlignment: Text.AlignVCenter
                                text: worldRow.modelData.time + "  " + Translation.timeZoneLabel(worldRow.modelData.zone, Qt.uiLanguage)
                                font.pixelSize: 17
                                elide: Text.ElideRight
                                wrapMode: Text.NoWrap
                            }
                            Action {
                                id: remove
                                key: "clock-world-remove-" + worldRow.modelData.zone
                                label: tools.words.remove
                                onClicked: tools.send({
                                    op: "world_remove",
                                    zone: worldRow.modelData.zone
                                })
                            }
                        }
                        Caption {
                            width: parent.width
                            text: qsTranslate("ClockTools", "%1 · %2 from local time").arg(worldRow.modelData.date).arg(worldRow.modelData.offset)
                            color: tools.dim
                        }
                    }
                }
            }
        }
    }
}
