pragma ComponentBehavior: Bound
import QtQuick
import "Liquid.js" as Liquid

// The clock island of the bar on liquid glass (docs/mockups/liquid-glass/clock.html): a
// 36 px Regular island of one width all year (measured on the widest short date), the line
// "09:41  Sun 27 Sep" with the date a step dimmer, an accent dot for notifications or the
// moon for Do not disturb. Hovering turns the whole island into a Clear drop. It draws
// through the shared IslandGlass like the left islands; the line lies under the glass and
// is drawn again on it while the drop is up. Without a GPU a flat stand-in is drawn.
// While the panel is on screen (ClockPanel) the panel draws the island; this one steps back.
// Coordinates: the bar's, which are screen coordinates offset by `screenOrigin`.
Item {
    id: island
    required property string time
    required property string date
    property bool dnd: false
    property int notificationCount: 0
    property HoverTip tip: null
    property DockBackdrop backdrop: null
    property point screenOrigin: Qt.point(0, 0)
    // The panel grew out of the island and is on screen: it carries the island (and this
    // drop) itself.
    property bool panelPresent: false
    // Overview slide (0..1) and how much of the island the launcher panel covers (0..1).
    property real shift: 0
    property real covered: 0
    signal clicked
    readonly property bool glassReady: backdrop !== null && backdrop.ready && GraphicsInfo.api !== GraphicsInfo.Software
    readonly property alias hit: hit
    readonly property alias dateLabel: line.dateLabel
    // For tests/glass-shots.py.
    readonly property alias glass: glass
    readonly property alias inkLayer: ink
    readonly property alias lineOnGlass: lineOnGlass
    readonly property var bubble: Liquid.liquid()
    readonly property real dpr: Screen.devicePixelRatio || 1
    readonly property int radius: Metrics.islandRadius
    readonly property real islandWidth: line.naturalWidth
    // Centred, on a whole device pixel of the screen so the line is not resampled.
    readonly property real islandX: Math.round((screenOrigin.x + (width - islandWidth) / 2) * dpr) / dpr - screenOrigin.x
    readonly property rect islandRect: Qt.rect(islandX, Metrics.top - 70 * shift, islandWidth, Metrics.islandHeight)
    readonly property real shownOpacity: (1 - covered) * (1 - shift)
    function glassStatus(): var {
        return {
            ready: glassReady,
            // island-local, like left_glass
            alpha: Math.round(Liquid.smooth(bubble.alpha.x) * 1000) / 1000,
            x: Math.round(bubble.x.x),
            y: Math.round(bubble.y.x),
            width: Math.round(bubble.w.x),
            height: Math.round(bubble.h.x),
            island_width: islandWidth
        };
    }

    // ---- Motion (clock.js frame): the island's hover drop ----
    property int tick: 0
    property bool animating: false
    property bool hovered: false
    function wake(): void {
        animating = true;
    }
    FrameAnimation {
        running: island.animating
        onTriggered: island.frame(Date.now(), frameTime)
    }
    function frame(now: real, elapsed: real): void {
        const dt = Math.min(elapsed > 0 ? elapsed : 1 / 60, .04);
        let active = false;
        // clock.js pointermove: pad(clockRect, 3), radius 15; openPanel() sinks it at once.
        if (panelPresent)
            active = Liquid.vanish(bubble, now) || active;
        else if (hovered)
            active = Liquid.place(bubble, Liquid.pad([0, 0, islandWidth, Metrics.islandHeight], 3), radius + 3, now) || active;
        else
            Liquid.dissolve(bubble, now);
        active = Liquid.advance(bubble, now, dt) || active;
        ++tick;
        if (!active)
            animating = false;
    }
    onHoveredChanged: wake()
    onPanelPresentChanged: wake()
    onIslandWidthChanged: wake()
    Component.onCompleted: wake()
    readonly property var drops: {
        tick;
        const d = Liquid.drop(bubble, islandRect.x, islandRect.y);
        return d ? [d] : [];
    }
    readonly property real bubbleAlpha: {
        tick;
        return Liquid.smooth(bubble.alpha.x);
    }

    // Ink: always the light glass, whatever lies underneath (2026-09-27).
    readonly property color ink: LiquidPalette.inkOnLight
    readonly property color dim: LiquidPalette.dimOnLight
    readonly property color accent: LiquidPalette.accentOnLight
    readonly property bool shown: !panelPresent && shownOpacity > .001

    // ---- Flat stand-in (no GPU): plate, drop, then the line on top ----
    Rectangle {
        x: island.islandRect.x
        y: island.islandRect.y
        width: island.islandRect.width
        height: island.islandRect.height
        radius: island.radius
        color: LiquidPalette.flatPlate
        opacity: island.shownOpacity
        visible: !island.glassReady && island.shown
    }
    Repeater {
        model: island.glassReady || !island.shown ? [] : island.drops
        Rectangle {
            required property var modelData
            x: modelData.rect.x
            y: modelData.rect.y
            width: modelData.rect.z
            height: modelData.rect.w
            radius: modelData.params.x
            opacity: modelData.params.w * island.shownOpacity
            color: LiquidPalette.flatDrop
            border.width: 1
            border.color: LiquidPalette.flatDropRim
        }
    }

    // ---- Under the glass: the line, fading while the drop is up ----
    Item {
        id: ink
        width: island.islandRect.x + island.islandRect.width + 16
        height: Metrics.top + Metrics.islandHeight + 16
        ClockCompactRow {
            id: line
            x: island.islandRect.x
            y: island.islandRect.y
            width: island.islandWidth
            time: island.time
            date: island.date
            dnd: island.dnd
            notificationCount: island.notificationCount
            ink: island.ink
            dim: island.dim
            accent: island.accent
            opacity: (1 - island.bubbleAlpha) * island.shownOpacity
            visible: island.shown
        }
    }
    ShaderEffectSource {
        id: inkTexture
        width: ink.width
        height: ink.height
        sourceItem: ink
        hideSource: island.glassReady
        live: true
        visible: false
    }
    IslandGlass {
        id: glass
        visible: island.glassReady && island.shown
        backdrop: island.backdrop
        plate: island.islandRect
        drops: island.drops
        uIcons: inkTexture
        uSceneSize: Qt.point(ink.width, ink.height)
        uCover: island.backdrop ? island.backdrop.coverFor(island.screenOrigin) : Qt.vector4d(0, 0, 1, 1)
        uOpacity: island.shownOpacity
    }
    // On the glass while the island is a drop (not bent by the drop's own edge).
    ClockCompactRow {
        id: lineOnGlass
        x: island.islandRect.x
        y: island.islandRect.y
        width: island.islandWidth
        time: island.time
        date: island.date
        dnd: island.dnd
        notificationCount: island.notificationCount
        ink: island.ink
        dim: island.dim
        accent: island.accent
        opacity: island.bubbleAlpha * island.shownOpacity
        visible: island.shown && opacity > .001
    }

    // ---- Input: the island itself (also the surface's input mask) ----
    Item {
        id: hit
        x: island.islandRect.x
        y: island.islandRect.y
        width: island.islandRect.width
        height: island.islandRect.height
        opacity: island.shownOpacity
        visible: island.shown
        TipTarget {
            tip: island.tip
            label: "Notifications and calendar"
            shortcut: "Super+N"
        }
        // Passive, like the tooltip's: a hovering MouseArea would take the hover from it.
        HoverHandler {
            onHoveredChanged: island.hovered = hovered
        }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: island.clicked()
        }
        Accessible.role: Accessible.Button
        Accessible.name: "Open notifications and calendar"
        Accessible.onPressAction: island.clicked()
    }
}
