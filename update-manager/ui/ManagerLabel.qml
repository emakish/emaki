// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import "file:@EMAKI_DATADIR@/shell" as Shell
import QtQuick.Layouts

Text {
    color: Shell.ShellPalette.managerInk
    font.family: "Noto Sans"
    font.pixelSize: 14
    textFormat: Text.PlainText
    wrapMode: Text.Wrap
    Layout.fillWidth: true
}
