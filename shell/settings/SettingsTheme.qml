// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma Singleton
import QtQuick
import ".." as Shell

QtObject {
    readonly property color ink: "#241018"
    readonly property color dim: Qt.rgba(36 / 255, 16 / 255, 24 / 255, .58)
    readonly property color faint: Qt.rgba(36 / 255, 16 / 255, 24 / 255, .32)
    readonly property color drop: Qt.rgba(36 / 255, 16 / 255, 24 / 255, .065)
    readonly property color dropHover: Qt.rgba(36 / 255, 16 / 255, 24 / 255, .11)
    readonly property color rim: Qt.rgba(36 / 255, 16 / 255, 24 / 255, .14)
    readonly property color field: Qt.rgba(1, 1, 1, .62)
    readonly property color card: Qt.rgba(1, 250 / 255, 246 / 255, .42)
    readonly property color accent: "#e2733f"
    readonly property color accentInk: "#c2481f"
    readonly property color accentText: "#2a0f05"
    readonly property color knob: "#fffaf6"
    readonly property color edge: Qt.rgba(226 / 255, 115 / 255, 63 / 255, .62)
    readonly property int radius: 16
    readonly property string fontFamily: Shell.ShellPalette.uiFont
}
