// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import "ScreenShare.js" as ScreenShare

Item {
    id: scene
    required property string borderMode
    required property int reservedSpace
    required property bool headless
    required property int testWidth
    property real testScale: 1
    readonly property real outputScale: output?.devicePixelRatio ?? testScale
    required property int testHeight
    required property NiriService niri
    property ShellServices shared: null
    property var authenticationPrompt: null
    readonly property bool authenticationPresent: authenticationPrompt?.visible ?? false
    onAuthenticationPresentChanged: {
        if (authenticationPresent) {
            deferredPeekIds = Array.from(new Set(deferredPeekIds.concat(peekIds)));
            peekIds = [];
            peekTimer.stop();
            closeClock();
            pinnedNotes.expanded = false;
        } else {
            // Output migration transfers the held arrivals before replaying them.
            Qt.callLater(replayDeferredPeeks);
            Qt.callLater(showHeld);
        }
    }
    Connections {
        target: UpdateService
        function onOpening(): void {
            scene.closeAll();
        }
    }
    property bool focusedOutput: true
    onFocusedOutputChanged: {
        if (!focusedOutput && keyboardSurface)
            closeAll();
        Qt.callLater(showHeld);
    }
    readonly property ShellServices context: shared ?? localServices
    readonly property ShellServices localServices: shared ? null : (servicesFactory.createObject(scene) as ShellServices)
    readonly property SettingsCatalog settings: context.settings
    readonly property DockStore dockStore: context.dockStore
    readonly property SystemService services: context.services
    readonly property NotificationStore notes: context.notifications
    readonly property NotificationStore notifications: notes
    readonly property NotificationService notificationService: context.notificationService
    readonly property AppIdentity appIdentity: context.identity
    readonly property AppIdentity identity: appIdentity
    readonly property WindowLabels dockLabels: context.dockLabels
    Component {
        id: servicesFactory
        ShellServices {
            niri: scene.niri
            live: !scene.headless
            panelOpen: scene.systemOpen
            settingsController.targetHost: scene
        }
    }
    property bool skipIntro: false
    readonly property bool startupModelsReady: services.startupReady && niri.connected && dockStore.restored && (!dockStore.on || !dockLabels.wanted || dockLabels.state === "ready") && (!settings.busy && settings.state !== "idle") && !dock.animating && !bar.leftIslands.animating
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
        // The window switcher passes over the screen: it does not bring a hidden bar out.
        openUI: (scene.modalOpen && !scene.switcherOpen) || scene.peekOpen
        keyboardActive: scene.barKeyboardActive
        configuredReserve: scene.reservedSpace
        pointerInside: barHover.hovered
        edgeHovered: edgeHover.hovered
        onBarVisibleChanged: {
            tip.hide();
            if (!barVisible && scene.barKeyboardActive)
                scene.closeAll();
        }
    }
    Item {
        id: edge
        z: 5
        x: 56
        y: 0
        width: Math.max(0, scene.viewportWidth - 56)
        height: 2
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
    readonly property alias dockEdge: dockEdge
    // The menu of a dock item lives inside the dock (it grows out of the bubble).
    readonly property alias dockPopup: dock.popupView
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
    DockPolicy {
        id: dockPolicy
        on: scene.dockStore.on
        autoHide: scene.dockStore.autoHide
        overview: scene.niri.overviewOpen
        presentation: scene.presentationState
        popupOpen: dock.popupOpen
        dragging: dock.dragging || dock.externalDragging
        pointerInside: dock.pointerInside
        edgeHovered: dockEdgeHover.hovered
        edgeDragging: dockEdgeDrop.containsDrag
        onDockVisibleChanged: {
            if (!dockVisible) {
                dock.leaveKeyboard();
                if (scene.keyboardSurface === "dock")
                    scene.keyboardSurface = "";
            }
        }
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
        DropArea {
            id: dockEdgeDrop
            anchors.fill: parent
            onEntered: drag => {
                drag.accepted = drag.hasUrls;
            }
            onDropped: drop => {
                drop.accepted = false;
            }
        }
    }
    Dock {
        id: dock
        skipIntro: scene.skipIntro
        z: 6
        visible: scene.dockStore.on
        // The item is the dock surface; it slides the plate itself (dock.js visible tween).
        x: scene.dockPlacement.x
        y: scene.dockPlacement.y
        width: scene.dockWindowWidth
        height: scene.dockWindowHeight
        screenOrigin: scene.dockOrigin
        screenSize: Qt.size(scene.viewportWidth, scene.viewportHeight)
        store: scene.dockStore
        policy: dockPolicy
        niri: scene.niri
        labels: scene.dockLabels
        catalog: launcherBody.appCatalog
        identity: scene.appIdentity
        onAssignRequested: appId => {
            scene.openLauncher(dock.keyboardActive);
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
    property bool barKeyboardActive: false
    property Item keyboardReturnTarget: null
    property string keyboardSurface: ""
    property bool shortcutsOpen: false
    readonly property alias shortcuts: shortcuts
    // Alt+Tab keeps OnDemand until the compositor confirms its atomic focus transfer.
    property bool switcherOpen: false
    readonly property alias switcher: switcher
    signal switcherRestarted(int previousTicket)
    // `sent`: when the binding's process started (ms since the epoch; 0 unknown). A call sent
    // before the last switcher closed belonged to that chord and arrived late (each press starts
    // a process): a Tab pressed just before Alt was released must not open a second switcher.
    function switchWindows(delta: int, sameApp: bool, sent: double): bool {
        if (sent > 0 && sent <= switcher.closedAt)
            return true;
        if (switcherOpen) {
            const previousTicket = switcher.focusTicket;
            const restarting = !switcher.armed;
            const accepted = switcher.step(delta, sameApp);
            if (accepted && restarting)
                switcherRestarted(previousTicket);
            return accepted;
        }
        if (!enabled || !focusedOutput || (!output && !headless) || authenticationPresent || firstRunPresent || !niri.immediateFocusReady)
            return false;
        // Only what holds the keyboard closes; notification previews stay.
        if (modalOpen || keyboardSurface !== "")
            closeAll();
        if (!switcher.step(delta, sameApp))
            return false;
        keyboardSurface = "switcher";
        switcherOpen = true;
        Qt.callLater(() => {
            if (scene.switcherOpen)
                scene.switcher.forceActiveFocus();
        });
        return true;
    }
    // The overlay lost the keyboard (the lock screen, another surface): close without switching.
    function switcherKeyboardLost(): void {
        if (switcherOpen)
            switcher.cancel();
    }
    function switcherClosed(): void {
        if (!switcherOpen)
            return;
        switcherOpen = false;
        if (keyboardSurface === "switcher")
            keyboardSurface = "";
    }
    function switcherStatus(): string {
        return JSON.stringify({
            open: switcherOpen,
            armed: switcher.armed,
            shown: switcher.shown,
            keyboard: switcher.keyboardReady,
            populated: switcher.populated,
            pending: switcher.pendingFocus >= 0 ? switcher.pendingFocus : null,
            same_app: switcher.appOnly,
            ids: switcher.rows.map(row => row.id),
            index: switcher.index,
            selected: switcher.current?.id ?? null,
            live: Object.keys(switcher.live).map(Number),
            recent: Object.keys(switcher.recent).map(Number),
            outcome: switcher.outcome,
            panel: rectangle(switcher.panel)
        });
    }
    // IPC entry points and bar activation share the same controls and close path.
    function openKeyboard(surface: string): bool {
        if (!enabled || !focusedOutput || (!output && !headless))
            return false;
        if (["bar", "dock"].includes(surface) && !niri.layerFocusSupported)
            return false;
        if (["sound", "light", "power", "wifi", "bt", "kb", "tray"].includes(surface)) {
            const result = openSystem(surface, true);
            Qt.callLater(() => {
                if (scene.systemOpen)
                    scene.systemPanel.takeFocus(true);
            });
            return result;
        }
        if (surface === "clock" || surface === "notifications") {
            openDrawer();
            keyboardSurface = drawerOpen ? surface : "";
            Qt.callLater(() => {
                if (scene.drawerOpen) {
                    if (surface === "notifications")
                        scene.clockPanel.focusNewest();
                    else
                        scene.clockPanel.takeFocus();
                }
            });
            return true;
        }
        if (surface === "privacy") {
            togglePrivacy(true);
            return true;
        }
        if (!["bar", "dock", "shortcuts"].includes(surface))
            return false;
        closeAll();
        if (surface === "bar") {
            keyboardSurface = surface;
            barKeyboardActive = true;
            Qt.callLater(() => {
                if (scene.barKeyboardActive)
                    scene.bar.takeFocus();
            });
        } else if (surface === "dock") {
            if (!dockStore.on)
                return false;
            keyboardSurface = surface;
            dock.takeFocus();
        } else {
            keyboardSurface = surface;
            shortcutsOpen = true;
            Qt.callLater(() => {
                if (scene.shortcutsOpen)
                    shortcuts.takeFocus();
            });
        }
        return true;
    }
    readonly property alias pinnedNotes: pinnedNotes
    readonly property bool pinnedNotesOpen: pinnedNotes.expanded
    readonly property bool pinnedNotesPresent: pinnedNotes.expanded || pinnedNotes.expansion > 0
    property real pinnedNotesOrigin: 0
    property bool pinnedNotesAnchored: false
    function togglePinnedNotes(keyboard): void {
        if (pinnedNotesOpen) {
            closeAll();
            return;
        }
        if (!enabled || authenticationPresent || !NotesService.pinnedNotes.length)
            return;
        // Keep the clicked island's origin while closing the clock behind its new plate.
        pinnedNotesOrigin = pinnedNotes.x;
        pinnedNotesAnchored = true;
        closePanels(false);
        keyboardSurface = keyboard === true ? "notes" : "";
        pinnedNotes.expanded = true;
        Qt.callLater(() => {
            if (pinnedNotes.expanded)
                pinnedNotes.takeFocus(keyboard === true);
        });
    }
    onPinnedNotesPresentChanged: {
        if (!pinnedNotesPresent)
            pinnedNotesAnchored = false;
        Qt.callLater(showHeld);
    }
    NotesIsland {
        id: pinnedNotes
        z: 30
        width: implicitWidth
        height: implicitHeight
        readonly property real restingX: scene.clockPresent && clockPanel.visible ? clockPanel.x + clockPanel.width + 12 : bar.x + bar.clockIsland.islandRect.x + bar.clockIsland.islandWidth + 12
        x: Math.max(Metrics.side, Math.min(scene.viewportWidth - width - Metrics.side, scene.pinnedNotesAnchored ? scene.pinnedNotesOrigin : restingX))
        Behavior on x {
            enabled: !scene.pinnedNotesPresent && !scene.pinnedNotesAnchored
            NumberAnimation {
                duration: Metrics.morphMs
                easing.type: Easing.OutCubic
            }
        }
        y: Metrics.top + (scene.pinnedNotesPresent ? 0 : bar.y) - 70 * barPolicy.overviewShift
        visible: NotesService.pinnedNotes.length > 0 && scene.enabled && !scene.authenticationPresent && (barPolicy.normalIslands || scene.pinnedNotesPresent) && barPolicy.overviewShift < 1
        backdrop: scene.pinnedNotesPresent ? scene.clockBackdrop : bar.backdrop
        screenOrigin: Qt.point(x, y)
        onToggleRequested: scene.togglePinnedNotes(scene.barKeyboardActive)
        keyboardFocused: bar.notesFocused
        // Detached: the first launch keeps its scope helper waiting for seconds, and a
        // Process still running then would drop the next open.
        onOpenRequested: noteId => {
            scene.closeAll();
            Quickshell.execDetached(AppLaunch.command("emaki-notes", ["emaki-notes", "--note", noteId]));
        }
        // The last note was unpinned while the keyboard was in the island: give it to the bar.
        onVisibleChanged: if (!visible && scene.keyboardSurface === "notes" && !NotesService.pinnedNotes.length)
            scene.keyboardBack(false)
        Keys.onEscapePressed: scene.keyboardBack(false)
    }
    property bool clockPresent: false
    property real clockExpansion: 0
    property var peekIds: []
    property var deferredPeekIds: []
    property double peekStarted: 0
    property double peekUntil: 0
    property double peekCooldown: 0
    readonly property bool peekOpen: peekIds.length > 0
    readonly property bool pairingPeekOpen: peekIds.some(id => notes.entries.some(e => e.id === id && e.critical === true && !e.batteryWarning && e.object))
    readonly property bool batteryPeekOpen: peekIds.some(id => notes.entries.some(e => e.id === id && e.batteryWarning === true))
    readonly property bool modalOpen: launcherOpen || drawerOpen || systemOpen || privacyOpen || pinnedNotesOpen || shortcutsOpen || switcherOpen
    // A held notice (NotificationStore.hold: the Secure Boot notices) stays until the person
    // chooses its action or dismisses it. Its preview has no timer, and whenever the preview is
    // free again it comes back: nothing else previewed, no drawer or panel open, no
    // authentication prompt and no first-run window (Welcome). It never covers Welcome: it
    // waits and appears when Welcome closes. Like the battery warning it ignores Do not disturb,
    // the overview and full screen; the drawer shows its whole text meanwhile.
    property bool firstRunPresent: false
    readonly property bool heldPeekOpen: peekIds.some(id => isHeld(id))
    function isHeld(id: int): bool {
        return notes.entries.some(e => e.id === id && e.held === true);
    }
    function showHeld(): void {
        // The launcher (and Settings in it) sets launcherOpen a turn after closing the other
        // panels, and a closing panel is still present: wait for both.
        if (!enabled || !focusedOutput || authenticationPresent || firstRunPresent || peekOpen || modalOpen || launcherPresent || systemPresent || privacyPresent || pinnedNotesPresent)
            return;
        const held = notes.entries.find(e => e.held === true);
        if (!held)
            return;
        showNotification(held.id);
        if (heldPeekOpen) {
            // No deadline: nothing to count down in the status or to carry to another output
            // (the scene that gains focus shows it again).
            peekTimer.stop();
            peekUntil = Date.now();
        }
    }
    onFirstRunPresentChanged: {
        if (firstRunPresent && heldPeekOpen) {
            peekIds = peekIds.filter(id => !isHeld(id));
            if (!peekOpen)
                endPeek();
        }
        Qt.callLater(showHeld);
    }
    onPeekIdsChanged: Qt.callLater(showHeld)
    onModalOpenChanged: Qt.callLater(showHeld)
    onLauncherPresentChanged: Qt.callLater(showHeld)
    onSystemPresentChanged: Qt.callLater(showHeld)
    onPrivacyPresentChanged: Qt.callLater(showHeld)
    property bool systemOpen: false
    property bool systemPresent: false
    property real systemExpansion: 0
    property string systemPage: ""
    // Whether the panel's own glass source is live (Surfaces: the overlay's screen capture
    // has settled; true without a capture). Opening starts the capture; the panel stays hidden
    // and the island stays as it is until the capture is live, at most systemWarmup ms. Opened
    // over the wallpaper stand-in, its first frames drew a darker, purple plate on the GPU
    // path before the capture took over (0.8.1).
    property bool systemGlassLive: true
    property bool systemWarming: false
    // 100 ms timer: the reveal lands within 120 ms including scheduling (measured 126–128 ms with 120).
    readonly property int systemWarmupMs: 100
    onSystemGlassLiveChanged: {
        if (systemGlassLive && systemWarming)
            revealSystem();
    }
    Timer {
        id: systemWarmup
        interval: scene.systemWarmupMs
        onTriggered: scene.revealSystem()
    }
    function revealSystem(): void {
        systemWarmup.stop();
        if (!systemWarming)
            return;
        systemWarming = false;
        if (!systemOpen)
            return;
        systemExpansion = 1;
        const keyboard = keyboardSurface !== "";
        Qt.callLater(() => {
            if (scene.systemOpen)
                systemPanel.takeFocus(keyboard);
        });
    }
    readonly property alias systemPanel: systemPanel
    readonly property alias systemBody: systemPanel.body
    Connections {
        target: scene.services
        // Initial native values settle under the login cover. The open panel
        // already shows the value (mockup: panel === k ? paintSys : showOsd).
        function onOsdRequested(page: string): void {
            if (!scene.focusedOutput || scene.skipIntro || (scene.systemOpen && scene.systemPage === page))
                return;
            if (page === "sound")
                osd.show("sound", Math.round((scene.services.sinkVolume >= 0 ? scene.services.sinkVolume : 0) * 100), scene.services.sinkMuted, scene.services.sinkMuted ? qsTranslate("ShellScene", "Muted") : (scene.services.backend?.sink?.description || scene.services.backend?.sink?.name || qsTranslate("ShellScene", "Sound")));
            else
                osd.show("light", scene.services.brightness.percent ?? 0, false, qsTranslate("ShellScene", "Brightness"));
        }
    }
    // Privacy: capture streams from the backend plus screencasts from the core model.
    // A JS array assigned through a var property can come back as a sequence object without filter/some.
    readonly property var captures: Array.from(services.backend?.captures ?? [])
    // A video stream reading a cast's PipeWire node is that program watching the screen.
    readonly property var castNodes: ScreenShare.castNodes(niri.casts)
    // A video stream reading nothing (source -1) uses no camera either.
    readonly property var deviceCaptures: scene.captures.filter(c => c.kind === "mic" || c.kind === "cam" && c.source !== -1 && !scene.castNodes.includes(c.source))
    // Only another program seeing the screen right now (ScreenShare.js), named after the
    // stream reading the cast's node.
    readonly property var screenShares: ScreenShare.shares(niri.casts, Array.from(services.backend?.readers ?? []), [context.captureNode], word => DesktopEntries.heuristicLookup(word)?.name ?? "")
    readonly property var privacyRows: scene.deviceCaptures.map(c => ({
                kind: "info",
                what: c.kind,
                label: c.name || qsTranslate("ShellScene", "An app"),
                note: c.kind === "mic" ? qsTranslate("ShellScene", "Using the microphone") : qsTranslate("ShellScene", "Using the camera"),
                tag: "",
                id: 0
            })).concat(scene.screenShares.map(s => ({
                kind: "info",
                what: "cast",
                label: s.name,
                note: s.window ? qsTranslate("ShellScene", "Sharing a window") : qsTranslate("ShellScene", "Sharing the screen"),
                tag: "",
                id: 0
            })))
    readonly property bool privacyMic: scene.deviceCaptures.some(c => c.kind === "mic")
    readonly property bool privacyCam: scene.deviceCaptures.some(c => c.kind === "cam")
    readonly property bool privacyCast: scene.screenShares.length > 0
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
    function togglePrivacy(keyboard): void {
        if (privacyOpen) {
            closeAll();
            return;
        }
        closePanels(true);
        keyboardSurface = keyboard === true ? "privacy" : "";
        privacyOpen = true;
        privacyPresent = true;
        privacyExpansion = 1;
        Qt.callLater(() => {
            if (scene.privacyOpen)
                privacyPopup.takeFocus(keyboard === true);
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
        keyboardFocused: bar.privacyFocused
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
        Keys.onEscapePressed: scene.keyboardBack(false)
        onToggle: {
            if (keyboardMode)
                scene.keyboardBack(false);
            else
                scene.togglePrivacy();
        }
    }
    readonly property alias osd: osd
    Osd {
        id: osd
        z: 35
        x: scene.viewportWidth - Metrics.side - width
        y: 52
        backdrop: scene.systemBackdrop
    }
    function openSystem(page: string, keyboard): bool {
        if (!["sound", "light", "power", "wifi", "bt", "kb", "tray"].includes(page))
            return false;
        if (systemOpen && systemPage === page) {
            closeSystem();
            return true;
        }
        if (systemOpen) {
            // Another page while open: keep the panel; the tab flows to the new cell and the
            // height springs to the new page (system.js rebuilds the view at once).
            tip.hide();
            systemBody.reset();
            systemPage = page;
            keyboardSurface = keyboard === true ? page : "";
            systemPanel.takeFocus(keyboard === true);
            return true;
        }
        closePanels(true);
        keyboardSurface = keyboard === true ? page : "";
        systemPage = page;
        systemOpen = true;
        systemPresent = true;
        systemWarming = true;
        if (systemGlassLive)
            revealSystem();
        else
            systemWarmup.restart();
        return true;
    }
    // Log out, Restart or Shut down from the command menu: the power panel's own question, with
    // Cancel focused (SystemBody.session settles it so the asking Enter or click never answers).
    function confirmSession(action: string, keyboard): bool {
        if (!(systemOpen && systemPage === "power") && !openSystem("power", keyboard === true))
            return false;
        systemBody.session(action);
        systemPanel.takeFocus(keyboard === true);
        return true;
    }
    function closeSystem(): void {
        keyboardReturnTarget = null;
        systemWarming = false;
        systemOpen = false;
        keyboardSurface = "";
        systemExpansion = 0;
        systemBody.reset();
        // A pairing request remains actionable when its settings panel closes.
        closeTimer.restart();
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
    readonly property alias clockPanel: clockPanel
    readonly property alias clockBody: clockPanel.body
    Connections {
        target: scene.notes
        function onArrived(id: int): void {
            if (scene.isHeld(id)) {
                Qt.callLater(scene.showHeld);
            } else if (scene.focusedOutput) {
                // Another arrival takes the preview from a held notice for its own few seconds
                // (the held one comes back when it ends); one that is not previewed (Do not
                // disturb, a panel open) leaves the held preview as it was.
                const held = scene.peekIds.filter(peekId => scene.isHeld(peekId));
                if (held.length)
                    scene.peekIds = scene.peekIds.filter(peekId => !scene.isHeld(peekId));
                scene.showNotification(id);
                if (held.length && !scene.peekOpen)
                    scene.peekIds = held;
            }
        }
        function onEffectiveDndChanged(): void {
            if (scene.notes.effectiveDnd)
                scene.retainCriticalPeek();
        }
        function onRulesChanged(): void {
            scene.peekIds = scene.peekIds.filter(id => scene.notes.entries.some(e => e.id === id && scene.notes.peekAllowed(e)));
            if (!scene.peekOpen)
                scene.endPeek();
        }
        function onEntriesChanged(): void {
            if (scene.peekOpen) {
                scene.peekIds = scene.peekIds.filter(id => scene.notes.entries.some(n => n.id === id && (!n.critical || n.batteryWarning || n.sessionUpdate || n.clockAlert || n.held === true || n.object)));
                if (!scene.peekOpen)
                    scene.endPeek();
            }
        }
    }
    onPresentationStateChanged: {
        if (keyboardSurface)
            closeAll();
        if (presentationState !== "clear")
            retainCriticalPeek();
    }
    // Head of the clock panel: 36 px with the compact time in a peek, 70 px with the big
    // time in the drawer. Animated on its own so closing mirrors opening (drawerOpen flips
    // at once, the head must not).
    readonly property real headTarget: drawerOpen && !batteryPeekOpen && !pairingPeekOpen ? 1 : 0
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
        if (keyboardSurface === "clock" || keyboardSurface === "notifications")
            keyboardSurface = "";
        endPeek();
        clockExpansion = pairingPeekOpen ? 1 : 0;
        closeTimer.restart();
    }
    function endPeek(): void {
        if (peekOpen)
            peekCooldown = Date.now() + Metrics.morphMs;
        // A live pairing request lasts until its sender closes or expires it.
        peekIds = peekIds.filter(id => notes.entries.some(e => e.id === id && e.critical === true && !e.batteryWarning && e.object));
        peekTimer.stop();
        if (pairingPeekOpen)
            return;
        if (!drawerOpen) {
            clockExpansion = 0;
            closeTimer.restart();
        }
    }
    function retainCriticalPeek(): void {
        peekIds = peekIds.filter(id => notes.entries.some(e => e.id === id && e.critical === true));
        if (!peekOpen)
            endPeek();
    }
    function showNotification(id: int): void {
        if (!focusedOutput)
            return;
        if (!notes.entries.some(e => e.id === id && notes.peekAllowed(e)))
            return;
        if (authenticationPresent) {
            if (!deferredPeekIds.includes(id))
                deferredPeekIds = deferredPeekIds.concat([id]);
            return;
        }
        const time = Date.now();
        // Intentional alarm and timer cards carry the same quiet-mode exemption.
        const critical = notes.entries.some(e => e.id === id && (e.critical === true || e.clockAlert === true));
        const pairing = notes.entries.some(e => e.id === id && e.critical === true && !e.batteryWarning && e.object);
        if (!critical && (niri.overviewOpen || modalOpen || notes.effectiveDnd || presentationState !== "clear" || time < peekCooldown))
            return;
        // A new pairing request replaces the current one so its actions are visible.
        // Other arrivals remain in the drawer while a request is pending.
        if ((pairingPeekOpen && !pairing) || (!critical && peekIds.some(peekId => notes.entries.some(e => e.id === peekId && e.critical === true))))
            return;
        if (critical)
            peekIds = [];
        if (!peekOpen || critical)
            peekStarted = time;
        if (!critical && time >= peekStarted + 8000) {
            endPeek();
            return;
        }
        peekIds = peekIds.concat([id]);
        clockPresent = true;
        clockExpansion = 1;
        peekUntil = critical ? time + 15000 : Math.min(time + 4000, peekStarted + 8000);
        peekTimer.interval = Math.max(1, peekUntil - time);
        peekTimer.restart();
    }
    function replayDeferredPeeks(): void {
        if (authenticationPresent || !focusedOutput)
            return;
        const ids = deferredPeekIds;
        deferredPeekIds = [];
        // A held notice comes back through showHeld, without a timer.
        for (const id of ids)
            if (!isHeld(id))
                showNotification(id);
    }
    // Move transient feedback without replaying an arrival or extending its deadline.
    function exportTransient(): var {
        const state = {
            ids: peekIds.slice(),
            deferredIds: deferredPeekIds.slice(),
            started: peekStarted,
            until: peekUntil,
            cooldown: peekCooldown,
            osd: osd.exportState()
        };
        peekTimer.stop();
        peekIds = [];
        deferredPeekIds = [];
        if (!drawerOpen) {
            clockExpansion = 0;
            clockPresent = false;
        }
        return state;
    }
    function importTransient(state: var): void {
        if (!state)
            return;
        const now = Date.now();
        peekCooldown = state.cooldown;
        peekStarted = state.started;
        peekUntil = state.until;
        peekIds = state.ids.filter(id => notes.entries.some(e => e.id === id && notes.peekAllowed(e) && (state.until > now || (e.critical && !e.batteryWarning && e.object))));
        deferredPeekIds = Array.from(new Set(deferredPeekIds.concat(state.deferredIds ?? [])));
        if (authenticationPresent) {
            deferredPeekIds = Array.from(new Set(deferredPeekIds.concat(peekIds)));
            peekIds = [];
        }
        if (niri.overviewOpen || modalOpen || notes.effectiveDnd || presentationState !== "clear")
            retainCriticalPeek();
        if (peekOpen) {
            clockPresent = true;
            clockExpansion = 1;
            if (peekUntil > now) {
                peekTimer.interval = Math.max(1, peekUntil - now);
                peekTimer.restart();
            }
        }
        osd.importState(state.osd);
        Qt.callLater(replayDeferredPeeks);
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
        refreshManaged: true
        outputWidth: Math.round(scene.headless ? scene.viewportWidth : scene.output?.width ?? 0)
        outputHeight: Math.round(scene.headless ? scene.viewportHeight : scene.output?.height ?? 0)
        outputScale: scene.outputScale
        outputName: scene.outputName
    }
    readonly property alias bar: bar
    readonly property alias barHoldBand: barHoldBand
    readonly property alias panel: panel
    readonly property alias input: launcherBody
    // Future drawers/panels/dock popups subscribe here, not directly to niri.
    signal closeAllRequested
    readonly property real viewportWidth: headless ? testWidth : Math.min(output?.width ?? 0, testWidth || Infinity)
    readonly property real viewportHeight: headless ? testHeight : Math.min(output?.height ?? 0, testHeight || Infinity)
    readonly property real settingsWidthLimit: Math.max(Metrics.islandHeight, Math.min(viewportWidth - 60, bar.systemGlass.islandRect.x - Metrics.logoX - Metrics.side))
    readonly property real panelWidth: launcherBody.settingsActive ? Math.min(Metrics.settingsWidth, settingsWidthLimit) : Math.min(Metrics.launcherWidth, Math.max(36, viewportWidth - Metrics.logoX - Metrics.side))
    // Settings use the larger approved proportions, with room around every output edge.
    readonly property real panelHeight: Math.min(launcherBody.desiredHeight, Math.max(36, viewportHeight - (launcherBody.settingsActive ? 74 : Metrics.top + Metrics.side)))

    function openLauncher(keyboard): void {
        if (!enabled || (!output && !headless))
            return;
        // A new opening interrupts the retained close frame with fresh launcher state.
        if (launcherPresent && !launcherOpen)
            launcherBody.reset();
        if (!launcherOpen)
            closePanels(true);
        if (launcherOpen) {
            launcherBody.takeFocus(keyboard === true);
            return;
        }
        keyboardSurface = keyboard === true ? "launcher" : "";
        launcherPresent = true;
        const serial = ++requestSerial;
        // Give the separate overlay the same first frame as the compact logo.
        Qt.callLater(() => {
            if (!scene.launcherPresent || serial !== scene.requestSerial)
                return;
            scene.launcherOpen = true;
            scene.expansion = 1;
            launcherBody.takeFocus(keyboard === true);
        });
    }
    function closeLauncher(): void {
        ++requestSerial;
        if (keyboardSurface === "launcher")
            keyboardSurface = "";
        launcherOpen = false; // Release keyboard immediately; compositor restores underlying focus.
        expansion = 0;
        launcherBody.settingsView?.flushPending();
        closeTimer.restart();
    }
    function closeAll(): void {
        closePanels(false);
    }
    function keyboardBack(systemOnly: bool): void {
        const target = keyboardReturnTarget;
        if (systemOnly)
            closeSystem();
        else
            closeAll();
        if (!target || !enabled || !focusedOutput || !niri.layerFocusSupported || (barPolicy.fullscreen && !niri.overviewOpen))
            return;
        keyboardSurface = "bar";
        // Before barKeyboardActive: Surfaces may activate the bar at once and call takeFocus().
        bar.returnItem = target;
        bar.returnUntil = Date.now() + 2000;
        barKeyboardActive = true;
        closeTimer.start();
        Qt.callLater(() => {
            if (scene.barKeyboardActive)
                bar.takeFocus();
        });
    }
    function closePanels(preserveBattery: bool): void {
        tip.hide();
        keyboardReturnTarget = null;
        bar.returnItem = null;
        keyboardSurface = "";
        barKeyboardActive = false;
        shortcutsOpen = false;
        switcher.cancel();
        switcherOpen = false;
        dock.leaveKeyboard();
        closeLauncher();
        if (!preserveBattery || !batteryPeekOpen)
            closeClock();
        else
            drawerOpen = false;
        systemWarming = false;
        systemOpen = false;
        systemExpansion = 0;
        systemBody.reset();
        dock.closePopup();
        privacyOpen = false;
        privacyExpansion = 0;
        pinnedNotes.expanded = false;
        closeAllRequested();
    }
    function outsidePress(x: real, y: real): void {
        outsidePressButton(x, y, Qt.LeftButton);
    }
    function outsidePressButton(x: real, y: real, button: int): void {
        const logo = bar.logo;
        // An open shade owns the overlay input; the neighbouring bar glyph is below it.
        if (!pinnedNotesPresent && pinnedNotes.visible && x >= pinnedNotes.x && x < pinnedNotes.x + pinnedNotes.width && y >= pinnedNotes.y && y < pinnedNotes.y + pinnedNotes.height) {
            togglePinnedNotes(false);
            return;
        }
        if (!launcherPresent && x >= logo.x && x < logo.x + logo.width && y >= logo.y && y < logo.y + logo.height)
            openLauncher();
        else if (!dockPressAt(x, y, button)) {
            if (systemOpen)
                closeSystem();
            else
                closeAll();
        }
    }
    function toggleLauncher(keyboard): void {
        if (launcherOpen)
            closeAll();
        else
            openLauncher(keyboard);
    }
    Connections {
        target: scene.niri
        function onLayerFocusSupportedChanged(): void {
            if (!scene.niri.layerFocusSupported && scene.keyboardSurface)
                scene.closeAll();
        }
        function onOverviewOpenChanged(): void {
            if (scene.niri.overviewOpen)
                scene.closePanels(true);
        }
    }
    onOutputChanged: {
        closeAll();
        launcherPresent = false;
    }
    onEnabledChanged: if (!enabled)
        closeAll()
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
            const returning = bar.settleReturn();
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
            if (!returning && (!scene.systemPresent || scene.systemOpen) && (!scene.launcherPresent || scene.launcherOpen) && (!scene.clockPresent || scene.drawerOpen || scene.peekOpen) && (!scene.privacyPresent || scene.privacyOpen))
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
            services: scene.services.status(),
            system_page: systemOpen ? systemPage : "closed",
            system_panel: systemOpen ? "open" : systemPresent ? "closing" : "closed",
            system_expansion: systemExpansion,
            clock_expansion: clockExpansion,
            clock_head: clockHeadExpansion,
            privacy: {
                mic: scene.deviceCaptures.filter(c => c.kind === "mic").length,
                cam: scene.deviceCaptures.filter(c => c.kind === "cam").length,
                cast: scene.screenShares.length,
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
                dnd: notes.effectiveDnd,
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
            // Which surface owns the keyboard and the bar cell that holds it (live checks).
            keyboard: {
                surface: keyboardSurface,
                bar: bar.keyboardItem ? bar.keyboardName() : null
            },
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
                edge_hovered: dockPolicy.edgeHovered,
                edge_dragging: dockPolicy.edgeDragging,
                external_dragging: dock.externalDragging,
                reveal_delay_ms: dockPolicy.revealDelayMs,
                hide_delay_ms: dockPolicy.hideDelayMs,
                slide_duration_ms: dockPolicy.slideDurationMs,
                visible_amount: dock.visibleAmount,
                output: outputName,
                edge_rect: {
                    x: dockOrigin.x + dockEdgeLocal.x,
                    y: dockOrigin.y + dockEdgeLocal.y,
                    width: dockEdgeLocal.width,
                    height: dockEdgeLocal.height
                },
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
                auto_hide_fullscreen: barPolicy.autoHideFullscreen,
                hide_mode: barPolicy.hideMode,
                reveal_delay_ms: barPolicy.revealDelayMs,
                hide_delay_ms: barPolicy.hideDelayMs,
                slide_duration_ms: barPolicy.slideDurationMs,
                overview_workspaces: barPolicy.overviewWorkspaces,
                overview_shift: barPolicy.overviewShift,
                visible: barPolicy.barVisible,
                normal_islands: barPolicy.normalIslands,
                workspace_island: barPolicy.workspaceIsland,
                edge_enabled: barPolicy.edgeEnabled,
                fullscreen_inset: barPolicy.fullscreenInset,
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
        policy: barPolicy
        skipIntro: scene.skipIntro
        tip: tip
        // Keep the hold band under this common hover ancestor so island hover targets
        // continue receiving events. Only pointer reveals extend input through the gaps.
        Item {
            id: barHoldBand
            width: bar.width
            height: Metrics.top + Metrics.islandHeight
            visible: barPolicy.hidesHere && barPolicy.barVisible && barPolicy.pointerRevealed && !barPolicy.overview
        }
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
        launcherRight: scene.launcherPresent ? Metrics.logoX + launcherBody.shownWidth : 0
        launcherExpansion: scene.launcherPresent ? scene.expansion : 0
        // An island stays drawn under its growing panel (the overlay, above the bar) until the
        // panel is half open: hiding it at once left 30–60 ms with neither drawn (27.09).
        clockPresent: (scene.authenticationPrompt?.clockCovered ?? false) || (scene.clockPresent && scene.clockExpansion > .5)
        notificationCount: scene.notes.count
        dnd: scene.notes.effectiveDnd
        services: scene.services
        notesItem: pinnedNotes
        onNotesClicked: {
            const target = keyboardItem;
            scene.togglePinnedNotes(true);
            scene.keyboardReturnTarget = target;
        }
        privacyItem: privacy
        onPrivacyClicked: {
            const target = keyboardItem;
            scene.togglePrivacy(true);
            scene.keyboardReturnTarget = target;
        }
        systemPresent: scene.systemPresent && scene.systemExpansion > .5
        onSystemClicked: page => {
            const target = keyboardItem;
            scene.openSystem(page, scene.barKeyboardActive);
            scene.keyboardReturnTarget = target;
        }
        onClockClicked: {
            const target = keyboardItem;
            if (scene.barKeyboardActive) {
                scene.openKeyboard("clock");
                scene.keyboardReturnTarget = target;
            } else
                scene.toggleDrawer();
        }
        onLaunch: {
            const target = keyboardItem;
            scene.toggleLauncher(scene.barKeyboardActive);
            scene.keyboardReturnTarget = target;
        }
        keyboardBoundary: scene.barKeyboardActive
        onLeaveKeyboard: scene.closeAll()
    }
    // What the launcher's glass refracts (Surfaces sets it on Wayland; headless: flat).
    property DockBackdrop launcherBackdrop: null
    // The launcher grows out of the bar's button on liquid glass (liquid-glass/launcher.html):
    ShortcutSheet {
        id: shortcuts
        visible: scene.shortcutsOpen
        x: Math.max(16, (scene.viewportWidth - width) / 2)
        y: Math.max(16, (scene.viewportHeight - height) / 2)
        width: Math.min(760, scene.viewportWidth - 32)
        height: Math.min(680, scene.viewportHeight - 32)
        z: 30
        wallpaperTexture: LiquidPalette.sharedCanvas ? wallpaper.texture : ""
        dpr: scene.outputScale
        onCloseRequested: scene.closeAll()
    }
    WindowSwitcher {
        id: switcher
        z: 35
        niri: scene.niri
        labels: scene.dockLabels
        identity: scene.appIdentity
        viewportWidth: scene.viewportWidth
        viewportHeight: scene.viewportHeight
        outputName: scene.outputName
        wallpaperTexture: wallpaper.texture
        dpr: scene.outputScale
        focus: scene.switcherOpen
        onFocusRequested: id => {
            focusTicket = scene.niri.focusWindowImmediate(id);
            if (focusTicket < 0)
                finishHandoff();
        }
        onFinished: scene.switcherClosed()
    }
    Connections {
        target: scene.niri
        function onImmediateFocusFailed(ticket: int): void {
            if (switcher.focusTicket === ticket)
                switcher.finishHandoff();
        }
    }
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
            settingsController: scene.context.settingsController
            sharedCatalog: scene.context.catalog
            sharedClipboard: scene.context.clipboard
            wallpaperState: wallpaper.state
            wallpaperTexture: wallpaper.texture
            liquid: wallpaper.ready
            niri: scene.niri
            identity: scene.appIdentity
            width: scene.panelWidth
            maximumWidth: settingsActive ? scene.settingsWidthLimit : Infinity
            height: scene.panelHeight
            expansion: scene.expansion
            opened: scene.launcherOpen
            backdrop: scene.launcherBackdrop
            origin: Qt.point(panel.x, panel.y)
            logoBubble: bar.leftIslands.logoBubble
            onBackRequested: scene.keyboardBack(false)
            onDismissed: scene.closeAll()
            onOpenFailed: reason => scene.notifications.local("Launcher", reason, "")
            onSettingsRequested: scene.context.settingsController.openOn(scene, "")
            onSettingsPageRequested: page => scene.context.settingsController.openOn(scene, page)
            onSessionRequested: action => scene.confirmSession(action, launcherBody.keyboardMode)
        }
    }
    // Each output mirrors the shared snapshot, including outputs added later.
    function applyManagedSettings(): void {
        const auto = scene.settings.value("bar.autohide");
        if (typeof auto === "boolean")
            barPolicy.autoHide = auto;
        const full = scene.settings.value("bar.autohide_fullscreen");
        barPolicy.autoHideFullscreen = typeof full === "boolean" ? full : true;
        const ovws = scene.settings.value("bar.overview_workspaces");
        if (typeof ovws === "boolean")
            barPolicy.overviewWorkspaces = ovws;
    }
    Component.onCompleted: applyManagedSettings()
    Connections {
        target: scene.settings
        function onValuesChanged(): void {
            scene.applyManagedSettings();
        }
    }
    // What the clock panel's glass refracts (Surfaces sets it on Wayland; headless: flat).
    property DockBackdrop clockBackdrop: null
    // The clock panel grows out of the bar's clock island on liquid glass
    // (liquid-glass/clock.html): the drawer (media, month, notifications) or a peek.
    ClockPanel {
        id: clockPanel
        clockTools: scene.context.clockTools
        z: scene.batteryPeekOpen || scene.pairingPeekOpen ? 40 : 0
        visible: scene.clockPresent && !(scene.authenticationPrompt?.clockCovered ?? false)
        viewportWidth: scene.viewportWidth
        viewportHeight: scene.viewportHeight
        expansion: scene.clockExpansion
        head: scene.clockHeadExpansion
        opened: scene.drawerOpen && !scene.batteryPeekOpen && !scene.pairingPeekOpen
        peekIds: scene.peekIds
        hidePreviewBodies: scene.privacyCast
        store: scene.notes
        serverState: scene.notificationService.state
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
        Keys.onEscapePressed: scene.keyboardBack(false)
        onActivated: scene.closeAll()
        onOpenHistory: scene.openDrawer()
        onToggle: scene.toggleDrawer()
    }
    // What the system panel's glass refracts (Surfaces sets it on Wayland; headless: flat) is
    // systemBackdrop above. The panel grows out of the bar's system island on liquid glass
    // (liquid-glass/system.html): 480 wide at the right edge, the island's cells as its head.
    SystemPanel {
        id: systemPanel
        visible: scene.systemPresent && !scene.systemWarming
        service: scene.services
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
        islandHoverKey: bar.systemGlass.dropKey
        focus: scene.systemOpen
        Keys.onEscapePressed: scene.keyboardBack(true)
        onPageRequested: page => scene.openSystem(page, systemPanel.keyboardMode)
        onSettingsRequested: page => scene.context.settingsController.openOn(scene, page)
    }
}
