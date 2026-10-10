// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls

Button {
    id: root
    font.family: SettingsTheme.fontFamily
    font.pixelSize: 13
    font.weight: Font.Medium
    implicitHeight: 34
    padding: 15
    focusPolicy: Qt.StrongFocus
    Keys.onReturnPressed: event => {
        if (!event.isAutoRepeat)
            root.clicked();
        event.accepted = true;
    }
    Keys.onEnterPressed: event => {
        if (!event.isAutoRepeat)
            root.clicked();
        event.accepted = true;
    }
    contentItem: Text {
        textFormat: Text.PlainText
        text: root.text
        font: root.font
        color: SettingsTheme.ink
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
    background: Rectangle {
        antialiasing: true
        radius: 11
        color: root.down || root.hovered ? SettingsTheme.dropHover : SettingsTheme.drop
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? SettingsTheme.accent : SettingsTheme.rim
    }
    opacity: enabled ? 1 : .45
}
