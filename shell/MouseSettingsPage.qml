// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "settings"

Column {
    id: page
    required property var catalog
    spacing: 20
    Timer {
        interval: 5000
        running: page.visible
        repeat: true
        onTriggered: {
            if (!page.catalog.busy && typeof page.catalog.send === "function")
                page.catalog.send("list", []);
        }
    }
    function value(key, fallback) {
        const row = catalog.row(key);
        return row ? row.value : fallback;
    }
    function defaultValue(key, fallback) {
        const row = catalog.row(key);
        return row ? row.default : fallback;
    }
    Text {
        width: parent.width
        visible: page.catalog.row("mouse.speed")?.editable === false
        text: page.catalog.row("mouse.speed")?.explanation || ""
        textFormat: Text.PlainText
        color: SettingsTheme.dim
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
        wrapMode: Text.WordWrap
    }
    SettingsCard {
        visible: page.catalog.row("mouse.speed")?.editable !== false
        width: parent.width
        title: "Mouse"
        SettingsSliderRow {
            objectName: "setting-mouse.speed"
            width: parent.width
            title: "Pointer speed"
            value: page.value("mouse.speed", 0)
            defaultValue: page.defaultValue("mouse.speed", 0)
            formatValue: value => Math.round((Number(value) + 1) * 50) + "%"
            explanation: "Pointer speed: " + Math.round((Number(value) + 1) * 50) + "% of the adjustment range."
            from: -1
            to: 1
            stepSize: 0.05
            unavailable: !page.catalog.row("mouse.speed")
            busy: page.catalog.writing
            onValueRequested: value => page.catalog.set("mouse.speed", String(Math.round(value * 100) / 100))
            onResetRequested: page.catalog.reset("mouse.speed")
        }
        SettingsToggleRow {
            objectName: "setting-mouse.natural_scroll"
            width: parent.width
            title: "Natural scrolling"
            value: page.value("mouse.natural_scroll", false)
            defaultValue: page.defaultValue("mouse.natural_scroll", false)
            explanation: value ? "Content moves in the direction you scroll." : "Content moves against the direction you scroll."
            unavailable: !page.catalog.row("mouse.natural_scroll")
            busy: page.catalog.writing
            onValueRequested: value => page.catalog.set("mouse.natural_scroll", String(value))
            onResetRequested: page.catalog.reset("mouse.natural_scroll")
        }
    }
    Text {
        width: parent.width
        visible: page.catalog.row("touchpad.speed")?.editable === false && text !== page.catalog.row("mouse.speed")?.explanation
        text: page.catalog.row("touchpad.speed")?.explanation || ""
        textFormat: Text.PlainText
        color: SettingsTheme.dim
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 13
        wrapMode: Text.WordWrap
    }
    SettingsCard {
        visible: page.catalog.row("touchpad.speed")?.editable !== false
        width: parent.width
        title: "Trackpad"
        SettingsSliderRow {
            objectName: "setting-touchpad.speed"
            width: parent.width
            title: "Pointer speed"
            value: page.value("touchpad.speed", 0)
            defaultValue: page.defaultValue("touchpad.speed", 0)
            formatValue: value => Math.round((Number(value) + 1) * 50) + "%"
            explanation: "Pointer speed: " + Math.round((Number(value) + 1) * 50) + "% of the adjustment range."
            from: -1
            to: 1
            stepSize: 0.05
            unavailable: !page.catalog.row("touchpad.speed")
            busy: page.catalog.writing
            onValueRequested: value => page.catalog.set("touchpad.speed", String(Math.round(value * 100) / 100))
            onResetRequested: page.catalog.reset("touchpad.speed")
        }
        SettingsToggleRow {
            objectName: "setting-touchpad.natural_scroll"
            width: parent.width
            title: "Natural scrolling"
            value: page.value("touchpad.natural_scroll", true)
            defaultValue: page.defaultValue("touchpad.natural_scroll", true)
            explanation: value ? "Content follows your fingers." : "Content moves against your fingers."
            unavailable: !page.catalog.row("touchpad.natural_scroll")
            busy: page.catalog.writing
            onValueRequested: value => page.catalog.set("touchpad.natural_scroll", String(value))
            onResetRequested: page.catalog.reset("touchpad.natural_scroll")
        }
        SettingsToggleRow {
            objectName: "setting-touchpad.tap"
            width: parent.width
            title: "Tap to click"
            value: page.value("touchpad.tap", true)
            defaultValue: page.defaultValue("touchpad.tap", true)
            explanation: value ? "Tap the trackpad to click." : "Press the trackpad to click."
            unavailable: !page.catalog.row("touchpad.tap")
            busy: page.catalog.writing
            onValueRequested: value => page.catalog.set("touchpad.tap", String(value))
            onResetRequested: page.catalog.reset("touchpad.tap")
        }
        SettingsChoiceRow {
            objectName: "setting-touchpad.two_finger_right_click"
            width: parent.width
            title: "Two-finger right click"
            value: page.value("touchpad.two_finger_right_click", null)
            defaultValue: page.defaultValue("touchpad.two_finger_right_click", null)
            options: [
                {
                    label: "Device default",
                    value: null
                },
                {
                    label: "Two fingers",
                    value: true
                },
                {
                    label: "Button areas",
                    value: false
                }
            ]
            explanation: value === null ? "Clicking and tapping use the trackpad’s device defaults." : value ? (page.value("touchpad.tap", true) ? "Click or tap with two fingers to right-click." : "Click with two fingers to right-click.") : (page.value("touchpad.tap", true) ? "Press the lower-right corner or tap with three fingers to right-click." : "Press the lower-right corner to right-click.")
            unavailable: !page.catalog.row("touchpad.two_finger_right_click")
            busy: page.catalog.writing
            onValueRequested: value => value === null ? page.catalog.reset("touchpad.two_finger_right_click") : page.catalog.set("touchpad.two_finger_right_click", String(value))
            onResetRequested: page.catalog.reset("touchpad.two_finger_right_click")
        }
        SettingsToggleRow {
            objectName: "setting-touchpad.disable_while_typing"
            width: parent.width
            title: "Disable while typing"
            value: page.value("touchpad.disable_while_typing", true)
            defaultValue: page.defaultValue("touchpad.disable_while_typing", true)
            explanation: value ? "The trackpad pauses while you type." : "The trackpad stays active while you type."
            unavailable: !page.catalog.row("touchpad.disable_while_typing")
            busy: page.catalog.writing
            onValueRequested: value => page.catalog.set("touchpad.disable_while_typing", String(value))
            onResetRequested: page.catalog.reset("touchpad.disable_while_typing")
        }
    }
    SettingsCard {
        width: parent.width
        title: "Gestures"
        SettingsToggleRow {
            objectName: "setting-gestures.dnd_edge_view_scroll"
            width: parent.width
            title: "Scroll while dragging"
            value: page.value("gestures.dnd_edge_view_scroll", true)
            defaultValue: page.defaultValue("gestures.dnd_edge_view_scroll", true)
            explanation: value ? "Drag to the left or right edge to scroll through windows." : "Dragging to the left or right edge does not scroll."
            unavailable: !page.catalog.row("gestures.dnd_edge_view_scroll")
            busy: page.catalog.writing
            onValueRequested: value => page.catalog.set("gestures.dnd_edge_view_scroll", String(value))
            onResetRequested: page.catalog.reset("gestures.dnd_edge_view_scroll")
        }
        SettingsToggleRow {
            objectName: "setting-gestures.dnd_edge_workspace_switch"
            width: parent.width
            title: "Switch workspaces while dragging in the overview"
            value: page.value("gestures.dnd_edge_workspace_switch", true)
            defaultValue: page.defaultValue("gestures.dnd_edge_workspace_switch", true)
            explanation: value ? "In the overview, drag to the top or bottom edge to switch workspaces." : "In the overview, dragging to the top or bottom edge keeps this workspace."
            unavailable: !page.catalog.row("gestures.dnd_edge_workspace_switch")
            busy: page.catalog.writing
            onValueRequested: value => page.catalog.set("gestures.dnd_edge_workspace_switch", String(value))
            onResetRequested: page.catalog.reset("gestures.dnd_edge_workspace_switch")
        }
    }
}
