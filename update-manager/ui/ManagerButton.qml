// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as C

C.Button {
    id: button
    property bool primary: false
    property bool danger: false
    property bool quiet: false
    property int badge: 0
    padding: 9
    leftPadding: 15
    rightPadding: 15
    implicitHeight: 34
    font.family: "Noto Sans"
    font.pixelSize: 13
    font.weight: primary ? Font.DemiBold : Font.Medium
    focusPolicy: visible && enabled ? Qt.StrongFocus : Qt.NoFocus
    opacity: enabled ? 1 : .45
    Keys.onReturnPressed: if (enabled) clicked()
    Keys.onEnterPressed: if (enabled) clicked()
    contentItem: RowLayout {
        spacing: 7
        Text {
            Layout.fillWidth: true
            text: button.text
            font: button.font
            textFormat: Text.PlainText
            color: button.danger ? (button.primary ? "#fff6f8" : "#a01b45") : button.primary ? "#2a0f05" : button.quiet ? "#c2481f" : "#241018"
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        Rectangle {
            visible: button.badge > 0
            implicitWidth: Math.max(20, badgeText.implicitWidth + 10)
            implicitHeight: 18
            radius: 9
            color: button.primary ? "#fffaf6" : "#e2733f"
            Text { id: badgeText; anchors.centerIn: parent; text: button.badge; font.family: "Noto Sans"; font.pixelSize: 11; font.bold: true; color: button.primary ? "#c2481f" : "#2a0f05" }
        }
    }
    background: Rectangle {
        radius: 11
        color: button.primary ? (button.danger ? "#a01b45" : "#e2733f") : button.hovered || button.down ? Qt.rgba(36/255,16/255,24/255,.11) : button.quiet ? "transparent" : Qt.rgba(36/255,16/255,24/255,.065)
        border.width: button.activeFocus ? 2 : 1
        border.color: button.activeFocus ? "#c2481f" : button.primary || button.quiet ? "transparent" : Qt.rgba(36/255,16/255,24/255,.14)
    }
}
