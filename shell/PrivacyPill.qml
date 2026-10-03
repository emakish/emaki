pragma ComponentBehavior: Bound
import QtQuick
import "Liquid.js" as Liquid

// The privacy indicator on liquid glass: a 36 px Regular island left of the system island
// while the microphone, camera or screen is in use — a pulsing dot and one symbol per kind in
// the alarm colour of the glass (PrivacyCompactRow). The pointer turns it into a Clear drop, as
// the clock island. It lives on the bar surface, so it stays when the bar hides. A click grows
// PrivacyPanel out of it; while the panel is on screen the panel draws the pill and this one
// steps back. Without a GPU a flat stand-in is drawn. Coordinates: its own; the glass maps
// them onto the screen at (x, y).
Item {
    id: pill
    required property HoverTip tip
    // The overview takes it away with the other non-workspace islands (same slide/fade).
    property real shift: 0
    property bool mic: false
    property bool cam: false
    property bool cast: false
    // What the glass refracts (Surfaces hands in the bar's backdrop; headless: flat stand-in).
    property DockBackdrop backdrop: null
    // The panel grew out of the pill and is on screen: it carries the pill (and this drop).
    property bool panelPresent: false
    signal clicked
    readonly property bool glassReady: backdrop !== null && backdrop.ready && GraphicsInfo.api !== GraphicsInfo.Software
    readonly property bool shown: !panelPresent
    readonly property int radius: Metrics.islandRadius
    readonly property var kinds: (mic ? ["mic"] : []).concat(cam ? ["cam"] : []).concat(cast ? ["cast"] : [])
    // The dot's pulse, shared with the panel's copy of the line.
    property real pulse: 1
    SequentialAnimation on pulse {
        running: pill.visible
        loops: Animation.Infinite
        NumberAnimation {
            to: .35
            duration: 800
        }
        NumberAnimation {
            to: 1
            duration: 800
        }
    }
    // For tests/glass-shots.py.
    readonly property alias glass: glass
    readonly property alias inkLayer: ink
    readonly property alias lineOnGlass: lineOnGlass
    width: line.naturalWidth
    height: Metrics.islandHeight
    opacity: 1 - shift

    // ---- The hover drop (clock.js: pad 3, radius 15); opening the panel sinks it ----
    readonly property var bubble: Liquid.liquid()
    property int tick: 0
    property bool animating: false
    property bool hovered: false
    FrameAnimation {
        running: pill.animating
        onTriggered: {
            const now = Date.now(), dt = Math.min(frameTime > 0 ? frameTime : 1 / 60, .04);
            let active = false;
            if (pill.panelPresent)
                active = Liquid.vanish(pill.bubble, now) || active;
            else if (pill.hovered && pill.visible)
                active = Liquid.place(pill.bubble, Liquid.pad([0, 0, pill.width, pill.height], 3), pill.radius + 3, now) || active;
            else
                Liquid.dissolve(pill.bubble, now);
            active = Liquid.advance(pill.bubble, now, dt) || active;
            ++pill.tick;
            if (!active)
                pill.animating = false;
        }
    }
    onHoveredChanged: animating = true
    onWidthChanged: animating = true
    onPanelPresentChanged: animating = true
    readonly property var drops: {
        tick;
        const d = Liquid.drop(bubble, 0, 0);
        return d ? [d] : [];
    }
    function glassStatus(): var {
        return {
            ready: glassReady,
            shown: shown,
            alpha: Math.round(Liquid.smooth(bubble.alpha.x) * 1000) / 1000,
            width: width
        };
    }

    // Ink: always the light glass, whatever lies underneath (2026-09-27).
    readonly property color alarm: LiquidPalette.dangerOnLight

    Rectangle {
        visible: !pill.glassReady && pill.shown
        width: pill.width
        height: pill.height
        radius: pill.radius
        color: LiquidPalette.flatPlate
    }
    Repeater {
        model: pill.glassReady || !pill.shown ? [] : pill.drops
        Rectangle {
            required property var modelData
            x: modelData.rect.x
            y: modelData.rect.y
            width: modelData.rect.z
            height: modelData.rect.w
            radius: modelData.params.x
            opacity: modelData.params.w
            color: LiquidPalette.flatDrop
            border.width: 1
            border.color: LiquidPalette.flatDropRim
        }
    }
    readonly property real hoverAlpha: {
        tick;
        return Liquid.smooth(bubble.alpha.x);
    }
    // ---- Under the glass (fading while the drop is up; drawn again on it) ----
    Item {
        id: ink
        width: pill.width + 16
        height: pill.height + 16
        PrivacyCompactRow {
            id: line
            kinds: pill.kinds
            pulse: pill.pulse
            alarm: pill.alarm
            opacity: 1 - pill.hoverAlpha
            visible: pill.shown
        }
    }
    ShaderEffectSource {
        id: inkTexture
        width: ink.width
        height: ink.height
        sourceItem: ink
        hideSource: pill.glassReady
        live: true
        visible: false
    }
    IslandGlass {
        id: glass
        visible: pill.glassReady && pill.shown
        backdrop: pill.backdrop
        plate: Qt.rect(0, 0, pill.width, pill.height)
        drops: pill.drops
        uIcons: inkTexture
        uSceneSize: Qt.point(ink.width, ink.height)
        uCover: pill.backdrop ? pill.backdrop.coverFor(Qt.point(pill.x, pill.y)) : Qt.vector4d(0, 0, 1, 1)
    }
    // On the glass while the pill is a drop.
    PrivacyCompactRow {
        id: lineOnGlass
        kinds: pill.kinds
        pulse: pill.pulse
        alarm: pill.alarm
        opacity: pill.hoverAlpha
        visible: pill.shown && opacity > .001
    }

    TipTarget {
        tip: pill.tip
        label: "Microphone, camera or screen in use"
        enabled: pill.shown
    }
    HoverHandler {
        onHoveredChanged: pill.hovered = hovered
    }
    MouseArea {
        anchors.fill: parent
        enabled: pill.shown
        cursorShape: Qt.PointingHandCursor
        onClicked: pill.clicked()
    }
    Accessible.role: Accessible.Button
    Accessible.name: "Microphone, camera or screen in use"
    Accessible.onPressAction: pill.clicked()
}
