// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
Button {
    id: control
    property bool primary: false
    padding: 9
    leftPadding: 12
    rightPadding: 12
    font.family: "Noto Sans"
    font.pixelSize: 12
    implicitHeight: 34
    opacity: enabled ? 1 : .45
    contentItem: Text { text: control.text; textFormat: Text.PlainText; font: control.font; color: control.primary ? ShellPalette.accentText : LiquidPalette.ink; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
    background: Rectangle { radius: 10; color: control.primary ? ShellPalette.accent : Qt.alpha(LiquidPalette.ink, control.hovered ? .12 : .06); border.width: control.activeFocus ? 2 : 0; border.color: LiquidPalette.accent }
}
