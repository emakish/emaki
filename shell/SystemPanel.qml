pragma ComponentBehavior: Bound
import QtQuick
import "Keyboard.js" as Keyboard
import "Liquid.js" as Liquid

// The system panel on liquid glass (docs/mockups/liquid-glass/system.html): one Regular
// plate that grows out of the bar's system island to 480 px, right-aligned, so the island's
// cells keep their pixels as the panel's head. The page's cell is the tab, a resting drop
// in orange (LiquidPalette.selectedDrop; every other drop is plain);
// the page's own resting drops are the knobs of its sliders and switches and its chosen row
// (SystemBody announces them with `restKey`); one hover drop flows to whatever is pressable
// (GlassTarget) and stays on the last one while the pointer crosses a gap. Up to eight drops
// share the plate (dock.frag). What the panel shows lies under the glass (uIcons); what a drop
// covers is drawn once more on the glass with the drop's alpha. Without a GPU the same layers
// are drawn flat.
// One piece (27.09: the open cell must look monolithic): once open, the plate's outline
// is its own rounded rectangle — no drop fuses into its edge or stands out of it — and the head
// never shows two drops for one tab: the island's hover drop on the clicked cell becomes the
// tab, and the tab flows to the next page's cell instead of melting and growing anew.
// The item is the plate's rectangle on the screen (its parent sits at the screen origin): the
// overlay's input and blur regions and the status read it.
Item {
    id: panel
    property bool keyboardBoundary: true
    property bool keyboardMode: false
    readonly property bool drawsKeyboardFocus: true
    readonly property alias focusRing: focusRing
    TapHandler {
        acceptedButtons: Qt.AllButtons
        onPressedChanged: if (pressed)
            panel.keyboardMode = false
    }
    KeyboardFocusKeeper {
        scope: panel
        enabled: panel.opened
    }
    Keys.onPressed: event => {
        panel.keyboardMode = true;
        Keyboard.handle(panel, event);
    }
    property bool keyboardFocusPending: false
    function takeFocus(keyboard): void {
        if (!opened)
            return;
        keyboardMode = keyboard !== false;
        keyboardFocusPending = true;
        applyKeyboardFocus();
    }
    function applyKeyboardFocus(): void {
        if (!keyboardFocusPending || !opened || contentAlpha <= .001)
            return;
        Qt.callLater(() => {
            if (!panel.opened || !panel.keyboardFocusPending || panel.contentAlpha <= .001)
                return;
            panel.keyboardFocusPending = false;
            Keyboard.focusFirst(Keyboard.targets(body.pages).length ? body.pages : panel);
        });
    }
    onContentAlphaChanged: applyKeyboardFocus()
    required property SystemService service
    required property NiriService niri
    property HoverTip tip: null
    property string page: ""
    property bool opened: false
    // The scene's morph (0..1).
    property real expansion: 0
    property real viewportWidth: 1536
    property real viewportHeight: 960
    // What the glass refracts (Surfaces sets it; headless: flat stand-in).
    property DockBackdrop backdrop: null
    // The bar island: where the plate starts (screen), its hover drop (carried while the
    // morph is young, island-local).
    property real islandX: viewportWidth - Metrics.side - islandWidth
    property real islandWidth: 280
    property var islandBubble: null
    // The island's hovered cell ("cell-sound"): drawn on the glass under the carried drop.
    property string islandHoverKey: ""
    signal pageRequested(string page)
    readonly property alias body: body
    readonly property alias head: head
    // For tests/glass-shots.py.
    readonly property alias panelGlass: panelGlass
    readonly property alias contentLayer: layer
    readonly property alias onGlassLayer: onGlass
    readonly property Item meterLayer: meterLayer
    // The layer at the screen origin (sliders read their knob drop against it).
    readonly property Item layerItem: layer
    readonly property bool glassReady: backdrop !== null && backdrop.ready && GraphicsInfo.api !== GraphicsInfo.Software
    readonly property real dpr: Screen.devicePixelRatio || 1
    // Geometry above keeps its original screen snapping. New offscreen caches must
    // follow this window's actual Qt render target, which can have another scale.
    readonly property real renderDpr: Window.window?.devicePixelRatio || dpr

    // ---- Geometry, screen coordinates (system.js panelRect()) ----
    readonly property real morph: Math.max(0, Math.min(1, expansion))
    readonly property real targetWidth: Math.min(480, viewportWidth - 20)
    // The grown plate's right edge is the bar's 10 px margin, on a whole device pixel.
    readonly property real frameX: Math.round((viewportWidth - Metrics.side - targetWidth) * dpr) / dpr
    readonly property real maxHeight: Math.max(Metrics.islandHeight, viewportHeight - 2 * Metrics.top)
    readonly property real targetHeight: Math.min(maxHeight, body.bodyHeight)
    readonly property var heightSpring: Liquid.spring(Metrics.islandHeight)
    // Opening from the island: the plate grows straight to its height; the spring only takes
    // later changes (a page switch, rows coming and going).
    function snapIfClosed(): void {
        if (morph >= .001)
            return;
        heightSpring.x = targetHeight;
        heightSpring.v = 0;
        ++tick;
    }
    onOpenedChanged: {
        if (!opened)
            keyboardFocusPending = false;
        snapIfClosed();
        wake();
    }
    onMorphChanged: wake()
    onPageChanged: wake()
    onTargetHeightChanged: wake()
    onViewportWidthChanged: wake()
    readonly property real shownWidth: islandWidth + (targetWidth - islandWidth) * morph
    readonly property real shownHeight: {
        tick;
        return Metrics.islandHeight + (Math.max(Metrics.islandHeight, heightSpring.x) - Metrics.islandHeight) * morph;
    }
    readonly property real shownX: islandX + (frameX - islandX) * morph
    readonly property real shownRadius: Liquid.mix(Metrics.islandRadius, Metrics.panelRadius, morph)
    readonly property real radius: shownRadius
    // The page arrives on the second half of the morph (system.js content); the head's cells
    // stay whole all the way (they are the island's).
    readonly property real contentAlpha: Liquid.smooth((morph - .55) / .45)
    x: shownX
    y: Metrics.top
    width: shownWidth
    height: shownHeight

    // ---- Motion (system.js frame) ----
    readonly property var hoverBubble: Liquid.liquid()
    // Resting drops by key (the tab, knobs, the chosen row); a key no longer asked for sinks
    // and is dropped once it is gone.
    property var restBubbles: ({})
    property var restItems: ({})
    // What rests on the glass, for the copies drawn on it (items, rebuilt when the set changes).
    property var restCopies: []
    property var restDots: []
    property GlassTarget hoverTarget: null
    property string hoverKey: ""
    // The morph is young (≤ 60 %): the plate carries the island's hover drop, not its own.
    // Set in frame(), like the drops, so the two never disagree for a frame (GOTCHAS).
    property bool carrying: true
    // What the hover drop is on (kept while it melts there) and what it is leaving (until it no
    // longer covers it), and how much every target lies on the glass (Liquid.cover by its drop;
    // the head's cells by the tab); set in frame().
    property GlassTarget hoverOn: null
    property GlassTarget hoverTrail: null
    property var coverage: ({})
    // GlassTarget: how much of the target lies on the glass. While the morph is young the
    // island's drop rides on the plate over its cell.
    function onGlassOf(key: string): real {
        return carrying && key === islandHoverKey ? islandAlpha : coverage[key] ?? 0;
    }
    property int tick: 0
    property bool animating: false
    function wake(): void {
        animating = true;
    }
    FrameAnimation {
        running: panel.animating
        onTriggered: panel.frame(Date.now(), frameTime)
    }
    // GlassTarget: the drop takes whatever pressable thing the pointer is on and stays there
    // across the gaps; a target inside another wins while the pointer is on it.
    function hover(target: GlassTarget): void {
        if (hoverTarget && hoverTarget !== target && hoverTarget.owner === target && hoverTarget.pointerInside)
            return;
        hoverTarget = target;
        hoverKey = target.key;
        wake();
    }
    function leave(): void {
        hoverTarget = null;
        hoverKey = "";
        wake();
    }
    function restBubble(key: string): var {
        tick;
        return restBubbles[key] ?? null;
    }
    // A rectangle of an item in screen coordinates (the layers sit at the screen origin).
    function screenRect(item: Item, r: rect): rect {
        const p = item.mapToItem(layer, r.x, r.y);
        return Qt.rect(p.x, p.y, r.width, r.height);
    }
    // Items announcing a resting drop (`restKey`, `restRect`, `restRadius`, `restKind`).
    function collect(item: var, list: var): void {
        if (!item || !item.visible)
            return;
        if (typeof item.restKey === "string" && item.restKey !== "")
            list.push(item);
        for (const child of item.children)
            collect(child, list);
    }
    // The body of a resting drop asked for the first time. A tab continues the drop the head
    // already shows, so one tab never appears as two drops: the tab of the page before flows
    // over to the new cell (system.js keeps one tab bubble), and on opening the island's hover
    // drop (on the clicked cell) carries on as the tab instead of melting and growing anew.
    // The island's key is not asked: the pointer has left the island by then, its drop has not.
    function newRest(key: string): var {
        if (!key.startsWith("cell-"))
            return Liquid.liquid();
        for (const other of Object.keys(restBubbles)) {
            if (!other.startsWith("cell-"))
                continue;
            const b = restBubbles[other];
            delete restBubbles[other];
            delete restItems[other];
            return b;
        }
        const b = Liquid.liquid();
        const island = islandBubble;
        if (island && island.alpha.x > .001) {
            const sides = [[b.x, island.x, islandX], [b.y, island.y, Metrics.top], [b.w, island.w, 0], [b.h, island.h, 0]];
            for (const [to, from, offset] of sides) {
                to.x = to.target = from.x + offset;
                to.v = from.v;
            }
            b.alpha.x = b.alpha.from = b.alpha.target = island.alpha.x;
            b.radius = island.radius;
            b.placed = true;
        }
        return b;
    }
    function frame(now: real, elapsed: real): void {
        const dt = Math.min(elapsed > 0 ? elapsed : 1 / 60, .04);
        let active = false;
        heightSpring.target = targetHeight;
        snapIfClosed();
        active = Liquid.step(heightSpring, dt, 420, .8) || active;
        // system.js "shown": open (not closing) and past 60 % of the morph.
        const shown = morph > .6 && opened;
        // The page's view (screen): drops of what scrolled away sink.
        const view = screenRect(body.scroller, Qt.rect(0, 0, body.scroller.width, body.scroller.height));
        const inView = r => r.y >= view.y - 8 && r.y + r.height <= view.y + view.height + 8;
        const wanted = {};
        if (shown) {
            const list = [];
            collect(head, list);
            const headCount = list.length;
            collect(body.pages, list);
            list.forEach((item, i) => {
                const r = screenRect(item, item.restRect);
                if (i >= headCount && !inView(r))
                    return;
                const key = item.restKey;
                let b = restBubbles[key];
                if (!b) {
                    b = newRest(key);
                    restBubbles[key] = b;
                }
                active = Liquid.place(b, [r.x, r.y, r.width, r.height], item.restRadius, now) || active;
                wanted[key] = true;
                restItems[key] = item;
            });
        }
        for (const key of Object.keys(restBubbles)) {
            if (wanted[key])
                continue;
            const b = restBubbles[key];
            active = Liquid.vanish(b, now) || active;
            if (b.alpha.x < .001 && b.alpha.target === 0) {
                delete restBubbles[key];
                delete restItems[key];
            }
        }
        // The hover drop. A target gone (a list rebuilt) takes its drop with it; what already
        // rests under a drop adds none (system.js: knobs, the tab, the chosen row).
        if (hoverKey && !hoverTarget)
            hoverKey = "";
        let t = hoverTarget && hoverTarget.visible && !wanted[hoverKey] ? hoverTarget : null;
        // The open head keeps one drop, the tab: cells stand 2 px apart and their drops reach
        // 3 px out, so a hover drop on the next cell lay over the tab. The cell still shows
        // the hand, its tip and takes the click (like a target with `drop: false`).
        const cell = hoverKey.startsWith("cell-");
        const dropped = t !== null && t.drop && shown && !cell;
        if (t) {
            const r = screenRect(t, Qt.rect(0, 0, t.width, t.height));
            if (!cell && !inView(r))
                t = null;
            else if (dropped)
                active = Liquid.place(hoverBubble, Liquid.pad([r.x, r.y, r.width, r.height], t.bubblePad), t.bubbleRadius, now) || active;
        }
        if (!(t && dropped)) {
            if (shown)
                Liquid.dissolve(hoverBubble, now);
            else
                active = Liquid.vanish(hoverBubble, now) || active;
        }
        active = Liquid.advance(hoverBubble, now, dt) || active;
        for (const key of Object.keys(restBubbles))
            active = Liquid.advance(restBubbles[key], now, dt) || active;
        // dual(), as in the ClockPanel: what a drop goes to or rests on lies on the glass with the
        // drop's alpha (the hover drop's target kept while it waits and melts there); what a drop
        // leaves or flows over, as much as it covers it (Liquid.cover). By the flag alone the
        // tab flowing to the next page's cell (newRest) left the cell it came from, and the cells
        // on its way, under the glass while it still covered them: its rim mirrored and split
        // their icons; off the plate the hovered target went under the melting drop.
        if (t && dropped && t !== hoverOn) {
            hoverTrail = hoverOn;
            hoverOn = t;
        }
        const map = {};
        const put = (item, v) => {
            if (item && item.key !== undefined)
                map[item.key] = Math.max(map[item.key] ?? 0, v);
            return v;
        };
        const covered = (item, b) => {
            // restItems keeps the page left behind until its drops are gone; its items may be
            // destroyed already (a reference to them is still truthy, its methods are not).
            if (!item || !b || typeof item.mapToItem !== "function")
                return 0;
            const r = screenRect(item, Qt.rect(0, 0, item.width, item.height));
            return Liquid.cover(b, [r.x, r.y, r.width, r.height]);
        };
        // By the resting key (a switch's knob is `<key>-knob`: the switch itself never fades).
        for (const key of Object.keys(restBubbles))
            map[key] = Liquid.smooth(restBubbles[key].alpha.x);
        // The head's cells as the island's: each as much as the tab covers it on its way.
        const tabKey = Object.keys(restBubbles).find(k => k.startsWith("cell-")) ?? "";
        for (let i = 0; tabKey && i < head.cells.count; ++i) {
            const c = head.cells.itemAt(i) as GlassTarget;
            put(c, covered(c, restBubbles[tabKey]));
        }
        put(hoverOn, Liquid.smooth(hoverBubble.alpha.x));
        if (put(hoverTrail, covered(hoverTrail, hoverBubble)) < .005)
            hoverTrail = null;
        if (hoverBubble.alpha.x < .001)
            hoverOn = null;
        coverage = map;
        // What rests on the glass: targets get a copy, switches a dot (kept while the drop sinks).
        const copies = [], dots = [];
        for (const key of Object.keys(restItems)) {
            const item = restItems[key];
            if (!item)
                continue;
            if (item.restKind === "target")
                copies.push({
                    key: key,
                    item: item
                });
            else if (item.restKind === "knob")
                dots.push({
                    key: key,
                    item: item
                });
        }
        const same = (a, b) => a.length === b.length && a.every((c, i) => c.key === b[i].key && c.item === b[i].item);
        if (!same(copies, restCopies))
            restCopies = copies;
        if (!same(dots, restDots))
            restDots = dots;
        // The bar advances the island's drop; keep drawing it while the morph is young.
        carrying = !(morph > .6);
        if (islandBubble && islandBubble.alpha.x > .001 && morph > 0 && carrying)
            active = true;
        ++tick;
        if (!active)
            animating = false;
    }
    // A drop as the plate draws it: inside the plate, 2 px in from its edge (where the tab and
    // the island's drop rest), however far its flow stretches it; null if nothing is left.
    function insidePlate(d: var): var {
        if (!d)
            return null;
        const r = d.rect, inset = 2;
        const x0 = Math.max(r.x, shownX + inset), y0 = Math.max(r.y, Metrics.top + inset);
        const x1 = Math.min(r.x + r.z, shownX + shownWidth - inset), y1 = Math.min(r.y + r.w, Metrics.top + shownHeight - inset);
        if (x1 - x0 < 1 || y1 - y0 < 1)
            return null;
        return Object.assign({}, d, {
            rect: Qt.vector4d(x0, y0, x1 - x0, y1 - y0),
            params: Qt.vector4d(Math.min(d.params.x, Math.min(x1 - x0, y1 - y0) / 2), d.params.y, d.params.z, d.params.w)
        });
    }
    // The tab first, then the hover drop, then the page's: eight at most.
    readonly property var drops: {
        tick;
        const list = [];
        if (!carrying) {
            const keys = Object.keys(restBubbles);
            const tabs = keys.filter(k => k.startsWith("cell-")).map(k => restBubbles[k]);
            const order = tabs.concat([hoverBubble]).concat(keys.filter(k => !k.startsWith("cell-")).map(k => restBubbles[k]));
            for (const b of order) {
                const d = insidePlate(Liquid.drop(b, 0, 0));
                if (!d || list.length >= 8)
                    continue;
                // The open page's tab: the orange drop (2026-09-27), coming in with the
                // second part of the morph — the island's plain drop carries on as the tab at
                // 60 % and must not turn orange in one frame. Knobs and the page's chosen rows
                // stay plain.
                d.selected = tabs.includes(b) ? tabTint : 0;
                list.push(d);
            }
        } else if (islandBubble) {
            const d = insidePlate(Liquid.drop(islandBubble, islandX, Metrics.top));
            if (d)
                list.push(d);
        }
        return list;
    }
    // Union of the drops with the plate: the island's meniscus (restUnion, the bar island's
    // value) while the island is still drawn under the growing plate, none once open — a drop
    // resting 2 px from the edge would otherwise lift the plate's outline over itself.
    // How orange the open page's tab is: none until the island's drop becomes the tab (60 %),
    // full when open.
    readonly property real tabTint: Liquid.smooth((morph - .6) / .4)
    readonly property real plateUnion: Liquid.BUBBLE.restUnion * (1 - Liquid.smooth((morph - .5) / .5))
    readonly property real islandAlpha: {
        tick;
        return islandBubble ? Liquid.smooth(islandBubble.alpha.x) : 0;
    }
    function glassStatus(): var {
        const rect = b => ({
                    alpha: Math.round(Liquid.smooth(b.alpha.x) * 1000) / 1000,
                    x: Math.round(b.x.x),
                    y: Math.round(b.y.x),
                    width: Math.round(b.w.x),
                    height: Math.round(b.h.x)
                });
        const keys = Object.keys(restBubbles).filter(k => restBubbles[k].alpha.target > 0);
        const tab = keys.find(k => k.startsWith("cell-"));
        // Kinds only: row keys carry network names and device paths, never in the status.
        return {
            ready: glassReady,
            width: Math.round(shownWidth),
            height: Math.round(shownHeight),
            hover_kind: hoverKey ? hoverKey.split("-")[0] : "",
            hover: rect(hoverBubble),
            tab: tab ? rect(restBubbles[tab]) : null,
            tab_page: tab ? tab.slice(5) : "",
            knobs: keys.filter(k => k.endsWith("-knob")).length,
            rows: keys.filter(k => !k.endsWith("-knob") && !k.startsWith("cell-")).length,
            drops: drops.length,
            selected: drops.filter(d => d.selected).length,
            scrollable: body.scroller.interactive
        };
    }

    // Ink: always the light glass, whatever lies underneath (2026-09-27).
    readonly property color ink: LiquidPalette.inkOnLight
    readonly property color dim: LiquidPalette.dimOnLight
    readonly property color faint: LiquidPalette.faintOnLight
    readonly property color accent: LiquidPalette.accentOnLight

    // ---- Input on the plate: presses stay in the panel; leaving the plate lets the hover
    // drop melt ----
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        onPressed: panel.keyboardMode = false
    }
    HoverHandler {
        onHoveredChanged: {
            if (!hovered)
                panel.leave();
        }
    }

    // ---- Flat stand-in (no GPU): the plate and the drops, under the content ----
    Rectangle {
        visible: !panel.glassReady
        width: panel.shownWidth
        height: panel.shownHeight
        radius: panel.shownRadius
        color: LiquidPalette.flatPlate
    }
    Repeater {
        model: panel.glassReady ? [] : panel.drops
        Rectangle {
            required property var modelData
            x: modelData.rect.x - panel.shownX
            y: modelData.rect.y - Metrics.top
            width: modelData.rect.z
            height: modelData.rect.w
            radius: modelData.params.x
            opacity: modelData.params.w
            color: modelData.selected > .5 ? LiquidPalette.selectedDrop : LiquidPalette.flatDrop
            border.width: 1
            border.color: LiquidPalette.flatDropRim
        }
    }

    // ---- Under the glass. The layer starts at the screen origin, so its texture lies on
    // whole device pixels; the head's cells sit at the island's, the page in its frame ----
    Item {
        id: layer
        x: -panel.shownX
        y: -Metrics.top
        width: panel.viewportWidth
        height: Metrics.top + panel.maxHeight + 16
        SystemCompactRow {
            id: head
            x: panel.islandX + 8
            y: Metrics.top + 5
            glass: panel
            tip: panel.tip
            niri: panel.niri
            services: panel.service
            activePage: panel.opened ? panel.page : ""
            ink: panel.ink
            onClicked: page => panel.pageRequested(page)
        }
        Item {
            // Flat stand-in only: the page ends where the plate does.
            x: panel.shownX
            y: Metrics.top
            width: panel.shownWidth
            height: panel.shownHeight
            clip: !panel.glassReady
            SystemBody {
                id: body
                x: panel.frameX - panel.shownX
                width: panel.targetWidth
                height: panel.targetHeight
                opacity: panel.contentAlpha
                visible: opacity > .001
                enabled: panel.opened
                glass: panel
                service: panel.service
                niri: panel.niri
                page: panel.page
                opened: panel.opened
            }
        }
    }
    ShaderEffectSource {
        id: contentTexture
        width: layer.width
        height: layer.height
        sourceItem: layer
        hideSource: panel.glassReady
        live: true
        visible: false
    }

    // Once the panel is still, only this hairline changes. Keep its original Qt
    // rasterization in a separate, padded texture. The static glass then retains
    // its rendered result; a small patch runs the very same optics for the meter.
    // During motion or near a clipped edge use the original single content layer.
    readonly property rect meterRect: {
        tick;
        body.scroller.contentY;
        const meter = body.micMeterItem;
        const p = body.micMeterOwner.mapToItem(layer, meter.x, meter.y);
        return Qt.rect(p.x, p.y, meter.width, meter.height);
    }
    // iconLayer() samples p and p ± (refraction + dispersion / 2) at most.
    readonly property real meterReach: Math.ceil(Liquid.BUBBLE.refraction + Liquid.BUBBLE.dispersion / 2) + 2
    readonly property rect meterPatch: Qt.rect(Math.floor((meterRect.x - meterReach) * renderDpr) / renderDpr, Math.floor((meterRect.y - meterReach) * renderDpr) / renderDpr, Math.ceil((meterRect.width + 2 * meterReach) * renderDpr) / renderDpr, Math.ceil((meterRect.height + 2 * meterReach) * renderDpr) / renderDpr)
    readonly property rect meterTextureRect: {
        const x = Math.floor((meterRect.x - 2) * renderDpr) / renderDpr;
        const y = Math.floor((meterRect.y - 2) * renderDpr) / renderDpr;
        return Qt.rect(x, y, Math.ceil((meterRect.x + meterRect.width + 2) * renderDpr) / renderDpr - x, Math.ceil((meterRect.y + meterRect.height + 2) * renderDpr) / renderDpr - y);
    }
    readonly property rect meterViewRect: {
        tick;
        const view = body.scroller;
        const p = view.mapToItem(layer, 0, 0);
        return Qt.rect(p.x, p.y, view.width, view.height);
    }
    // Reparenting bypasses the Flickable's clip. Keep the original content path
    // whenever the meter (including its sampling padding) touches that clip.
    readonly property bool meterInsideView: meterTextureRect.x >= meterViewRect.x && meterTextureRect.y >= meterViewRect.y && meterTextureRect.x + meterTextureRect.width <= meterViewRect.x + meterViewRect.width && meterTextureRect.y + meterTextureRect.height <= meterViewRect.y + meterViewRect.height
    SystemMeterPolicy {
        id: meterPolicy
        glassReady: panel.glassReady
        opened: panel.opened
        soundPage: panel.page === "sound"
        settled: panel.morph === 1 && !panel.animating
        meterVisible: body.micMeterOwner.visible && body.micMeterItem.visible
        insideView: panel.meterInsideView
        insidePlate: panel.meterPatch.x > panel.shownX + panel.shownRadius && panel.meterPatch.x + panel.meterPatch.width < panel.shownX + panel.shownWidth - panel.shownRadius && panel.meterPatch.y > Metrics.top + panel.shownRadius && panel.meterPatch.y + panel.meterPatch.height < Metrics.top + panel.shownHeight - panel.shownRadius
    }
    readonly property bool cacheMeterPanel: meterPolicy.cacheEnabled
    readonly property bool separateMeter: meterPolicy.separateMeter
    Item {
        id: meterLayer
    }
    ShaderEffectSource {
        id: meterTexture
        sourceItem: panel.separateMeter ? body.micMeterItem : null
        sourceRect: Qt.rect(panel.meterTextureRect.x - panel.meterRect.x, panel.meterTextureRect.y - panel.meterRect.y, panel.meterTextureRect.width, panel.meterTextureRect.height)
        textureSize: Qt.size(Math.round(panel.meterTextureRect.width * panel.renderDpr), Math.round(panel.meterTextureRect.height * panel.renderDpr))
        hideSource: true
        live: panel.separateMeter
        visible: false
    }

    // ---- The glass itself ----
    component PanelGlass: IslandGlass {
        visible: panel.glassReady
        backdrop: panel.backdrop
        sceneOffset: Qt.point(panel.shownX, Metrics.top)
        plate: Qt.rect(panel.shownX, Metrics.top, panel.shownWidth, panel.shownHeight)
        drops: panel.drops
        uBulge: Qt.vector4d(panel.plateUnion, 0, 1, 0)
        uRadius: panel.shownRadius
        uIcons: contentTexture
        uSceneSize: Qt.point(layer.width, layer.height)
        uCover: panel.backdrop ? panel.backdrop.coverFor(Qt.point(0, 0)) : Qt.vector4d(0, 0, 1, 1)
    }
    PanelGlass {
        id: panelGlass
        readonly property rect cacheRect: {
            const x = Math.floor(uOrigin.x * panel.renderDpr) / panel.renderDpr;
            const y = Math.floor(uOrigin.y * panel.renderDpr) / panel.renderDpr;
            return Qt.rect(x - uOrigin.x, y - uOrigin.y, Math.ceil((uOrigin.x + width) * panel.renderDpr) / panel.renderDpr - x, Math.ceil((uOrigin.y + height) * panel.renderDpr) / panel.renderDpr - y);
        }
        visible: panel.glassReady && (!panel.cacheMeterPanel || panel.separateMeter)
        layer.enabled: panel.cacheMeterPanel
        layer.live: panel.separateMeter
        layer.smooth: false
        layer.sourceRect: cacheRect
        layer.textureSize: Qt.size(Math.round(cacheRect.width * panel.renderDpr), Math.round(cacheRect.height * panel.renderDpr))
    }
    // Keep the settled cache and its texture alive during transient fallback, but
    // render the original direct shader while moving or clipping. Reusing the
    // cache's fractional-pixel sampling during a morph would change those pixels.
    PanelGlass {
        visible: panel.cacheMeterPanel && !panel.separateMeter
    }
    PanelGlass {
        visible: panel.separateMeter
        x: panel.meterPatch.x - panel.shownX
        y: panel.meterPatch.y - Metrics.top
        width: panel.meterPatch.width
        height: panel.meterPatch.height
        uOrigin: Qt.point(panel.meterPatch.x, panel.meterPatch.y)
        uHasDynamicIcons: 1
        uDynamicIcons: meterTexture
        uDynamicRect: Qt.vector4d(panel.meterTextureRect.x, panel.meterTextureRect.y, panel.meterTextureRect.width, panel.meterTextureRect.height)
    }

    // ---- On the glass: what the drops cover (their own pixels, on whole device pixels) and
    // the switches' dots ----
    Item {
        id: onGlass
        x: -panel.shownX
        y: -Metrics.top
        width: layer.width
        height: layer.height
        // The island's hovered cell under the island's drop, while the morph is young.
        GlassCopy {
            target: panel.carrying && panel.islandHoverKey ? panel.head.cell(panel.islandHoverKey.slice(5)) : null
            space: layer
            tick: panel.tick
            alpha: panel.islandAlpha
        }
        // What the hover drop is on and what it is leaving (a row, a word, a button), as much as
        // it covers them; a target resting under its own drop has its copy below.
        Repeater {
            model: 2
            GlassCopy {
                required property int index
                readonly property GlassTarget item: {
                    const t = index === 0 ? panel.hoverOn : panel.hoverTrail;
                    return t && t !== (index ? panel.hoverOn : null) && !panel.restCopies.some(c => c.item === t) ? t : null;
                }
                target: item
                space: layer
                tick: panel.tick
                alpha: item ? (panel.coverage[item.key] ?? 0) * (item.key.startsWith("cell-") ? 1 : panel.contentAlpha) : 0
            }
        }
        // The head's cells the flowing tab covers (the tab's own cell has its copy below).
        Repeater {
            model: panel.head.pages
            GlassCopy {
                required property string modelData
                readonly property GlassTarget item: {
                    panel.tick;
                    const c = panel.head.cell(modelData) as GlassTarget;
                    return c && !panel.carrying && !panel.restCopies.some(r => r.item === c) ? c : null;
                }
                target: item
                space: layer
                tick: panel.tick
                alpha: item ? panel.coverage[item.key] ?? 0 : 0
            }
        }
        // Targets under their resting drop: the tab, the chosen row.
        Repeater {
            model: panel.restCopies
            GlassCopy {
                required property var modelData
                target: modelData.item
                space: layer
                tick: panel.tick
                alpha: panel.onGlassOf(modelData.key) * (modelData.key.startsWith("cell-") ? 1 : panel.contentAlpha)
            }
        }
        // system.js 'knob': the dot rides the flowing drop, white on the accent, dim on the
        // plate.
        Repeater {
            model: panel.restDots
            Rectangle {
                id: dot
                required property var modelData
                readonly property var b: {
                    panel.tick;
                    const bubble = panel.restBubbles[dot.modelData.key];
                    return bubble ? [bubble.x.x + bubble.w.x / 2, bubble.y.x + bubble.h.x / 2, Liquid.smooth(bubble.alpha.x)] : [0, 0, 0];
                }
                visible: b[2] > .001
                opacity: b[2] * panel.contentAlpha
                x: b[0] - 4.5
                y: b[1] - 4.5
                width: 9
                height: 9
                radius: 4.5
                color: dot.modelData.item?.on ? LiquidPalette.text : panel.dim
            }
        }
    }
    // Focus stays crisp above the refracting content texture.
    FocusRing {
        id: focusRing
        anchors.fill: null
        readonly property Item focused: panel.Window.window?.activeFocusItem ?? null
        readonly property bool belongs: focused !== null && Keyboard.boundary(focused) === panel
        readonly property point position: {
            panel.tick;
            body.scroller.contentY;
            return belongs ? focused.mapToItem(panel, 0, 0) : Qt.point(0, 0);
        }
        x: position.x
        y: position.y
        width: belongs ? focused.width : 0
        height: belongs ? focused.height : 0
        shown: panel.opened && belongs
        radius: 10
        z: 100
    }
}
