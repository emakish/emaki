pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// The dock on liquid glass (docs/mockups/liquid-glass/dock.html, accepted
// 2026-09-24): a Regular plate, one Clear bubble that flows between icons and carries the
// icon under its glass, a menu that grows out of the bubble on a wide neck.
// Motion is dock.js line for line (springs, tweens, bubble flow); data is the shell's own
// (pinned apps, running windows by identity, drag to reorder/pin/unpin, menus, launch).
// The item covers the whole dock surface; the plate sits at its bottom edge.
Item {
    id: dock
    property bool keyboardActive: false
    property string keyboardKey: ""
    property int keyboardRow: -1
    function takeFocus(): void {
        keyboardActive = true;
        if (!iconKeys.includes(keyboardKey))
            keyboardKey = iconKeys[0] ?? "";
        forceActiveFocus(Qt.TabFocusReason);
        if (keyboardKey)
            targetBubble(keyboardKey);
    }
    function leaveKeyboard(): void {
        closePopup();
        keyboardActive = false;
        focus = false;
        releaseBubble();
    }
    onKeyboardActiveChanged: {
        policy.keyboardActive = keyboardActive;
        if (!keyboardActive) {
            focus = false;
            releaseBubble();
        }
    }
    function moveKeyboard(step: int): void {
        if (popupOpen) {
            const choices = popup.rows.map((r, i) => r.kind === "line" ? -1 : i).filter(i => i >= 0);
            if (!choices.length)
                return;
            keyboardRow = choices[(choices.indexOf(keyboardRow) + step + choices.length) % choices.length];
            hoverMenuRow(keyboardRow);
        } else if (iconKeys.length) {
            keyboardKey = iconKeys[(iconKeys.indexOf(keyboardKey) + step + iconKeys.length) % iconKeys.length];
            targetBubble(keyboardKey);
        }
    }
    Keys.onPressed: event => {
        if (!keyboardActive)
            return;
        if (event.key === Qt.Key_Escape) {
            if (popupOpen)
                closePopup();
            else
                leaveKeyboard();
        } else if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab || event.key === Qt.Key_Left || event.key === Qt.Key_Right || event.key === Qt.Key_Up || event.key === Qt.Key_Down) {
            moveKeyboard(event.key === Qt.Key_Backtab || event.key === Qt.Key_Left || event.key === Qt.Key_Up || (event.modifiers & Qt.ShiftModifier) ? -1 : 1);
        } else if (event.key === Qt.Key_Menu || (event.key === Qt.Key_F10 && (event.modifiers & Qt.ShiftModifier))) {
            menu(keyboardKey);
        } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_Space) {
            if (popupOpen && keyboardRow >= 0) {
                const row = popup.rows[keyboardRow];
                popupAction(row.kind, row.id);
                if (["window", "new", "assign"].includes(row.kind))
                    leaveKeyboard();
            } else {
                activate(keyboardKey);
                if (!popupOpen)
                    leaveKeyboard();
            }
        }
        event.accepted = true;
    }
    Item {
        z: 20
        readonly property var ringRect: {
            dock.tick;
            return dock.popupOpen && dock.keyboardRow >= 0 && dock.keyboardRow < dock.popup.rows.length ? dock.menuRowRect(dock.keyboardRow) : dock.itemRect(dock.keyboardKey);
        }
        x: ringRect?.x ?? 0
        y: ringRect?.y ?? 0
        width: ringRect?.width ?? 0
        height: ringRect?.height ?? 0
        FocusRing {
            shown: dock.keyboardActive && parent.width > 0
            radius: 11
        }
    }
    property bool skipIntro: false
    required property DockStore store
    required property DockPolicy policy
    required property NiriService niri
    required property WindowLabels labels
    required property AppCatalog catalog
    required property AppIdentity identity
    // "Choose app…" for a window no desktop entry claims: the launcher picks one.
    signal assignRequested(string appId)
    // Surfaces.qml hands in a DockBackdrop on Wayland; headless it stays null and a flat
    // stand-in is drawn instead of glass.
    property Item backdrop: null
    // Wallpaper only: what the bubble bends past the plate (it does not see windows).
    // Same object as `backdrop` when there is no live capture.
    property Item wallBackdrop: backdrop
    readonly property Item wallSource: wallBackdrop !== null && wallBackdrop.ready ? wallBackdrop : backdrop
    // Where this item's top left is on the output, and the output's logical size.
    property point screenOrigin: Qt.point(0, 0)
    property size screenSize: Qt.size(width, height)
    readonly property string shaderDir: Quickshell.env("EMAKI_SHELL_SHADER_DIR") || Quickshell.shellPath("shaders")
    readonly property bool glassReady: backdrop !== null && backdrop.ready && GraphicsInfo.api !== GraphicsInfo.Software
    readonly property vector4d cover: backdrop ? backdrop.coverFor(screenOrigin) : Qt.vector4d(screenOrigin.x / Math.max(1, screenSize.width), screenOrigin.y / Math.max(1, screenSize.height), 1 / Math.max(1, screenSize.width), 1 / Math.max(1, screenSize.height))

    // Geometry of the plate and its slots (dock.js layout(); app.js renderDock).
    // At pitch 54 the 84 px bubble would reach 10 px into each neighbouring icon and bend
    // it. Neighbours make way instead (2026-09-25, "like the macOS dock"): icons on
    // each side of the bubble slide apart by `makeWay`, only where the bubble is and only
    // while it is there; the plate grows with them. At rest the row keeps pitch 54.
    // makeWay = bubble half width 42 − (pitch − 22, where the neighbour icon starts) + 1.
    readonly property real makeWay: bubbleOptions.size / 2 - (pitch - 22) + 1
    readonly property int cell: 50
    readonly property int pitch: 54 // 50 px item + 4 px gap
    readonly property int gap: pitch - cell
    // The line sits in the middle of the gap next to it: gap + 4 | 1 px | gap + 4.
    readonly property int separatorAdvance: gap + 9
    readonly property int padding: 8
    readonly property int plateHeight: 66
    // 2026-09-25: the bubble's bottom (75 px below the plate top) clears the
    // screen edge by 2 px: band = 75 + 2, the plate 11 px above the edge.
    readonly property int band: policy.thickness
    // Bubble recipe, dock.js BUBBLE (84 px; refraction 14).
    readonly property var bubbleOptions: ({
            size: 84,
            restUnion: 18,
            motionUnion: 30,
            stiffness: 620,
            hideDelay: 300,
            thickness: 9,
            edge: 18,
            dispersion: 16,
            refraction: 14
        })

    // ---- Data: which apps, in which order ----
    property string popupKey: ""
    property string popupMode: "" // "windows" | "menu"
    readonly property bool popupOpen: popupKey !== ""
    property string dragKey: ""
    property var dragKeys: []
    readonly property bool dragging: dragKey !== ""
    // Icon that pulses: the app gtk-launch is starting right now.
    readonly property string launchingKey: catalog.launchState === "pending" ? catalog.pendingId : ""
    readonly property var entries: buildEntries()
    // Delegates keep stableKeys as their model: a reorder during a drag must not recreate
    // the item under the pointer. Display order is `keys`.
    readonly property var stableKeys: layoutKeys(entries)
    readonly property var keys: dragging ? dragKeys : stableKeys
    readonly property var iconKeys: stableKeys.filter(k => k !== "sep")
    readonly property real length: layout(keys).width
    function buildEntries(): var {
        const byKey = {};
        const running = [];
        const workspaceIndex = {};
        const learned = identity.learned; // Read here so a learned match rebuilds the dock.
        for (const w of niri.workspaces)
            workspaceIndex[w.id] = w.idx;
        for (const w of niri.windows.slice().sort((a, b) => a.id - b.id)) {
            const meta = labels.values[w.id];
            const found = identity.lookup(meta?.appId ?? "");
            const entry = found.entry;
            const key = entry ? entry.id : meta?.appId ? "app:" + meta.appId : "";
            if (!key)
                continue; // Labels unavailable: identity unknown, never guess an app.
            if (!byKey[key]) {
                byKey[key] = {
                    key: key,
                    id: entry ? entry.id : "",
                    entry: entry,
                    appId: meta.appId,
                    source: found.source,
                    label: entry ? entry.name : meta.appId,
                    icon: entry?.icon ? Quickshell.iconPath(entry.icon, true) : "",
                    pinned: false,
                    windows: []
                };
                running.push(key);
            }
            byKey[key].windows.push({
                id: w.id,
                title: meta?.title || "Window #" + w.id,
                workspace: workspaceIndex[w.workspace_id] ?? 0
            });
        }
        const result = [];
        for (const id of store.pinned) {
            const entry = DesktopEntries.byId(id);
            const found = byKey[id];
            result.push({
                key: id,
                id: id,
                entry: entry,
                appId: found ? found.appId : "",
                source: found ? found.source : "",
                label: entry ? entry.name : id,
                icon: entry?.icon ? Quickshell.iconPath(entry.icon, true) : "",
                pinned: true,
                windows: found ? found.windows : []
            });
        }
        for (const key of running)
            if (!store.pinned.includes(key))
                result.push(byKey[key]);
        return result;
    }
    function layoutKeys(list: var): var {
        const pinned = list.filter(e => e.pinned).map(e => e.key);
        const extra = list.filter(e => !e.pinned).map(e => e.key);
        // Nothing pinned: no leading separator (the mockup never renders an empty pinned set).
        return extra.length && pinned.length ? pinned.concat(["sep"], extra) : pinned.concat(extra);
    }
    // dock.js layout(): slot x per key inside the plate, the separator, the plate width.
    function layout(order: var): var {
        let at = padding;
        const slots = {};
        let separator = -1;
        for (const key of order) {
            if (key === "sep") {
                separator = at + 4;
                at += separatorAdvance;
            } else {
                slots[key] = at;
                at += pitch;
            }
        }
        return {
            slots: slots,
            separator: separator,
            // The last slot already added its gap; the plate ends `padding` after the icon.
            width: order.length ? at + padding - gap : padding * 2
        };
    }
    function entryOf(key: string): var {
        return entries.find(e => e.key === key) ?? null;
    }

    // ---- Menus ----
    function closePopup(): void {
        if (!popupOpen)
            return;
        keyboardRow = -1;
        popupKey = "";
        popupMode = "";
        aim(popup.progress, 0, 260);
        // launcher.js vanish(): the hover drop goes with the menu, quickly.
        menuDissolve.stop();
        menuRow = -1;
        aim(menuDrop.alpha, 0, 110);
        scheduleHide();
        wake();
    }
    function openPopup(key: string, mode: string): void {
        popupKey = key;
        popupMode = mode;
        popup.owner = key;
        popup.model = buildPopup();
        popup.rows = popup.model?.rows ?? [];
        popup.height = popupHeightOf(popup.rows);
        if (keyboardActive) {
            keyboardRow = -1;
            moveKeyboard(1);
        }
        if (popup.progress.x < .001) {
            const v = visual(key);
            popup.anchor.x = v.center[0];
            popup.anchor.v = 0;
        }
        targetBubble(key);
        aim(popup.progress, 1, 280);
        wake();
    }
    function launch(item: var): void {
        if (item?.entry)
            catalog.launch(item.entry);
    }
    // Left click: none → launch, one → focus, several → window list (app.js dockClick).
    function activate(key: string): void {
        const item = entryOf(key);
        if (!item)
            return;
        if (popupKey === key && popupMode === "windows") {
            closePopup();
            return;
        }
        closePopup();
        if (item.windows.length === 0)
            launch(item);
        else if (item.windows.length === 1) {
            aim(visual(key).press, 1, 100);
            visual(key).releaseAt = Date.now() + 100;
            niri.focusWindow(item.windows[0].id);
        } else
            openPopup(key, "windows");
        wake();
    }
    function menu(key: string): void {
        if (!entryOf(key))
            return;
        if (popupKey === key && popupMode === "menu") {
            closePopup();
            return;
        }
        openPopup(key, "menu");
    }
    readonly property var popupModel: popupOpen ? popup.model : null
    function row(kind: string, label: string, mark: string, extra: var): var {
        return Object.assign({
            kind: kind,
            label: label,
            mark: mark,
            danger: false,
            note: "",
            tag: "",
            id: 0
        }, extra ?? {});
    }
    function buildPopup(): var {
        const item = entryOf(popupKey);
        if (!item)
            return null;
        const rows = [];
        if (popupMode === "windows") {
            for (const w of item.windows)
                rows.push(row("window", w.title, "▤", {
                    id: w.id,
                    tag: String(w.workspace)
                }));
            rows.push(row("line", "", ""));
            rows.push(row("new", "New window", "+"));
            return {
                header: item.label + " · " + item.windows.length + " windows",
                rows: rows
            };
        }
        if (item.entry)
            rows.push(row("new", "New window", "+"));
        if (item.pinned || item.entry)
            rows.push(item.pinned ? row("unpin", "Unpin from Dock", "−") : row("pin", "Pin to Dock", "+"));
        // No entry claims this window: let the person say which app it is. A learned or
        // guessed match can be corrected the same way.
        if (item.appId && !item.entry)
            rows.push(row("assign", "Choose app…", "?", {
                note: "Pick the app this window belongs to"
            }));
        else if (item.appId && (item.source === "learned" || item.source === "guess"))
            rows.push(row("assign", "Not this app…", "?", {
                note: "Pick the right app for this window"
            }));
        if (item.windows.length) {
            rows.push(row("line", "", ""));
            rows.push(row("closeall", "Close " + (item.windows.length === 1 ? "window" : "all " + item.windows.length + " windows"), "×", {
                danger: true
            }));
        }
        return {
            header: item.label,
            rows: rows
        };
    }
    function rowHeight(r: var): int {
        return r.kind === "line" ? 9 : r.note ? 48 : 36;
    }
    // dock.js: 8 px padding, 24 px title, 36 px rows, 9 px lines.
    function popupHeightOf(rows: var): int {
        return 16 + 24 + rows.reduce((n, r) => n + rowHeight(r), 0);
    }
    function popupAction(kind: string, value: double): void {
        const item = entryOf(popupKey);
        closePopup();
        if (!item)
            return;
        if (kind === "window")
            niri.focusWindow(value);
        else if (kind === "new")
            launch(item);
        else if (kind === "pin")
            store.pin(item.id);
        else if (kind === "unpin")
            store.unpin(item.id);
        else if (kind === "assign")
            assignRequested(item.appId);
        else if (kind === "closeall")
            for (const w of item.windows)
                niri.closeWindow(w.id);
    }
    onEntriesChanged: {
        if (keyboardActive && !iconKeys.includes(keyboardKey))
            keyboardKey = iconKeys[0] ?? "";
        // An open menu follows its app (a window closed, a title changed).
        if (popupOpen) {
            const model = buildPopup();
            if (!model) {
                closePopup();
                return;
            }
            popup.model = model;
            popup.rows = model.rows;
            popup.height = popupHeightOf(model.rows);
        }
        wake();
    }
    onKeysChanged: wake()
    onLaunchingKeyChanged: {
        if (launchingKey)
            visual(launchingKey).launchStart = Date.now();
        wake();
    }

    // ---- Motion (dock.js) ----
    function clamp(x: real, a: real, b: real): real {
        return Math.max(a, Math.min(b, x));
    }
    function mix(a: real, b: real, t: real): real {
        return a + (b - a) * t;
    }
    function smoothStep(t: real): real {
        t = clamp(t, 0, 1);
        return t * t * (3 - 2 * t);
    }
    function easeOut(t: real): real {
        return 1 - Math.pow(1 - t, 3);
    }
    function returnEase(t: real): real {
        if (t === 1)
            return 1;
        return 1 - Math.exp(-7 * t) * (Math.cos(10 * t) + .7 * Math.sin(10 * t));
    }
    function tween(x: real): var {
        return {
            x: x,
            from: x,
            target: x,
            start: 0,
            duration: 160
        };
    }
    function aim(t: var, target: real, duration: real): void {
        if (t.target === target)
            return;
        t.from = t.x;
        t.target = target;
        t.start = Date.now();
        t.duration = duration;
        wake();
    }
    function tickTween(t: var, now: real, easing: var): bool {
        const f = clamp((now - t.start) / t.duration, 0, 1);
        t.x = mix(t.from, t.target, easing(f));
        return f < 1 && t.from !== t.target;
    }
    function spring(x: real): var {
        return {
            x: x,
            v: 0,
            target: x
        };
    }
    // launcher.js step(): a side of the hover drop (k 620, ζ .66, the dock bubble's spring).
    function stepSide(s: var, dt: real): bool {
        const k = bubbleOptions.stiffness, c = 2 * .66 * Math.sqrt(k), n = Math.max(1, Math.ceil(dt / .004)), h = dt / n;
        for (let i = 0; i < n; i++) {
            s.v += (k * (s.target - s.x) - c * s.v) * h;
            s.x += s.v * h;
        }
        if (Math.abs(s.x - s.target) < .05 && Math.abs(s.v) < .8) {
            s.x = s.target;
            s.v = 0;
            return false;
        }
        return true;
    }
    // ---- Hover drop over the menu rows ----
    readonly property var menuDrop: ({
            x: ({
                    x: 0,
                    v: 0,
                    target: 0
                }),
            y: ({
                    x: 0,
                    v: 0,
                    target: 0
                }),
            w: ({
                    x: 0,
                    v: 0,
                    target: 0
                }),
            h: ({
                    x: 0,
                    v: 0,
                    target: 0
                }),
            alpha: ({
                    x: 0,
                    from: 0,
                    target: 0,
                    start: 0,
                    duration: 160
                })
        })
    property int menuRow: -1
    property real liveTop: -100000
    function menuRowTop(index: int): int {
        let at = 8 + 24;
        for (let i = 0; i < index; ++i)
            at += rowHeight(popup.rows[i]);
        return at;
    }
    function menuRowRect(index: int): rect {
        return Qt.rect(popupRect.x + 8, popupRect.y + menuRowTop(index), popupRect.width - 16, rowHeight(popup.rows[index]));
    }
    function hoverMenuRow(index: int): void {
        menuDissolve.stop();
        const d = menuDrop;
        if (d.alpha.x < .001) {
            // Born out of the app's bubble: start as the bubble, then fly to the row.
            const s = bubbleOptions.size;
            for (const [side, value] of [[d.x, bubble.x.x - s / 2], [d.y, bubble.y - s / 2], [d.w, s], [d.h, s]]) {
                side.x = value;
                side.v = 0;
            }
        }
        menuRow = index;
        aim(d.alpha, 1, 160);
        wake();
    }
    function leaveMenuRow(index: int): void {
        if (menuRow === index)
            menuDissolve.restart();
    }
    // launcher.js dissolve(): wait, then melt in place.
    Timer {
        id: menuDissolve
        interval: dock.bubbleOptions.hideDelay
        onTriggered: {
            dock.menuRow = -1;
            dock.aim(dock.menuDrop.alpha, 0, 220);
        }
    }
    function stepSpring(s: var, dt: real): bool {
        const n = Math.max(1, Math.ceil(dt / .006)), h = dt / n, w = 25;
        for (let i = 0; i < n; i++) {
            s.v += (w * w * (s.target - s.x) - 2 * .76 * w * s.v) * h;
            s.x += s.v * h;
        }
        if (Math.abs(s.x - s.target) < .02 && Math.abs(s.v) < .08) {
            s.x = s.target;
            s.v = 0;
            return false;
        }
        return true;
    }
    // Per-icon motion state, created on first use and kept across reorders.
    property var visuals: ({})
    function visual(key: string): var {
        let v = visuals[key];
        if (!v) {
            v = {
                press: tween(0),
                releaseAt: 0,
                returnX: spring(0),
                returnY: spring(0),
                returning: false,
                returnUntil: 0,
                detach: spring(0),
                center: [0, 0],
                scale: 1,
                hover: 0,
                launchStart: 0,
                alpha: 1
            };
            visuals[key] = v;
        }
        return v;
    }
    // Plain objects mutated in place; `tick` tells bindings that they moved.
    readonly property var visibility: ({
            x: 0,
            from: 0,
            target: 0,
            start: 0,
            duration: 320
        })
    readonly property var plateWidth: ({
            x: 16,
            v: 0,
            target: 16
        })
    readonly property var popup: ({
            owner: "",
            model: null,
            rows: [],
            height: 157,
            progress: ({
                    x: 0,
                    from: 0,
                    target: 0,
                    start: 0,
                    duration: 280
                }),
            anchor: ({
                    x: 0,
                    v: 0,
                    target: 0
                })
        })
    // One interruptible liquid body; x/v survive every retarget, including reversals.
    readonly property var bubble: ({
            owner: "",
            source: "",
            x: ({
                    x: 0,
                    v: 0,
                    target: 0
                }),
            y: 0,
            origin: 0,
            span: 0,
            alpha: ({
                    x: 0,
                    from: 0,
                    target: 0,
                    start: 0,
                    duration: 160
                }),
            parts: [],
            following: false,
            bridge: 0,
            balance: 1
        })
    property var drag: null // {key, center: [x, y], remove}
    property var press: null // {key, start: [x, y], center: [x, y], order}
    property string hovered: ""
    property bool pointerInside: false
    property real suppressClickUntil: 0
    Connections {
        target: dock.policy
        function onDockVisibleChanged(): void {
            if (dock.skipIntro)
                dock.snapStartup();
            else
                dock.aim(dock.visibility, dock.policy.dockVisible ? 1 : 0, 320);
            if (!dock.policy.dockVisible)
                dock.closePopup();
        }
    }
    function holdBubble(): bool {
        if (keyboardActive || drag || popup.progress.x > .001 || popup.progress.target === 1)
            return true;
        for (const key in visuals)
            if (visuals[key].returning)
                return true;
        return false;
    }
    function targetBubble(key: string): void {
        const slots = geometry.slots;
        if (!(key in slots))
            return;
        bubbleTimer.stop();
        const x = geometry.base.x + slots[key] + 25;
        const changed = bubble.owner !== key || bubble.x.target !== x;
        if (bubble.alpha.x < .001 && bubble.alpha.target === 0) {
            bubble.x.x = x;
            bubble.x.v = 0;
        }
        if (bubble.owner !== key) {
            bubble.source = bubble.owner;
            bubble.origin = bubble.x.x;
            bubble.span = Math.abs(x - bubble.origin);
            bubble.owner = key;
        }
        bubble.x.target = x;
        aim(bubble.alpha, 1, 160);
        if (changed)
            wake();
    }
    function releaseBubble(): void {
        if (holdBubble() || bubbleTimer.running)
            return;
        bubbleTimer.restart();
    }
    Timer {
        id: bubbleTimer
        interval: dock.bubbleOptions.hideDelay
        onTriggered: {
            if (!dock.holdBubble() && !dock.pointerInside && !dock.hovered)
                dock.aim(dock.bubble.alpha, 0, 220);
        }
    }
    function stepBubble(dt: real): bool {
        const s = bubble.x;
        const k = bubbleOptions.stiffness, c = 2 * .66 * Math.sqrt(k), n = Math.max(1, Math.ceil(dt / .004)), h = dt / n;
        for (let i = 0; i < n; i++) {
            s.v += (k * (s.target - s.x) - c * s.v) * h;
            s.x += s.v * h;
        }
        // Keep the landing within 4 px even after a long jump across the separator.
        const direction = Math.sign(s.target - bubble.origin);
        if (direction && direction * (s.x - s.target) > 4) {
            s.x = s.target + direction * 4;
            s.v = Math.min(Math.abs(s.v), 12) * direction;
        }
        if (Math.abs(s.x - s.target) < .08 && Math.abs(s.v) < 1) {
            s.x = s.target;
            s.v = 0;
            return false;
        }
        return true;
    }
    function setHover(key: string, on: bool): void {
        if (on) {
            hovered = key;
            if (!holdBubble())
                targetBubble(key);
        } else if (hovered === key) {
            hovered = "";
            releaseBubble();
        }
        wake();
    }
    function scheduleHide(): void {
        policy.scheduleHide();
    }

    // ---- Pointer (dock.js pointermove / icon buttons) ----
    function keyAt(x: real, y: real): string {
        for (const key of keys) {
            if (key === "sep")
                continue;
            const r = itemRect(key);
            if (r && x >= r.x && x < r.x + r.width && y >= r.y && y < r.y + r.height)
                return key;
        }
        return "";
    }
    function itemRect(key: string): var {
        // Slots from the current order, not the last frame's: right after a pin or reorder
        // the cached slots still hold the old places for one frame.
        const slots = layout(keys).slots;
        if (!(key in slots))
            return null;
        return {
            x: geometry.base.x + slots[key] + (geometry.push[key] ?? 0),
            y: geometry.plate.y + padding,
            width: cell,
            height: cell
        };
    }
    function pointerMoved(x: real, y: real): void {
        const p = geometry.plate;
        const inside = (x >= p.x - 6 && x <= p.x + p.width + 6 && y >= p.y - 12 && y <= p.y + p.height + 8);
        if (inside !== pointerInside) {
            pointerInside = inside;
            if (!inside) {
                if (hovered)
                    setHover(hovered, false);
                releaseBubble();
            }
        }
        const over = drag ? "" : keyAt(x, y);
        if (over !== hovered) {
            if (hovered)
                setHover(hovered, false);
            if (over)
                setHover(over, true);
        }
        if (inside && !holdBubble() && y >= p.y - 12) {
            let best = "", distance = 1e9;
            for (const key of keys) {
                if (key === "sep")
                    continue;
                const d = Math.abs(geometry.base.x + geometry.slots[key] + 25 - x);
                if (d < distance) {
                    distance = d;
                    best = key;
                }
            }
            if (best)
                targetBubble(best);
        }
    }
    function pointerLeft(): void {
        pointerInside = false;
        if (hovered)
            setHover(hovered, false);
        releaseBubble();
    }
    function pointerPressed(x: real, y: real): bool {
        const key = keyAt(x, y);
        if (!key)
            return false;
        const v = visual(key);
        press = {
            key: key,
            start: [x, y],
            center: [v.center[0], v.center[1]]
        };
        aim(v.press, 1, 100);
        return true;
    }
    function pointerDragged(x: real, y: real): void {
        if (!press)
            return;
        const dx = x - press.start[0], dy = y - press.start[1];
        const key = press.key;
        if (!drag && Math.hypot(dx, dy) > 5) {
            dragKeys = layoutKeys(entries);
            dragKey = key;
            drag = {
                key: key,
                center: [press.center[0], press.center[1]],
                remove: false
            };
            closePopup();
            aim(visual(key).press, 0, 320);
            visual(key).detach.target = 1;
            visual(key).returning = false;
            targetBubble(key);
        }
        if (!drag)
            return;
        drag.center = [clamp(press.center[0] + dx, 25, width - 25), clamp(press.center[1] + dy - 9, 28, height - 25)];
        const item = entryOf(key);
        // Lifting a pinned app off the dock unpins it; running apps just return.
        drag.remove = -dy > 70 && item !== null && item.pinned;
        if (!drag.remove) {
            const order = dragKeys.slice();
            const index = order.indexOf(key);
            const l = layout(order);
            const x0 = drag.center[0] - geometry.plate.x;
            for (let j = 0; j < order.length; j++) {
                const other = order[j] === "sep" ? l.separator + 2.5 : l.slots[order[j]] + 25;
                if (j !== index && ((j < index && x0 < other) || (j > index && x0 > other))) {
                    order.splice(index, 1);
                    order.splice(j, 0, key);
                    dragKeys = order;
                    break;
                }
            }
        }
        suppressClickUntil = Date.now() + 500;
        wake();
    }
    function pointerReleased(x: real, y: real, button: int): void {
        if (!press)
            return;
        const key = press.key;
        const wasDrag = !!drag;
        const v = visual(key);
        if (wasDrag) {
            const remove = drag.remove;
            const order = dragKeys;
            const center = drag.center;
            drag = null;
            dragKey = "";
            dragKeys = [];
            const item = entryOf(key);
            if (item && remove)
                store.unpin(item.id);
            else if (item) {
                const separator = order.indexOf("sep");
                const pinnedKeys = (separator < 0 ? order.filter(k => entryOf(k)?.pinned) : order.slice(0, separator)).filter(k => k !== "sep");
                // Unresolved apps have no desktop ID and cannot be pinned.
                store.reorder(pinnedKeys.map(k => entryOf(k)).filter(e => e && e.id).map(e => e.id));
                const target = geometry.base.x + layout(layoutKeys(entries)).slots[key] + 25;
                v.returnX.x = center[0] - target;
                v.returnY.x = center[1] - (geometry.plate.y + 33);
                v.returnX.v = v.returnY.v = 0;
                v.returnX.target = v.returnY.target = 0;
                v.returning = true;
                v.returnUntil = Date.now() + 380;
            }
            v.detach.target = 0;
            hovered = "";
        }
        press = null;
        releaseBubble();
        aim(v.press, 0, 340);
        if (!wasDrag && Date.now() >= suppressClickUntil) {
            if (button === Qt.RightButton)
                menu(key);
            else
                activate(key);
        }
        scheduleHide();
        wake();
    }
    function pointerCanceled(): void {
        if (!press)
            return;
        const v = visual(press.key);
        press = null;
        drag = null;
        dragKey = "";
        dragKeys = [];
        v.detach.x = v.detach.target = 0;
        v.returning = false;
        aim(v.press, 0, 320);
        releaseBubble();
        scheduleHide();
        wake();
    }

    // ---- Frame ----
    property int tick: 0
    property bool animating: false
    property real lastFrame: 0
    function wake(): void {
        animating = true;
    }
    function snapStartup(): void {
        const shown = policy.dockVisible ? 1 : 0;
        visibility.x = visibility.from = visibility.target = shown;
        plateWidth.x = plateWidth.target = layout(keys).width;
        plateWidth.v = 0;
        advance(Date.now(), 1 / 60);
    }
    onSkipIntroChanged: {
        if (skipIntro)
            snapStartup();
    }
    FrameAnimation {
        running: dock.animating
        onTriggered: dock.frame(Date.now(), frameTime)
    }
    Component.onCompleted: {
        const shown = policy.dockVisible ? 1 : 0;
        visibility.x = visibility.from = visibility.target = shown;
        plateWidth.x = plateWidth.target = layout(keys).width;
        advance(Date.now(), 1 / 60);
    }
    function frame(now: real, elapsed: real): void {
        const dt = Math.min(elapsed > 0 ? elapsed : 1 / 60, .04);
        let active = false;
        if (skipIntro)
            snapStartup();
        active = tickTween(visibility, now, smoothStep) || active;
        const wasPopup = popup.progress.x > 0;
        active = tickTween(popup.progress, now, smoothStep) || active;
        if (wasPopup && popup.progress.x === 0) {
            popup.owner = "";
            if (hovered)
                targetBubble(hovered);
            else
                releaseBubble();
        }
        active = tickTween(bubble.alpha, now, smoothStep) || active;
        if (bubble.alpha.x > .001 && !drag)
            active = stepBubble(dt) || active;
        plateWidth.target = layout(keys).width;
        active = stepSpring(plateWidth, dt) || active;
        for (const key in visuals) {
            const v = visuals[key];
            active = tickTween(v.press, now, v.press.target === 0 ? returnEase : easeOut) || active;
            if (v.releaseAt && now >= v.releaseAt) {
                v.releaseAt = 0;
                aim(v.press, 0, 340);
                active = true;
            } else if (v.releaseAt)
                active = true;
            active = stepSpring(v.detach, dt) || active;
            if (v.returning) {
                const x = stepSpring(v.returnX, dt), y = stepSpring(v.returnY, dt);
                v.returning = x || y;
                if (now >= v.returnUntil) {
                    v.returnX.x = v.returnY.x = v.returnX.v = v.returnY.v = 0;
                    v.returning = false;
                }
                active = v.returning || active;
            }
        }
        active = active || !!drag || launchingKey !== "";
        const settling = advance(now, dt);
        active = active || settling;
        if (!active)
            animating = false;
    }
    // Everything the glass, the icons and the menu read; recomputed each frame.
    property var geometry: ({
            base: Qt.rect(0, 0, 16, 66),
            plate: Qt.rect(0, 0, 16, 66),
            slots: ({}),
            separator: -1,
            separatorX: -1,
            push: ({})
        })
    property var parts: []
    property rect popupRect: Qt.rect(0, 0, 0, 0)
    property real popupProgress: 0
    property rect popupContent: Qt.rect(0, 0, 250, 0)
    property real popupContentOpacity: 0
    property rect tipRect: Qt.rect(0, 0, 0, 0)
    property real tipOpacity: 0
    property string tipLabel: ""
    property bool tipRemove: false
    readonly property real visibleAmount: {
        tick;
        return visibility.x;
    }
    function advance(now: real, dt: real): bool {
        let settling = false;
        const l = layout(keys);
        const pw = Math.max(16, plateWidth.x);
        // Hidden: slide the whole band plus 6 px off the edge (dock.js: 80 for its 74 px band).
        // `plate` is the row at rest; the drawn plate (geometry.plate) grows around the bubble.
        const plate = Qt.rect(width / 2 - pw / 2, height - band + (1 - visibility.x) * (band + 6), pw, plateHeight);
        geometry = {
            base: plate,
            plate: geometry.plate,
            slots: l.slots,
            separator: l.separator,
            separatorX: geometry.separatorX,
            push: geometry.push
        };
        let following = "";
        if (drag)
            following = drag.key;
        else
            for (const key in visuals)
                if (visuals[key].returning && key in l.slots) {
                    following = key;
                    break;
                }
        if (following) {
            targetBubble(following);
            const v = visual(following);
            const normal = [plate.x + l.slots[following] + 25, plate.y + 33];
            const center = drag ? [drag.center[0], drag.center[1]] : [normal[0] + v.returnX.x, normal[1] + v.returnY.x];
            bubble.x.x = bubble.x.target = center[0];
            bubble.x.v = 0;
            bubble.origin = center[0];
            bubble.span = 0;
            bubble.y = center[1];
            bubble.following = true;
        } else {
            if (bubble.following) {
                bubble.following = false;
                bubble.origin = bubble.x.x;
                releaseBubble();
            }
            if (popup.owner && (popup.progress.x > .001 || popup.progress.target))
                targetBubble(popup.owner);
            if (bubble.owner && bubble.owner in l.slots) {
                const target = plate.x + l.slots[bubble.owner] + 25;
                if (bubble.x.target !== target) {
                    bubble.x.target = target;
                    settling = true;
                }
            }
            bubble.y = plate.y + 33;
        }
        const amount = smoothStep(bubble.alpha.x), distance = bubble.x.target - bubble.origin;
        const progress = bubble.span > 1 ? clamp((bubble.x.x - bubble.origin) / (distance || 1), 0, 1) : 1;
        const speed = clamp(Math.abs(bubble.x.v) / 650, 0, 1);
        const stretch = Math.min(bubble.span, 74) * Math.sin(Math.PI * progress) * (.72 + .28 * speed);
        const direction = Math.sign(distance), k = mix(bubbleOptions.restUnion, bubbleOptions.motionUnion, speed);
        // Preserve deformation as well as velocity on a quick reversal; 22 ms low-pass.
        const flowTarget = direction * stretch, blend = following ? 1 : 1 - Math.exp(-dt / .022);
        bubble.bridge = mix(bubble.bridge, flowTarget, blend);
        bubble.balance = mix(bubble.balance, progress, blend);
        if (Math.abs(bubble.bridge - flowTarget) > .03 || Math.abs(bubble.balance - progress) > .001)
            settling = true;
        else {
            bubble.bridge = flowTarget;
            bubble.balance = progress;
        }
        const list = [];
        if (amount > .001) {
            const t = bubble.balance, S = bubbleOptions.size, halfWidths = S / 2 * (Math.pow(1 - t, .28) + Math.pow(t, .28));
            // Even a three-slot jump or a small union setting must leave a real neck.
            const flowUnion = Math.max(k, 2 * Math.max(0, Math.abs(bubble.bridge) - halfWidths) + 6);
            const add = (cx, weight, id) => {
                if (weight < .002)
                    return;
                const size = S * Math.pow(weight, .28);
                list.push({
                    id: id,
                    rect: Qt.vector4d(cx - size / 2, bubble.y - size / 2, size, size),
                    params: Qt.vector4d(size * .34, flowUnion, 1, amount)
                });
            };
            if (Math.abs(bubble.bridge) > .15) {
                add(bubble.x.x - bubble.bridge * bubble.balance, 1 - bubble.balance, bubble.source);
                add(bubble.x.x + bubble.bridge * (1 - bubble.balance), bubble.balance, bubble.owner);
            } else
                add(bubble.x.x, 1, bubble.owner);
        }
        parts = list;
        // Make way: everything left of the bubble slides left, everything right slides
        // right, by up to makeWay; the icon under the bubble stays. Scaled by how far the
        // bubble has appeared, so the row closes again as it fades.
        const push = {};
        let pushLeft = 0, pushRight = 0;
        const shift = x => makeWay * amount * clamp((x - bubble.x.x) / pitch, -1, 1);
        for (const key of keys) {
            const x = key === "sep" ? plate.x + l.separator + .5 : plate.x + l.slots[key] + 25;
            push[key] = amount > .001 ? shift(x) : 0;
            pushLeft = Math.min(pushLeft, push[key]);
            pushRight = Math.max(pushRight, push[key]);
        }
        geometry = {
            base: plate,
            plate: Qt.rect(plate.x + pushLeft, plate.y, plate.width - pushLeft + pushRight, plate.height),
            slots: l.slots,
            separator: l.separator,
            separatorX: l.separator >= 0 ? plate.x + l.separator + (push.sep ?? 0) : -1,
            push: push
        };
        for (const key of keys) {
            if (key === "sep")
                continue;
            const v = visual(key);
            const slotX = plate.x + l.slots[key] + 25 + push[key];
            const arrival = 1 - smoothStep((Math.hypot(bubble.x.x - slotX, bubble.y - (plate.y + 33)) - 4) / 22);
            const h = arrival * amount;
            v.hover = h;
            let center = [slotX, plate.y + 33];
            if (drag && drag.key === key)
                center = [drag.center[0], drag.center[1]];
            else if (v.returning)
                center = [slotX + v.returnX.x, plate.y + 33 + v.returnY.x];
            v.center = center;
            v.scale = (1 + .06 * (drag && drag.key === key ? 1 : h)) * (1 - .08 * v.press.x);
            v.alpha = launchingKey === key ? .675 + .325 * Math.cos((now - v.launchStart) * Math.PI / 420) : 1;
        }
        if (popup.owner && popup.owner in l.slots)
            popup.anchor.x = popup.anchor.target = bubble.x.x;
        // The menu slides up out from under the plate (2026-09-25): from "top at the
        // plate's bottom edge" (entirely hidden) to 12 px above the plate. A calm pane.
        const p = popup.progress.x, slide = smoothStep(p);
        const cx = clamp(popup.anchor.x, 137, width - 137);
        const top = mix(plate.y + plateHeight, plate.y - 12 - popup.height, slide);
        popupRect = Qt.rect(cx - 125, top, 250, popup.height);
        popupProgress = p;
        popupContent = popupRect;
        popupContentOpacity = visibility.x;
        // The hover drop over a menu row (launcher.js liquid()): four spring sides; born out
        // of the app's bubble, flows between rows, melts in place when the pointer leaves.
        const drop = menuDrop;
        if (menuRow >= 0 && menuRow < popup.rows.length && p > .001) {
            const r = menuRowRect(menuRow);
            drop.x.target = r.x;
            drop.y.target = r.y;
            drop.w.target = r.width;
            drop.h.target = r.height;
        }
        let dropActive = false;
        for (const s of [drop.x, drop.y, drop.w, drop.h])
            dropActive = stepSide(s, dt) || dropActive;
        dropActive = tickTween(drop.alpha, now, smoothStep) || dropActive;
        const a = smoothStep(drop.alpha.x);
        if (a > .001 && p > .001) {
            const vx = drop.x.v + drop.w.v / 2, vy = drop.y.v + drop.h.v / 2;
            const sx = Math.min(28, Math.abs(vx) * .035), sy = Math.min(16, Math.abs(vy) * .035);
            // A tail behind the motion, a nose in front: the body stretches, it does not teleport.
            const r = Qt.vector4d(drop.x.x - (vx < 0 ? sx : sx * .35), drop.y.x - (vy < 0 ? sy : sy * .35), drop.w.x + sx * 1.35, drop.h.x + sy * 1.35);
            // Close to the app's bubble the two melt together (a neck), apart they are two drops.
            const dist = Math.hypot(r.x + r.z / 2 - bubble.x.x, r.y + r.w / 2 - bubble.y);
            const union = mix(18, 3, clamp((dist - 30) / 60, 0, 1));
            list.push({
                id: "menu-drop",
                rect: r,
                params: Qt.vector4d(Math.min(12, Math.min(r.z, r.w) / 2), union, 1, a)
            });
            parts = list.slice(); // a new array: the same one would not notify the shader
        }
        settling = settling || dropActive;
        liveTop = p > .001 ? bubble.y - bubbleOptions.size / 2 : -100000;
        // Tooltip: the hovered app's name, or "Unpin" while a pinned app is lifted off.
        const tipKey = drag && drag.remove ? drag.key : hovered;
        const tipEntry = tipKey ? entryOf(tipKey) : null;
        if (tipEntry && !popup.progress.target && p < .03 && (!drag || drag.remove) && visibility.x > .1) {
            const label = drag && drag.remove ? "Unpin" : tipEntry.label + (!tipEntry.pinned && tipEntry.entry ? " · Right-click to pin" : "");
            const tw = Math.max(56, tipMetrics.measure(label) + 24);
            const center = visual(tipKey).center;
            const tcx = clamp(center[0], tw / 2 + 8, width - tw / 2 - 8);
            tipLabel = label;
            tipRemove = !!(drag && drag.remove);
            tipOpacity = drag ? 1 : visual(tipKey).hover;
            tipRect = Qt.rect(tcx - tw / 2, center[1] - 62, tw, 28);
        } else {
            tipOpacity = 0;
            tipLabel = "";
        }
        ++tick;
        return settling;
    }
    TextMetrics {
        id: tipMetrics
        font.family: ShellPalette.uiFont
        font.pixelSize: 11
        function measure(label: string): real {
            text = label;
            return advanceWidth;
        }
    }

    // ---- Screen rectangles for the surface masks and status ----
    readonly property rect plateHitRect: {
        tick;
        const p = geometry.plate;
        return Qt.rect(p.x - 6, p.y - 12, p.width + 12, p.height + 20);
    }
    readonly property bool popupInteractive: popupProgress > .7
    // Fully out from under the plate: rows sit where they will stay.
    readonly property bool popupSettled: popupProgress > .999
    function plateScreenRect(): var {
        const p = geometry.plate;
        return {
            x: p.x + screenOrigin.x,
            y: p.y + screenOrigin.y,
            width: p.width,
            height: p.height
        };
    }
    function popupScreenRect(): var {
        return {
            x: popupContent.x + screenOrigin.x,
            y: popupContent.y + screenOrigin.y,
            width: popupContent.width,
            height: popupContent.height
        };
    }

    // ---- Ink: always the light glass, whatever lies underneath (2026-09-27) ----
    readonly property color ink: "#241018"
    readonly property color controlFill: "#0d17111a"
    readonly property color danger: "#a01b45"
    readonly property color dotColor: Qt.rgba(36 / 255, 16 / 255, 24 / 255, .72)
    readonly property color lineColor: Qt.rgba(.13, .12, .16, .30)

    // ---- Icon layer: drawn under the glass (uIcons), or directly without glass ----
    Item {
        id: iconLayer
        width: dock.width
        height: dock.height
        opacity: dock.glassReady ? 1 : dock.visibleAmount
        Repeater {
            model: dock.iconKeys
            delegate: Item {
                id: icon
                required property string modelData
                readonly property var item: dock.entryOf(modelData)
                // The state object is the same every frame; read it through `tick` so each
                // binding below re-evaluates when the frame moved it.
                readonly property var v: dock.visual(modelData)
                readonly property real cx: {
                    dock.tick;
                    return v.center[0];
                }
                readonly property real cy: {
                    dock.tick;
                    return v.center[1];
                }
                readonly property real iconScale: {
                    dock.tick;
                    return v.scale;
                }
                readonly property real alpha: {
                    dock.tick;
                    return v.alpha;
                }
                readonly property bool removing: {
                    dock.tick;
                    return dock.drag !== null && dock.drag.key === modelData && dock.drag.remove;
                }
                x: cx - 25
                y: cy - 25
                width: 50
                height: 50
                z: dock.dragKey === modelData ? 2 : 1
                opacity: (removing ? .35 : 1) * alpha
                Item {
                    anchors.centerIn: parent
                    width: 44
                    height: 44
                    scale: icon.iconScale
                    Rectangle {
                        anchors.fill: parent
                        radius: 12
                        color: ShellPalette.surfaceHigh
                        visible: image.status !== Image.Ready
                        Text {
                            anchors.centerIn: parent
                            text: (icon.item?.label ?? "?").slice(0, 1).toLocaleUpperCase()
                            font.family: ShellPalette.uiFont
                            font.pixelSize: 15
                            font.weight: Font.DemiBold
                            color: ShellPalette.text
                        }
                    }
                    Image {
                        id: image
                        anchors.fill: parent
                        sourceSize.width: 88
                        sourceSize.height: 88
                        source: icon.item?.icon ?? ""
                        smooth: true
                        mipmap: true
                    }
                }
                // Up to three dots 26 px below the icon centre, 7 px apart.
                Repeater {
                    model: Math.min(3, icon.item?.windows.length ?? 0)
                    Rectangle {
                        required property int index
                        readonly property int count: Math.min(3, icon.item?.windows.length ?? 0)
                        x: 25 - (count * 4 + (count - 1) * 3) / 2 + index * 7
                        y: 25 + 26 - 2
                        width: 4
                        height: 4
                        radius: 2
                        color: dock.dotColor
                    }
                }
            }
        }
    }
    ShaderEffectSource {
        id: iconTexture
        width: dock.width
        height: dock.height
        sourceItem: iconLayer
        hideSource: dock.glassReady
        live: true
        visible: false
    }

    // ---- Glass: the shared GlassShape (shaders/dock.frag), dock defaults ----
    component DockGlass: GlassShape {
        backdrop: dock.backdrop
        wallBackdrop: dock.wallSource
        uIcons: iconTexture
        uMenu: menuTexture
        uSceneSize: Qt.point(dock.width, dock.height)
        uCover: dock.cover
        uDematerialize: 1 - dock.smoothStep(dock.visibleAmount)
        visible: dock.glassReady && dock.visibleAmount > .001
    }
    function vec(r: rect): vector4d {
        return Qt.vector4d(r.x, r.y, r.width, r.height);
    }
    function unionRect(a: rect, b: rect): rect {
        const x = Math.min(a.x, b.x), y = Math.min(a.y, b.y);
        return Qt.rect(x, y, Math.max(a.x + a.width, b.x + b.width) - x, Math.max(a.y + a.height, b.y + b.height) - y);
    }
    // One draw for plate + bubble + neck + menu, as surfaces() builds `root`.
    DockGlass {
        id: body
        readonly property rect bounds: {
            dock.tick;
            let b = dock.geometry.plate;
            if (dock.popupProgress > .001) {
                b = dock.unionRect(b, dock.popupRect);
            }
            for (const part of dock.parts)
                b = dock.unionRect(b, Qt.rect(part.rect.x, part.rect.y, part.rect.z, part.rect.w));
            const pad = Math.ceil(28 * 1.8 + 5 + 16 + 6);
            return Qt.rect(Math.floor(b.x - pad), Math.floor(b.y - pad), Math.ceil(b.width + pad * 2), Math.ceil(b.height + pad * 2));
        }
        x: bounds.x
        y: bounds.y
        width: bounds.width
        height: bounds.height
        uRect: {
            dock.tick;
            return dock.vec(dock.geometry.plate);
        }
        uRadius: 18
        uEdge: dock.bubbleOptions.edge
        uThickness: dock.bubbleOptions.thickness
        uRefraction: dock.bubbleOptions.refraction
        uDispersion: dock.bubbleOptions.dispersion
        uSpecular: .12
        uRim: 0
        uRimWidth: .3
        uInnerShadow: .3
        uInnerWidth: 3.5
        uAdaptation: 0
        uIsDock: 2
        uOpacity: dock.clamp(dock.visibleAmount, 0, 1)
        drops: dock.parts
        uBulge: {
            dock.tick;
            return Qt.vector4d(dock.bubbleOptions.restUnion, 0, dock.smoothStep(dock.bubble.alpha.x), 0);
        }
        uHasB: dock.popupProgress > .001 ? 1 : 0
        uRectB: dock.vec(dock.popupRect)
        uRadiusB: 18
        uLiveTop: dock.liveTop
        uHasIcons: 1
    }
    // Separator and menu lines: thin flat marks on the plate (uFlat in the mockup).
    Rectangle {
        readonly property var g: {
            dock.tick;
            return dock.geometry;
        }
        visible: g.separatorX >= 0 && dock.visibleAmount > .001
        x: g.separatorX
        y: g.plate.y + 15
        width: 1
        height: 36
        radius: .5
        color: dock.lineColor
        opacity: dock.visibleAmount
    }
    // Headless / software stand-in: flat plate and menu, no optics.
    Rectangle {
        visible: !dock.glassReady && dock.visibleAmount > .001
        z: -1
        readonly property rect r: {
            dock.tick;
            return dock.geometry.plate;
        }
        x: r.x
        y: r.y
        width: r.width
        height: r.height
        radius: 18
        color: LiquidPalette.flatPlate
        opacity: dock.visibleAmount
    }
    Rectangle {
        visible: !dock.glassReady && dock.popupProgress > .001
        z: -1
        x: dock.popupRect.x
        y: dock.popupRect.y
        width: dock.popupRect.width
        height: dock.popupRect.height
        radius: 18
        color: LiquidPalette.flatPanel
    }

    // ---- Menu content ----
    // The text lives under the glass (uMenu), so the hover drop bends it like the launcher's
    // bubble bends its rows. On top only invisible hit areas remain.
    Item {
        id: menuInk
        width: dock.width
        height: dock.height
        Item {
            x: dock.popupContent.x
            y: dock.popupContent.y
            width: dock.popupContent.width
            height: dock.popupContent.height
            visible: dock.popupProgress > .005
            opacity: dock.popupContentOpacity
            Text {
                x: 16
                y: 8
                height: 24
                width: parent.width - 32
                verticalAlignment: Text.AlignVCenter
                text: {
                    dock.tick;
                    return dock.popup.model?.header ?? "";
                }
                textFormat: Text.PlainText
                elide: Text.ElideRight
                font.family: ShellPalette.uiFont
                font.pixelSize: 10
                font.weight: Font.DemiBold
                font.letterSpacing: .3
                color: dock.ink
                opacity: .65
            }
            Repeater {
                model: popupView.shownRows
                delegate: Item {
                    id: inkRow
                    required property var modelData
                    required property int index
                    x: 8
                    y: popupView.rowTop(index)
                    width: dock.popupContent.width - 16
                    height: dock.rowHeight(modelData)
                    Rectangle {
                        visible: inkRow.modelData.kind === "line"
                        x: 8
                        y: 4
                        width: parent.width - 16
                        height: 1
                        color: dock.lineColor
                    }
                    Text {
                        visible: inkRow.modelData.kind !== "line"
                        x: 9
                        width: 16
                        height: 36
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        text: inkRow.modelData.mark
                        font.family: ShellPalette.uiFont
                        font.pixelSize: 15
                        color: inkRow.modelData.danger ? dock.danger : dock.ink
                        opacity: .6
                    }
                    Text {
                        visible: inkRow.modelData.kind !== "line"
                        x: 35
                        y: inkRow.modelData.note ? 7 : 0
                        height: inkRow.modelData.note ? implicitHeight : 36
                        width: parent.width - 35 - (inkRow.modelData.tag ? 30 : 10)
                        verticalAlignment: Text.AlignVCenter
                        text: inkRow.modelData.label
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        font.family: ShellPalette.uiFont
                        font.pixelSize: 12
                        color: inkRow.modelData.danger ? dock.danger : dock.ink
                    }
                    Text {
                        visible: inkRow.modelData.note !== ""
                        x: 35
                        y: 26
                        width: parent.width - 45
                        text: inkRow.modelData.note
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        font.family: ShellPalette.uiFont
                        font.pixelSize: 10
                        color: dock.ink
                        opacity: .6
                    }
                    Text {
                        visible: inkRow.modelData.tag !== ""
                        anchors.right: parent.right
                        anchors.rightMargin: 12
                        height: 36
                        verticalAlignment: Text.AlignVCenter
                        text: inkRow.modelData.tag
                        font.family: ShellPalette.uiFont
                        font.pixelSize: 9
                        color: dock.ink
                        opacity: .6
                    }
                }
            }
        }
    }
    ShaderEffectSource {
        id: menuTexture
        width: dock.width
        height: dock.height
        sourceItem: menuInk
        hideSource: dock.glassReady
        live: true
        visible: false
    }
    readonly property alias popupView: popupView
    Item {
        id: popupView
        readonly property var rows: {
            dock.tick;
            return dock.popupOpen ? dock.popup.rows : [];
        }
        readonly property var shownRows: {
            dock.tick;
            return dock.popup.owner ? dock.popup.rows : [];
        }
        x: dock.popupContent.x
        y: dock.popupContent.y
        width: dock.popupContent.width
        height: dock.popupContent.height
        visible: dock.popupProgress > .005
        enabled: dock.popupInteractive
        z: 3
        function rowTop(index: int): int {
            let at = 8 + 24;
            for (let i = 0; i < index; ++i)
                at += dock.rowHeight(shownRows[i]);
            return at;
        }
        // Presses on the menu body never fall through to the windows below.
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.AllButtons
        }
        Repeater {
            model: popupView.shownRows
            delegate: MouseArea {
                id: hit
                required property var modelData
                required property int index
                x: 8
                y: popupView.rowTop(index)
                width: popupView.width - 16
                height: dock.rowHeight(modelData)
                enabled: modelData.kind !== "line"
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onContainsMouseChanged: {
                    if (containsMouse)
                        dock.hoverMenuRow(index);
                    else
                        dock.leaveMenuRow(index);
                }
                onClicked: dock.popupAction(modelData.kind, modelData.id)
            }
        }
    }

    // ---- Tooltip: its own small glass (Regular) above the icon ----
    DockGlass {
        id: tipGlass
        readonly property int pad: Math.ceil(28 * 1.8 + 5 + 12 + 6)
        visible: dock.glassReady && dock.tipOpacity > .001
        x: dock.tipRect.x - pad
        y: dock.tipRect.y - pad
        width: dock.tipRect.width + pad * 2
        height: dock.tipRect.height + pad * 2
        uRect: dock.vec(dock.tipRect)
        uRadius: 9
        uHasIcons: 0
        uOpacity: dock.tipOpacity * dock.visibleAmount
    }
    Rectangle {
        visible: !dock.glassReady && dock.tipOpacity > .001
        x: dock.tipRect.x
        y: dock.tipRect.y
        width: dock.tipRect.width
        height: dock.tipRect.height
        radius: 9
        color: LiquidPalette.flatPanel
        opacity: dock.tipOpacity * dock.visibleAmount
    }
    Text {
        visible: dock.tipOpacity > .001
        x: dock.tipRect.x
        y: dock.tipRect.y
        width: dock.tipRect.width
        height: dock.tipRect.height
        z: 4
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        text: dock.tipLabel
        textFormat: Text.PlainText
        font.family: ShellPalette.uiFont
        font.pixelSize: 11
        color: dock.tipRemove ? dock.danger : dock.ink
        opacity: dock.tipOpacity * dock.visibleAmount
    }

    // ---- Input on the plate ----
    MouseArea {
        id: plateHit
        x: dock.plateHitRect.x
        y: dock.plateHitRect.y
        width: dock.plateHitRect.width
        height: dock.plateHitRect.height
        enabled: dock.visibleAmount > .08
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        cursorShape: dock.keyAt(mouseX + x, mouseY + y) ? Qt.PointingHandCursor : Qt.ArrowCursor
        onPositionChanged: mouse => {
            const p = mapToItem(dock, mouse.x, mouse.y);
            if (pressed && (pressedButtons & Qt.LeftButton))
                dock.pointerDragged(p.x, p.y);
            else
                dock.pointerMoved(p.x, p.y);
        }
        onExited: {
            if (!pressed)
                dock.pointerLeft();
        }
        onPressed: mouse => {
            const p = mapToItem(dock, mouse.x, mouse.y);
            dock.pointerPressed(p.x, p.y);
        }
        onReleased: mouse => {
            const p = mapToItem(dock, mouse.x, mouse.y);
            dock.pointerReleased(p.x, p.y, mouse.button);
            if (!containsMouse)
                dock.pointerLeft();
        }
        onCanceled: dock.pointerCanceled()
    }
}
