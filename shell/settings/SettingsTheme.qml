// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma Singleton
import QtQuick
import ".." as Shell

QtObject {
    readonly property color ink: Shell.ShellPalette.settingsInk
    readonly property color dim: Shell.ShellPalette.settingsDim
    readonly property color faint: Shell.ShellPalette.settingsFaint
    readonly property color drop: Shell.ShellPalette.settingsDrop
    readonly property color dropHover: Shell.ShellPalette.settingsDropHover
    readonly property color rim: Shell.ShellPalette.settingsRim
    readonly property color field: Shell.ShellPalette.settingsField
    readonly property color card: Shell.ShellPalette.settingsCard
    readonly property color accent: Shell.ShellPalette.accent
    readonly property color accentInk: Shell.Appearance.dark ? Shell.ShellPalette.accentOnDark : Shell.ShellPalette.accentOnLight
    readonly property color accentText: Shell.ShellPalette.accentText
    readonly property color knob: Shell.ShellPalette.settingsKnob
    readonly property color edge: Qt.rgba(accent.r, accent.g, accent.b, .62)
    readonly property color error: Shell.ShellPalette.settingsError
    readonly property color displaySelected: Shell.ShellPalette.settingsDisplaySelected
    readonly property color displayIdle: Shell.ShellPalette.settingsDisplayIdle
    readonly property color tooltip: Shell.ShellPalette.settingsTooltip
    readonly property color sidebar: Shell.ShellPalette.settingsSidebar
    readonly property color strip: Shell.ShellPalette.settingsStrip
    readonly property color whiteRim: Shell.ShellPalette.settingsWhiteRim
    readonly property color iconRim: Shell.ShellPalette.settingsIconRim
    readonly property color popup: Shell.ShellPalette.settingsPopup
    readonly property int radius: 16
    readonly property string fontFamily: Shell.ShellPalette.uiFont
}
