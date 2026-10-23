// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "file:@EMAKI_DATADIR@/shell" as Shell
import QtQuick.Layouts
import QtQuick.Controls as C

C.Button {
    id: button
    property bool primary: false
    property bool danger: false
    property bool quiet: false
    property int badge: 0
    // Set where a row narrower than the button gives it less than its width.
    property bool squeezed: false
    padding: 9
    leftPadding: 15
    rightPadding: 15
    implicitHeight: 34
    font.family: "Noto Sans"
    font.pixelSize: 13
    font.weight: primary ? Font.DemiBold : Font.Medium
    focusPolicy: visible && enabled ? Qt.StrongFocus : Qt.NoFocus
    Accessible.name: text
    C.ToolTip.visible: label.truncated && (hovered || activeFocus)
    C.ToolTip.text: text
    opacity: enabled ? 1 : .45
    Keys.onReturnPressed: if (enabled) clicked()
    Keys.onEnterPressed: if (enabled) clicked()
    contentItem: RowLayout {
        spacing: 7
        Text {
            id: label
            objectName: "manager-button-label"
            Layout.fillWidth: true
            text: button.text
            font: button.font
            textFormat: Text.PlainText
            // A button squeezed by a narrow window shortens its label instead of spilling out.
            elide: button.squeezed ? Text.ElideRight : Text.ElideNone
            color: button.danger ? (button.primary ? Shell.ShellPalette.managerDangerBackground : Shell.ShellPalette.managerError) : button.primary ? Shell.ShellPalette.accentText : button.quiet ? Shell.ShellPalette.accentForeground : Shell.ShellPalette.managerInk
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
        Rectangle {
            visible: button.badge > 0
            implicitWidth: Math.max(20, badgeText.implicitWidth + 10)
            implicitHeight: 18
            radius: 9
            color: button.primary ? Shell.ShellPalette.managerBackground : Shell.ShellPalette.accent
            Text { id: badgeText; anchors.centerIn: parent; text: button.badge; font.family: "Noto Sans"; font.pixelSize: 11; font.bold: true; color: button.primary ? Shell.ShellPalette.accentForeground : Shell.ShellPalette.accentText }
        }
    }
    background: Rectangle {
        radius: 11
        color: button.primary ? (button.danger ? Shell.ShellPalette.managerError : Shell.ShellPalette.accent) : button.hovered || button.down ? Qt.alpha(Shell.ShellPalette.managerInk, .11) : button.quiet ? "transparent" : Qt.alpha(Shell.ShellPalette.managerInk, .065)
        border.width: button.activeFocus ? 2 : 1
        border.color: button.activeFocus ? Shell.ShellPalette.accentForeground : button.primary || button.quiet ? "transparent" : Qt.alpha(Shell.ShellPalette.managerInk, .14)
    }
}
