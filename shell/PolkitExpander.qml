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
        color: expander.down ? LiquidPalette.polkitPressed : expander.hovered ? LiquidPalette.polkitHover : "transparent"
        border.width: expander.activeFocus ? 1 : 0
        border.color: LiquidPalette.polkitDim
    }
    contentItem: Row {
        spacing: 5
        Text {
            text: expander.text
            textFormat: Text.PlainText
            font: expander.font
            color: LiquidPalette.polkitDim
        }
        Icon {
            anchors.verticalCenter: parent.verticalCenter
            kind: "tray"
            width: 12
            height: 12
            ink: LiquidPalette.polkitDim
            rotation: expander.checked ? 0 : 180
        }
    }
}
