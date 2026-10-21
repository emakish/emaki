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
    readonly property string theme: catalog.value("appearance.theme") || "light"
    // Hands an arrow to the nearest parent that navigates its page (SettingsView's keyboardNavigate),
    // as SettingsSliderRow does. Untyped on purpose: the parents are plain Items to the type checker.
    function navigatePage(scope, event): bool {
        for (; scope; scope = scope.parent) {
            if (typeof scope.keyboardNavigate === "function") {
                scope.keyboardNavigate(event);
                return true;
            }
        }
        return false;
    }

    SettingsCard {
        width: parent.width
        title: qsTranslate("SettingsAppearancePage", "Theme")
        SettingsSegmentedRow {
            width: parent.width
            catalog: page.catalog
            settingKey: "appearance.theme"
            title: qsTranslate("SettingsAppearancePage", "Appearance")
            explanation: qsTranslate("SettingsAppearancePage", "Choose light, dark or your own hours.")
            fallbackValue: "light"
            controlWidth: 304
            options: [
                {
                    value: "light",
                    label: qsTranslate("SettingsAppearancePage", "Light")
                },
                {
                    value: "dark",
                    label: qsTranslate("SettingsAppearancePage", "Dark")
                },
                {
                    value: "automatic",
                    label: qsTranslate("SettingsAppearancePage", "Automatic")
                }
            ]
        }
        Repeater {
            model: [
                {
                    key: "appearance.light_time",
                    title: qsTranslate("SettingsAppearancePage", "Light starts"),
                    fallback: "07:00",
                    other: "appearance.dark_time",
                    otherDefault: "19:00"
                },
                {
                    key: "appearance.dark_time",
                    title: qsTranslate("SettingsAppearancePage", "Dark starts"),
                    fallback: "19:00",
                    other: "appearance.light_time",
                    otherDefault: "07:00"
                }
            ]
            delegate: SettingsRow {
                id: timeRow
                required property var modelData
                width: parent.width
                visible: page.theme === "automatic"
                catalog: page.catalog
                settingKey: modelData.key
                title: modelData.title
                explanation: qsTranslate("SettingsAppearancePage", "Use local time in 24-hour format (HH:MM).")
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
                        validator: RegularExpressionValidator {
                            regularExpression: /([01][0-9]|2[0-3]):[0-5][0-9]/
                        }
                        onEditingFinished: {
                            if (!acceptableInput)
                                timeRow.errorText = qsTranslate("SettingsAppearancePage", "Enter a time such as 07:00.");
                            else if (text === (page.catalog.value(timeRow.modelData.other) || timeRow.modelData.otherDefault))
                                timeRow.errorText = qsTranslate("SettingsAppearancePage", "Choose two different times.");
                            else {
                                timeRow.errorText = "";
                                if (text !== timeRow.value)
                                    timeRow.requestValue(text);
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
        title: qsTranslate("SettingsAppearancePage", "Accent colour")
        SettingsRow {
            id: accentRow
            width: parent.width
            catalog: page.catalog
            settingKey: "appearance.accent"
            title: qsTranslate("SettingsAppearancePage", "Accent colour")
            explanation: qsTranslate("SettingsAppearancePage", "Colour for icons, highlights and controls.")
            fallbackValue: ShellPalette.defaultAccent
            formatValue: value => ShellPalette.accents.find(choice => choice.id === value)?.name || value
            controlWidth: 304
            control: Component {
                Row {
                    spacing: 6
                    Repeater {
                        id: accents
                        model: ShellPalette.accents
                        delegate: AbstractButton {
                            id: choice
                            required property var modelData
                            required property int index
                            readonly property bool selected: accentRow.value === modelData.id
                            function navigate(event): void {
                                const offset = event.key === Qt.Key_Left ? -1 : event.key === Qt.Key_Right ? 1 : 0;
                                const next = index + offset;
                                if (offset && next >= 0 && next < accents.count) {
                                    accents.itemAt(next).forceActiveFocus(Qt.TabFocusReason);
                                    event.accepted = true;
                                    return;
                                }
                                // Let the page choose a spatial neighbour beyond this row.
                                if (page.navigatePage(parent, event))
                                    return;
                                event.accepted = false;
                            }
                            width: 32
                            height: 36
                            objectName: "accent-" + modelData.id
                            checked: selected
                            Accessible.name: modelData.name
                            Accessible.role: Accessible.RadioButton
                            Accessible.checked: selected
                            ToolTip.visible: hovered
                            ToolTip.text: modelData.name
                            Keys.onPressed: event => {
                                if ([Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_Tab, Qt.Key_Backtab].includes(event.key))
                                    navigate(event);
                            }
                            Keys.onReturnPressed: clicked()
                            Keys.onEnterPressed: clicked()
                            onClicked: accentRow.requestValue(modelData.id)
                            background: Rectangle {
                                anchors.centerIn: parent
                                width: 30
                                height: 30
                                radius: 15
                                color: choice.modelData.base
                                border.width: choice.selected ? 3 : 1
                                border.color: choice.selected ? SettingsTheme.ink : SettingsTheme.rim
                                Rectangle {
                                    objectName: "accent-focus-ring"
                                    anchors.centerIn: parent
                                    width: 36
                                    height: 36
                                    radius: 18
                                    color: "transparent"
                                    border.width: 1
                                    border.color: SettingsTheme.ink
                                    visible: choice.activeFocus
                                }
                                Rectangle {
                                    objectName: "accent-selected-mark"
                                    anchors.centerIn: parent
                                    width: 8
                                    height: 8
                                    radius: 4
                                    color: SettingsTheme.ink
                                    visible: choice.selected
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
