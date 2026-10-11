// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls

ToolButton {
    id: expander
    checkable: true
    font.family: ShellPalette.uiFont
    font.pixelSize: 12
    background: Rectangle {
        radius: 6
        color: expander.down ? "#e7d5cb" : expander.hovered ? "#f1e5de" : "transparent"
        border.width: expander.activeFocus ? 1 : 0
        border.color: "#806c62"
    }
    contentItem: Row {
        spacing: 5
        Text {
            text: expander.text
            textFormat: Text.PlainText
            font: expander.font
            color: "#806c62"
        }
        Icon {
            anchors.verticalCenter: parent.verticalCenter
            kind: "tray"
            width: 12
            height: 12
            ink: "#806c62"
            rotation: expander.checked ? 0 : 180
        }
    }
}
