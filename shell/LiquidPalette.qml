// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma Singleton
import QtQuick

QtObject {
    // Semantic aliases keep the glass and opaque controls on the same palette.
    // Warm light defaults retain their original floating-point alpha values;
    // dark glass uses brighter ink independently of wallpaper brightness.
    readonly property color text: ShellPalette.glassText
    readonly property color secondary: ShellPalette.glassSecondary
    readonly property color dim: ShellPalette.glassDim
    readonly property color accentText: ShellPalette.accentText
    readonly property color inkOnLight: ShellPalette.inkOnLight
    readonly property color inkOnDark: ShellPalette.inkOnDark
    readonly property color dimOnLight: ShellPalette.dimOnLight
    readonly property color dimOnDark: ShellPalette.dimOnDark
    readonly property color faintOnLight: ShellPalette.faintOnLight
    readonly property color faintOnDark: ShellPalette.faintOnDark
    readonly property color dangerOnLight: ShellPalette.dangerOnLight
    readonly property color dangerOnDark: ShellPalette.dangerOnDark
    readonly property color accentOnLight: ShellPalette.accentOnLight
    readonly property color accentOnDark: ShellPalette.accentOnDark
    readonly property color ink: ShellPalette.dark ? inkOnDark : inkOnLight
    readonly property color plateDim: ShellPalette.dark ? ShellPalette.settingsDim : dimOnLight
    readonly property color faint: ShellPalette.dark ? ShellPalette.settingsFaint : faintOnLight
    readonly property color danger: ShellPalette.dark ? dangerOnDark : dangerOnLight
    readonly property color accent: ShellPalette.dark ? accentOnDark : accentOnLight
    readonly property real selectionShade: ShellPalette.dark ? .32 : 1
    readonly property color selectedDrop: Qt.rgba(ShellPalette.accent.r * selectionShade, ShellPalette.accent.g * selectionShade, ShellPalette.accent.b * selectionShade, .9)
    // The Settings canvas (0.8.1 item 16): the body of Settings is the shell's Regular glass
    // under this cream (light) or brown (dark) tint; its top part is the plain glass. With
    // sharedCanvas every other surface's body takes the same material under its own top part
    // (CanvasTint). On since the owner accepted the pictures; off gives back the 0.8.0 surfaces.
    // Settings, Report a problem and Programs & Updates draw it either way.
    readonly property bool sharedCanvas: true
    readonly property color canvas: ShellPalette.settingsSidebar
    readonly property color flatPlate: ShellPalette.flatPlate
    readonly property color flatPanel: ShellPalette.flatPanel
    readonly property color flatDrop: ShellPalette.flatDrop
    readonly property color flatDropRim: ShellPalette.flatDropRim
    readonly property color dockControl: ShellPalette.dockControlFill
    readonly property color dockDot: ShellPalette.dockDot
    readonly property color dockLine: ShellPalette.dockLine
    readonly property color metricFaint: ShellPalette.metricsFaint
    readonly property color metricDim: ShellPalette.metricsDim
    readonly property color metricLine: ShellPalette.metricsLine
    readonly property color polkitCard: ShellPalette.polkitCard
    readonly property color polkitAccent: ShellPalette.polkitAccent
    readonly property color polkitDanger: ShellPalette.polkitDanger
    readonly property color polkitInk: ShellPalette.polkitInk
    readonly property color polkitField: ShellPalette.polkitField
    readonly property color polkitFocus: ShellPalette.dark ? ShellPalette.accentOnDark : ShellPalette.polkitFocus
    readonly property color polkitBorder: ShellPalette.polkitBorder
    readonly property color polkitDim: ShellPalette.polkitDim
    readonly property color polkitKey: ShellPalette.polkitKey
    readonly property color polkitKeyText: ShellPalette.polkitKeyText
    readonly property color polkitHazard: ShellPalette.polkitHazard
    readonly property color polkitAllow: ShellPalette.polkitAllow
    readonly property color polkitAllowText: ShellPalette.polkitAllowText
    readonly property color polkitPressed: ShellPalette.polkitPressed
    readonly property color polkitHover: ShellPalette.polkitHover
    readonly property vector3d trackAccent: Appearance.accent === "orange" ? ShellPalette.trackAccent : Qt.vector3d(ShellPalette.accent.r, ShellPalette.accent.g, ShellPalette.accent.b)
    readonly property vector3d trackRegularAccent: Appearance.accent === "orange" ? ShellPalette.trackRegularAccent : Qt.vector3d(ShellPalette.accent.r, ShellPalette.accent.g, ShellPalette.accent.b)
    // Keyboard focus on an accent control (FocusRing.strong): the hover rim's ink, dense
    // enough for 3:1 against the opaque accent. Pale controls keep flatDropRim.
    readonly property color focusOnAccent: Qt.rgba(36 / 255, 16 / 255, 24 / 255, .75)
    // The same over a selected drop (selectedDrop). Light draws that drop in the accent, so
    // it is focusOnAccent; dark shades it to selectionShade, where the dark ink is lost
    // (1.25:1) and the dark theme's own rim ink at the same density stands out.
    readonly property color focusOnSelected: ShellPalette.dark ? Qt.rgba(flatDropRim.r, flatDropRim.g, flatDropRim.b, focusOnAccent.a) : focusOnAccent
    // The outer row of a two-colour strong rim (WCAG C40), for an accent fill whose rim's
    // outer row lies on the plate (Keep this state): the dark ink vanishes on the dark plate,
    // so dark draws that row in its own rim ink. Light needs none: one ink faces both there.
    readonly property color focusOuterOnAccent: ShellPalette.dark ? focusOnSelected : Qt.rgba(0, 0, 0, 0)
}
