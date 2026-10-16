// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "settings"

// A Settings button: the pale drop with its rim; `primary` fills it with the accent, the way
// Settings marks the chosen segment. Keyboard focus is the shell's FocusRing (hover look on the
// pale drop, the strong rim on the accent).
Rectangle {
    id: button
    required property string text
    property bool primary: false
    property bool selected: false
    property bool round: false
    property real labelSize: 14
    signal clicked
    implicitWidth: round ? implicitHeight : label.implicitWidth + 36
    implicitHeight: 40
    radius: round ? height / 2 : 12
    antialiasing: true
    color: primary || selected ? (pointer.containsMouse ? Qt.lighter(SettingsTheme.accent, 1.06) : SettingsTheme.accent) : pointer.containsMouse ? SettingsTheme.dropHover : SettingsTheme.drop
    border.width: 1
    border.color: primary || selected ? Qt.rgba(SettingsTheme.accent.r, SettingsTheme.accent.g, SettingsTheme.accent.b, 1) : SettingsTheme.rim
    opacity: enabled ? 1 : .45
    activeFocusOnTab: true
    readonly property alias focusRing: focusRing
    FocusRing {
        id: focusRing
        shown: button.activeFocus
        strong: button.primary || button.selected
        strongInk: LiquidPalette.focusOnAccent
        radius: button.radius
    }
    Accessible.role: Accessible.Button
    Accessible.name: text
    Accessible.onPressAction: if (enabled)
        clicked()
    Keys.onReturnPressed: event => {
        if (!event.isAutoRepeat)
            clicked();
    }
    Keys.onEnterPressed: event => {
        if (!event.isAutoRepeat)
            clicked();
    }
    Keys.onSpacePressed: clicked()
    Text {
        id: label
        anchors.centerIn: parent
        text: button.text
        textFormat: Text.PlainText
        color: button.primary || button.selected ? SettingsTheme.accentText : SettingsTheme.ink
        font.family: SettingsTheme.fontFamily
        font.pixelSize: button.labelSize
        font.weight: button.primary || button.selected ? Font.DemiBold : Font.Medium
    }
    MouseArea {
        id: pointer
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: {
            button.forceActiveFocus();
            button.clicked();
        }
    }
}
