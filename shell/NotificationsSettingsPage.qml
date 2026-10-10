// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import "settings"

Column {
    id: page
    required property var catalog
    property var store: null
    property string validationError: ""
    property double now: Date.now()
    readonly property var schedule: decode("notifications.schedule", {
        enabled: false,
        start: "22:00",
        end: "07:00"
    })
    readonly property var rules: decode("notifications.rules", {})
    readonly property double until: Number(value("notifications.until", "0"))
    spacing: 20

    function value(key, fallback) {
        const row = catalog.row(key);
        return row ? row.value : fallback;
    }
    function decode(key, fallback) {
        try {
            return JSON.parse(value(key, JSON.stringify(fallback)));
        } catch (error) {
            return fallback;
        }
    }
    function saveSchedule(field, next) {
        if ((field === "start" || field === "end") && !/^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(next)) {
            validationError = "Enter a time from 00:00 to 23:59.";
            return false;
        }
        validationError = "";
        const updated = Object.assign({}, schedule);
        updated[field] = next;
        catalog.set("notifications.schedule", JSON.stringify(updated));
        return true;
    }
    function resetSchedule(field) {
        const row = catalog.row("notifications.schedule");
        const defaults = row ? JSON.parse(row.default) : {
            enabled: false,
            start: "22:00",
            end: "07:00"
        };
        saveSchedule(field, defaults[field]);
    }
    function setRule(id, rule) {
        const updated = Object.assign({}, rules);
        if (rule === "allow")
            delete updated[id];
        else
            updated[id] = rule;
        catalog.set("notifications.rules", JSON.stringify(updated));
    }
    Timer {
        interval: 1000
        running: true
        repeat: true
        onTriggered: page.now = Date.now()
    }
    component TimeRow: SettingsRow {
        id: timeRow
        required property string field
        enabled: page.schedule.enabled === true
        opacity: enabled ? 1 : .45
        value: page.schedule[field]
        defaultValue: field === "start" ? "22:00" : "07:00"
        busy: page.catalog.writing
        unavailable: !page.catalog.row("notifications.schedule")
        controlWidth: 100
        onValueRequested: next => page.saveSchedule(field, next)
        onResetRequested: page.resetSchedule(field)
        control: Component {
            TextField {
                id: timeInput
                leftPadding: 13
                rightPadding: 13
                topPadding: 8
                bottomPadding: 8
                text: String(timeRow.value)
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 13
                color: SettingsTheme.ink
                selectByMouse: true
                Accessible.name: timeRow.title
                maximumLength: 5
                onEditingFinished: {
                    if (text !== String(timeRow.value))
                        timeRow.requestValue(text);
                    if (page.validationError === "")
                        text = Qt.binding(() => String(timeRow.value));
                }
                background: Rectangle {
                    radius: 10
                    color: SettingsTheme.field
                    border.width: timeInput.activeFocus ? 2 : 1
                    border.color: timeInput.activeFocus ? SettingsTheme.accent : SettingsTheme.rim
                }
            }
        }
    }
    SettingsCard {
        width: parent.width
        title: "Do not disturb"
        SettingsToggleRow {
            objectName: "setting-notifications.dnd"
            width: parent.width
            title: "Do not disturb now"
            explanation: "Keep notifications in history without showing popups."
            value: page.value("notifications.dnd", false)
            defaultValue: false
            busy: page.catalog.writing
            unavailable: !page.catalog.row("notifications.dnd")
            onValueRequested: next => page.catalog.set("notifications.dnd", String(next))
            onResetRequested: page.catalog.reset("notifications.dnd")
        }
        SettingsChoiceRow {
            objectName: "setting-notifications.until"
            width: parent.width
            title: "Pause notifications until"
            explanation: page.until > page.now ? "Paused until " + Qt.formatDateTime(new Date(page.until), "ddd HH:mm") + "." : "Pause popups for a limited time."
            value: page.until > page.now ? "active" : "off"
            defaultValue: "off"
            options: [
                {
                    label: "Off",
                    value: "off"
                },
                {
                    label: "30 minutes",
                    value: "30"
                },
                {
                    label: "1 hour",
                    value: "60"
                },
                {
                    label: "2 hours",
                    value: "120"
                }
            ].concat(page.until > page.now ? [
                {
                    label: Qt.formatDateTime(new Date(page.until), "ddd HH:mm"),
                    value: "active"
                }
            ] : [])
            busy: page.catalog.writing
            unavailable: !page.catalog.row("notifications.until")
            onValueRequested: next => {
                if (next !== "active")
                    page.catalog.set("notifications.until", next === "off" ? "0" : String(Date.now() + Number(next) * 60000));
            }
            onResetRequested: page.catalog.reset("notifications.until")
        }
        SettingsToggleRow {
            objectName: "setting-notifications.schedule"
            width: parent.width
            title: "Use a daily schedule"
            explanation: "Pause popups between these local times; matching times pause all day."
            value: page.schedule.enabled
            defaultValue: false
            busy: page.catalog.writing
            unavailable: !page.catalog.row("notifications.schedule")
            onValueRequested: next => page.saveSchedule("enabled", next)
            onResetRequested: page.resetSchedule("enabled")
        }
        TimeRow {
            objectName: "settings-notifications-start"
            width: parent.width
            field: "start"
            title: "Start time"
            explanation: "Begin the daily pause at this time (HH:MM)."
        }
        TimeRow {
            objectName: "settings-notifications-end"
            width: parent.width
            field: "end"
            title: "End time"
            explanation: "End the daily pause at this time (HH:MM)."
        }
    }
    Text {
        objectName: "settings-notifications-validation"
        width: parent.width
        visible: text !== ""
        text: page.validationError
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
        color: SettingsTheme.accentInk
    }
    Text {
        width: parent.width
        text: page.store && page.store.effectiveDnd ? "Do not disturb is active. Timers and schedules apply even when the manual switch is off." : "Do not disturb is off."
        textFormat: Text.PlainText
        wrapMode: Text.WordWrap
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
        color: SettingsTheme.dim
    }
    SettingsCard {
        width: parent.width
        objectName: "setting-notifications.rules"
        title: "Applications"
        Repeater {
            model: page.store ? page.store.applications : []
            SettingsChoiceRow {
                required property var modelData
                objectName: "settings-notifications-app-" + modelData.id
                width: parent.width
                title: modelData.name
                explanation: "Allow popups, keep only in history, or block notifications."
                value: page.rules[modelData.id] || "allow"
                defaultValue: "allow"
                options: [
                    {
                        label: "Allow",
                        value: "allow"
                    },
                    {
                        label: "Silent",
                        value: "silent"
                    },
                    {
                        label: "Off",
                        value: "off"
                    }
                ]
                busy: page.catalog.writing
                unavailable: !page.catalog.row("notifications.rules")
                onValueRequested: next => page.setRule(modelData.id, next)
                onResetRequested: page.setRule(modelData.id, "allow")
            }
        }
        Text {
            width: parent.width
            visible: !page.store || page.store.applications.length === 0
            text: "Applications appear here after sending a notification."
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            font.family: SettingsTheme.fontFamily
            font.pixelSize: 13
            color: SettingsTheme.dim
        }
    }
}
