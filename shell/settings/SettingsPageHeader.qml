// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick

Column {
    property string title: ""
    property string explanation: ""
    spacing: 4
    Text {
        textFormat: Text.PlainText
        width: parent.width
        text: parent.title
        color: SettingsTheme.ink
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 30
        font.weight: Font.DemiBold
        wrapMode: Text.WordWrap
    }
    Text {
        textFormat: Text.PlainText
        width: parent.width
        text: parent.explanation
        color: SettingsTheme.dim
        font.family: SettingsTheme.fontFamily
        font.pixelSize: 14
        wrapMode: Text.WordWrap
    }
}
