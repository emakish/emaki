// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick

SettingsRow {
    id: root
    property var options: []
    formatValue: value => root.options.find(option => option.value === value)?.label || String(value)
    controlWidth: 230
    control: Component {
        Item {
            implicitHeight: 38
            Rectangle {
                antialiasing: true
                x: 0
                y: 0
                width: parent.width
                height: parent.height
                z: -1
                radius: 12
                color: SettingsTheme.drop
                border.color: SettingsTheme.rim
            }
            Repeater {
                model: root.options
                SettingsButton {
                    id: segment
                    required property var modelData
                    required property int index
                    width: (root.controlWidth - 8 - 2 * (root.options.length - 1)) / root.options.length
                    x: 4 + index * (width + 2)
                    y: 4
                    text: modelData.label
                    font.pixelSize: 13
                    padding: 6
                    implicitHeight: 30
                    contentItem: Text {
                        textFormat: Text.PlainText
                        text: segment.text
                        font: segment.font
                        color: root.value === segment.modelData.value ? SettingsTheme.accentText : SettingsTheme.dim
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                    }
                    Accessible.name: root.title + ": " + text
                    Accessible.role: Accessible.RadioButton
                    Accessible.checked: root.value === modelData.value
                    onClicked: root.requestValue(modelData.value)
                    background: Rectangle {
                        antialiasing: true
                        radius: 9
                        color: root.value === segment.modelData.value ? SettingsTheme.accent : "transparent"
                        border.width: parent.activeFocus ? 2 : 0
                        border.color: parent.activeFocus ? SettingsTheme.accentInk : SettingsTheme.rim
                    }
                }
            }
        }
    }
}
