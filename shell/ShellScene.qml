pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

Item {
    id: scene
    required property string borderMode
    required property int reservedSpace
    required property bool headless
    required property int testWidth
    required property int testHeight
    required property NiriService niri
    property bool skipIntro: false
    readonly property bool startupModelsReady: services.startupReady && niri.connected && dockStore.restored && (!dockLabels.wanted || dockLabels.state === "ready") && (!launcherBody.settings.profile || (!launcherBody.settings.busy && launcherBody.settings.state !== "idle")) && !dock.animating && !bar.leftIslands.animating
    readonly property string startupRevision: skipIntro ? JSON.stringify({
        model: niri.model,
        pinned: dockStore.pinned,
        dockOn: dockStore.on,
        dockAutoHide: dockStore.autoHide,
        barAutoHide: barPolicy.autoHide,
        barOverview: barPolicy.overviewWorkspaces,
        dockEntries: dock.keys,
        dockVisible: dock.visibleAmount,
        dockWidth: dock.length,
        systemWidth: bar.systemGlass.islandWidth,
        systemCells: bar.systemGlass.row.pages.map(page => [page, bar.systemGlass.row.iconOf(page), page === "power" ? bar.systemGlass.row.batteryText : page === "kb" ? niri.layoutLabel : ""]),
        layout: niri.layoutLabel
    }) : ""
    property string outputName: output?.name || niri.model?.focused_output || ""
    property ShellScreen output
    readonly property alias barPolicy: barPolicy
    readonly property alias tip: tip
    readonly property alias edge: edge
    BarPolicy {
        id: barPolicy
        skipIntro: scene.skipIntro
        overview: scene.niri.overviewOpen
        presentation: scene.presentationState
        openUI: scene.modalOpen || scene.peekOpen
        configuredReserve: scene.reservedSpace
        pointerInside: barHover.hovered
        edgeHovered: edgeHover.hovered
        onBarVisibleChanged: tip.hide()
    }
    Item {
        id: edge
        z: 5
        x: 56
        y: 0
        width: Math.max(0, scene.viewportWidth - 56)
        height: 8
        visible: barPolicy.edgeEnabled
        HoverHandler {
            id: edgeHover
        }
    }
    HoverTip {
        id: tip
        viewportWidth: scene.viewportWidth
    }
    // Dock: its own surface (Surfaces.qml) or, headless, items placed at dockOrigin by the fixture.
    readonly property alias dock: dock
    readonly property alias dockPolicy: dockPolicy
    readonly property alias dockStore: dockStore
    readonly property alias dockEdge: dockEdge
    // The menu of a dock item lives inside the dock (it grows out of the bubble).
    readonly property alias dockPopup: dock.popupView
    readonly property alias dockLabels: dockLabels
    // The dock lives at the bottom only (2026-09-24: a side dock fights the niri strip).
    readonly property real dockWindowWidth: viewportWidth
    // The surface is taller than the 77 px band: the bubble's menu and the tooltip grow up
    // out of the plate inside the same glass. Only the band is reserved; the rest is
    // transparent and outside the input mask.
    readonly property real dockHeadroom: Math.max(0, Math.min(480, viewportHeight - dockPolicy.thickness - 120))
    readonly property real dockWindowHeight: dockPolicy.thickness + dockHeadroom
    readonly property point dockOrigin: Qt.point(0, viewportHeight - dockWindowHeight)
    // Reveal strip at the very screen edge, 2 px: the pointer clamps there anyway, and a wider
    // strip stole clicks and scrolls from the bottom 8 px of every window (auto-hide has no reserve).
    readonly property rect dockEdgeLocal: Qt.rect(0, dockWindowHeight - 2, viewportWidth, 2)
    // On Wayland the dock items live in their own surface at dockOrigin; headless they sit in this scene.
    readonly property point dockScreenOffset: headless ? Qt.point(0, 0) : dockOrigin
    readonly property point dockPlacement: headless ? dockOrigin : Qt.point(0, 0)
    DockStore {
        id: dockStore
    }
    DockPolicy {
        id: dockPolicy
        on: dockStore.on
        autoHide: dockStore.autoHide
        overview: scene.niri.overviewOpen
        presentation: scene.presentationState
        popupOpen: dock.popupOpen
        dragging: dock.dragging
        pointerInside: dock.pointerInside
        edgeHovered: dockEdgeHover.hovered
        onDockVisibleChanged: {
            if (!dockVisible)
                dock.closePopup();
        }
    }
    WindowLabels {
        id: dockLabels
        niri: scene.niri
        active: dockStore.on
        onAppeared: (id, appId) => appIdentity.windowAppeared(appId)
    }
    // app_id → desktop entry for the dock and the launcher; learned matches in apps.json.
    readonly property alias identity: appIdentity
    AppIdentity {
        id: appIdentity
        catalog: launcherBody.appCatalog
    }
    Item {
        id: dockEdge
        z: 5
        x: scene.dockPlacement.x + scene.dockEdgeLocal.x
        y: scene.dockPlacement.y + scene.dockEdgeLocal.y
        width: scene.dockEdgeLocal.width
        height: scene.dockEdgeLocal.height
        visible: dockPolicy.edgeEnabled
        HoverHandler {
            id: dockEdgeHover
        }
    }
    Dock {
        id: dock
        skipIntro: scene.skipIntro
        z: 6
        visible: dockStore.on
        // The item is the dock surface; it slides the plate itself (dock.js visible tween).
        x: scene.dockPlacement.x
        y: scene.dockPlacement.y
        width: scene.dockWindowWidth
        height: scene.dockWindowHeight
        screenOrigin: scene.dockOrigin
        screenSize: Qt.size(scene.viewportWidth, scene.viewportHeight)
        store: dockStore
        policy: dockPolicy
        niri: scene.niri
        labels: dockLabels
        catalog: launcherBody.appCatalog
        identity: appIdentity
        onAssignRequested: appId => {
            scene.openLauncher();
            launcherBody.beginAssign(appId);
        }
    }
    function dockScreenRect(key: string): var {
        const r = dock.itemRect(key);
        if (!r)
            return null;
        return {
            x: dock.x + r.x + dockScreenOffset.x,
            y: dock.y + r.y + dockScreenOffset.y,
            width: r.width,
            height: r.height
        };
    }
    // Overlay covers the dock while its popup is open: route those presses back to the dock.
    function dockPressAt(x: real, y: real, button: int): bool {
        if (!dockPolicy.dockVisible)
            return false;
        const local = Qt.point(x - dockScreenOffset.x - dock.x, y - dockScreenOffset.y - dock.y);
        const key = dock.keyAt(local.x, local.y);
        if (!key)
            return false;
        closeAll();
        if (button === Qt.RightButton)
            dock.menu(key);
        else
            dock.activate(key);
        return true;
    }
    property bool launcherOpen: false
    property bool launcherPresent: false
    property real expansion: 0
    property int requestSerial: 0
    property bool drawerOpen: false
    property bool clockPresent: false
    property real clockExpansion: 0
    property var peekIds: []
    property double peekStarted: 0
    property double peekUntil: 0
    property double peekCooldown: 0
    readonly property bool peekOpen: peekIds.length > 0
    readonly property bool modalOpen: launcherOpen || drawerOpen || systemOpen || privacyOpen
    property bool systemOpen: false
    property bool systemPresent: false
    property real systemExpansion: 0
    property string systemPage: ""
    readonly property alias services: services
    readonly property alias systemPanel: systemPanel
    readonly property alias systemBody: systemPanel.body
    SystemService {
        id: services
        live: !scene.headless
        panelOpen: scene.systemOpen
        onLowBattery: percent => notes.systemBattery(percent)
        // Initial native values settle under the login cover. The open panel
        // already shows the value (mockup: panel === k ? paintSys : showOsd).
        onOsdRequested: page => {
            if (scene.skipIntro || (scene.systemOpen && scene.systemPage === page))
                return;
            if (page === "sound")
                osd.show("sound", Math.round((services.sinkVolume >= 0 ? services.sinkVolume : 0) * 100), services.sinkMuted, services.sinkMuted ? "Muted" : (services.backend?.sink?.description || services.backend?.sink?.name || "Sound"));
            else
                osd.show("light", services.brightness.percent ?? 0, false, "Brightness");
        }
    }
    // Privacy: capture streams from the backend plus screencasts from the core model.
    // A JS array assigned through a var property can come back as a sequence object without filter/some.
    readonly property var captures: Array.from(services.backend?.captures ?? [])
    readonly property var privacyRows: scene.captures.filter(c => c.kind === "mic" || c.kind === "cam").map(c => ({
                kind: "info",
                what: c.kind,
                label: c.name || "An app",
                note: c.kind === "mic" ? "Using the microphone" : "Using the camera",
                tag: "",
                id: 0
            })).concat(niri.casts.map(c => ({
                kind: "info",
                what: "cast",
                label: c.target === "window" ? "Window " + c.window_id : c.target === "output" ? "Screen " + c.output : "Screen share",
                note: "Sharing the screen",
                tag: c.is_active ? "" : "Paused",
                id: 0
            })))
    readonly property bool privacyMic: scene.captures.some(c => c.kind === "mic")
    readonly property bool privacyCam: scene.captures.some(c => c.kind === "cam")
    readonly property bool privacyCast: niri.casts.length > 0
    readonly property bool privacyActive: privacyMic || privacyCam || privacyCast
    // The panel grows out of the pill like the other islands' (open, present while it morphs).
    property bool privacyOpen: false
    property bool privacyPresent: false
    property real privacyExpansion: 0
    readonly property alias privacy: privacy
    readonly property alias privacyPopup: privacyPopup
    // The last capture ended: the pill is gone at once, and its panel with it.
    onPrivacyActiveChanged: {
        if (!privacyActive) {
            privacyOpen = false;
            privacyPresent = false;
            privacyExpansion = 0;
        }
    }
    function togglePrivacy(): void {
        if (privacyOpen) {
            closeAll();
            return;
        }
        closeAll();
        if (!privacyActive)
            return;
        privacyOpen = true;
        privacyPresent = true;
        privacyExpansion = 1;
        Qt.callLater(() => {
            if (scene.privacyOpen)
                privacyPopup.forceActiveFocus();
        });
    }
    Behavior on privacyExpansion {
        NumberAnimation {
            id: privacyMorph
            duration: Metrics.morphMs
            easing.type: Easing.BezierSpline
            easing.bezierCurve: Metrics.morphCurve
        }
    }
    PrivacyPill {
        id: privacy
        z: 7
        tip: tip
        shift: barPolicy.overviewShift
        visible: scene.privacyActive && opacity > 0
        mic: scene.privacyMic
        cam: scene.privacyCam
        cast: scene.privacyCast
        // Right of it: the system island when the bar shows, else the mockup's 10 px edge.
        x: barPolicy.barVisible ? bar.systemIsland.x - 8 - width : scene.viewportWidth - Metrics.side - width
        y: Metrics.top - 70 * shift
        // Drawn under its growing panel until the panel is half open (as the other islands).
        panelPresent: scene.privacyPresent && scene.privacyExpansion > .5
        onClicked: scene.togglePrivacy()
    }
    // What the privacy panel, the OSD and the system panel refract in the overlay (Surfaces
    // sets it on Wayland; headless: flat).
    property DockBackdrop systemBackdrop: null
    // The privacy panel grows out of the pill on liquid glass: 300 wide, its right edge at the
    // pill's, the pill's line as its head.
    PrivacyPanel {
        id: privacyPopup
        z: 30
        visible: scene.privacyPresent
        rows: scene.privacyRows
        opened: scene.privacyOpen
        expansion: scene.privacyExpansion
        viewportWidth: scene.viewportWidth
        viewportHeight: scene.viewportHeight
        backdrop: scene.systemBackdrop
        islandX: privacy.x
        islandWidth: privacy.width
        islandBubble: privacy.bubble
        kinds: privacy.kinds
        pulse: privacy.pulse
        focus: scene.privacyOpen
        Keys.onEscapePressed: scene.closeAll()
        onToggle: scene.togglePrivacy()
    }
    readonly property alias osd: osd
    Osd {
        id: osd
        z: 35
        x: scene.viewportWidth - Metrics.side - width
        y: 52
        backdrop: scene.systemBackdrop
    }
    function openSystem(page: string): bool {
        if (!["sound", "light", "power", "wifi", "bt", "kb", "tray"].includes(page))
            return false;
        if (systemOpen && systemPage === page) {
            closeAll();
            return true;
        }
        if (systemOpen) {
            // Another page while open: keep the panel; the tab flows to the new cell and the
            // height springs to the new page (system.js rebuilds the view at once).
            tip.hide();
            systemBody.reset();
            systemPage = page;
            return true;
        }
        closeAll();
        systemPage = page;
        systemOpen = true;
        systemPresent = true;
        systemExpansion = 1;
        Qt.callLater(() => {
            if (scene.systemOpen)
                systemPanel.forceActiveFocus();
        });
        return true;
    }
    Behavior on systemExpansion {
        NumberAnimation {
            id: sysMorph
            duration: Metrics.morphMs
            easing.type: Easing.BezierSpline
            easing.bezierCurve: Metrics.morphCurve
        }
    }
    readonly property string presentationState: niri.presentationState(outputName)
    readonly property alias notifications: notes
    readonly property alias notificationService: notificationService
    readonly property alias clockPanel: clockPanel
    readonly property alias clockBody: clockPanel.body
    // A failed launch (dock or launcher) is reported where the user looks: the drawer,
    // with the launcher's exit code as the body. The dock icon has no room for a message.
    Connections {
        target: launcherBody.appCatalog
        function onFailed(id: string, name: string, reason: string): void {
            notes.local("Emaki", "Couldn’t open " + (name || id), reason);
        }
    }
    NotificationStore {
        id: notes
        onArrived: id => scene.showNotification(id)
        onDndChanged: {
            if (dnd)
                scene.endPeek();
        }
        onEntriesChanged: {
            if (scene.peekOpen) {
                scene.peekIds = scene.peekIds.filter(id => notes.entries.some(n => n.id === id));
                if (!scene.peekOpen)
                    scene.endPeek();
            }
        }
    }
    NotificationService {
        id: notificationService
        store: notes
    }
    onPresentationStateChanged: {
        if (presentationState !== "clear")
            endPeek();
    }
    // Head of the clock panel: 36 px with the compact time in a peek, 70 px with the big
    // time in the drawer. Animated on its own so closing mirrors opening (drawerOpen flips
    // at once, the head must not).
    readonly property real headTarget: drawerOpen ? 1 : 0
    property real clockHeadExpansion: 0
    Behavior on clockHeadExpansion {
        NumberAnimation {
            duration: Metrics.morphMs
            easing.type: Easing.BezierSpline
            easing.bezierCurve: Metrics.morphCurve
        }
    }
    onHeadTargetChanged: clockHeadExpansion = headTarget
    function openDrawer(): void {
        if (!enabled || (!output && !headless))
            return;
        closeAll();
        clockPresent = true;
        drawerOpen = true;
        clockExpansion = 1;
        Qt.callLater(() => {
            if (scene.drawerOpen)
                clockPanel.forceActiveFocus();
        });
    }
    function toggleDrawer(): void {
        if (drawerOpen)
            closeAll();
        else
            openDrawer();
    }
    function closeClock(): void {
        drawerOpen = false;
        endPeek();
        clockExpansion = 0;
        closeTimer.restart();
    }
    function endPeek(): void {
        if (peekOpen)
            peekCooldown = Date.now() + Metrics.morphMs;
        peekIds = [];
        peekTimer.stop();
        if (!drawerOpen) {
            clockExpansion = 0;
            closeTimer.restart();
        }
    }
    function showNotification(id: int): void {
        const time = Date.now();
        if (notes.dnd || niri.overviewOpen || presentationState !== "clear" || modalOpen || time < peekCooldown)
            return;
        if (!peekOpen)
            peekStarted = time;
        if (time >= peekStarted + 8000) {
            endPeek();
            return;
        }
        peekIds = peekIds.concat([id]);
        clockPresent = true;
        clockExpansion = 1;
        peekUntil = Math.min(time + 4000, peekStarted + 8000);
        peekTimer.interval = Math.max(1, peekUntil - time);
        peekTimer.restart();
    }
    Timer {
        id: peekTimer
        onTriggered: scene.endPeek()
    }
    Behavior on clockExpansion {
        NumberAnimation {
            id: clockMorph
            duration: Metrics.morphMs
            easing.type: Easing.BezierSpline
            easing.bezierCurve: Metrics.morphCurve
        }
    }
    readonly property alias wallpaper: wallpaper
    // Conservative: expanded overlay may cover windows; never paint wallpaper over them.
    property bool panelWallpaperExposed: false
    WallpaperSource {
        id: wallpaper
        outputWidth: Math.round(scene.headless ? scene.viewportWidth : scene.output?.width ?? 0)
        outputHeight: Math.round(scene.headless ? scene.viewportHeight : scene.output?.height ?? 0)
        outputScale: scene.output?.devicePixelRatio ?? 1
        outputName: scene.outputName
    }
    readonly property alias bar: bar
    readonly property alias panel: panel
    readonly property alias input: launcherBody
    // Future drawers/panels/dock popups subscribe here, not directly to niri.
    signal closeAllRequested
    readonly property real viewportWidth: headless ? testWidth : Math.min(output?.width ?? 0, testWidth || Infinity)
    readonly property real viewportHeight: headless ? testHeight : Math.min(output?.height ?? 0, testHeight || Infinity)
    readonly property real panelWidth: Math.min(Metrics.launcherWidth, Math.max(36, viewportWidth - Metrics.logoX - Metrics.side))
    // Content follows the mockup's 500px body cap; surfaces keep their accepted morph.
    readonly property real panelHeight: Math.min(launcherBody.desiredHeight, Math.max(36, viewportHeight - Metrics.top - Metrics.side))

    function openLauncher(): void {
        if (!enabled || (!output && !headless))
            return;
        if (!launcherOpen)
            closeAll();
        if (launcherOpen) {
            launcherBody.takeFocus();
            return;
        }
        launcherPresent = true;
        const serial = ++requestSerial;
        // Give the separate overlay the same first frame as the compact logo.
        Qt.callLater(() => {
            if (!scene.launcherPresent || serial !== scene.requestSerial)
                return;
            scene.launcherOpen = true;
            scene.expansion = 1;
            launcherBody.takeFocus();
        });
    }
    function closeLauncher(): void {
        ++requestSerial;
        launcherOpen = false; // Release keyboard immediately; compositor restores underlying focus.
        expansion = 0;
        launcherBody.reset();
        closeTimer.restart();
    }
    function closeAll(): void {
        tip.hide();
        closeLauncher();
        closeClock();
        systemOpen = false;
        systemExpansion = 0;
        systemBody.reset();
        dock.closePopup();
        privacyOpen = false;
        privacyExpansion = 0;
        closeAllRequested();
    }
    function outsidePress(x: real, y: real): void {
        outsidePressButton(x, y, Qt.LeftButton);
    }
    function outsidePressButton(x: real, y: real, button: int): void {
        const logo = bar.logo;
        if (!launcherPresent && x >= logo.x && x < logo.x + logo.width && y >= logo.y && y < logo.y + logo.height)
            openLauncher();
        else if (!dockPressAt(x, y, button))
            closeAll();
    }
    function toggleLauncher(): void {
        if (launcherOpen)
            closeAll();
        else
            openLauncher();
    }
    Connections {
        target: scene.niri
        function onOverviewOpenChanged(): void {
            if (scene.niri.overviewOpen)
                scene.closeAll();
        }
    }
    onOutputChanged: {
        closeAll();
        launcherPresent = false;
    }
    Behavior on expansion {
        NumberAnimation {
            id: morph
            duration: Metrics.morphMs
            easing.type: Easing.BezierSpline
            easing.bezierCurve: Metrics.morphCurve
        }
    }
    Timer {
        id: closeTimer
        interval: 16
        repeat: true
        onTriggered: {
            if (!scene.launcherOpen && scene.expansion === 0 && !morph.running) {
                scene.launcherPresent = false;
                launcherBody.reset();
            }
            if (!scene.drawerOpen && !scene.peekOpen && scene.clockExpansion === 0 && !clockMorph.running)
                scene.clockPresent = false;
            if (!scene.systemOpen && scene.systemExpansion === 0 && !sysMorph.running)
                scene.systemPresent = false;
            if (!scene.privacyOpen && scene.privacyExpansion === 0 && !privacyMorph.running)
                scene.privacyPresent = false;
            if ((!scene.systemPresent || scene.systemOpen) && (!scene.launcherPresent || scene.launcherOpen) && (!scene.clockPresent || scene.drawerOpen || scene.peekOpen) && (!scene.privacyPresent || scene.privacyOpen))
                stop();
        }
    }
    function rectangle(item: Item): var {
        return {
            x: item.x,
            y: item.y,
            width: item.width,
            height: item.height
        };
    }
    function status(): string {
        return JSON.stringify({
            schema_version: 1,
            data: "live_services",
            services: services.status(),
            system_page: systemOpen ? systemPage : "closed",
            system_panel: systemOpen ? "open" : systemPresent ? "closing" : "closed",
            system_expansion: systemExpansion,
            clock_expansion: clockExpansion,
            clock_head: clockHeadExpansion,
            privacy: {
                mic: scene.captures.filter(c => c.kind === "mic").length,
                cam: scene.captures.filter(c => c.kind === "cam").length,
                cast: niri.casts.length,
                visible: privacy.visible,
                open: privacyOpen,
                panel: privacyOpen ? "open" : privacyPresent ? "closing" : "closed",
                expansion: privacyExpansion,
                rows: privacyRows.length,
                rect: rectangle(privacy),
                // The pill (its drop, whether it draws itself) and the panel's plate.
                pill: privacy.glassStatus(),
                panel_rect: rectangle(privacyPopup)
            },
            osd: {
                shown: osd.shown,
                kind: osd.kind,
                value: osd.value,
                muted: osd.muted
            },
            session_confirmation: systemBody.confirmation,
            niri: {
                connection: niri.connection,
                reason: niri.reason,
                generation: niri.generation,
                overview_open: niri.connected ? niri.overviewOpen : null,
                workspaces: niri.workspaces.map(w => ({
                            id: w.id,
                            idx: w.idx,
                            output: w.output,
                            active: w.is_active
                        })),
                window_count: niri.windows.length,
                layout_count: niri.layouts?.names.length ?? 0,
                layout: niri.layoutLabel,
                action: niri.actionState,
                action_reason: niri.actionReason
            },
            media: {
                count: clockBody.player?.playerCount ?? 0,
                playing: clockBody.player?.playing ?? false,
                action: clockBody.player?.actionState ?? "idle"
            },
            notifications: {
                server: notificationService.state,
                count: notes.count,
                dnd: notes.dnd,
                groups: notes.groups().length,
                expanded_groups: Object.keys(notes.expanded).length,
                action: notes.actionState,
                peek_count: peekIds.length,
                peek_remaining_ms: peekOpen ? Math.max(0, peekUntil - Date.now()) : 0,
                presentation: presentationState
            },
            drawer: drawerOpen ? "open" : clockPresent ? (peekOpen ? "peek" : "closing") : "closed",
            clock_panel: rectangle(clockPanel),
            calendar: {
                year: clockBody.calendar.year,
                month: clockBody.calendar.month + 1
            },
            material: {
                wallpaper: wallpaper.state,
                panel_wallpaper: panelWallpaperExposed
            },
            // Liquid glass of the launcher and the left islands: drops (alpha, rect) and
            // whether the GPU glass or the flat stand-in is drawn.
            launcher_glass: launcherBody.glassStatus(),
            left_glass: bar.leftIslands.glassStatus(),
            // The clock island (its hover drop) and the clock panel (its four drops).
            clock_glass: {
                island: bar.clockIsland.glassStatus(),
                panel: clockPanel.glassStatus()
            },
            // The system island (its hover drop, cells) and the system panel (tab, knobs,
            // chosen rows, hover; kinds only, no names).
            system_glass: {
                island: bar.systemGlass.glassStatus(),
                panel: systemPanel.glassStatus()
            },
            time: bar.clockTime,
            date: bar.clockDate,
            workspace_output: bar.workspaceStrip.outputName,
            workspace_active: bar.workspaceStrip.activeIndex,
            workspace_count: bar.workspaceStrip.entries.length,
            search: launcherBody.searchStatus(),
            border: borderMode,
            launcher: launcherOpen ? "open" : launcherPresent ? "closing" : "closed",
            input_focused: launcherBody.inputFocused,
            query_length: launcherBody.queryLength,
            output: output?.name ?? null,
            headless: headless,
            exclusive_zone: barPolicy.effectiveReserve,
            dock: {
                on: dockStore.on,
                auto_hide: dockStore.autoHide,
                pinned: dockStore.pinned,
                visible: dockPolicy.dockVisible,
                reserve: dockPolicy.reserve,
                edge_enabled: dockPolicy.edgeEnabled,
                revealed: dockPolicy.revealed,
                dragging: dock.dragging,
                // Desktop ID whose icon pulses while gtk-launch runs ("" otherwise).
                launching: dock.launchingKey,
                popup: dock.popupOpen ? dock.popupMode : "closed",
                // The menu has slid all the way out from under the plate.
                popup_ready: dock.popupOpen && dock.popupSettled,
                popup_rows: dockPopup.rows.length,
                labels: dockLabels.state,
                learned: Object.keys(appIdentity.learned).length,
                // Running unpinned apps stay anonymous here: app IDs are presentation-only data.
                entries: dock.entries.map(e => ({
                            id: e.pinned ? e.id : null,
                            pinned: e.pinned,
                            source: e.source,
                            windows: e.windows.length
                        })),
                rect: dock.plateScreenRect(),
                origin: {
                    x: dockOrigin.x,
                    y: dockOrigin.y
                },
                popup_rect: dock.popupScreenRect()
            },
            bar_policy: {
                auto_hide: barPolicy.autoHide,
                overview_workspaces: barPolicy.overviewWorkspaces,
                overview_shift: barPolicy.overviewShift,
                visible: barPolicy.barVisible,
                normal_islands: barPolicy.normalIslands,
                workspace_island: barPolicy.workspaceIsland,
                edge_enabled: barPolicy.edgeEnabled,
                revealed: barPolicy.revealed,
                tooltip: tip.shown,
                needs_core_key: true
            },
            viewport: {
                width: viewportWidth,
                height: viewportHeight
            },
            logo: rectangle(bar.logo),
            workspaces: rectangle(bar.workspaces),
            clock: rectangle(bar.clock),
            system: rectangle(bar.systemIsland),
            launcher_panel: rectangle(panel)
        });
    }
    StaticBar {
        id: bar
        y: barPolicy.barVisible ? 0 : -60
        Behavior on y {
            enabled: !scene.skipIntro
            NumberAnimation {
                duration: 280
                easing.type: Easing.OutCubic
            }
        }
        policy: barPolicy
        skipIntro: scene.skipIntro
        tip: tip
        HoverHandler {
            id: barHover
            enabled: barPolicy.barVisible
        }
        width: scene.viewportWidth
        height: 84
        borderMode: scene.borderMode
        niri: scene.niri
        wallpaper: wallpaper
        wallpaperExposed: barPolicy.effectiveReserve >= Metrics.top + Metrics.islandHeight
        outputName: scene.outputName
        launcherOpen: scene.launcherOpen
        launcherPresent: scene.launcherPresent
        // Right edge of the growing launcher panel: islands fade as it covers them.
        launcherRight: scene.launcherPresent ? Metrics.logoX + Metrics.islandHeight + (scene.panelWidth - Metrics.islandHeight) * scene.expansion : 0
        launcherExpansion: scene.launcherPresent ? scene.expansion : 0
        // An island stays drawn under its growing panel (the overlay, above the bar) until the
        // panel is half open: hiding it at once left 30–60 ms with neither drawn (27.09).
        clockPresent: scene.clockPresent && scene.clockExpansion > .5
        notificationCount: notes.count
        dnd: notes.dnd
        services: services
        systemPresent: scene.systemPresent && scene.systemExpansion > .5
        onSystemClicked: page => scene.openSystem(page)
        onClockClicked: scene.toggleDrawer()
        onLaunch: scene.toggleLauncher()
    }
    // What the launcher's glass refracts (Surfaces sets it on Wayland; headless: flat).
    property DockBackdrop launcherBackdrop: null
    // The launcher grows out of the bar's button on liquid glass (liquid-glass/launcher.html):
    // LauncherBody draws the plate, its content and the drops. This item is the plate's
    // rectangle while it morphs; the overlay's input and blur regions and the status read it.
    Item {
        id: panel
        readonly property real radius: launcherBody.shownRadius
        visible: scene.launcherPresent
        x: Metrics.logoX
        y: Metrics.top
        width: launcherBody.shownWidth
        height: launcherBody.shownHeight
        // Presses on the plate stay in the launcher; on empty space they return to the search.
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.AllButtons
            onPressed: launcherBody.takeFocus()
        }
        LauncherBody {
            id: launcherBody
            wallpaperState: wallpaper.state
            wallpaperTexture: wallpaper.texture
            liquid: wallpaper.ready
            niri: scene.niri
            dock: dockStore
            identity: appIdentity
            barPolicy: barPolicy
            width: scene.panelWidth
            height: scene.panelHeight
            expansion: scene.expansion
            opened: scene.launcherOpen
            backdrop: scene.launcherBackdrop
            origin: Qt.point(panel.x, panel.y)
            logoBubble: bar.leftIslands.logoBubble
            onDismissed: scene.closeAll()
        }
    }
    // With a settings profile the core governs bar.* / dock.* (override or core
    // default, so undo to "unset" is visible); pinned stays in dock.json, whose
    // on/position/auto_hide then only mirror the core. Without a profile: env/dock.json.
    Connections {
        target: launcherBody.settings
        function onValuesChanged(): void {
            const s = launcherBody.settings;
            const core = key => s.profile ? s.value(key) : null;
            const auto = core("bar.autohide");
            if (typeof auto === "boolean")
                barPolicy.autoHide = auto;
            const ovws = core("bar.overview_workspaces");
            if (typeof ovws === "boolean")
                barPolicy.overviewWorkspaces = ovws;
            const on = core("dock.on");
            if (typeof on === "boolean")
                dockStore.on = on;
            const autoHide = core("dock.auto_hide");
            if (typeof autoHide === "boolean")
                dockStore.autoHide = autoHide;
        }
    }
    // What the clock panel's glass refracts (Surfaces sets it on Wayland; headless: flat).
    property DockBackdrop clockBackdrop: null
    // The clock panel grows out of the bar's clock island on liquid glass
    // (liquid-glass/clock.html): the drawer (media, month, notifications) or a peek.
    ClockPanel {
        id: clockPanel
        visible: scene.clockPresent
        viewportWidth: scene.viewportWidth
        viewportHeight: scene.viewportHeight
        expansion: scene.clockExpansion
        head: scene.clockHeadExpansion
        opened: scene.drawerOpen
        peekIds: scene.peekIds
        store: notes
        serverState: notificationService.state
        mediaEnabled: !scene.headless || Quickshell.env("EMAKI_TEST_MPRIS") === "1"
        today: bar.dateTime
        time: bar.clockTime
        date: bar.clockDate
        longDate: bar.clockLongDate
        backdrop: scene.clockBackdrop
        islandX: bar.x + bar.clockIsland.islandRect.x
        islandWidth: bar.clockIsland.islandWidth
        islandBubble: bar.clockIsland.bubble
        focus: scene.drawerOpen
        Keys.onEscapePressed: scene.closeAll()
        onActivated: scene.closeAll()
        onOpenHistory: scene.openDrawer()
        onToggle: scene.toggleDrawer()
    }
    // What the system panel's glass refracts (Surfaces sets it on Wayland; headless: flat) is
    // systemBackdrop above. The panel grows out of the bar's system island on liquid glass
    // (liquid-glass/system.html): 480 wide at the right edge, the island's cells as its head.
    SystemPanel {
        id: systemPanel
        visible: scene.systemPresent
        service: services
        niri: scene.niri
        tip: tip
        page: scene.systemPage
        opened: scene.systemOpen
        expansion: scene.systemExpansion
        viewportWidth: scene.viewportWidth
        viewportHeight: scene.viewportHeight
        backdrop: scene.systemBackdrop
        islandX: bar.x + bar.systemGlass.islandRect.x
        islandWidth: bar.systemGlass.islandWidth
        islandBubble: bar.systemGlass.bubble
        islandHoverKey: bar.systemGlass.hoverKey
        focus: scene.systemOpen
        Keys.onEscapePressed: scene.closeAll()
        onPageRequested: page => scene.openSystem(page)
    }
}
