pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// test-bar.py: the bar's input mask after the bar slid out under a fullscreen window and
// back. Every time the mask region announces a change, the logo's position in the bar's
// window is recorded; after the bar has come back to rest the last recorded position must
// be the logo's position at rest, or the compositor keeps an input region from mid-slide.
Timer {
    id: driver
    required property ShellScene scene
    property var surfaces: null
    interval: 20
    repeat: true
    running: true
    property int stage: 0
    property var mask: null
    property var seen: []
    property real restedAt: 0
    function logoY(): real {
        return Math.round(scene.bar.logo.mapToItem(null, 0, 0).y);
    }
    function publish(full: bool): void {
        const name = scene.outputName || "Fixture";
        const outputs = {};
        outputs[name] = {
            logical: {
                width: scene.viewportWidth,
                height: scene.viewportHeight
            }
        };
        scene.niri.receive(JSON.stringify({
            schema_version: 1,
            ipc_release: "26.04",
            generation: 1,
            connection: {
                status: "connected",
                reason: "synchronized"
            },
            model: {
                focused_output: name,
                overview_open: false,
                links_pending: false,
                keyboard_layouts: {
                    names: ["English (US)"],
                    current_idx: 0
                },
                outputs: outputs,
                workspaces: {
                    "1": {
                        id: 1,
                        idx: 1,
                        is_active: true,
                        is_focused: true,
                        output: name
                    }
                },
                windows: full ? {
                    "8": {
                        id: 8,
                        workspace_id: 1,
                        layout: {
                            tile_size: [scene.viewportWidth, scene.viewportHeight]
                        }
                    }
                } : {},
                casts: {}
            }
        }));
    }
    function check(ok: bool, label: string): void {
        if (!ok)
            throw new Error(label + " " + JSON.stringify({
                y: scene.bar.y,
                logo: logoY(),
                seen: seen,
                presentation: scene.barPolicy.presentation
            }));
    }
    onTriggered: {
        try {
            if (stage === 0) {
                if (!surfaces)
                    return;
                publish(false);
                stage = 1;
            } else if (stage === 1) {
                if (scene.barPolicy.presentation !== "clear" || scene.bar.y !== 0)
                    return;
                mask = scene.bar.QsWindow.window?.mask ?? null;
                check(mask !== null, "the bar's window has no input mask");
                mask.changed.connect(() => driver.seen = driver.seen.concat([driver.logoY()]));
                publish(true);
                stage = 2;
            } else if (stage === 2) {
                if (scene.bar.y !== -60)
                    return;
                check(!scene.barPolicy.barVisible, "a covering window did not hide the bar");
                publish(false);
                stage = 3;
            } else if (stage === 3) {
                if (scene.bar.y !== 0)
                    return;
                restedAt = Date.now();
                stage = 4;
            } else if (stage === 4) {
                if (Date.now() - restedAt < 200)
                    return;
                check(seen.length > 0, "the mask never changed while the bar slid");
                check(logoY() === Metrics.top, "the logo did not come back to the top margin");
                check(seen[seen.length - 1] === logoY(), "the input mask was last rebuilt with the logo away from its place");
                console.log("BAR_MASK_OK " + JSON.stringify({
                    changes: seen.length,
                    last: seen[seen.length - 1]
                }));
                stop();
                Qt.exit(0);
            }
        } catch (error) {
            console.error("BAR_MASK_FAILED " + error.message);
            stop();
            Qt.exit(2);
        }
    }
}
