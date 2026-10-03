pragma ComponentBehavior: Bound
import QtQuick
import "Liquid.js" as Liquid

// The system island of the bar on liquid glass (docs/mockups/liquid-glass/system.html): a
// 36 px Regular island at the bar's right edge with the cells of SystemCompactRow, 8 px in
// from its left end and 16 px from its right end (where the panel's head keeps them). The
// pointer turns the cell under it into a Clear drop (pad 3, radius 13) that flows from cell
// to cell and stays on the last one until the pointer leaves the island. It draws through the
// shared IslandGlass like the clock; the cells lie under the glass and each is drawn again on
// it as much as the drop covers it. Without a GPU a flat stand-in is drawn. While the panel is
// on screen (SystemPanel) the panel draws the cells; this one steps back.
// Coordinates: the bar's, which are screen coordinates offset by `screenOrigin`.
Item {
    id: island
    required property NiriService niri
    required property SystemService services
    property HoverTip tip: null
    property DockBackdrop backdrop: null
    property point screenOrigin: Qt.point(0, 0)
    // The panel grew out of the island and is on screen: it carries the cells (and this drop).
    property bool panelPresent: false
    // Overview slide (0..1).
    property real shift: 0
    signal clicked(string page)
    readonly property bool glassReady: backdrop !== null && backdrop.ready && GraphicsInfo.api !== GraphicsInfo.Software
    // The island's rectangle (input mask, blur region, status, the privacy pill's anchor).
    readonly property alias hit: hit
    readonly property alias row: cells
    // For tests/glass-shots.py.
    readonly property alias glass: glass
    readonly property alias inkLayer: ink
    readonly property alias onGlassLayer: onGlass
    readonly property var bubble: Liquid.liquid()
    readonly property real dpr: Screen.devicePixelRatio || 1
    readonly property int radius: Metrics.islandRadius
    // system.js: cells + 8 on the left + 16 on the right.
    readonly property real islandWidth: Math.ceil(cells.cellsWidth) + 24
    // Right edge at the bar's 10 px margin, on a whole device pixel of the screen.
    readonly property real islandX: Math.round((screenOrigin.x + width - Metrics.side - islandWidth) * dpr) / dpr - screenOrigin.x
    readonly property rect islandRect: Qt.rect(islandX, Metrics.top - 70 * shift, islandWidth, Metrics.islandHeight)
    readonly property real shownOpacity: 1 - shift
    readonly property bool shown: !panelPresent && shownOpacity > .001
    function glassStatus(): var {
        return {
            ready: glassReady,
            // island-local, like clock_glass.island
            alpha: Math.round(Liquid.smooth(bubble.alpha.x) * 1000) / 1000,
            x: Math.round(bubble.x.x),
            y: Math.round(bubble.y.x),
            width: Math.round(bubble.w.x),
            height: Math.round(bubble.h.x),
            hover_key: hoverKey,
            island_width: islandWidth,
            cells: cells.pages.length
        };
    }

    // ---- Motion (system.js frame): the hover drop ----
    property int tick: 0
    property bool animating: false
    property GlassTarget hoverTarget: null
    property string hoverKey: ""
    function wake(): void {
        animating = true;
    }
    // GlassTarget: the drop takes the cell the pointer is on and stays across the 2 px gaps.
    function hover(target: GlassTarget): void {
        hoverTarget = target;
        hoverKey = target.key;
        wake();
    }
    function leave(): void {
        hoverTarget = null;
        hoverKey = "";
        wake();
    }
    FrameAnimation {
        running: island.animating
        onTriggered: island.frame(Date.now(), frameTime)
    }
    function frame(now: real, elapsed: real): void {
        const dt = Math.min(elapsed > 0 ? elapsed : 1 / 60, .04);
        let active = false;
        if (hoverKey && !hoverTarget)
            hoverKey = "";
        // system.js: pad(tab.rect, 3), radius 13; opening the panel sinks it at once.
        if (panelPresent)
            active = Liquid.vanish(bubble, now) || active;
        else if (hoverTarget && hoverTarget.visible) {
            const p = hoverTarget.mapToItem(ink, 0, 0);
            active = Liquid.place(bubble, Liquid.pad([p.x - islandRect.x, p.y - islandRect.y, hoverTarget.width, hoverTarget.height], 3), 13, now) || active;
        } else
            Liquid.dissolve(bubble, now);
        active = Liquid.advance(bubble, now, dt) || active;
        // dual() for every cell: the hovered one lies on the glass with the drop's alpha (dual()
        // of system.js), every other one as much as the drop still covers it (Liquid.cover) —
        // not only the hovered one. The pointer runs ahead of the flowing drop: the cells the
        // drop still covered went back under the glass at once, and its 32 px lens mirrored and
        // split their icons (27.09: "flickers"); leaving the island put the last cell
        // under the glass for the whole wait and melt of its drop.
        const map = {};
        const alpha = panelPresent ? 0 : Liquid.smooth(bubble.alpha.x);
        for (let i = 0; i < cells.cells.count; ++i) {
            const c = cells.cells.itemAt(i) as GlassTarget;
            if (!c)
                continue;
            const p = c.mapToItem(ink, 0, 0);
            const v = c === hoverTarget ? alpha : Liquid.cover(bubble, [p.x - islandRect.x, p.y - islandRect.y, c.width, c.height]);
            if (v > .001)
                map[c.key] = v;
        }
        coverage = map;
        ++tick;
        if (!active)
            animating = false;
    }
    // Key of a cell → how much it lies on the glass (0..1); set in frame().
    property var coverage: ({})
    function onGlassOf(key: string): real {
        return coverage[key] ?? 0;
    }
    onPanelPresentChanged: wake()
    onIslandWidthChanged: wake()
    Component.onCompleted: wake()
    readonly property var drops: {
        tick;
        const d = Liquid.drop(bubble, islandRect.x, islandRect.y);
        return d ? [d] : [];
    }

    // Ink: always the light glass, whatever lies underneath (2026-09-27).
    readonly property color ink: LiquidPalette.inkOnLight

    // ---- Flat stand-in (no GPU): plate, drop, then the cells on top ----
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

    // ---- Under the glass: the cells (the hovered one fades while the drop is up) ----
    Item {
        id: ink
        width: island.islandRect.x + island.islandRect.width + 16
        height: Metrics.top + Metrics.islandHeight + 16
        // The island's rectangle, parent of the cells: its HoverHandler sees the pointer
        // together with them (a HoverHandler on a sibling above would take the hover away).
        Item {
            x: island.islandRect.x
            y: island.islandRect.y
            width: island.islandRect.width
            height: island.islandRect.height
            opacity: island.shownOpacity
            visible: island.shown
            HoverHandler {
                onHoveredChanged: {
                    if (!hovered)
                        island.leave();
                }
            }
            SystemCompactRow {
                id: cells
                x: 8
                y: 5
                glass: island
                tip: island.tip
                niri: island.niri
                services: island.services
                ink: island.ink
                onClicked: page => island.clicked(page)
            }
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
    // On the glass: each cell as much as the drop covers it (not bent by the drop over it).
    Item {
        id: onGlass
        width: ink.width
        height: ink.height
        visible: island.shown
        Repeater {
            model: cells.pages
            GlassCopy {
                required property string modelData
                target: {
                    island.tick;
                    return cells.cell(modelData);
                }
                space: ink
                tick: island.tick
                alpha: island.onGlassOf("cell-" + modelData) * island.shownOpacity
            }
        }
    }

    // ---- The island's rectangle for the surface's input mask and the status; no handlers
    // (the cells under the glass take clicks, scrolls, hover and tooltips themselves) ----
    Item {
        id: hit
        x: island.islandRect.x
        y: island.islandRect.y
        width: island.islandRect.width
        height: island.islandRect.height
        opacity: island.shownOpacity
        visible: island.shown
    }
}
