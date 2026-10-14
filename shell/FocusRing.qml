// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import "Keyboard.js" as Keyboard

// Keyboard focus in the hover look: the plain drop's fill and light rim. Only a control
// whose own fill is the accent (orange) sets `strong`: the light rim vanishes on orange, so
// there the same ink is drawn stronger. Never a dark outline on the pale plate.
Rectangle {
    id: ring
    readonly property var boundary: Keyboard.boundary(parent)
    readonly property bool keyboardMode: !boundary || boundary.keyboardMode === undefined || boundary.keyboardMode
    property bool shown: parent ? parent.activeFocus : false
    property bool strong: false
    anchors.fill: parent
    color: LiquidPalette.flatDrop
    border.color: strong ? LiquidPalette.focusOnAccent : LiquidPalette.flatDropRim
    border.width: strong ? 2 : 1
    radius: 8
    visible: shown && keyboardMode
    z: 1000
}
