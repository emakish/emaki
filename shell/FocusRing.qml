// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import "Keyboard.js" as Keyboard

Rectangle {
    id: ring
    readonly property var boundary: Keyboard.boundary(parent)
    readonly property bool keyboardMode: !boundary || boundary.keyboardMode === undefined || boundary.keyboardMode
    property bool shown: parent ? parent.activeFocus : false
    anchors.fill: parent
    color: "transparent"
    border.color: ShellPalette.text
    border.width: 2
    radius: 8
    visible: shown && keyboardMode
    z: 1000
    Rectangle {
        anchors.fill: parent
        anchors.margins: 1
        color: "transparent"
        border.color: ShellPalette.background
        border.width: 1
        radius: Math.max(0, ring.radius - 1)
    }
}
