pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell

// Run only through tests/test-strip.py: the workspaces island alone, flat stand-in, at the
// scale QT_SCALE_FACTOR gives (1, 1.25, 2). Saves strip@<scale>x.png and prints the cell rects.
ShellRoot {
    id: root
    readonly property string destination: Quickshell.env("EMAKI_SHELL_SHOT_DIR")
    readonly property string shotScale: Quickshell.env("QT_SCALE_FACTOR")
    NiriService {
        id: niri
        binary: ""
    }
    Window {
        id: window
        visible: true
        width: 1536
        height: 960
        color: "#1a1412"
        ShellScene {
            id: scene
            anchors.fill: parent
            borderMode: "soft"
            reservedSpace: Metrics.reservedSpace
            headless: true
            testWidth: 1536
            testHeight: 960
            niri: niri
        }
    }
    Timer {
        id: tick
        interval: 900
        onTriggered: {
            const island = scene.bar.workspaces;
            const strip = scene.bar.workspaceStrip;
            const cells = [];
            // The cells are the strip's children that carry a workspace position; inside a
            // cell: the digit Text, the mark Rectangle, the MouseArea.
            for (const item of strip.children) {
                if (item.position === undefined)
                    continue;
                const p = item.mapToItem(null, 0, 0);
                const glyph = item.children[0].mapToItem(null, 0, 0);
                const mark = item.children[1].mapToItem(null, 0, 0);
                cells.push({
                    glyph: [glyph.x, glyph.y, item.children[0].width, item.children[0].height],
                    mark: [mark.x, mark.y, item.children[1].width, item.children[1].height],
                    x: p.x,
                    y: p.y,
                    width: item.width,
                    height: item.height,
                    current: item.current,
                    occupied: item.occupied
                });
            }
            console.log("CELLS " + JSON.stringify({
                island: {
                    x: island.x,
                    y: island.y,
                    width: island.width,
                    height: island.height
                },
                strip: strip.mapToItem(null, 0, 0),
                dpr: strip.dpr,
                cells: cells
            }));
            scene.grabToImage(result => {
                if (!result.saveToFile(root.destination + "/strip@" + root.shotScale + "x.png"))
                    throw new Error("Screenshot save failed");
                console.log("STRIP_COMPLETE");
                Qt.quit();
            });
        }
    }
    Component.onCompleted: Qt.callLater(() => {
        Quickshell.watchFiles = false;
        const workspaces = {};
        for (let i = 1; i <= 5; ++i)
            workspaces[String(100 + i)] = {
                id: 100 + i,
                idx: i,
                output: "Fixture",
                is_active: i === 2
            };
        niri.receive(JSON.stringify({
            schema_version: 1,
            ipc_release: "26.04",
            generation: 1,
            connection: {
                status: "connected",
                reason: "synchronized"
            },
            model: {
                focused_output: "Fixture",
                overview_open: false,
                outputs: {
                    Fixture: {
                        logical: {
                            width: 1536,
                            height: 960
                        }
                    }
                },
                workspaces: workspaces,
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
                keyboard_layouts: {
                    names: ["English (US)"],
                    current_idx: 0
                }
            }
        }));
        tick.start();
    })
}
