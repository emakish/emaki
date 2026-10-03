pragma Singleton
import QtQuick

QtObject {
    readonly property color text: "#fbf3ea"
    readonly property color secondary: Qt.rgba(1, 244 / 255, 232 / 255, .72)
    readonly property color dim: Qt.rgba(1, 244 / 255, 232 / 255, .38)
    readonly property color accentText: "#2a0f05"
    // Ink on the Regular glass (docs/mockups/liquid-glass, regularPalette): dark ink on a
    // light plate, light ink on a dark one. Shared by the dock menu, the launcher and the
    // left bar islands.
    readonly property color inkOnLight: "#241018"
    readonly property color inkOnDark: "#f4e9f0"
    readonly property color dimOnLight: Qt.rgba(36 / 255, 16 / 255, 24 / 255, .55)
    readonly property color dimOnDark: Qt.rgba(244 / 255, 233 / 255, 240 / 255, .6)
    readonly property color faintOnLight: Qt.rgba(36 / 255, 16 / 255, 24 / 255, .3)
    readonly property color faintOnDark: Qt.rgba(244 / 255, 233 / 255, 240 / 255, .35)
    readonly property color dangerOnLight: "#a01b45"
    readonly property color dangerOnDark: "#ffb3c9"
    // Words in the accent on the glass (clock.js accent(): "Show all", "Clear", the unread
    // dot, a picked day, the DND track): the accent darkened on a light plate, lightened on a
    // dark one, so it keeps its contrast on both.
    readonly property color accentOnLight: "#c2481f"
    readonly property color accentOnDark: "#ff9b6a"
    // The selected / current drop — today in the calendar, the chosen player, the open page's
    // tab — is orange, hover stays the plain drop (2026-09-27: "orange, like the Arch
    // icon"; the colour moves to settings later). The orange is the launcher's Arch arrow
    // (ShellPalette.accent = tokens.toml accent). Alpha is the share of the drop's body it
    // takes on the glass (GlassShape uSelectedColor) and the flat stand-in's fill; the rim
    // light and the highlight stay on top, the dark ink stays readable on it.
    readonly property color selectedDrop: Qt.rgba(ShellPalette.accent.r, ShellPalette.accent.g, ShellPalette.accent.b, .9)
    // Without a GPU (software Qt, headless tests) and for the frame before a capture, the glass
    // is a flat stand-in: the plate and the drops on it. Light like the glass, which is always
    // the light one (2026-09-27), so the dark ink reads on both and an opening panel
    // does not flash dark. The dock uses the same plate; its menu and tooltip the denser one.
    readonly property color flatPlate: Qt.rgba(1, .973, .953, .70)
    readonly property color flatPanel: Qt.rgba(1, .973, .953, .86)
    readonly property color flatDrop: Qt.rgba(36 / 255, 16 / 255, 24 / 255, .07)
    readonly property color flatDropRim: Qt.rgba(36 / 255, 16 / 255, 24 / 255, .16)
}
