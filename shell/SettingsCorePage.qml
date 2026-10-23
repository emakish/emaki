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
        title: qsTranslate("SettingsCorePage", "Panel")
        SettingsChoiceRow {
            id: panelHidingChoice
            catalog: page.catalog
            settingKey: "bar.autohide_mode"
            controlWidth: Math.max(180, Math.ceil(fullscreenLabel.advanceWidth
                + (controlItem?.leftPadding ?? 10) + (controlItem?.rightPadding ?? 10)
                + (controlItem?.contentItem.leftPadding ?? 13) + (controlItem?.contentItem.rightPadding ?? 26)))
            TextMetrics {
                id: fullscreenLabel
                text: panelHidingChoice.options.find(option => option.value === "fullscreen")?.label || ""
                font: panelHidingChoice.controlItem?.font ?? Qt.font({family: SettingsTheme.fontFamily, pixelSize: 13})
            }
            width: parent.width
            title: qsTranslate("SettingsCorePage", "Hide the panel automatically")
            fallbackValue: "fullscreen"
            explanation: qsTranslate("SettingsCorePage", "Choose when the panel hides. Move to the top edge to show it.")
            options: [
                {
                    value: "always",
                    label: qsTranslate("SettingsCorePage", "Always")
                },
                {
                    value: "desktop",
                    label: qsTranslate("SettingsCorePage", "On Desktop Only")
                },
                {
                    value: "fullscreen",
                    label: qsTranslate("SettingsCorePage", "In Full Screen Only")
                },
                {
                    value: "never",
                    label: qsTranslate("SettingsCorePage", "Never")
                }
            ]
        }
        SettingsToggleRow {
            catalog: page.catalog
            settingKey: "bar.overview_workspaces"
            width: parent.width
            title: qsTranslate("SettingsCorePage", "Show workspaces in the overview")
            fallbackValue: true
            explanation: value ? qsTranslate("SettingsCorePage", "Workspace numbers stay visible in the overview.") : qsTranslate("SettingsCorePage", "Workspace numbers hide in the overview.")
        }
    }
    SettingsCard {
        width: parent.width
        visible: page.section === "panel"
        title: qsTranslate("SettingsCorePage", "Dock")
        SettingsToggleRow {
            catalog: page.catalog
            settingKey: "dock.on"
            width: parent.width
            title: qsTranslate("SettingsCorePage", "Show the dock")
            fallbackValue: true
            explanation: value ? qsTranslate("SettingsCorePage", "Your pinned and running apps appear in the dock.") : qsTranslate("SettingsCorePage", "The dock is hidden.")
        }
        SettingsToggleRow {
            catalog: page.catalog
            settingKey: "dock.auto_hide"
            width: parent.width
            title: qsTranslate("SettingsCorePage", "Hide the dock automatically")
            fallbackValue: true
            explanation: value ? qsTranslate("SettingsCorePage", "Move to the bottom edge to show your apps.") : qsTranslate("SettingsCorePage", "The dock stays visible beside your windows.")
        }
    }
    SettingsCard {
        width: parent.width
        visible: page.section === "windows"
        title: qsTranslate("SettingsCorePage", "Window spacing")
        SettingsSliderRow {
            unit: "px"
            catalog: page.catalog
            settingKey: "appearance.gaps"
            width: parent.width
            title: qsTranslate("SettingsCorePage", "Gaps")
            fallbackValue: 2
            explanation: qsTranslate("SettingsCorePage", "%1 px of wallpaper between windows.").arg(Number(value).toFixed(0))
            from: 0
            to: 64
            stepSize: 1
        }
    }
    SettingsCard {
        width: parent.width
        visible: page.section === "windows"
        title: qsTranslate("SettingsCorePage", "New windows")
        SettingsSegmentedRow {
            id: newWindowWidthChoice
            catalog: page.catalog
            settingKey: "windows.default_column_width"
            controlWidth: Math.max(230, 4 * (fullWidthLabel.width + 12) + 14)
            TextMetrics {
                id: fullWidthLabel
                text: newWindowWidthChoice.options.find(option => option.value === "full")?.label || ""
                font.family: SettingsTheme.fontFamily
                font.pixelSize: 13
                font.weight: Font.Medium
            }
            width: parent.width
            title: qsTranslate("SettingsCorePage", "Width of a new window")
            explanation: qsTranslate("SettingsCorePage", "Choose how much of the screen a new column uses.")
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
                    label: qsTranslate("SettingsCorePage", "Full")
                }
            ]
        }
    }
    SettingsCard {
        width: parent.width
        visible: page.section === "windows"
        title: qsTranslate("SettingsCorePage", "Focus")
        SettingsSegmentedRow {
            catalog: page.catalog
            settingKey: "windows.focus_follows_mouse"
            width: parent.width
            title: qsTranslate("SettingsCorePage", "Focus follows the mouse")
            explanation: qsTranslate("SettingsCorePage", "Use your existing focus behavior, or focus windows under the pointer.")
            fallbackValue: false
            options: [
                {
                    value: false,
                    label: qsTranslate("SettingsCorePage", "Inherited")
                },
                {
                    value: true,
                    label: qsTranslate("SettingsCorePage", "Follow mouse")
                }
            ]
        }
    }
}
