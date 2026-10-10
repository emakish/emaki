pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import "Liquid.js" as Liquid
import "Keyboard.js" as Keyboard

// The clock panel on liquid glass (docs/mockups/liquid-glass/clock.html): one Regular plate
// that grows out of the bar's clock island, 560 wide for the drawer (media, month,
// notifications), 440 for a peek (a notification arriving). Its resting drops are today,
// the chosen player (both orange: LiquidPalette.selectedDrop) and the Do not disturb knob;
// one hover drop (plain) flows to whatever is pressable (GlassTarget) and stays on the last
// one while the pointer crosses a gap. What the panel shows lies under the glass (uIcons);
// what a drop covers is drawn once more on the glass, as much as the drop covers it. Without
// a GPU the same layers are drawn flat.
// The item is the plate's rectangle on the screen (its parent sits at the screen origin):
// the overlay's input and blur regions and the status read it.
Item {
    id: panel
    readonly property bool keyboardBoundary: true
    property alias keyboardMode: body.keyboardMode
    KeyboardFocusKeeper {
        scope: panel
        enabled: panel.opened && body.keyboardMode
    }
    property bool keyboardFocusPending: false
    property bool keyboardNewest: false
    function takeFocus(): void {
        body.keyboardMode = true;
        keyboardNewest = false;
        keyboardFocusPending = true;
        applyKeyboardFocus();
    }
    function focusNewest(): void {
        body.keyboardMode = true;
        keyboardNewest = true;
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
            if (panel.keyboardNewest)
                body.focusNewest();
            else
                Keyboard.focusFirst(panel);
        });
    }
    onContentAlphaChanged: applyKeyboardFocus()
    Keys.onPressed: event => {
        if (event.key === Qt.Key_Delete) {
            body.dismissFocused((event.modifiers & Qt.ControlModifier) !== 0);
            event.accepted = true;
        } else
            Keyboard.handle(panel, event);
    }
    required property NotificationStore store
    property string serverState: "disabled"
    property bool mediaEnabled: false
    property real viewportWidth: 1536
    property real viewportHeight: 960
    // The scene's morph (0..1) and the head's own (the drawer's big time; 0 in a peek).
    property real expansion: 0
    property real head: 0
    property bool opened: false
    property var peekIds: []
    property bool hidePreviewBodies: false
    property date today: new Date()
    property string time: ""
    property string date: ""
    property string longDate: ""
    // What the glass refracts (Surfaces sets it; headless: flat stand-in).
    property DockBackdrop backdrop: null
    // The bar island: where the plate starts, its hover drop (carried while the morph is young).
    property real islandX: (viewportWidth - islandWidth) / 2
    property real islandWidth: 180
    property var islandBubble: null
    signal activated
    signal openHistory
    signal toggle
    readonly property alias body: body
    // For tests/glass-shots.py.
    readonly property alias panelGlass: panelGlass
    readonly property alias contentLayer: layer
    readonly property alias onGlassLayer: onGlass
    readonly property bool glassReady: backdrop !== null && backdrop.ready && GraphicsInfo.api !== GraphicsInfo.Software
    readonly property real dpr: Screen.devicePixelRatio || 1

    // ---- Geometry, screen coordinates ----
    readonly property real morph: Math.max(0, Math.min(1, expansion))
    // The drawer's content stays while the plate shrinks back (clock.js close()); a peek
    // replaces it at once.
    property bool drawerContent: false
    onOpenedChanged: {
        if (opened)
            drawerContent = true;
        else if (peekIds.length)
            drawerContent = false;
        if (!opened) {
            body.keyboardMode = false;
            keyboardFocusPending = false;
        }
        snapIfClosed();
        wake();
    }
    onPeekIdsChanged: {
        if (!opened && peekIds.length)
            drawerContent = false;
        snapIfClosed();
        wake();
    }
    onMorphChanged: {
        if (morph === 0 && !opened)
            drawerContent = false;
        wake();
    }
    // Opening from the island: the plate grows straight to its size, the springs only take
    // later changes (a peek turning into the drawer, rows coming and going).
    function snapIfClosed(): void {
        if (morph >= .001)
            return;
        widthSpring.x = targetWidth;
        widthSpring.v = 0;
        heightSpring.x = targetHeight;
        heightSpring.v = 0;
        ++tick;
    }
    readonly property real targetWidth: Math.min(drawerContent ? 560 : 440, viewportWidth - 20)
    readonly property real maxHeight: Math.max(Metrics.islandHeight, viewportHeight - 2 * Metrics.top)
    readonly property real targetHeight: Math.min(maxHeight, drawerContent ? body.drawerHeight : body.peekHeight)
    // The frame the content is laid out in: the grown plate, on a whole device pixel.
    readonly property real frameX: Math.round((viewportWidth - targetWidth) / 2 * dpr) / dpr
    readonly property var widthSpring: Liquid.spring(440)
    readonly property var heightSpring: Liquid.spring(Metrics.islandHeight)
    readonly property real shownWidth: {
        tick;
        return islandWidth + (widthSpring.x - islandWidth) * morph;
    }
    readonly property real shownHeight: {
        tick;
        return Metrics.islandHeight + (Math.max(Metrics.islandHeight, heightSpring.x) - Metrics.islandHeight) * morph;
    }
    readonly property real shownX: {
        tick;
        return islandX + ((viewportWidth - widthSpring.x) / 2 - islandX) * morph;
    }
    readonly property real shownRadius: Liquid.mix(Metrics.islandRadius, Metrics.panelRadius, morph)
    readonly property real radius: shownRadius
    // The content arrives on the second half of the morph; the island's line goes in the
    // first 40 % of the head's (clock.js islandAlpha), and stays in a peek.
    readonly property real contentAlpha: Liquid.smooth((morph - .55) / .45)
    readonly property real lineAlpha: 1 - Liquid.smooth(head * 2.5)
    x: shownX
    y: Metrics.top
    width: shownWidth
    height: shownHeight

    // ---- Motion (clock.js frame) ----
    readonly property var hoverBubble: Liquid.liquid()
    readonly property var todayBubble: Liquid.liquid()
    readonly property var playerBubble: Liquid.liquid()
    readonly property var knobBubble: Liquid.liquid()
    property GlassTarget hoverTarget: null
    property string hoverKey: ""
    property int tick: 0
    property bool animating: false
    function wake(): void {
        animating = true;
    }
    FrameAnimation {
        running: panel.animating
        onTriggered: panel.frame(Date.now(), frameTime)
    }
    onTargetWidthChanged: wake()
    onTargetHeightChanged: wake()
    onViewportWidthChanged: wake()
    // clock.js pointermove: the drop takes whatever pressable thing the pointer is on and
    // stays there across the gaps; a target inside another wins while the pointer is on it.
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
    // A rectangle of an item in screen coordinates (the layers sit at the screen origin).
    function screenRect(item: Item): rect {
        const p = item.mapToItem(layer, 0, 0);
        return Qt.rect(p.x, p.y, item.width, item.height);
    }
    function frame(now: real, elapsed: real): void {
        const dt = Math.min(elapsed > 0 ? elapsed : 1 / 60, .04);
        let active = false;
        widthSpring.target = targetWidth;
        heightSpring.target = targetHeight;
        // Closed: the next morph starts from the right size, not from the last one.
        snapIfClosed();
        active = Liquid.step(widthSpring, dt, 420, .8) || active;
        active = Liquid.step(heightSpring, dt, 420, .8) || active;
        // clock.js "shown": open (not closing) and past 60 % of the morph.
        const shown = morph > .6 && (opened || peekIds.length > 0);
        // The hover drop. A target gone (a dismissed row) takes its drop with it.
        if (hoverKey && !hoverTarget)
            hoverKey = "";
        const t = hoverTarget && hoverTarget.visible && hoverTarget.drop ? hoverTarget : null;
        if (t && shown) {
            const r = screenRect(t);
            active = Liquid.place(hoverBubble, Liquid.pad([r.x, r.y, r.width, r.height], t.bubblePad), t.bubbleRadius, now) || active;
        } else if (shown)
            Liquid.dissolve(hoverBubble, now);
        else
            active = Liquid.vanish(hoverBubble, now) || active;
        // Today, the chosen player, the DND knob: the drawer's resting drops.
        const today = drawerContent ? body.calendar.todayTarget : null;
        if (today && shown) {
            const r = screenRect(today);
            active = Liquid.place(todayBubble, [r.x, r.y, r.width, r.height], 11, now) || active;
        } else
            active = Liquid.vanish(todayBubble, now) || active;
        const player = drawerContent && body.hasMedia ? body.player.playerTarget : null;
        if (player && shown) {
            const r = screenRect(player);
            active = Liquid.place(playerBubble, Liquid.pad([r.x, r.y, r.width, r.height], 2), 11, now) || active;
        } else
            active = Liquid.vanish(playerBubble, now) || active;
        if (drawerContent && shown) {
            const k = body.knobRect;
            active = Liquid.place(knobBubble, [frameX + k.x, Metrics.top + k.y, k.width, k.height], 10, now) || active;
        } else
            active = Liquid.vanish(knobBubble, now) || active;
        for (const b of [hoverBubble, todayBubble, playerBubble, knobBubble])
            active = Liquid.advance(b, now, dt) || active;
        // dual(): what a drop goes to or rests on lies on the glass with the drop's alpha (kept
        // while the drop waits and melts there); what it is leaving, as much as it still covers
        // it (Liquid.cover). By the flag alone the chosen player's old name went back under the
        // glass the moment another was chosen, while its drop still covered it and flowed away,
        // and the drop's rim mirrored and split the name (27.09: players switch "very
        // strangely"); leaving the plate put the hovered target under the waiting, then melting,
        // drop the same way.
        follow("hover", t && shown ? t : null);
        follow("player", player && shown ? player : null);
        follow("today", today && shown ? today : null);
        const map = {}, list = [];
        const put = (item, v) => {
            if (!item)
                return 0;
            map[item.key] = Math.max(map[item.key] ?? 0, v);
            if (v > .001 && !list.includes(item))
                list.push(item);
            return v;
        };
        const covered = (item, b) => {
            const r = item ? screenRect(item) : Qt.rect(0, 0, 0, 0);
            return Liquid.cover(b, [r.x, r.y, r.width, r.height]);
        };
        for (const [role, b] of [["hover", hoverBubble], ["player", playerBubble], ["today", todayBubble]]) {
            put(panel[role + "On"], Liquid.smooth(b.alpha.x));
            const trail = panel[role + "Trail"];
            if (put(trail, covered(trail, b)) < .005)
                panel[role + "Trail"] = null;
            if (b.alpha.x < .001)
                panel[role + "On"] = null;
        }
        coverage = map;
        if (list.length !== copies.length || list.some((c, i) => c !== copies[i]))
            copies = list;
        // The bar advances the island's drop; keep drawing it while the morph is young.
        if (islandBubble && islandBubble.alpha.x > .001 && morph > 0 && morph < .6)
            active = true;
        ++tick;
        if (!active)
            animating = false;
    }
    readonly property var drops: {
        tick;
        const list = [];
        if (morph > .6) {
            for (const b of [todayBubble, playerBubble, knobBubble, hoverBubble]) {
                const d = Liquid.drop(b, 0, 0);
                if (!d)
                    continue;
                // Today and the chosen player: the orange drop; hover and the knob stay plain
                // (2026-09-27).
                d.selected = b === todayBubble || b === playerBubble;
                list.push(d);
            }
        } else if (islandBubble) {
            const d = Liquid.drop(islandBubble, islandX, Metrics.top);
            if (d)
                list.push(d);
        }
        return list;
    }
    // What each drop is on (`<role>On`, kept while it melts there) and what it is leaving
    // (`<role>Trail`, until it no longer covers it); set in frame().
    property GlassTarget hoverOn: null
    property GlassTarget hoverTrail: null
    property GlassTarget playerOn: null
    property GlassTarget playerTrail: null
    property GlassTarget todayOn: null
    property GlassTarget todayTrail: null
    function follow(role: string, target: GlassTarget): void {
        if (target && target !== panel[role + "On"]) {
            panel[role + "Trail"] = panel[role + "On"];
            panel[role + "On"] = target;
        }
    }
    // Key of a target → how much it lies on the glass (0..1), and the targets drawn on it.
    property var coverage: ({})
    property var copies: []
    function onGlassOf(key: string): real {
        return coverage[key] ?? 0;
    }
    readonly property real knobAlpha: {
        tick;
        return Liquid.smooth(knobBubble.alpha.x);
    }
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
        return {
            ready: glassReady,
            width: Math.round(shownWidth),
            height: Math.round(shownHeight),
            hover_key: hoverKey,
            hover: rect(hoverBubble),
            today: rect(todayBubble),
            player: rect(playerBubble),
            knob: rect(knobBubble),
            selected: drops.filter(d => d.selected).length
        };
    }

    // Ink: always the light glass, whatever lies underneath (2026-09-27).
    readonly property color ink: LiquidPalette.inkOnLight
    readonly property color dim: LiquidPalette.dimOnLight
    readonly property color faint: LiquidPalette.faintOnLight
    readonly property color accent: LiquidPalette.accentOnLight

    // ---- Input on the plate: presses stay in the panel; the head toggles it; leaving the
    // plate lets the hover drop melt ----
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
    }
    MouseArea {
        width: parent.width
        height: panel.drawerContent ? body.header : Metrics.islandHeight
        onClicked: panel.toggle()
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
            color: modelData.selected ? LiquidPalette.selectedDrop : LiquidPalette.flatDrop
            border.width: 1
            border.color: LiquidPalette.flatDropRim
        }
    }

    // ---- Under the glass. The layer starts at the screen origin, so its texture lies on
    // whole device pixels; the island's line sits at the island, the content in its frame.
    Item {
        id: layer
        x: -panel.shownX
        y: -Metrics.top
        width: Math.max(panel.frameX + panel.targetWidth, panel.islandX + panel.islandWidth) + 16
        height: Metrics.top + panel.maxHeight + 16
        ClockCompactRow {
            x: panel.islandX
            y: Metrics.top
            width: panel.islandWidth
            time: panel.time
            date: panel.date
            dnd: panel.store.effectiveDnd
            notificationCount: panel.store.count
            ink: panel.ink
            dim: panel.dim
            accent: panel.accent
            opacity: panel.lineAlpha * (1 - panel.islandAlpha)
            visible: opacity > .001
        }
        Item {
            // Flat stand-in only: the content ends where the plate does.
            x: panel.shownX
            y: Metrics.top
            width: panel.shownWidth
            height: panel.shownHeight
            clip: !panel.glassReady
            ClockBody {
                id: body
                x: panel.frameX - panel.shownX
                width: panel.targetWidth
                height: panel.targetHeight
                opacity: panel.contentAlpha
                visible: opacity > .001
                glass: panel
                store: panel.store
                opened: panel.drawerContent
                peekIds: panel.peekIds
                hidePreviewBodies: panel.hidePreviewBodies
                serverState: panel.serverState
                mediaEnabled: panel.mediaEnabled
                today: panel.today
                time: panel.time
                longDate: panel.longDate
                maxHeight: panel.maxHeight
                onActivated: panel.activated()
                onOpenHistory: panel.openHistory()
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

    // ---- The glass itself ----
    IslandGlass {
        id: panelGlass
        visible: panel.glassReady
        backdrop: panel.backdrop
        sceneOffset: Qt.point(panel.shownX, Metrics.top)
        plate: Qt.rect(panel.shownX, Metrics.top, panel.shownWidth, panel.shownHeight)
        drops: panel.drops
        uRadius: panel.shownRadius
        uIcons: contentTexture
        uSceneSize: Qt.point(layer.width, layer.height)
        uCover: panel.backdrop ? panel.backdrop.coverFor(Qt.point(0, 0)) : Qt.vector4d(0, 0, 1, 1)
    }

    // ---- On the glass: the island's line while the island's drop rides along, then the
    // targets under a drop (their own pixels, copied onto whole device pixels) and the knob.
    component GlassCopy: ShaderEffectSource {
        id: copy
        property Item target: null
        property real alpha: 0
        readonly property rect r: {
            panel.tick;
            return target ? panel.screenRect(target) : Qt.rect(0, 0, 0, 0);
        }
        readonly property real sx: Math.floor(r.x * panel.dpr) / panel.dpr
        readonly property real sy: Math.floor(r.y * panel.dpr) / panel.dpr
        readonly property real sw: Math.ceil((r.x + r.width) * panel.dpr) / panel.dpr - sx
        readonly property real sh: Math.ceil((r.y + r.height) * panel.dpr) / panel.dpr - sy
        sourceItem: target
        sourceRect: Qt.rect(sx - r.x, sy - r.y, sw, sh)
        x: sx
        y: sy
        width: sw
        height: sh
        textureSize: Qt.size(Math.max(1, Math.round(sw * panel.dpr)), Math.max(1, Math.round(sh * panel.dpr)))
        live: true
        hideSource: false
        opacity: alpha
        visible: target !== null && alpha > .001 && sw > 0 && sh > 0
    }
    Item {
        id: onGlass
        x: -panel.shownX
        y: -Metrics.top
        width: layer.width
        height: layer.height
        ClockCompactRow {
            x: panel.islandX
            y: Metrics.top
            width: panel.islandWidth
            time: panel.time
            date: panel.date
            dnd: panel.store.effectiveDnd
            notificationCount: panel.store.count
            ink: panel.ink
            dim: panel.dim
            accent: panel.accent
            opacity: panel.lineAlpha * panel.islandAlpha
            visible: opacity > .001
        }
        Item {
            width: parent.width
            height: parent.height
            opacity: panel.contentAlpha
            visible: opacity > .001
            // What the drops cover, each once, as much as they cover it.
            Repeater {
                model: panel.copies
                GlassCopy {
                    required property GlassTarget modelData
                    target: modelData
                    alpha: modelData ? panel.onGlassOf(modelData.key) : 0
                }
            }
            // clock.js 'knob': the dot rides the flowing drop, white on the accent, dim on
            // the plate.
            Rectangle {
                readonly property var b: {
                    panel.tick;
                    return [panel.knobBubble.x.x + panel.knobBubble.w.x / 2, panel.knobBubble.y.x + panel.knobBubble.h.x / 2];
                }
                visible: panel.drawerContent && panel.knobAlpha > .001
                opacity: panel.knobAlpha
                x: b[0] - 4.5
                y: b[1] - 4.5
                width: 9
                height: 9
                radius: 4.5
                color: panel.store.effectiveDnd ? LiquidPalette.text : panel.dim
            }
        }
    }
    FocusRing {
        anchors.fill: null
        readonly property Item focused: panel.Window.window?.activeFocusItem ?? null
        readonly property bool belongs: focused !== null && focused !== panel && Keyboard.boundary(focused) === panel
        readonly property point position: {
            panel.tick;
            body.scroller.contentY;
            return belongs ? focused.mapToItem(panel, 0, 0) : Qt.point(0, 0);
        }
        x: position.x
        y: position.y
        width: belongs ? focused.width : 0
        height: belongs ? focused.height : 0
        shown: panel.opened && panel.glassReady && belongs
        radius: 10
    }
}
