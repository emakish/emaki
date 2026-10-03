pragma ComponentBehavior: Bound
import QtQuick

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
    property int releaseFrame: 0
    property bool releaseDone: false
    property bool configured: false
    readonly property bool bubbleReady: {
        scene.dock.tick;
        return shot !== "dock-bubble" || (scene.dock.bubble.owner === "fixture-editor" && scene.dock.bubble.alpha.x === 1);
    }
    readonly property var renderStats: {
        scene.dock.tick;
        return {
            bubble_owner: scene.dock.bubble.owner,
            bubble_alpha: scene.dock.bubble.alpha.x,
            dock_region_top: dockMap.region.y,
            dock_region_height: dockMap.region.height,
            popup_settled: scene.dock.popupSettled
        };
    }
    readonly property bool ready: configured && bubbleReady && (shot !== "dock-release" || releaseDone) && dockMap.ready && barMap.ready && launcherMap.ready && clockMap.ready && systemMap.ready && !scene.dock.animating && !scene.input.animating && !scene.bar.leftIslands.animating && !scene.bar.clockIsland.animating && !scene.bar.systemGlass.animating && !scene.clockPanel.animating && !scene.systemPanel.animating && (shot !== "dock-menu" || scene.dock.popupSettled) && (shot !== "launcher" || scene.expansion === 1) && (shot !== "clock" || scene.clockExpansion === 1) && (!shot.startsWith("system-") || scene.systemExpansion === 1)
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
            scene.dockStore.pinned = root.shot === "dock-release" ? ["fixture-browser", "fixture-files"] : ["fixture-browser", "fixture-editor", "fixture-files"];
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
            if (root.shot === "launcher")
                scene.openLauncher();
            else if (root.shot === "clock")
                scene.openDrawer();
            else if (root.shot.startsWith("system-"))
                scene.openSystem(root.shot.slice(7));
            if (root.shot === "dock-menu" || root.shot === "dock-bubble" || root.shot === "dock-release")
                menu.start();
            root.configured = true;
        }
    }
    Timer {
        id: menu
        interval: 150
        onTriggered: {
            if (root.shot === "dock-bubble")
                scene.dock.setHover("fixture-editor", true);
            else if (root.shot === "dock-release") {
                const v = scene.dock.visual("fixture-editor");
                scene.dock.pointerPressed(v.center[0], v.center[1]);
                scene.dock.pointerDragged(v.center[0], 30);
                release.start();
            } else
                scene.dock.menu("fixture-editor");
        }
    }
    Timer {
        id: release
        interval: 250
        onTriggered: {
            const dock = scene.dock;
            if (!dock.drag || !dock.bubble.following)
                throw new Error("Release fixture must lift a running app");
            dock.pointerReleased(dock.drag.center[0], 30, Qt.LeftButton);
            if (!dock.visual("fixture-editor").returning)
                throw new Error("Release fixture must return an unpinned app");
            const now = Date.now();
            for (let i = 1; i <= root.releaseFrame; ++i)
                dock.frame(now + i * 16, .016);
            // Snapshot an exact return step, not an arbitrary wall-clock phase.
            dock.animating = false;
            root.releaseDone = true;
        }
    }
}
