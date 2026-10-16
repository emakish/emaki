// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import "Keyboard.js" as Keyboard

// Keyboard focus in the hover look: the plain drop's fill and light rim. Only a control
// whose own fill is the accent (orange) sets `strong`: the light rim vanishes on orange, so
// there a 2 px rim of `strongInk` is drawn. Never a dark outline on the pale plate.
Rectangle {
    id: ring
    readonly property var boundary: Keyboard.boundary(parent)
    readonly property bool keyboardMode: !boundary || boundary.keyboardMode === undefined || boundary.keyboardMode
    property bool shown: parent ? parent.activeFocus : false
    property bool strong: false
    // The strong rim's ink: focusOnAccent on a fill of the accent itself (a switch's track,
    // Keep this state); a control under a selected drop sets LiquidPalette.focusOnSelected.
    property color strongInk: LiquidPalette.focusOnAccent
    // A two-colour strong rim (WCAG C40): the outer of its 2 px in outerInk, facing the
    // plate, the inner in strongInk, facing the fill. Transparent (the default): one ink.
    property color outerInk: "transparent"
    anchors.fill: parent
    color: LiquidPalette.flatDrop
    border.color: strong ? strongInk : LiquidPalette.flatDropRim
    border.width: strong ? 2 : 1
    radius: 8
    visible: shown && keyboardMode
    z: 1000
    Rectangle {
        anchors.fill: parent
        visible: ring.strong && ring.outerInk.a > 0
        color: "transparent"
        border.color: ring.outerInk
        border.width: 1
        radius: ring.radius
    }
}
