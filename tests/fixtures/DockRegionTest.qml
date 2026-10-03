pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell

// Real Dock release/return/menu sequence, with no IPC, compositor or live service.
ShellRoot {
    id: root
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
    NiriService {
        id: service
        binary: ""
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
                    windows: {
                        "501": {
                            id: 501,
                            workspace_id: 101
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
                    Qt.quit();
                }
            }
        }
    }
    Timer {
        interval: 8000
        running: true
        onTriggered: {
            throw new Error("Dock region sequence timed out at stage " + root.stage);
        }
    }
}
