pragma ComponentBehavior: Bound
import QtQuick
import "SystemIcons.js" as SystemIcons

// The volume/brightness indicator on liquid glass: a Regular plate 250 × 54 under the bar at
// the right edge (the old mockup's .osd place), gone after 1.4 s, no pointer input. Its line
// is the panel's slider without the knob (system.html has no OSD of its own): the symbol,
// what changed, the track with the accent up to the value, the figure. Without a GPU a flat
// stand-in is drawn. Coordinates: its own; the glass maps them onto the screen at (x, y).
Item {
    id: osd
    property string kind: ""
    property int value: 0
    property bool muted: false
    property string label: ""
    property bool shown: false
    property double deadline: 0
    // What the glass refracts (Surfaces sets it; headless: flat stand-in).
    property DockBackdrop backdrop: null
    readonly property bool glassReady: backdrop !== null && backdrop.ready && GraphicsInfo.api !== GraphicsInfo.Software
    readonly property int radius: Metrics.panelRadius
    // For tests/glass-shots.py.
    readonly property alias glass: glass
    readonly property alias inkLayer: ink
    function show(page: string, percent: int, isMuted: bool, text: string): void {
        kind = page;
        value = Math.max(0, Math.min(100, percent));
        muted = isMuted;
        label = text;
        shown = true;
        deadline = Date.now() + 1400;
        hide.interval = 1400;
        hide.restart();
    }
    function dismiss(): void {
        hide.stop();
        shown = false;
    }
    function exportState(): var {
        const state = {
            shown: shown,
            kind: kind,
            value: value,
            muted: muted,
            label: label,
            deadline: deadline
        };
        dismiss();
        return state;
    }
    function importState(state: var): void {
        if (!state?.shown || state.deadline <= Date.now())
            return;
        kind = state.kind;
        value = state.value;
        muted = state.muted;
        label = state.label;
        deadline = state.deadline;
        shown = true;
        hide.interval = Math.max(1, deadline - Date.now());
        hide.restart();
    }
    Timer {
        id: hide
        interval: 1400
        onTriggered: osd.shown = false
    }
    visible: shown
    width: 250
    height: 54

    // Ink: always the light glass, whatever lies underneath (2026-09-27).
    readonly property color ink: LiquidPalette.inkOnLight
    readonly property color dim: LiquidPalette.dimOnLight
    readonly property color faint: LiquidPalette.faintOnLight
    readonly property color accent: LiquidPalette.accentOnLight

    Rectangle {
        visible: !osd.glassReady
        width: osd.width
        height: osd.height
        radius: osd.radius
        color: LiquidPalette.flatPlate
    }
    // ---- Under the glass ----
    Item {
        id: ink
        width: osd.width + 16
        height: osd.height + 16
        Item {
            x: 16
            y: 18
            width: 18
            height: 18
            SymbolIcon {
                id: symbol
                width: 18
                height: 18
                name: osd.kind === "sound" ? SystemIcons.sound(true, osd.value / 100, osd.muted) : "display-brightness-symbolic"
                ink: osd.ink
            }
            Icon {
                visible: !symbol.found
                width: 18
                height: 18
                kind: osd.kind === "sound" ? (osd.muted ? "muted" : "sound") : "light"
                ink: osd.ink
            }
        }
        Text {
            x: 46
            y: 9
            width: osd.width - 46 - 56
            height: 16
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
            text: osd.label
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 11
            font.weight: Font.Medium
            color: osd.dim
        }
        Rectangle {
            id: track
            x: 46
            y: 31
            width: osd.width - 46 - 56
            height: 8
            radius: 4
            color: osd.faint
        }
        Rectangle {
            x: track.x
            y: track.y
            width: Math.max(8, track.width * (osd.kind === "sound" && osd.muted ? 0 : osd.value) / 100)
            height: 8
            radius: 4
            color: osd.muted ? osd.faint : osd.accent
            Behavior on width {
                NumberAnimation {
                    duration: 120
                }
            }
        }
        Text {
            x: osd.width - 16 - 40
            width: 40
            height: osd.height
            horizontalAlignment: Text.AlignRight
            verticalAlignment: Text.AlignVCenter
            text: osd.kind === "sound" && osd.muted ? "—" : String(osd.value)
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 13
            font.weight: Font.DemiBold
            font.features: ({
                    tnum: 1
                })
            color: osd.ink
        }
    }
    ShaderEffectSource {
        id: inkTexture
        width: ink.width
        height: ink.height
        sourceItem: ink
        hideSource: osd.glassReady
        live: true
        visible: false
    }
    IslandGlass {
        id: glass
        visible: osd.glassReady
        backdrop: osd.backdrop
        plate: Qt.rect(0, 0, osd.width, osd.height)
        uRadius: osd.radius
        uIcons: inkTexture
        uSceneSize: Qt.point(ink.width, ink.height)
        uCover: osd.backdrop ? osd.backdrop.coverFor(Qt.point(osd.x, osd.y)) : Qt.vector4d(0, 0, 1, 1)
    }
}
