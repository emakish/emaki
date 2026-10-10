// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "settings"

Column {
    id: page
    required property var catalog
    required property string section
    spacing: 20
    SettingsCard {
        width: parent.width
        visible: page.section === "panel"
        title: "Panel"
        SettingsToggleRow {
            catalog: page.catalog
            settingKey: "bar.autohide"
            width: parent.width
            title: "Hide the panel automatically"
            fallbackValue: false
            explanation: value ? "Move to the top edge to show the panel." : "The panel stays visible above your windows."
        }
        SettingsToggleRow {
            catalog: page.catalog
            settingKey: "bar.overview_workspaces"
            width: parent.width
            title: "Show workspaces in the overview"
            fallbackValue: true
            explanation: value ? "Workspace numbers stay visible in the overview." : "Workspace numbers hide in the overview."
        }
    }
    SettingsCard {
        width: parent.width
        visible: page.section === "panel"
        title: "Dock"
        SettingsToggleRow {
            catalog: page.catalog
            settingKey: "dock.on"
            width: parent.width
            title: "Show the dock"
            fallbackValue: true
            explanation: value ? "Your pinned and running apps appear in the dock." : "The dock is hidden."
        }
        SettingsToggleRow {
            catalog: page.catalog
            settingKey: "dock.auto_hide"
            width: parent.width
            title: "Hide the dock automatically"
            fallbackValue: true
            explanation: value ? "Move to the bottom edge to show your apps." : "The dock stays visible beside your windows."
        }
    }
    SettingsCard {
        width: parent.width
        visible: page.section === "windows"
        title: "Window spacing"
        SettingsSliderRow {
            unit: "px"
            catalog: page.catalog
            settingKey: "appearance.gaps"
            width: parent.width
            title: "Gaps"
            fallbackValue: 2
            explanation: value + " px of wallpaper between windows."
            from: 0
            to: 64
            stepSize: 1
        }
    }
    SettingsCard {
        width: parent.width
        visible: page.section === "windows"
        title: "New windows"
        SettingsSegmentedRow {
            catalog: page.catalog
            settingKey: "windows.default_column_width"
            width: parent.width
            title: "Width of a new window"
            explanation: "Choose how much of the screen a new column uses."
            fallbackValue: "full"
            options: [
                {
                    value: "third",
                    label: "⅓"
                },
                {
                    value: "half",
                    label: "½"
                },
                {
                    value: "twothirds",
                    label: "⅔"
                },
                {
                    value: "full",
                    label: "Full"
                }
            ]
        }
    }
    SettingsCard {
        width: parent.width
        visible: page.section === "windows"
        title: "Focus"
        SettingsSegmentedRow {
            catalog: page.catalog
            settingKey: "windows.focus_follows_mouse"
            width: parent.width
            title: "Focus follows the mouse"
            explanation: "Use your existing focus behavior, or focus windows under the pointer."
            fallbackValue: false
            options: [
                {
                    value: false,
                    label: "Inherited"
                },
                {
                    value: true,
                    label: "Follow mouse"
                }
            ]
        }
    }
}
