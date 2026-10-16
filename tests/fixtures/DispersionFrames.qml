// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import DispersionInput 1.0

// Loaded by render-shell.cpp in a copied, isolated shell module. Real production glass.
Item {
    id: root
    width: 1536
    height: 960
    property string shot: "bar"
    property string wallpaper: ""
    property string background: wallpaper
    property real textureScale: 2
    property bool shared: false
    property bool cropDock: false
    property bool retainedDockRegion: false
    property bool cachedBarBand: false
    property bool configured: false
    property real frameMs: 50
    property bool captured: false
    property bool pointerHovered: false
    property bool pointerClicked: false
    property bool pointerLeft: false
    DispersionPointer {
        id: pointer
    }
    readonly property bool ready: captured
    readonly property var renderStats: ({
            scenario: shot,
            pointer_hovered: pointerHovered,
            pointer_clicked: pointerClicked,
            pointer_left: pointerLeft,
            frame_ms: frameMs,
            battery: scene.bar.systemGlass.glassStatus(),
            battery_coverage: scene.bar.systemGlass.onGlassOf("cell-power"),
            panel_morph: scene.systemPanel.morph,
            panel_row_rect: glyphRect(),
            panel_drops: scene.systemPanel.drops.map(d => [d.rect.x, d.rect.y, d.rect.z, d.rect.w, d.params.w, d.selected || 0]),
            panel_carrying: scene.systemPanel.carrying,
            panel_cell_on_glass: scene.systemPanel.onGlassOf("cell-power"),
            dock_amount: scene.dock.visibleAmount,
            dock_bubble_alpha: scene.dock.bubble.alpha.x
        })
    function freeze(owner) {
        for (let i = 0; i < owner.data.length; ++i) {
            const o = owner.data[i];
            if (o && o.frameTime !== undefined && o.running !== undefined)
                o.running = false;
        }
    }
    function morphAt(ms) {
        const x = Math.max(0, Math.min(1, ms / Metrics.morphMs));
        const c = Metrics.morphCurve;
        let lo = 0, hi = 1;
        for (let i = 0; i < 30; ++i) {
            const t = (lo + hi) / 2, u = 1 - t;
            if (3 * u * u * t * c[0] + 3 * u * t * t * c[2] + t * t * t < x)
                lo = t;
            else
                hi = t;
        }
        const t = (lo + hi) / 2, u = 1 - t;
        return 3 * u * u * t * c[1] + 3 * u * t * t * c[3] + t * t * t;
    }
    // Only regular/bold static faces are shipped for the fixture's UI family.
    // Name the face explicitly so font discovery order cannot choose a different
    // nearest weight between independent render processes.
    function pinFonts(item) {
        if (item.font !== undefined && item.font.family === ShellPalette.uiFont)
            item.font.styleName = item.font.weight >= Font.DemiBold ? "Bold" : "Regular";
        for (let i = 0; i < item.children.length; ++i)
            pinFonts(item.children[i]);
    }
    function findTarget(item, key) {
        if (item.key === key)
            return item;
        for (let i = 0; i < item.children.length; ++i) {
            const found = findTarget(item.children[i], key);
            if (found)
                return found;
        }
        return null;
    }
    function glyphRect() {
        const row = findTarget(scene.systemPanel.body, "profile-balanced");
        if (!row)
            return [];
        const p = row.mapToItem(root, 0, 0);
        return [p.x, p.y, row.width, row.height];
    }
    function sample() {
        pinFonts(scene);
        const island = scene.bar.systemGlass, dock = scene.dock;
        freeze(island);
        freeze(scene.systemPanel);
        freeze(dock);
        const now = Date.now();
        if (shot === "battery") {
            pointer.move(island.row.cell("power"));
            root.pointerHovered = island.hoverKey === "cell-power";
            island.frame(now, 1 / 60);
            for (let t = Math.min(1000 / 60, frameMs); t <= frameMs && t > 0; t = Math.min(t + 1000 / 60, frameMs)) {
                island.frame(now + t, 1 / 60);
                if (t === frameMs)
                    break;
            }
        } else if (shot === "panel" || shot === "panelclick" || shot === "panelleave") {
            // The real click path: the island's settled hover drop on the battery cell is
            // carried by the opening panel and handed over as the tab (morph .6).
            const panel = scene.systemPanel;
            panel.restBubbles = ({});
            panel.restItems = ({});
            panel.expansion = 0;
            panel.frame(now, 1 / 60);
            for (let t = Math.min(1000 / 60, frameMs); t <= frameMs && t > 0; t = Math.min(t + 1000 / 60, frameMs)) {
                const m = morphAt(t);
                panel.expansion = m;
                island.panelPresent = m > .5;
                island.frame(now + t, 1 / 60);
                panel.frame(now + t, 1 / 60);
                if (t === frameMs)
                    break;
            }
        } else if (shot === "dock-bubble") {
            dock.setHover("fixture-editor", true);
            dock.bubble.alpha.start = now;
            dock.frame(now, 1 / 60);
            for (let t = Math.min(1000 / 60, frameMs); t <= frameMs && t > 0; t = Math.min(t + 1000 / 60, frameMs)) {
                dock.frame(now + t, 1 / 60);
                if (t === frameMs)
                    break;
            }
        } else if (shot === "dock") {
            dock.skipIntro = false;
            dock.visibility.x = dock.visibility.from = 0;
            dock.visibility.target = 1;
            dock.visibility.start = now;
            dock.visibility.duration = 320;
            dock.frame(now + frameMs, 1 / 60);
        }
        root.captured = true;
    }
    function prepare() {
        const island = scene.bar.systemGlass;
        const panel = scene.systemPanel;
        const now = Date.now();
        freeze(island);
        freeze(panel);
        if (shot === "panel" || shot === "panelclick" || shot === "panelleave") {
            island.panelPresent = false;
            pointer.move(island.row.cell("power"));
            root.pointerHovered = island.hoverKey === "cell-power";
            for (let t = -600; t <= 0; t += 1000 / 60)
                island.frame(now + t, 1 / 60);
            island.frame(now, 1 / 60);
            pointer.click(island.row.cell("power"));
            root.pointerClicked = scene.systemOpen && scene.systemPage === "power";
            freeze(scene);
            panel.expansion = 0;
            // A modal overlay takes compositor pointer focus. Also exercise that window
            // leave explicitly; the natural click lets Qt resolve hover as the panel maps
            // inside this offscreen render window.
            if (shot === "panelleave") {
                pointer.leave(island);
                root.pointerLeft = island.hoverKey === "";
            }
            freeze(island);
            freeze(panel);
        }
        capture.start();
    }
    Timer {
        interval: 750
        running: root.configured
        onTriggered: root.prepare()
    }
    Timer {
        id: capture
        interval: 150
        onTriggered: root.sample()
    }
    Image {
        anchors.fill: parent
        source: root.background
    }
    component Backdrop: DockBackdrop {
        property bool bandCrop: false
        mode: "wallpaper"
        wallpaperTexture: root.configured ? root.wallpaper : ""
        screenWidth: root.width
        screenHeight: root.height
        dpr: root.textureScale
        Component.onCompleted: {
            if ("sharedWallpaper" in this)
                this["sharedWallpaper"] = root.shared && !bandCrop;
            if ("cacheWallpaper" in this)
                this["cacheWallpaper"] = bandCrop;
        }
    }
    DockBackdropRegion {
        id: dockBounds
        dock: scene.dock
        screenWidth: root.width
        screenHeight: root.height
        windowTop: scene.dockOrigin.y
        dpr: root.textureScale
    }
    Backdrop {
        id: dockMap
        readonly property bool needsHeadroom: scene.dock.popupOpen || scene.dock.popupProgress > 0 || scene.dock.drag !== null
        readonly property real visibleTop: needsHeadroom ? scene.dockOrigin.y : root.height - scene.dock.band - 120
        readonly property real regionTop: root.cropDock ? Math.max(0, Math.floor((visibleTop - Math.max(120, 3 * clearSigma + 26 + 4 / dpr)) * dpr / 4) * 4 / dpr) : 0
        region: root.retainedDockRegion ? dockBounds.region : Qt.rect(0, regionTop, root.width, root.height - regionTop)
    }
    Backdrop {
        id: barMap
        bandCrop: root.cachedBarBand
        region: Qt.rect(0, 0, root.width, 240)
    }
    Backdrop {
        id: launcherMap
        region: Qt.rect(0, 0, 850, 704)
    }
    Backdrop {
        id: clockMap
        region: Qt.rect(368, 0, 800, root.height)
    }
    Backdrop {
        id: systemMap
        region: Qt.rect(736, 0, 800, root.height)
    }
    SystemFixture {
        id: backend
    }
    NiriService {
        id: niri
        binary: ""
    }
    ShellScene {
        id: scene
        anchors.fill: parent
        borderMode: "soft"
        reservedSpace: Metrics.reservedSpace
        headless: true
        testWidth: 1536
        testHeight: 960
        niri: niri
        skipIntro: true
    }
    Timer {
        interval: 150
        running: true
        onTriggered: {
            scene.services.backend = backend;
            scene.services.brightness = {
                state: "ready",
                percent: 70
            };
            scene.services.profiles = {
                state: "ready",
                profiles: ["power-saver", "balanced", "performance"],
                current: "balanced"
            };
            scene.bar.dateTime = new Date(2026, 8, 23, 14, 32, 0);
            scene.bar.backdrop = barMap;
            scene.dock.backdrop = dockMap;
            scene.dock.wallBackdrop = dockMap;
            scene.launcherBackdrop = launcherMap;
            scene.clockBackdrop = clockMap;
            scene.systemBackdrop = systemMap;
            scene.privacy.backdrop = barMap;
            scene.wallpaper.enabled = false;
            scene.dockStore.autoHide = false;
            scene.dockStore.pinned = ["fixture-browser", "fixture-editor", "fixture-files"];
            scene.dockLabels.values = {
                "501": {
                    title: "Fixture page",
                    appId: "fixture-browser"
                },
                "502": {
                    title: "Fixture notes",
                    appId: "fixture-editor"
                },
                "503": {
                    title: "Fixture draft",
                    appId: "fixture-editor"
                }
            };
            niri.model = {
                focused_output: "Fixture",
                overview_open: false,
                windows: {
                    "501": {
                        id: 501,
                        workspace_id: 101
                    },
                    "502": {
                        id: 502,
                        workspace_id: 102
                    },
                    "503": {
                        id: 503,
                        workspace_id: 103
                    }
                },
                workspaces: {
                    "101": {
                        id: 101,
                        idx: 1,
                        output: "Fixture",
                        is_active: true
                    },
                    "102": {
                        id: 102,
                        idx: 2,
                        output: "Fixture",
                        is_active: false
                    },
                    "103": {
                        id: 103,
                        idx: 3,
                        output: "Fixture",
                        is_active: false
                    }
                },
                keyboard_layouts: {
                    names: ["English (US)", "Russian"],
                    current_idx: 0
                }
            };
            niri.connection = "connected";
            niri.reason = "synchronized";
            scene.input.setMode("Apps");

            if (root.shot === "panelclick" || root.shot === "panelleave")
                scene.bar.systemGlass.panelPresent = false;
            root.configured = true;
        }
    }
}
