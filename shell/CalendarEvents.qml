// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: events
    property var service: NotesService
    property var glass: null
    property string selectedDate: Qt.formatDate(new Date(), "yyyy-MM-dd")
    // The day the user clicked in the grid, "" while no day is picked: only a click moves a draft.
    property string pickedDate: ""
    // The open draft's day; browsing months or midnight passing does not move it.
    property string draftDate: ""
    signal selectDate(string date)
    onPickedDateChanged: if (editing && pickedDate)
        draftDate = pickedDate
    readonly property string headingDate: editing && draftDate ? draftDate : selectedDate
    //: Qt date pattern: d, dddd and MMMM are fields; put literal words in single quotes, as in 'de'.
    readonly property string dateHeading: new Date(Number(headingDate.slice(0, 4)), Number(headingDate.slice(5, 7)) - 1, Number(headingDate.slice(8, 10))).toLocaleDateString(Qt.locale(), qsTranslate("CalendarEvents", "dddd, d MMMM")).replace(/^[а-яё]/, first => first.toUpperCase())
    readonly property color fieldColor: ink.r > 0.5 ? "#383239" : "#fffaf8"
    readonly property color rim: Qt.rgba(dim.r, dim.g, dim.b, 0.25)
    property bool editing: false
    property var original: null
    property string pending: ""
    property string error: ""
    property bool confirmingDelete: false
    readonly property color ink: glass ? glass.ink : LiquidPalette.inkOnDark
    readonly property color dim: glass ? glass.dim : LiquidPalette.dimOnDark
    readonly property color accent: glass ? glass.accent : LiquidPalette.accentOnDark
    readonly property var dayEvents: (service.events || []).filter(e => e.date === selectedDate).sort((a, b) => (a.duration === "all-day" ? "" : a.start).localeCompare(b.duration === "all-day" ? "" : b.start))
    readonly property var durations: [15, 30, 60, 120, "all-day", "custom"]
    readonly property var reminders: [null, 0, 5, 10, 30, 60, 1440]
    implicitHeight: content.height
    height: implicitHeight
    function edit(event): void {
        original = event || null;
        titleField.text = event ? event.title : "";
        draftDate = event ? event.date : selectedDate;
        selectDate(draftDate);
        hourField.value = event ? Number(event.start.slice(0, 2)) : 9;
        minuteField.value = event ? Number(event.start.slice(3, 5)) : 0;
        durationField.currentIndex = event ? Math.max(0, durations.indexOf(event.duration)) : 1;
        if (event && durations.indexOf(event.duration) < 0)
            durationField.currentIndex = 5;
        customField.text = String(event && typeof event.duration === "number" ? event.duration : 45);
        reminderField.currentIndex = event ? Math.max(0, reminders.indexOf(event.reminder)) : 0;
        error = "";
        confirmingDelete = false;
        editing = true;
        titleField.forceActiveFocus();
    }
    function save(): void {
        if (pending)
            return;
        const start = String(hourField.value).padStart(2, "0") + ":" + String(minuteField.value).padStart(2, "0");
        const duration = durations[durationField.currentIndex] === "custom" ? Number(customField.text) : durations[durationField.currentIndex];
        if (!titleField.text.trim() || !/^\d{4}-\d{2}-\d{2}$/.test(draftDate) || (duration !== "all-day" && !/^([01]\d|2[0-3]):[0-5]\d$/.test(start)) || (duration !== "all-day" && (!Number.isInteger(duration) || duration <= 0))) {
            error = qsTranslate("CalendarEvents", "Enter a title and a positive duration.");
            return;
        }
        const fields = {
            title: titleField.text.trim(),
            date: draftDate,
            start: duration === "all-day" ? "00:00" : start,
            duration: duration,
            reminder: reminders[reminderField.currentIndex]
        };
        if (original) {
            fields.eventId = original.id;
            fields.revision = original.revision;
        }
        error = "";
        pending = String(service.request("calendar.save", fields));
        if (!pending)
            error = service.error || qsTranslate("CalendarEvents", "Calendar is reconnecting. Your changes are still here.");
    }
    function remove(): void {
        if (pending || !original)
            return;
        pending = String(service.request("calendar.delete", {
            eventId: original.id,
            revision: original.revision
        }));
        if (!pending)
            error = service.error || qsTranslate("CalendarEvents", "Calendar is reconnecting. Try again shortly.");
    }
    Connections {
        target: events.service
        function onCompleted(result) {
            if (String(result.id) !== events.pending)
                return;
            events.pending = "";
            if (result.ok) {
                events.editing = false;
                events.confirmingDelete = false;
            } else
                events.error = result.error || qsTranslate("CalendarEvents", "The event could not be saved. Your changes are still here.");
        }
    }
    HoverHandler {
        onHoveredChanged: if (hovered && events.glass && typeof events.glass.leave === "function")
            events.glass.leave()
    }
    component Caption: Text {
        color: events.dim
        font.family: ShellPalette.uiFont
        font.pixelSize: 12
        textFormat: Text.PlainText
    }
    component Field: TextField {
        color: events.ink
        font.family: ShellPalette.uiFont
        font.pixelSize: 13
        selectByMouse: true
        height: 38
        leftPadding: 12
        placeholderTextColor: events.dim
        background: Rectangle {
            color: events.fieldColor
            radius: 10
            border.color: parent.activeFocus ? events.accent : events.rim
            border.width: 1
        }
    }
    component Choice: ComboBox {
        id: choice
        height: 38
        font.family: ShellPalette.uiFont
        font.pixelSize: 13
        contentItem: Text {
            leftPadding: 12
            rightPadding: 28
            text: choice.displayText
            font: choice.font
            color: events.ink
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        indicator: Text {
            x: choice.width - 25
            height: choice.height
            text: "⌄"
            color: events.dim
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 10
            color: events.fieldColor
            border.color: choice.activeFocus ? events.accent : events.rim
        }
        delegate: ItemDelegate {
            id: option
            required property var modelData
            required property int index
            width: choice.width
            height: 36
            highlighted: choice.highlightedIndex === index
            contentItem: Text {
                text: option.modelData
                color: events.ink
                font: choice.font
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
            }
            background: Rectangle {
                radius: 7
                color: option.highlighted ? events.rim : "transparent"
            }
        }
        popup: Popup {
            y: choice.height + 4
            width: choice.width
            padding: 5
            margins: 8
            implicitHeight: Math.min(contentItem.implicitHeight + 10, 270)
            contentItem: ListView {
                clip: true
                implicitHeight: contentHeight
                model: choice.popup.visible ? choice.delegateModel : null
                currentIndex: choice.highlightedIndex
                ScrollBar.vertical: ScrollBar {}
            }
            background: Rectangle {
                radius: 10
                color: events.fieldColor
                border.color: events.rim
            }
        }
    }
    component TimePart: SpinBox {
        id: part
        from: 0
        editable: false
        wrap: true
        height: 38
        font.family: ShellPalette.uiFont
        font.pixelSize: 14
        contentItem: Text {
            text: String(part.value).padStart(2, "0")
            font: part.font
            color: events.ink
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            color: events.fieldColor
            radius: 10
            border.color: part.activeFocus ? events.accent : events.rim
        }
        up.indicator: Rectangle {
            x: part.width - width
            width: 30
            height: part.height
            radius: 9
            color: part.up.pressed ? events.rim : "transparent"
            Text { anchors.centerIn: parent; text: "+"; color: events.dim; font.pixelSize: 18 }
        }
        down.indicator: Rectangle {
            width: 30
            height: part.height
            radius: 9
            color: part.down.pressed ? events.rim : "transparent"
            Text { anchors.centerIn: parent; text: "−"; color: events.dim; font.pixelSize: 18 }
        }
    }
    component Action: Button {
        id: action
        property bool primary: false
        leftPadding: 14
        rightPadding: 14
        font.family: ShellPalette.uiFont
        font.pixelSize: 13
        implicitHeight: 36
        contentItem: Text {
            text: action.text
            color: action.primary ? (events.ink.r > 0.5 ? "#29262c" : "#ffffff") : events.ink
            font: action.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            textFormat: Text.PlainText
        }
        background: Rectangle {
            radius: 10
            color: action.primary ? events.accent : action.down || action.hovered ? Qt.rgba(events.accent.r, events.accent.g, events.accent.b, 0.12) : "transparent"
            border.color: action.activeFocus ? events.accent : "transparent"
        }
    }
    Column {
        id: content
        width: parent.width
        spacing: 8
        Row {
            width: parent.width
            Caption {
                width: parent.width - 110
                height: 30
                verticalAlignment: Text.AlignVCenter
                text: events.dateHeading
                color: events.ink
                font.pixelSize: 15
                font.weight: Font.DemiBold
                elide: Text.ElideRight
            }
            Action {
                width: 110
                text: qsTranslate("CalendarEvents", "New event")
                visible: !events.editing
                onClicked: events.edit(null)
            }
        }
        Column {
            visible: !events.editing
            width: parent.width
            spacing: 2
            Caption {
                visible: events.dayEvents.length === 0
                text: qsTranslate("CalendarEvents", "No events. A little space in the day.")
                height: 28
                verticalAlignment: Text.AlignVCenter
            }
            Repeater {
                model: events.dayEvents
                Action {
                    required property var modelData
                    width: content.width
                    height: 46
                    text: (modelData.duration === "all-day" ? qsTranslate("CalendarEvents", "All day") : modelData.start + " · " + qsTranslate("CalendarEvents", "%n min", "", Number(modelData.duration))) + "  ·  " + modelData.title
                    onClicked: events.edit(modelData)
                    contentItem: Text {
                        text: parent.text
                        color: events.ink
                        font.family: ShellPalette.uiFont
                        font.pixelSize: 13
                        elide: Text.ElideRight
                        verticalAlignment: Text.AlignVCenter
                        textFormat: Text.PlainText
                    }
                }
            }
        }
        Column {
            visible: events.editing
            width: parent.width
            spacing: 12
            Caption {
                text: events.original ? qsTranslate("CalendarEvents", "Edit event") : qsTranslate("CalendarEvents", "New event")
            }
            Field {
                id: titleField
                objectName: "event-title"
                width: parent.width
                placeholderText: qsTranslate("CalendarEvents", "Title")
                Accessible.name: qsTranslate("CalendarEvents", "Event title")
                onAccepted: events.save()
            }
            Caption {
                text: qsTranslate("CalendarEvents", "Choose a day in the calendar above")
            }
            Column {
                width: parent.width
                spacing: 5
                Caption { text: qsTranslate("CalendarEvents", "Start time") }
                Row {
                    width: parent.width
                    spacing: 8
                    enabled: durationField.currentIndex !== 4
                    opacity: enabled ? 1 : 0.45
                    TimePart {
                        id: hourField
                        objectName: "event-hour"
                        width: 116
                        to: 23
                        Accessible.name: qsTranslate("CalendarEvents", "Start hour")
                    }
                    Caption { text: ":"; height: 38; verticalAlignment: Text.AlignVCenter }
                    TimePart {
                        id: minuteField
                        objectName: "event-minute"
                        width: 116
                        to: 59
                        Accessible.name: qsTranslate("CalendarEvents", "Start minute")
                    }
                }
            }
            Row {
                width: parent.width
                spacing: 8
                Column {
                    width: (parent.width - 8) / 2
                    spacing: 3
                    Caption {
                        text: qsTranslate("CalendarEvents", "Duration")
                    }
                    Choice {
                        id: durationField
                        objectName: "event-duration"
                        width: parent.width
                        model: [qsTranslate("CalendarEvents", "15 minutes"), qsTranslate("CalendarEvents", "30 minutes"), qsTranslate("CalendarEvents", "1 hour"), qsTranslate("CalendarEvents", "2 hours"), qsTranslate("CalendarEvents", "All day"), qsTranslate("CalendarEvents", "Custom")]
                        Accessible.name: qsTranslate("CalendarEvents", "Duration")
                    }
                }
                Column {
                    width: (parent.width - 8) / 2
                    spacing: 3
                    Caption {
                        text: qsTranslate("CalendarEvents", "Reminder")
                    }
                    Choice {
                        id: reminderField
                        objectName: "event-reminder"
                        width: parent.width
                        model: [qsTranslate("CalendarEvents", "None"), qsTranslate("CalendarEvents", "At start"), qsTranslate("CalendarEvents", "5 minutes before"), qsTranslate("CalendarEvents", "10 minutes before"), qsTranslate("CalendarEvents", "30 minutes before"), qsTranslate("CalendarEvents", "1 hour before"), qsTranslate("CalendarEvents", "1 day before")]
                        Accessible.name: qsTranslate("CalendarEvents", "Reminder")
                    }
                }
            }
            Caption {
                visible: durationField.currentIndex === 5
                text: qsTranslate("CalendarEvents", "Duration in minutes")
            }
            Field {
                id: customField
                objectName: "event-custom"
                visible: durationField.currentIndex === 5
                width: parent.width
                placeholderText: qsTranslate("CalendarEvents", "Duration in minutes")
                Accessible.name: qsTranslate("CalendarEvents", "Custom duration in minutes")
                validator: IntValidator {
                    bottom: 1
                    top: 525600
                }
            }
            Caption {
                width: parent.width
                visible: events.error !== ""
                text: events.error
                wrapMode: Text.Wrap
            }
            RowLayout {
                width: parent.width
                spacing: 8
                enabled: events.pending === ""
                Action {
                    visible: !!events.original
                    text: events.confirmingDelete ? qsTranslate("CalendarEvents", "Confirm delete") : qsTranslate("CalendarEvents", "Delete")
                    onClicked: {
                        if (events.confirmingDelete)
                            events.remove();
                        else
                            events.confirmingDelete = true;
                    }
                }
                Item { Layout.fillWidth: true }
                Action {
                    text: qsTranslate("CalendarEvents", "Cancel")
                    onClicked: {
                        events.editing = false;
                        events.confirmingDelete = false;
                    }
                }
                Action {
                    text: qsTranslate("CalendarEvents", "Save event")
                    primary: true
                    onClicked: events.save()
                }
            }
        }
    }
}
