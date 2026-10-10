// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls

SettingsRow {
    id: root
    formatValue: value => value ? "On" : "Off"
    controlWidth: 44
    control: Component {
        AbstractButton {
            id: toggle
            implicitWidth: 44
            implicitHeight: 26
            focusPolicy: Qt.StrongFocus
            Accessible.name: root.title
            Accessible.role: Accessible.CheckBox
            Accessible.checked: !!root.value
            onClicked: root.requestValue(!root.value)
            background: Rectangle {
                antialiasing: true
                radius: 13
                color: root.value ? SettingsTheme.accent : SettingsTheme.rim
                border.width: toggle.activeFocus ? 2 : 0
                border.color: SettingsTheme.accentInk
                Rectangle {
                    antialiasing: true
                    x: root.value ? 21 : 3
                    Behavior on x {
                        NumberAnimation {
                            duration: 180
                            easing.type: Easing.OutCubic
                        }
                    }
                    y: 3
                    width: 20
                    height: 20
                    radius: 10
                    color: "#fffaf6"
                }
            }
        }
    }
}
