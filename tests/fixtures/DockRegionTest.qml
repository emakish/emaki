pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell
import Quickshell.Wayland
import InputMaskProbe 1.0

// Real Dock release/return/menu sequence, with no IPC, compositor or live service.
ShellRoot {
    id: root
    property int testedDockLayer: 0
    property int stage: 0
    property double since: 0
    property int returnFrames: 0
    property int changes: 0
    property real expandedTop: 0
    function check(value: bool, message: string): void {
        if (!value)
            throw new Error(message);
    }
    function next(value: int): void {
        stage = value;
        since = Date.now();
    }
    function setFullscreen(value: bool): void {
        const state = JSON.parse(JSON.stringify(service.model));
        state.windows["501"].layout.tile_size = value ? [1536, 960] : [800, 600];
        service.model = state;
    }
    NiriService {
        id: service
        binary: ""
    }
    InputMaskProbe {
        id: maskProbe
    }
    FloatingWindow {
        id: inputWindow
        visible: true
        implicitWidth: scene.dockWindowWidth
        implicitHeight: scene.dockWindowHeight
        mask: DockInputRegion {
            edge: inputEdge
            edgeEnabled: scene.dockPolicy.edgeEnabled
            plateRect: scene.dock.plateHitRect
            plateEnabled: scene.dock.visibleAmount > .08
            popupRect: scene.dock.popupContent
            popupEnabled: scene.dock.popupInteractive
        }
        Item {
            id: inputEdge
            y: inputWindow.height - 2
            width: inputWindow.width
            height: 2
        }
    }
    Window {
        visible: true
        width: 1536
        height: 960
        ShellScene {
            id: scene
            anchors.fill: parent
            niri: service
            headless: true
            testWidth: 1536
            testHeight: 960
            skipIntro: true
        }
    }
    DockBackdropRegion {
        id: bounds
        dock: scene.dock
        screenWidth: 1536
        screenHeight: 960
        windowTop: scene.dockOrigin.y
        dpr: 2
        onExpandedChanged: ++root.changes
    }
    Timer {
        interval: 16
        running: true
        repeat: true
        onTriggered: {
            const dock = scene.dock, now = Date.now(), elapsed = now - root.since;
            if (root.stage === 0) {
                scene.wallpaper.enabled = false;
                if (dock.animating || dock.visibleAmount !== 0)
                    return;
                const x = Math.floor(inputWindow.width / 2), y = inputWindow.height - 1;
                root.check(dock.plateHitRect.y < y, "hidden plate padding overlaps reveal strip");
                for (const edgeX of [0, x, inputWindow.width - 1])
                    root.check(maskProbe.contains(inputWindow.contentItem, edgeX, y), "hidden dock keeps the complete bottom edge in native input mask");
                root.check(!maskProbe.contains(inputWindow.contentItem, x, y - 2), "hidden dock does not steal input above its two-pixel edge");
                root.check(scene.dockPolicy.reserve === 0, "hidden dock reserves no workspace space");
                maskProbe.move(scene, x, scene.height - 1);
                root.next(-1);
            } else if (root.stage === -1 && elapsed > 150 && !dock.animating && dock.visibleAmount === 1) {
                root.check(scene.dockPolicy.edgeHovered && scene.dockPolicy.revealed, "real edge hover reveals the dock");
                maskProbe.move(scene, 0, 0);
                root.next(-2);
            } else if (root.stage === -2 && elapsed > 150 && !dock.animating && dock.visibleAmount === 0) {
                root.check(!scene.dockPolicy.edgeHovered && !scene.dockPolicy.revealed, "leaving the edge hides the dock");
                scene.dockStore.autoHide = false;
                scene.dockStore.pinned = [];
                scene.dockLabels.values = {
                    "501": {
                        title: "Running unpinned app",
                        appId: "fixture-editor"
                    }
                };
                service.model = {
                    focused_output: "Fixture",
                    overview_open: false,
                    outputs: {
                        "Fixture": {
                            logical: {
                                width: 1536,
                                height: 960
                            }
                        }
                    },
                    windows: {
                        "501": {
                            id: 501,
                            workspace_id: 101,
                            layout: {
                                tile_size: [800, 600]
                            }
                        }
                    },
                    workspaces: {
                        "101": {
                            id: 101,
                            idx: 1,
                            output: "Fixture",
                            is_active: true
                        }
                    }
                };
                service.connection = "connected";
                root.next(1);
            } else if (root.stage === 1 && elapsed > 200 && !dock.animating && dock.visibleAmount === 1) {
                root.check(scene.dockPolicy.reserve === 77, "pinned dock reserves only its band");
                const r = dock.plateHitRect;
                root.check(maskProbe.contains(inputWindow.contentItem, Math.floor(r.x + r.width / 2), Math.floor(r.y + 20)), "visible plate receives input");
                root.check(!maskProbe.contains(inputWindow.contentItem, 0, inputWindow.height - 1), "pinned dock releases unused edge");
                root.check(!bounds.expanded, "idle must start cropped");
                const entry = dock.entryOf("fixture-editor");
                root.check(entry !== null && !entry.pinned && entry.windows.length === 1, "fixture must be a running unpinned app");
                const v = dock.visual("fixture-editor");
                root.check(dock.pointerPressed(v.center[0], v.center[1]), "press the real icon");
                dock.pointerDragged(v.center[0], 30);
                root.check(bounds.expanded, "drag expands in the same call");
                root.expandedTop = bounds.regionTop;
                root.changes = 0;
                root.next(2);
            } else if (root.stage === 2 && elapsed > 200) {
                root.check(dock.bubble.following && dock.drag !== null, "bubble must follow lifted icon");
                dock.pointerReleased(dock.drag.center[0], 30, Qt.LeftButton);
                root.check(dock.drag === null && dock.visual("fixture-editor").returning, "release starts return spring");
                root.check(bounds.expanded && bounds.regionTop === root.expandedTop, "release must not shrink in that frame");
                root.check(scene.dockOrigin.y + dock.bubble.y < 642, "release must exercise the old clamped region");
                root.next(3);
            } else if (root.stage === 3) {
                root.check(bounds.expanded && bounds.regionTop === root.expandedTop, "retain bounds through returning icon/bubble");
                root.check(bounds.regionTop + bounds.blurReach <= bounds.windowTop, "expanded region must contain top-edge blur support");
                if (dock.visual("fixture-editor").returning)
                    ++root.returnFrames;
                if (!dock.animating && !bounds.needsHeadroom)
                    root.next(4);
            } else if (root.stage === 4 && elapsed > 200) {
                root.check(bounds.expanded && root.changes === 0, "do not reclaim during brief idle");
                dock.menu("fixture-editor");
                root.next(5);
            } else if (root.stage === 5 && dock.popupSettled) {
                root.check(bounds.expanded && root.changes === 0, "reopen menu reuses allocated bounds");
                dock.closePopup();
                root.next(6);
            } else if (root.stage === 6) {
                root.check(bounds.expanded, "closing menu keeps headroom");
                if (!dock.animating && !bounds.needsHeadroom)
                    root.next(7);
            } else if (root.stage === 7) {
                if (elapsed < bounds.idleMilliseconds - 40)
                    root.check(bounds.expanded, "wait a settled idle interval before reclaiming");
                if (!bounds.expanded) {
                    root.check(root.returnFrames >= 3 && root.changes === 1, "observe multiple return frames and one final reclaim");
                    bounds.dpr = 1;
                    root.check(bounds.blurReach >= 126, "DPR 1 needs full Regular kernel and filter/refraction support");
                    bounds.screenHeight = 961;
                    root.check(bounds.regionTop === 0, "odd physical height retains original lattice");
                    console.log("DOCK_REGION_OK", JSON.stringify({
                        return_frames: root.returnFrames,
                        retained_region_top: root.expandedTop,
                        region_reclaims_after_drag: root.changes,
                        regular_reach_at_dpr1: bounds.blurReach
                    }));
                    root.check(root.testedDockLayer === WlrLayer.Top, "ordinary pinned dock stays below overlays");
                    root.setFullscreen(true);
                    root.next(8);
                }
            } else if (root.stage === 8 && elapsed > 200 && !dock.animating && dock.visibleAmount === 0) {
                root.check(root.testedDockLayer === WlrLayer.Overlay, "fullscreen raises the reveal strip");
                root.check(maskProbe.contains(inputWindow.contentItem, 0, inputWindow.height - 1), "fullscreen preserves native edge input");
                scene.dockStore.on = false;
                root.next(9);
            } else if (root.stage === 9 && elapsed > 100) {
                root.check(root.testedDockLayer === WlrLayer.Overlay, "disable keeps fullscreen layer selection");
                root.check(!maskProbe.contains(inputWindow.contentItem, 0, inputWindow.height - 1), "disabled dock releases native edge input");
                scene.dockStore.on = true;
                scene.dockStore.autoHide = true;
                root.next(10);
            } else if (root.stage === 10 && elapsed > 100) {
                root.check(root.testedDockLayer === WlrLayer.Overlay, "reenabled autohide dock stays above fullscreen");
                root.check(maskProbe.contains(inputWindow.contentItem, 0, inputWindow.height - 1), "reenabled dock restores native edge input");
                root.setFullscreen(false);
                root.next(11);
            } else if (root.stage === 11 && elapsed > 100) {
                root.check(root.testedDockLayer === WlrLayer.Top, "leaving fullscreen restores normal stacking");
                scene.dockStore.on = false;
                root.next(12);
            } else if (root.stage === 12 && elapsed > 100) {
                scene.dockStore.on = true;
                scene.dockStore.autoHide = false;
                root.next(13);
            } else if (root.stage === 13 && elapsed > 100 && !dock.animating && dock.visibleAmount === 1) {
                root.check(root.testedDockLayer === WlrLayer.Top, "remapped pinned dock remains below panels and launcher");
                root.check(!maskProbe.contains(inputWindow.contentItem, 0, inputWindow.height - 1), "ordinary pinned dock still releases unused edge");
                console.log("DOCK_LAYER_OK");
                Qt.quit();
            }
        }
    }
    Timer {
        interval: 10000
        running: true
        onTriggered: {
            throw new Error("Dock region sequence timed out at stage " + root.stage);
        }
    }
}
