// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import "settings"

Column {
    id: page
    required property var catalog
    spacing: 20
    readonly property string theme: catalog.value("appearance.theme") || "dark"

    SettingsCard {
        width: parent.width
        title: "Theme"
        SettingsSegmentedRow {
            width: parent.width
            catalog: page.catalog
            settingKey: "appearance.theme"
            title: "Appearance"
            explanation: "Choose light, dark or your own hours."
            fallbackValue: "dark"
            controlWidth: 304
            options: [{value: "light", label: "Light"}, {value: "dark", label: "Dark"}, {value: "automatic", label: "Automatic"}]
        }
        Repeater {
            model: [{key: "appearance.light_time", title: "Light starts", fallback: "07:00", other: "appearance.dark_time", otherDefault: "19:00"},
                {key: "appearance.dark_time", title: "Dark starts", fallback: "19:00", other: "appearance.light_time", otherDefault: "07:00"}]
            delegate: SettingsRow {
                id: timeRow
                required property var modelData
                width: parent.width
                visible: page.theme === "automatic"
                catalog: page.catalog
                settingKey: modelData.key
                title: modelData.title
                explanation: "Use local time in 24-hour format (HH:MM)."
                fallbackValue: modelData.fallback
                control: Component {
                    TextField {
                        objectName: timeRow.settingKey + "-input"
                        text: timeRow.value
                        color: SettingsTheme.ink
                        selectionColor: SettingsTheme.accent
                        selectedTextColor: SettingsTheme.accentText
                        font.family: SettingsTheme.fontFamily
                        selectByMouse: true
                        Accessible.name: timeRow.title
                        validator: RegularExpressionValidator { regularExpression: /([01][0-9]|2[0-3]):[0-5][0-9]/ }
                        onEditingFinished: {
                            if (!acceptableInput) timeRow.errorText = "Enter a time such as 07:00.";
                            else if (text === (page.catalog.value(timeRow.modelData.other) || timeRow.modelData.otherDefault))
                                timeRow.errorText = "Choose two different times.";
                            else {
                                timeRow.errorText = "";
                                if (text !== timeRow.value) timeRow.requestValue(text);
                            }
                            text = Qt.binding(() => timeRow.value);
                        }
                        background: Rectangle {
                            radius: 8
                            color: SettingsTheme.field
                            border.color: parent.activeFocus ? SettingsTheme.accent : SettingsTheme.rim
                        }
                    }
                }
            }
        }
    }
    SettingsCard {
        width: parent.width
        title: "Accent colour"
        SettingsRow {
            id: accentRow
            width: parent.width
            catalog: page.catalog
            settingKey: "appearance.accent"
            title: "Accent colour"
            explanation: "Colour for icons, highlights and controls."
            fallbackValue: "orange"
            formatValue: value => ShellPalette.accents.find(choice => choice.id === value)?.name || value
            controlWidth: 304
            control: Component {
                Row {
                    spacing: 6
                    Repeater {
                        model: ShellPalette.accents
                        delegate: AbstractButton {
                            id: choice
                            required property var modelData
                            width: 32
                            height: 36
                            objectName: "accent-" + modelData.id
                            checkable: true
                            checked: accentRow.value === modelData.id
                            Accessible.name: modelData.name
                            Accessible.role: Accessible.RadioButton
                            Accessible.checked: checked
                            ToolTip.visible: hovered
                            ToolTip.text: modelData.name
                            onClicked: accentRow.requestValue(modelData.id)
                            background: Rectangle {
                                anchors.centerIn: parent
                                width: 30
                                height: 30
                                radius: 15
                                color: choice.modelData.base
                                border.width: choice.checked || choice.activeFocus ? 3 : 1
                                border.color: choice.checked || choice.activeFocus ? SettingsTheme.ink : SettingsTheme.rim
                                Rectangle {
                                    anchors.centerIn: parent
                                    width: 8
                                    height: 8
                                    radius: 4
                                    color: SettingsTheme.ink
                                    visible: choice.checked
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
