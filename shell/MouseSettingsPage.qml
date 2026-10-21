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
        title: qsTranslate("MouseSettingsPage", "Mouse")
        SettingsSliderRow {
            objectName: "setting-mouse.speed"
            width: parent.width
            title: qsTranslate("MouseSettingsPage", "Pointer speed")
            value: page.value("mouse.speed", 0)
            defaultValue: page.defaultValue("mouse.speed", 0)
            formatValue: value => Math.round((Number(value) + 1) * 50) + "%"
            explanation: qsTranslate("MouseSettingsPage", "Pointer speed: %1% of the adjustment range.").arg(Math.round((Number(value) + 1) * 50).toFixed(0))
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
            title: qsTranslate("MouseSettingsPage", "Natural scrolling")
            value: page.value("mouse.natural_scroll", false)
            defaultValue: page.defaultValue("mouse.natural_scroll", false)
            explanation: value ? qsTranslate("MouseSettingsPage", "Content moves in the direction you scroll.") : qsTranslate("MouseSettingsPage", "Content moves against the direction you scroll.")
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
        title: qsTranslate("MouseSettingsPage", "Trackpad")
        SettingsSliderRow {
            objectName: "setting-touchpad.speed"
            width: parent.width
            title: qsTranslate("MouseSettingsPage", "Pointer speed")
            value: page.value("touchpad.speed", 0)
            defaultValue: page.defaultValue("touchpad.speed", 0)
            formatValue: value => Math.round((Number(value) + 1) * 50) + "%"
            explanation: qsTranslate("MouseSettingsPage", "Pointer speed: %1% of the adjustment range.").arg(Math.round((Number(value) + 1) * 50).toFixed(0))
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
            title: qsTranslate("MouseSettingsPage", "Natural scrolling")
            value: page.value("touchpad.natural_scroll", true)
            defaultValue: page.defaultValue("touchpad.natural_scroll", true)
            explanation: value ? qsTranslate("MouseSettingsPage", "Content follows your fingers.") : qsTranslate("MouseSettingsPage", "Content moves against your fingers.")
            unavailable: !page.catalog.row("touchpad.natural_scroll")
            busy: page.catalog.writing
            onValueRequested: value => page.catalog.set("touchpad.natural_scroll", String(value))
            onResetRequested: page.catalog.reset("touchpad.natural_scroll")
        }
        SettingsToggleRow {
            objectName: "setting-touchpad.tap"
            width: parent.width
            title: qsTranslate("MouseSettingsPage", "Tap to click")
            value: page.value("touchpad.tap", true)
            defaultValue: page.defaultValue("touchpad.tap", true)
            explanation: value ? qsTranslate("MouseSettingsPage", "Tap the trackpad to click.") : qsTranslate("MouseSettingsPage", "Press the trackpad to click.")
            unavailable: !page.catalog.row("touchpad.tap")
            busy: page.catalog.writing
            onValueRequested: value => page.catalog.set("touchpad.tap", String(value))
            onResetRequested: page.catalog.reset("touchpad.tap")
        }
        SettingsChoiceRow {
            objectName: "setting-touchpad.two_finger_right_click"
            width: parent.width
            title: qsTranslate("MouseSettingsPage", "Two-finger right click")
            value: page.value("touchpad.two_finger_right_click", null)
            defaultValue: page.defaultValue("touchpad.two_finger_right_click", null)
            options: [
                {
                    label: qsTranslate("MouseSettingsPage", "Device default"),
                    value: null
                },
                {
                    label: qsTranslate("MouseSettingsPage", "Two fingers"),
                    value: true
                },
                {
                    label: qsTranslate("MouseSettingsPage", "Button areas"),
                    value: false
                }
            ]
            explanation: value === null ? qsTranslate("MouseSettingsPage", "Clicking and tapping use the trackpad’s device defaults.") : value ? (page.value("touchpad.tap", true) ? qsTranslate("MouseSettingsPage", "Click or tap with two fingers to right-click.") : qsTranslate("MouseSettingsPage", "Click with two fingers to right-click.")) : (page.value("touchpad.tap", true) ? qsTranslate("MouseSettingsPage", "Press the lower-right corner or tap with three fingers to right-click.") : qsTranslate("MouseSettingsPage", "Press the lower-right corner to right-click."))
            unavailable: !page.catalog.row("touchpad.two_finger_right_click")
            busy: page.catalog.writing
            onValueRequested: value => value === null ? page.catalog.reset("touchpad.two_finger_right_click") : page.catalog.set("touchpad.two_finger_right_click", String(value))
            onResetRequested: page.catalog.reset("touchpad.two_finger_right_click")
        }
        SettingsToggleRow {
            objectName: "setting-touchpad.disable_while_typing"
            width: parent.width
            title: qsTranslate("MouseSettingsPage", "Disable while typing")
            value: page.value("touchpad.disable_while_typing", true)
            defaultValue: page.defaultValue("touchpad.disable_while_typing", true)
            explanation: value ? qsTranslate("MouseSettingsPage", "The trackpad pauses while you type.") : qsTranslate("MouseSettingsPage", "The trackpad stays active while you type.")
            unavailable: !page.catalog.row("touchpad.disable_while_typing")
            busy: page.catalog.writing
            onValueRequested: value => page.catalog.set("touchpad.disable_while_typing", String(value))
            onResetRequested: page.catalog.reset("touchpad.disable_while_typing")
        }
    }
    SettingsCard {
        width: parent.width
        title: qsTranslate("MouseSettingsPage", "Gestures")
        SettingsToggleRow {
            objectName: "setting-gestures.dnd_edge_view_scroll"
            width: parent.width
            title: qsTranslate("MouseSettingsPage", "Scroll while dragging")
            value: page.value("gestures.dnd_edge_view_scroll", true)
            defaultValue: page.defaultValue("gestures.dnd_edge_view_scroll", true)
            explanation: value ? qsTranslate("MouseSettingsPage", "Drag to the left or right edge to scroll through windows.") : qsTranslate("MouseSettingsPage", "Dragging to the left or right edge does not scroll.")
            unavailable: !page.catalog.row("gestures.dnd_edge_view_scroll")
            busy: page.catalog.writing
            onValueRequested: value => page.catalog.set("gestures.dnd_edge_view_scroll", String(value))
            onResetRequested: page.catalog.reset("gestures.dnd_edge_view_scroll")
        }
        SettingsToggleRow {
            objectName: "setting-gestures.dnd_edge_workspace_switch"
            width: parent.width
            title: qsTranslate("MouseSettingsPage", "Switch workspaces while dragging in the overview")
            value: page.value("gestures.dnd_edge_workspace_switch", true)
            defaultValue: page.defaultValue("gestures.dnd_edge_workspace_switch", true)
            explanation: value ? qsTranslate("MouseSettingsPage", "In the overview, drag to the top or bottom edge to switch workspaces.") : qsTranslate("MouseSettingsPage", "In the overview, dragging to the top or bottom edge keeps this workspace.")
            unavailable: !page.catalog.row("gestures.dnd_edge_workspace_switch")
            busy: page.catalog.writing
            onValueRequested: value => page.catalog.set("gestures.dnd_edge_workspace_switch", String(value))
            onResetRequested: page.catalog.reset("gestures.dnd_edge_workspace_switch")
        }
    }
}
