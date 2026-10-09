pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell
import Quickshell.Wayland

// Loaded only on Wayland. The review controller can run headlessly without this backend.
Scope {
    id: surfaces
    required property ShellScene controller
    property SessionStartup startup: null
    property bool sharedFocus: false
    property bool reportFrames: true
    signal painted(string name)
    readonly property bool pairingReady: !pairingKeyboard || (surfaces.pendingLayer !== "panel" && overlay.contentItem.Window.active)
    readonly property bool keyboardPanel: controller.modalOpen && controller.keyboardSurface !== ""
    property string pendingLayer: ""
    property string armedLayer: ""
    readonly property int barKeyboardFocus: top.WlrLayershell.keyboardFocus
    readonly property int dockKeyboardFocus: dockWindow.WlrLayershell.keyboardFocus
    readonly property int panelKeyboardFocus: overlay.WlrLayershell.keyboardFocus
    // Exclusive requests focus on the retained surface. Once acknowledged, the
    // fork preserves that focus across OnDemand so outside focus actions can yield.
    function layerActive(layer: string): bool {
        return layer === "bar" ? top.contentItem.Window.active : layer === "dock" ? dockWindow.contentItem.Window.active : overlay.contentItem.Window.active;
    }
    function acquireKeyboard(layer: string): void {
        armedLayer = "";
        if (!controller.niri.layerFocusSupported || !controller.focusedOutput) {
            pendingLayer = "";
            return;
        }
        pendingLayer = layer;
        if (layerActive(layer))
            keyboardActiveChanged(layer, true);
    }
    function releaseKeyboard(layer: string): void {
        if (pendingLayer === layer)
            pendingLayer = "";
        if (armedLayer === layer)
            armedLayer = "";
    }
    Connections {
        target: surfaces.controller.niri
        function onLayerFocusSupportedChanged(): void {
            if (!surfaces.controller.niri.layerFocusSupported) {
                surfaces.pendingLayer = "";
                surfaces.armedLayer = "";
            } else if (surfaces.keyboardPanel || surfaces.pairingKeyboard) {
                surfaces.acquireKeyboard("panel");
            }
        }
    }
    function keyboardActiveChanged(layer: string, active: bool): void {
        if (!controller.niri.layerFocusSupported)
            return;
        const wanted = layer === "bar" ? controller.barKeyboardActive : layer === "dock" ? controller.dock.keyboardActive : keyboardPanel;
        if (active && (wanted || (layer === "panel" && pairingKeyboard))) {
            if (pendingLayer === layer)
                pendingLayer = "";
            armedLayer = layer;
            if (layer === "bar")
                controller.bar.takeFocus();
            else if (layer === "dock")
                controller.dock.takeFocus();
            else if (controller.launcherOpen)
                controller.input.takeFocus(true);
            else if (controller.systemOpen) {
                if (wanted)
                    controller.systemPanel.takeFocus(true);
                else
                    controller.systemPanel.forceActiveFocus();
            } else if (controller.privacyOpen)
                controller.privacyPopup.takeFocus(true);
            else if (controller.shortcutsOpen)
                controller.shortcuts.takeFocus();
            else if (controller.drawerOpen) {
                if (controller.keyboardSurface === "notifications")
                    controller.clockPanel.focusNewest();
                else
                    controller.clockPanel.takeFocus();
            }
        } else if (!active && armedLayer === layer) {
            armedLayer = "";
            // The native pairing prompt owns focus until its reply. Its panel
            // remains available without stealing focus back.
            if (wanted && !(layer === "panel" && pairingKeyboard))
                controller.closeAll();
        }
    }
    onKeyboardPanelChanged: {
        if (keyboardPanel)
            acquireKeyboard("panel");
        else if (!pairingKeyboard)
            releaseKeyboard("panel");
    }
    Connections {
        target: surfaces.controller
        function onFocusedOutputChanged(): void {
            if (!surfaces.controller.focusedOutput) {
                surfaces.pendingLayer = "";
                surfaces.armedLayer = "";
            } else if (surfaces.pairingKeyboard) {
                surfaces.acquirePairingKeyboard();
            }
        }
        function onBarKeyboardActiveChanged(): void {
            if (surfaces.controller.barKeyboardActive)
                surfaces.acquireKeyboard("bar");
            else
                surfaces.releaseKeyboard("bar");
        }
    }
    Connections {
        target: surfaces.controller.dock
        function onKeyboardActiveChanged(): void {
            if (surfaces.controller.dock.keyboardActive)
                surfaces.acquireKeyboard("dock");
            else
                surfaces.releaseKeyboard("dock");
        }
    }
    readonly property bool pairingKeyboard: controller.systemOpen && controller.systemPage === "bt" && (controller.systemBody.rows.some(r => r.action === "bt-cancel-pair") || controller.services.pendingKind === "bt-pair")
    onPairingKeyboardChanged: acquirePairingKeyboard()
    function acquirePairingKeyboard(): void {
        if (pairingKeyboard)
            acquireKeyboard("panel");
        else if (!keyboardPanel)
            releaseKeyboard("panel");
    }
    Binding {
        target: surfaces.controller.services
        when: !surfaces.sharedFocus
        property: "pairingFocusManaged"
        value: true
    }
    Binding {
        target: surfaces.controller.services
        when: !surfaces.sharedFocus
        property: "pairingFocusReady"
        value: surfaces.pairingReady
    }
    readonly property bool barMapped: top.visible && (top.contentItem.Window.window?.visible ?? false)
    readonly property bool dockMapped: dockWindow.visible && (dockWindow.contentItem.Window.window?.visible ?? false)
    readonly property bool overlayMapped: overlay.visible && (overlay.contentItem.Window.window?.visible ?? false)
    readonly property bool wallpaperSettled: dockWallpaper.state !== "idle" && dockWallpaper.state !== "loading"
    readonly property bool liveMaterialsReady: (!controller.barPolicy.barVisible || barBackdrop.ready) && (!controller.dockStore.on || controller.dock.visibleAmount === 0 || dockBackdrop.ready)
    readonly property bool wallMaterialsReady: (!controller.barPolicy.barVisible || ((barWallBackdrop.item as DockBackdrop)?.ready ?? false)) && (!controller.dockStore.on || controller.dock.visibleAmount === 0 || ((dockWallBackdrop.item as DockBackdrop)?.ready ?? false))
    readonly property bool materialsReady: wallpaperSettled && liveMaterialsReady && (!dockWallpaper.ready || dockBackdrop.mode !== "capture" || wallMaterialsReady)
    // Seal a failed-start fallback before the first revealed frame. A late
    // decoder/capture must not turn a flat strip into glass after the drain.
    // Ordinary starts and successful handoffs keep the existing live pipeline.
    property string startupMaterial: ""
    readonly property string backdropMode: dockBackdrop.mode
    function sealStartupMaterial(): void {
        if (!(startup?.enabled ?? false) || startupMaterial)
            return;
        startupMaterial = materialsReady ? "live" : wallMaterialsReady ? "wallpaper" : "flat";
    }
    function selectBackdrop(live: DockBackdrop, wall: DockBackdrop): DockBackdrop {
        if (startupMaterial === "flat")
            return null;
        if (startupMaterial === "wallpaper")
            return wall;
        return live.ready || !wall ? live : wall;
    }
    Connections {
        target: surfaces.startup
        function onRequestFrames(): void {
            top.contentItem.Window.window?.update();
            dockWindow.contentItem.Window.window?.update();
            overlay.contentItem.Window.window?.update();
        }
    }
    Connections {
        target: top.contentItem.Window.window
        function onActiveChanged(): void {
            surfaces.keyboardActiveChanged("bar", top.contentItem.Window.active);
        }
        function onFrameSwapped(): void {
            surfaces.painted("bar");
            if (surfaces.reportFrames)
                surfaces.startup?.painted("bar");
        }
    }
    Connections {
        target: dockWindow.contentItem.Window.window
        function onActiveChanged(): void {
            surfaces.keyboardActiveChanged("dock", dockWindow.contentItem.Window.active);
        }
        function onFrameSwapped(): void {
            surfaces.painted("dock");
            if (surfaces.reportFrames)
                surfaces.startup?.painted("dock");
        }
    }
    Connections {
        target: overlay.contentItem.Window.window
        function onActiveChanged(): void {
            surfaces.keyboardActiveChanged("panel", overlay.contentItem.Window.active);
        }
        function onFrameSwapped(): void {
            surfaces.painted("overlay");
            if (surfaces.reportFrames)
                surfaces.startup?.painted("overlay");
        }
    }
    Component.onCompleted: {
        controller.bar.parent = top.contentItem;
        controller.edge.parent = top.contentItem;
        controller.privacy.parent = top.contentItem;
        controller.privacyPopup.parent = overlay.contentItem;
        controller.tip.parent = overlay.contentItem;
        controller.panel.parent = overlay.contentItem;
        controller.clockPanel.parent = overlay.contentItem;
        controller.systemPanel.parent = overlay.contentItem;
        controller.shortcuts.parent = overlay.contentItem;
        controller.osd.parent = overlay.contentItem;
        controller.dock.parent = dockWindow.contentItem;
        controller.dockEdge.parent = dockWindow.contentItem;
        dockBackdrop.parent = dockWindow.contentItem;
        // A capture starts anew each time its glass shows (DockBackdrop): until its first
        // frame every glass refracts the still wallpaper instead of the flat stand-in.
        controller.dock.backdrop = Qt.binding(() => surfaces.selectBackdrop(dockBackdrop, dockWallBackdrop.item));
        dockWallBackdrop.parent = dockWindow.contentItem;
        controller.dock.wallBackdrop = Qt.binding(() => surfaces.startupMaterial === "flat" ? null : dockWallBackdrop.item ?? controller.dock.backdrop);
        // The left islands and the launcher draw the same glass as the dock, each over its
        // own surface's backdrop (a texture belongs to one window).
        barBackdrop.parent = top.contentItem;
        barWallBackdrop.parent = top.contentItem;
        controller.bar.backdrop = Qt.binding(() => surfaces.selectBackdrop(barBackdrop, barWallBackdrop.item));
        launcherBackdrop.parent = overlay.contentItem;
        launcherWallBackdrop.parent = overlay.contentItem;
        controller.launcherBackdrop = Qt.binding(() => launcherBackdrop.ready || !launcherWallBackdrop.item ? launcherBackdrop : launcherWallBackdrop.item);
        clockBackdrop.parent = overlay.contentItem;
        clockWallBackdrop.parent = overlay.contentItem;
        controller.clockBackdrop = Qt.binding(() => clockBackdrop.ready || !clockWallBackdrop.item ? clockBackdrop : clockWallBackdrop.item);
        // The system island and the privacy pill are islands of the bar: its backdrop. The
        // system panel, the OSD and the privacy panel share the overlay's right-hand one.
        controller.privacy.backdrop = Qt.binding(() => controller.bar.backdrop);
        systemBackdrop.parent = overlay.contentItem;
        systemWallBackdrop.parent = overlay.contentItem;
        controller.systemBackdrop = Qt.binding(() => systemBackdrop.ready || !systemWallBackdrop.item ? systemBackdrop : systemWallBackdrop.item);
    }
    // Dock: bottom edge; pinned dock reserves 77 px, auto-hidden one only listens at the edge.
    // The surface reaches higher than the band: the menu and tooltip grow inside it.
    PanelWindow {
        id: dockWindow
        screen: surfaces.controller.output
        visible: surfaces.controller.enabled && surfaces.controller.dockStore.on && surfaces.controller.output !== null
        anchors {
            bottom: true
            left: true
            right: true
        }
        implicitWidth: surfaces.controller.dockWindowWidth
        implicitHeight: surfaces.controller.dockWindowHeight
        color: "transparent"
        exclusiveZone: surfaces.controller.dockPolicy.reserve
        exclusionMode: ExclusionMode.Normal
        WlrLayershell.namespace: "emaki-test-dock"
        // Raise the reveal strip only over fullscreen windows; ordinary docks
        // stay below launcher and panel overlays regardless of mapping order.
        WlrLayershell.layer: surfaces.controller.dockPolicy.fullscreen ? WlrLayer.Overlay : WlrLayer.Top
        WlrLayershell.keyboardFocus: surfaces.controller.enabled && surfaces.controller.dockPolicy.dockVisible && surfaces.controller.dock.keyboardActive && surfaces.controller.niri.layerFocusSupported ? (surfaces.pendingLayer === "dock" ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.OnDemand) : WlrKeyboardFocus.None
        mask: DockInputRegion {
            edge: surfaces.controller.dockEdge
            edgeEnabled: surfaces.controller.dockPolicy.edgeEnabled
            plateRect: surfaces.controller.dock.plateHitRect
            plateEnabled: surfaces.controller.dock.visibleAmount > .08
            popupRect: surfaces.controller.dock.popupContent
            popupEnabled: surfaces.controller.dock.popupInteractive
        }
    }
    // What the dock glass refracts: a live capture on Emaki's niri, else the wallpaper.
    // Keep the established screen-DPR sampling. Window DPR is the resource-sizing
    // candidate, but reducing it changes fine-detail refraction (opt round 1 renders).
    WallpaperSource {
        id: dockWallpaper
        refreshManaged: true
        variant: "sharp"
        outputWidth: Math.round(surfaces.controller.viewportWidth)
        outputHeight: Math.round(surfaces.controller.viewportHeight)
        outputScale: surfaces.controller.output?.devicePixelRatio ?? 1
        outputName: surfaces.controller.outputName
    }
    DockBackdropRegion {
        id: dockRegion
        dock: surfaces.controller.dock
        screenWidth: surfaces.controller.viewportWidth
        screenHeight: surfaces.controller.viewportHeight
        windowTop: surfaces.controller.dockOrigin.y
        dpr: surfaces.controller.output?.devicePixelRatio ?? 1
    }
    DockBackdrop {
        id: dockBackdrop
        mode: Quickshell.env("EMAKI_SHELL_DOCK_BACKDROP") === "capture" ? "capture" : "wallpaper"
        screen: surfaces.controller.output
        wallpaperTexture: dockWallpaper.texture
        sharedWallpaper: true
        screenWidth: surfaces.controller.viewportWidth
        screenHeight: surfaces.controller.viewportHeight
        dpr: surfaces.controller.output?.devicePixelRatio ?? 1
        region: dockRegion.region
        active: surfaces.controller.dockStore.on && surfaces.controller.dock.visibleAmount > 0
    }
    // The bubble bends the wallpaper only (it does not see windows): with a live capture it
    // needs its own static copy; without one the capture-less backdrop already is that.
    Loader {
        id: dockWallBackdrop
        active: dockBackdrop.mode === "capture"
        sourceComponent: DockBackdrop {
            mode: "wallpaper"
            wallpaperTexture: dockWallpaper.texture
            sharedWallpaper: true
            screenWidth: surfaces.controller.viewportWidth
            screenHeight: surfaces.controller.viewportHeight
            dpr: surfaces.controller.output?.devicePixelRatio ?? 1
            region: dockBackdrop.region
        }
    }
    // Under the left islands: the bar's band only (islands, drops, shadows and the blur's
    // reach), not the whole output.
    DockBackdrop {
        id: barBackdrop
        mode: dockBackdrop.mode
        screen: surfaces.controller.output
        wallpaperTexture: dockWallpaper.texture
        cacheWallpaper: true
        screenWidth: surfaces.controller.viewportWidth
        screenHeight: surfaces.controller.viewportHeight
        dpr: surfaces.controller.output?.devicePixelRatio ?? 1
        region: Qt.rect(0, 0, surfaces.controller.viewportWidth, Math.min(surfaces.controller.viewportHeight, 240))
        active: surfaces.controller.enabled && surfaces.controller.barPolicy.barVisible
    }
    Loader {
        id: barWallBackdrop
        active: dockBackdrop.mode === "capture"
        sourceComponent: DockBackdrop {
            mode: "wallpaper"
            wallpaperTexture: dockWallpaper.texture
            cacheWallpaper: true
            screenWidth: surfaces.controller.viewportWidth
            screenHeight: surfaces.controller.viewportHeight
            dpr: surfaces.controller.output?.devicePixelRatio ?? 1
            region: barBackdrop.region
        }
    }
    // Under the launcher: its corner of the screen, while it is on screen.
    DockBackdrop {
        id: launcherBackdrop
        mode: dockBackdrop.mode
        screen: surfaces.controller.output
        wallpaperTexture: dockWallpaper.texture
        sharedWallpaper: true
        screenWidth: surfaces.controller.viewportWidth
        screenHeight: surfaces.controller.viewportHeight
        dpr: surfaces.controller.output?.devicePixelRatio ?? 1
        region: Qt.rect(0, 0, Math.min(surfaces.controller.viewportWidth, Metrics.logoX + surfaces.controller.panelWidth + 120), Math.min(surfaces.controller.viewportHeight, Metrics.top + Metrics.launcherHeader + Metrics.launcherBodyMax + Metrics.launcherFoot + 120))
        active: surfaces.controller.launcherPresent
    }
    // A live capture needs a frame or two after the overlay appears; until then the panel
    // refracts the still wallpaper instead of flashing its flat stand-in.
    Loader {
        id: launcherWallBackdrop
        active: dockBackdrop.mode === "capture"
        sourceComponent: DockBackdrop {
            mode: "wallpaper"
            wallpaperTexture: dockWallpaper.texture
            sharedWallpaper: true
            screenWidth: surfaces.controller.viewportWidth
            screenHeight: surfaces.controller.viewportHeight
            dpr: surfaces.controller.output?.devicePixelRatio ?? 1
            region: launcherBackdrop.region
        }
    }
    // Under the clock panel: the middle of the screen, as wide as the drawer (560) and as tall
    // as the screen allows, plus the shadow's reach, while the panel is on screen.
    DockBackdrop {
        id: clockBackdrop
        readonly property real reach: 560 + 2 * 120
        mode: dockBackdrop.mode
        screen: surfaces.controller.output
        wallpaperTexture: dockWallpaper.texture
        sharedWallpaper: true
        screenWidth: surfaces.controller.viewportWidth
        screenHeight: surfaces.controller.viewportHeight
        dpr: surfaces.controller.output?.devicePixelRatio ?? 1
        region: Qt.rect(Math.max(0, Math.floor((surfaces.controller.viewportWidth - reach) / 2)), 0, Math.min(surfaces.controller.viewportWidth, reach), surfaces.controller.viewportHeight)
        active: surfaces.controller.clockPresent
    }
    Loader {
        id: clockWallBackdrop
        active: dockBackdrop.mode === "capture"
        sourceComponent: DockBackdrop {
            mode: "wallpaper"
            wallpaperTexture: dockWallpaper.texture
            sharedWallpaper: true
            screenWidth: surfaces.controller.viewportWidth
            screenHeight: surfaces.controller.viewportHeight
            dpr: surfaces.controller.output?.devicePixelRatio ?? 1
            region: clockBackdrop.region
        }
    }
    // Under the system panel, the OSD and the privacy panel: the right 800 px of the screen in
    // its full height (the panel is 480 at the edge, the privacy one left of the island), while
    // one of them is on screen.
    DockBackdrop {
        id: systemBackdrop
        readonly property real reach: 800
        mode: dockBackdrop.mode
        screen: surfaces.controller.output
        wallpaperTexture: dockWallpaper.texture
        sharedWallpaper: true
        screenWidth: surfaces.controller.viewportWidth
        screenHeight: surfaces.controller.viewportHeight
        dpr: surfaces.controller.output?.devicePixelRatio ?? 1
        region: Qt.rect(Math.max(0, surfaces.controller.viewportWidth - reach), 0, Math.min(surfaces.controller.viewportWidth, reach), surfaces.controller.viewportHeight)
        active: surfaces.controller.systemPresent || surfaces.controller.osd.shown || surfaces.controller.privacyPresent
    }
    Loader {
        id: systemWallBackdrop
        active: dockBackdrop.mode === "capture"
        sourceComponent: DockBackdrop {
            mode: "wallpaper"
            wallpaperTexture: dockWallpaper.texture
            sharedWallpaper: true
            screenWidth: surfaces.controller.viewportWidth
            screenHeight: surfaces.controller.viewportHeight
            dpr: surfaces.controller.output?.devicePixelRatio ?? 1
            region: systemBackdrop.region
        }
    }
    PanelWindow {
        id: top
        screen: surfaces.controller.output
        visible: surfaces.controller.enabled && surfaces.controller.output !== null
        anchors {
            top: true
            left: true
            right: true
        }
        implicitHeight: 84
        color: "transparent"
        // Always Normal: setting exclusiveZone forces Normal anyway, so a conditional
        // Ignore never sticks. Zone 0 reserves nothing, which is all the 0 case needs.
        exclusiveZone: surfaces.controller.barPolicy.effectiveReserve
        exclusionMode: ExclusionMode.Normal
        WlrLayershell.namespace: "emaki-test-bar"
        WlrLayershell.layer: WlrLayer.Top
        WlrLayershell.keyboardFocus: surfaces.controller.enabled && surfaces.controller.barPolicy.barVisible && (!surfaces.controller.barPolicy.fullscreen || surfaces.controller.niri.overviewOpen) && surfaces.controller.barKeyboardActive && surfaces.controller.niri.layerFocusSupported ? (surfaces.pendingLayer === "bar" ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.OnDemand) : WlrKeyboardFocus.None
        mask: barInput
        BackgroundEffect.blurRegion: barBlur
    }
    // The overlay stays mapped, transparent
    // and without input while nothing is open (mask: the empty clockInput region,
    // keyboard None). Quickshell deletes a
    // layer-shell window on visible: false (WlrLayershell::deleteOnInvisible), so toggling
    // visibility made a new QQuickWindow and wl_surface for every panel, hint and OSD: a first
    // frame of 23–58 ms in the VM (17–77 ms on a laptop) with the bar's button already
    // gone and the panel not yet drawn (27.09).
    Connections {
        target: surfaces.controller
        function onModalOpenChanged(): void {
            if (surfaces.controller.modalOpen)
                Qt.callLater(() => {
                    if (surfaces.controller.launcherOpen)
                        surfaces.controller.input.takeFocus(surfaces.controller.keyboardSurface === "launcher");
                    else if (surfaces.controller.systemOpen)
                        surfaces.controller.systemPanel.takeFocus(surfaces.keyboardPanel);
                    else if (surfaces.controller.drawerOpen)
                        surfaces.controller.clockPanel.forceActiveFocus();
                    else if (surfaces.controller.privacyOpen)
                        surfaces.controller.privacyPopup.takeFocus(surfaces.keyboardPanel);
                    else if (surfaces.controller.shortcutsOpen)
                        surfaces.controller.shortcuts.takeFocus();
                });
        }
    }
    PanelWindow {
        id: overlay
        screen: surfaces.controller.output
        visible: surfaces.controller.enabled && surfaces.controller.output !== null
        anchors {
            top: true
            bottom: true
            left: true
            right: true
        }
        color: "transparent"
        // No exclusiveZone here: setting it switches exclusionMode back to Normal
        // (Quickshell panelinterface.hpp), and the compositor then pushes this whole
        // surface below the bar's reserve — panels opened 52 px too low.
        exclusionMode: ExclusionMode.Ignore
        WlrLayershell.namespace: "emaki-test-launcher"
        WlrLayershell.layer: WlrLayer.Overlay
        // Pointer-opened panels retain their existing modal policy. Keyboard
        // openings and native pairing yield when niri focuses another surface.
        WlrLayershell.keyboardFocus: !surfaces.controller.enabled || !surfaces.controller.modalOpen ? WlrKeyboardFocus.None : !surfaces.controller.niri.layerFocusSupported ? (surfaces.pairingKeyboard ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.Exclusive) : surfaces.pairingKeyboard || surfaces.keyboardPanel ? (surfaces.pendingLayer === "panel" ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.OnDemand) : WlrKeyboardFocus.Exclusive
        // The dock popup takes pointer input (outside press closes it) but never the keyboard.
        // With only the dock menu open, the overlay catches outside presses but leaves a hole
        // over the menu and the plate: those live in the dock surface underneath.
        mask: surfaces.controller.modalOpen ? null : surfaces.controller.dock.popupOpen ? dockMenuHole : clockInput
        BackgroundEffect.blurRegion: launcherBlur
        MouseArea {
            anchors.fill: parent
            enabled: surfaces.controller.modalOpen || surfaces.controller.dock.popupOpen
            acceptedButtons: Qt.AllButtons
            onPressed: mouse => {
                mouse.accepted = true;
                surfaces.controller.outsidePressButton(mouse.x, mouse.y, mouse.button);
            }
        }
    }
    Region {
        id: barInput
        Region {
            item: surfaces.controller.edge
            intersection: surfaces.controller.barPolicy.edgeEnabled ? Intersection.Combine : Intersection.Subtract
        }
        Region {
            item: surfaces.controller.bar.logo
            intersection: surfaces.controller.bar.logo.visible ? Intersection.Combine : Intersection.Subtract
            radius: Metrics.islandRadius
        }
        Region {
            item: surfaces.controller.bar.workspaces
            intersection: surfaces.controller.bar.workspaces.visible ? Intersection.Combine : Intersection.Subtract
            radius: Metrics.islandRadius
        }
        // Hover highlights need pointer input on these islands too.
        Region {
            item: surfaces.controller.bar.clock
            intersection: surfaces.controller.bar.clock.visible ? Intersection.Combine : Intersection.Subtract
            radius: Metrics.islandRadius
        }
        Region {
            item: surfaces.controller.bar.systemIsland
            intersection: surfaces.controller.bar.systemIsland.visible ? Intersection.Combine : Intersection.Subtract
            radius: Metrics.islandRadius
        }
        Region {
            item: surfaces.controller.privacy
            intersection: surfaces.controller.privacy.visible && surfaces.controller.privacy.shown ? Intersection.Combine : Intersection.Subtract
            radius: Metrics.islandRadius
        }
    }
    // A Region with an `item` follows only that item's own x, y, width and height (Quickshell
    // core/region.hpp: "In some cases the region does not update automatically"). The bar's
    // islands ride on the bar, which ShellScene slides up out of sight when it hides (fullscreen
    // window, auto-hide) and back: without these rebuilds the input region stayed where the
    // slide began, above the surface, and the bar took no pointer until the shell restarted.
    Connections {
        target: surfaces.controller.bar
        function onYChanged(): void {
            barInput.changed();
            barBlur.changed();
        }
    }
    Region {
        id: barBlur
        // The left islands blur what is under them themselves when their glass is drawn;
        // the compositor's blur stays for the flat stand-in.
        Region {
            item: surfaces.controller.bar.logo
            intersection: surfaces.controller.bar.logo.visible && !surfaces.controller.bar.leftIslands.glassReady ? Intersection.Combine : Intersection.Subtract
            radius: Metrics.islandRadius
        }
        Region {
            item: surfaces.controller.bar.workspaces
            intersection: surfaces.controller.bar.workspaces.visible && !surfaces.controller.bar.leftIslands.glassReady ? Intersection.Combine : Intersection.Subtract
            radius: Metrics.islandRadius
        }
        Region {
            item: surfaces.controller.bar.clock
            intersection: surfaces.controller.bar.clock.visible && !surfaces.controller.bar.clockIsland.glassReady ? Intersection.Combine : Intersection.Subtract
            radius: Metrics.islandRadius
        }
        Region {
            item: surfaces.controller.bar.systemIsland
            intersection: surfaces.controller.bar.systemIsland.visible && !surfaces.controller.bar.systemGlass.glassReady ? Intersection.Combine : Intersection.Subtract
            radius: Metrics.islandRadius
        }
        Region {
            item: surfaces.controller.privacy
            intersection: surfaces.controller.privacy.visible && surfaces.controller.privacy.shown && !surfaces.controller.privacy.glassReady ? Intersection.Combine : Intersection.Subtract
            radius: Metrics.islandRadius
        }
    }
    Region {
        id: dockMenuHole
        x: 0
        y: 0
        width: surfaces.controller.viewportWidth
        height: surfaces.controller.viewportHeight
        Region {
            readonly property rect r: surfaces.controller.dock.popupContent
            x: r.x + surfaces.controller.dockOrigin.x
            y: r.y + surfaces.controller.dockOrigin.y
            width: r.width
            height: r.height
            intersection: Intersection.Subtract
        }
        Region {
            readonly property rect r: surfaces.controller.dock.plateHitRect
            x: r.x + surfaces.controller.dockOrigin.x
            y: r.y + surfaces.controller.dockOrigin.y
            width: r.width
            height: r.height
            intersection: Intersection.Subtract
        }
    }
    Region {
        id: clockInput
        Region {
            item: surfaces.controller.clockPanel
            intersection: surfaces.controller.peekOpen ? Intersection.Combine : Intersection.Subtract
            radius: Math.round(surfaces.controller.clockPanel.radius)
        }
    }
    Region {
        id: launcherBlur
        // The OSD stays outside every input mask: it never takes pointer or keyboard input.
        Region {
            item: surfaces.controller.osd
            intersection: surfaces.controller.osd.shown && !surfaces.controller.osd.glassReady ? Intersection.Combine : Intersection.Subtract
            radius: Math.round(surfaces.controller.osd.radius)
        }
        Region {
            item: surfaces.controller.systemPanel
            intersection: surfaces.controller.systemPresent && !surfaces.controller.systemPanel.glassReady ? Intersection.Combine : Intersection.Subtract
            radius: Math.round(surfaces.controller.systemPanel.radius)
        }
        Region {
            item: surfaces.controller.panel
            intersection: surfaces.controller.launcherPresent && !surfaces.controller.input.glassReady ? Intersection.Combine : Intersection.Subtract
            radius: Math.round(surfaces.controller.panel.radius)
        }
        Region {
            item: surfaces.controller.clockPanel
            intersection: surfaces.controller.clockPresent && !surfaces.controller.clockPanel.glassReady ? Intersection.Combine : Intersection.Subtract
            radius: Math.round(surfaces.controller.clockPanel.radius)
        }
        Region {
            item: surfaces.controller.privacyPopup
            intersection: surfaces.controller.privacyPresent && !surfaces.controller.privacyPopup.glassReady ? Intersection.Combine : Intersection.Subtract
            radius: Math.round(surfaces.controller.privacyPopup.radius)
        }
    }
}
