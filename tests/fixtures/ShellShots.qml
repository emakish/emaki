pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell
import QtTest

// Run only through tests/shell-shots.py: a Qt offscreen Window, never a layer surface.
ShellRoot {
    id: root
    readonly property string destination: Quickshell.env("EMAKI_SHELL_SHOT_DIR")
    readonly property int shotScale: Number(Quickshell.env("QT_SCALE_FACTOR"))
    readonly property string fallback: Quickshell.env("EMAKI_FIXTURE_FALLBACK")
    property int stage: 0
    property int wallpaperWaits: 0
    property int iconIndex: 0
    readonly property var kinds: ["logo", "corner", "tray", "file", "wifi", "wired", "bt", "sound", "light", "battery", "power-saver", "balanced", "performance", "lock", "sleep", "restart", "shutdown", "logout", "hibernate"]
    readonly property var sizes: [16, 18, 22, 36]

    // The first visible GlassTarget of the clock panel whose key starts with `prefix`.
    function findTarget(item: Item, prefix: string): Item {
        if (item.visible && typeof item.key === "string" && item.key.startsWith(prefix))
            return item;
        for (const child of item.children) {
            const found = findTarget(child, prefix);
            if (found)
                return found;
        }
        return null;
    }
    function save(item: Item, name: string, next: var): void {
        const accepted = item.grabToImage(result => {
            if (!result.saveToFile(destination + "/" + name + "@" + shotScale + "x.png"))
                throw new Error("Screenshot save failed");
            next();
        }, Qt.size(item.width, item.height));
        if (!accepted)
            throw new Error("Screenshot grab failed");
    }

    SystemFixture {
        id: systemFixture
    }
    NiriService {
        id: niri
        binary: ""
    }
    Window {
        visible: true
        width: 1536
        height: 960
        color: "transparent"
        Item {
            id: canvas
            anchors.fill: parent
            Image {
                anchors.fill: parent
                source: Quickshell.env("EMAKI_FIXTURE_WALLPAPER")
                fillMode: Image.PreserveAspectCrop
            }
            ShellScene {
                id: scene
                anchors.fill: parent
                borderMode: "soft" // Explicit fixture parameter, not a design decision.
                reservedSpace: Metrics.reservedSpace
                headless: true
                testWidth: 1536
                testHeight: 960
                niri: niri
                Component.onCompleted: {
                    scene.services.backend = systemFixture;
                    scene.services.brightness = {
                        state: "ready",
                        percent: 70
                    };
                    scene.services.profiles = {
                        state: "ready",
                        profiles: ["power-saver", "balanced", "performance"],
                        current: "balanced"
                    };
                }
            }
        }
        Item {
            id: atlas
            visible: root.stage >= 46 && root.stage < 100
            Repeater {
                id: icons
                model: root.kinds.length * root.sizes.length
                Icon {
                    required property int index
                    kind: root.kinds[Math.floor(index / root.sizes.length)]
                    width: root.sizes[index % root.sizes.length]
                    height: width
                    x: (index % 24) * 48
                    y: Math.floor(index / 24) * 48 + 700
                    ink: "white"
                }
            }
        }
    }
    TestCase {
        id: pointer
        name: "LiquidPointer"
        when: false
    }
    Component {
        id: fixtureMedia
        MediaBlock {
            players: [
                {
                    dbusName: "fixture",
                    identity: "Fixture player",
                    trackTitle: "Fixture track",
                    trackArtist: "Fixture artist",
                    isPlaying: true,
                    canTogglePlaying: true,
                    canGoNext: true,
                    canGoPrevious: true
                },
                // A second player: the row of players and the chosen one's orange drop.
                {
                    dbusName: "fixture-two",
                    identity: "Second player",
                    trackTitle: "Second track",
                    trackArtist: "Fixture artist",
                    isPlaying: false,
                    canTogglePlaying: true,
                    canGoNext: false,
                    canGoPrevious: false
                }
            ]
        }
    }
    Timer {
        id: tick
        interval: 700
        onTriggered: {
            if (root.stage === 0) {
                if (root.fallback) {
                    if (scene.wallpaper.state !== root.fallback || scene.bar.systemGlass.glassReady)
                        throw new Error("Incorrect flat fallback");
                    root.save(canvas, "bar-flat-" + root.fallback, () => {
                        console.log("FLAT_FALLBACK_OK", root.fallback);
                        console.log("SHOTS_COMPLETE");
                        Qt.quit();
                    });
                    return;
                }
                if (!scene.wallpaper.ready) {
                    if (++root.wallpaperWaits > 12) {
                        console.error("Wallpaper fixture not ready", scene.wallpaper.state);
                        Qt.quit();
                        return;
                    }
                    tick.restart();
                    return;
                }
                root.save(scene.bar.dateLabel, "date-text", () => root.save(canvas, "bar", () => {
                        root.stage = 1;
                        pointer.mouseMove(scene.bar.clock, scene.bar.clock.width * .8, 18);
                        tick.restart();
                    }));
            } else if (root.stage === 1) {
                // The clock island is on glass: hovering turns it into a Clear drop.
                if (JSON.parse(scene.status()).clock_glass.island.alpha < .99)
                    throw new Error("Hover did not raise the clock island's drop");
                root.save(canvas, "bar-hover", () => {
                    root.stage = 2;
                    // The launcher button turns into a Clear bubble under the pointer.
                    pointer.mouseMove(scene.bar.logo, 18, 18);
                    tick.restart();
                });
            } else if (root.stage === 2) {
                if (JSON.parse(scene.status()).left_glass.logo.alpha < .99)
                    throw new Error("Hover did not raise the button's bubble");
                root.save(canvas, "bar-logo-hover", () => {
                    pointer.mouseMove(canvas, 1000, 600);
                    root.stage = 3;
                    scene.openLauncher();
                    tick.restart();
                });
            } else if (root.stage === 3) {
                const glass = JSON.parse(scene.status()).launcher_glass;
                if (glass.mode.alpha < .99 || glass.select.alpha < .99 || glass.select_shape !== "tile")
                    throw new Error("Launcher drops missing: " + JSON.stringify(glass));
                root.save(canvas, "launcher", () => {
                    root.stage = 4;
                    // The arrows move the one selection bubble to the next tile.
                    pointer.keyClick(Qt.Key_Right);
                    tick.restart();
                });
            } else if (root.stage === 4) {
                if (JSON.parse(scene.status()).search.selected_index !== 1)
                    throw new Error("Arrow did not move the selection");
                root.save(canvas, "launcher-selected", () => {
                    root.stage = 200;
                    scene.context.settingsController.openOn(scene, "panel");
                    tick.restart();
                });
            } else if (root.stage === 200) {
                const state = JSON.parse(scene.status());
                if (state.launcher_panel.width !== Math.min(1240, state.system.x - 20) || state.launcher_panel.height !== 810)
                    throw new Error("Settings did not grow inside the launcher: " + JSON.stringify(state.launcher_panel));
                if (!scene.input.settingsActive || !scene.input.settingsView)
                    throw new Error("Settings view is not hosted by the launcher");
                root.save(canvas, "launcher-settings", () => {
                    root.stage = 5;
                    scene.closeAll();
                    tick.restart();
                });
            } else if (root.stage === 5) {
                // The system island on glass: the pointer on the sound cell raises its drop.
                pointer.mouseMove(scene.bar.systemGlass.row.cell("sound"), 15, 13);
                root.stage = 100;
                tick.restart();
            } else if (root.stage === 100) {
                const island = JSON.parse(scene.status()).system_glass.island;
                if (island.alpha < .99 || island.hover_key !== "cell-sound")
                    throw new Error("Hover did not raise the system cell's drop: " + JSON.stringify(island));
                root.save(canvas, "bar-system-hover", () => {
                    pointer.mouseMove(canvas, 1000, 600);
                    root.stage = 6;
                    // The drop melts after the hide delay; then everything must be at rest.
                    idle.start();
                });
            } else if (root.stage === 6) {
                // At rest the liquid frame loops stop: no frame is drawn for nothing.
                if (scene.bar.leftIslands.animating || scene.input.animating || scene.bar.clockIsland.animating || scene.clockPanel.animating || scene.bar.systemGlass.animating || scene.systemPanel.animating)
                    throw new Error("Liquid motion did not settle");
                console.log("MATERIAL_IDLE_OK");
                root.stage = 7;
                const now = Date.now();
                scene.notifications.entries = [0, 1, 2, 40, 60, 800].map((age, i) => ({
                            id: 900 + i,
                            app: i < 4 ? "Chat" : "Build",
                            summary: i < 4 ? "A fixture message" : "Fixture task complete",
                            body: "Synthetic notification for review",
                            time: now - age * 60000,
                            object: null,
                            deadline: 0,
                            actions: []
                        }));
                scene.openDrawer();
                tick.restart();
            } else if (root.stage === 7) {
                root.save(canvas, "drawer", () => {
                    root.stage = 8;
                    scene.notifications.dnd = true;
                    tick.restart();
                });
            } else if (root.stage === 8) {
                root.save(canvas, "drawer-dnd", () => {
                    root.stage = 9;
                    scene.closeAll();
                    scene.notifications.dnd = false;
                    scene.showNotification(900);
                    tick.restart();
                });
            } else if (root.stage === 9) {
                root.save(canvas, "notification-peek", () => {
                    root.stage = 10;
                    for (let id = 901; id <= 905; ++id)
                        scene.showNotification(id);
                    tick.restart();
                });
            } else if (root.stage === 10) {
                root.save(canvas, "notification-flood", () => {
                    root.stage = 11;
                    scene.closeAll();
                    tick.restart();
                });
            } else if (root.stage === 11) {
                scene.openLauncher();
                scene.input.setMode("Clipboard");
                root.stage = 12;
                tick.restart();
            } else if (root.stage === 12) {
                scene.input.clipboard.entries = [
                    {
                        id: "10",
                        preview: "A synthetic clipboard item",
                        image: false
                    },
                    {
                        id: "11",
                        preview: "[[ binary data png 1x1 ]]",
                        image: true
                    }
                ];
                root.stage = 13;
                tick.restart();
            } else if (root.stage === 13) {
                root.save(canvas, "launcher-clipboard", () => {
                    scene.input.setMode("All");
                    scene.input.setQuery("emaki documentation");
                    root.stage = 14;
                    tick.restart();
                });
            } else if (root.stage === 14) {
                root.save(canvas, "launcher-web", () => {
                    scene.input.setMode("Apps"); // an empty All is Recent now; Frequent lives in Apps
                    scene.input.setQuery("");
                    scene.input.appCatalog.counts = ({
                            "fixture-browser": 9,
                            "fixture-editor": 4
                        });
                    root.stage = 15;
                    tick.restart();
                });
            } else if (root.stage === 15) {
                root.save(canvas, "launcher-frequent", () => {
                    scene.clockBody.mediaSource = fixtureMedia;
                    scene.clockBody.mediaEnabled = true;
                    scene.openDrawer();
                    root.stage = 18;
                    tick.restart();
                });
            } else if (root.stage === 18) {
                root.save(canvas, "drawer-media", () => {
                    scene.closeAll();
                    scene.systemBody.service.canHibernate = true;
                    scene.openSystem("sound");
                    root.stage = 19;
                    tick.restart();
                });
            } else if (root.stage >= 19 && root.stage <= 26) {
                const pages = ["sound", "power", "wifi", "bt", "light", "kb", "tray", "power-confirm"];
                // The sound page on glass: the tab, the knobs of volume, microphone and the
                // app, the chosen output and input rest under their drops.
                const glass = JSON.parse(scene.status()).system_glass.panel;
                if (root.stage === 19 && (!glass.tab || glass.tab.alpha < .99 || glass.tab_page !== "sound" || glass.knobs < 3 || glass.rows < 2))
                    throw new Error("System panel drops missing: " + JSON.stringify(glass));
                // Only the open page's tab is the orange drop (knobs and chosen rows stay plain).
                if (root.stage === 19 && glass.selected !== 1)
                    throw new Error("System panel: not exactly the tab selected: " + JSON.stringify(glass));
                if (root.stage === 19)
                    console.log("SELECTED_DROPS " + JSON.stringify({
                        shot: "system-sound",
                        orange: [glass.tab],
                        plain: []
                    }));
                root.save(canvas, "system-" + pages[root.stage - 19], () => {
                    ++root.stage;
                    if (root.stage === 26) {
                        scene.openSystem("power");
                        scene.systemBody.session("hibernate");
                    } else if (root.stage <= 25)
                        scene.openSystem(pages[root.stage - 19]);
                    else {
                        scene.closeAll();
                        scene.barPolicy.autoHide = true;
                    }
                    tick.restart();
                });
            } else if (root.stage >= 27 && root.stage <= 31) {
                const names = ["bar-hidden", "bar-revealed", "bar-overview", "bar-overview-hidden", "bar-tooltip"];
                root.save(canvas, names[root.stage - 27], () => {
                    ++root.stage;
                    if (root.stage === 28)
                        scene.barPolicy.revealed = true;
                    if (root.stage === 29)
                        niri.model = Object.assign({}, niri.model, {
                            overview_open: true
                        });
                    if (root.stage === 30)
                        scene.barPolicy.overviewWorkspaces = false;
                    if (root.stage === 31) {
                        scene.barPolicy.autoHide = false;
                        niri.model = Object.assign({}, niri.model, {
                            overview_open: false
                        });
                        scene.tip.offer(scene.bar.logo, "Launcher", "Super+D");
                    }
                    if (root.stage === 32) {
                        scene.tip.hide();
                        // Dock fixture: pinned IDs and private labels are synthetic; no niri socket.
                        scene.dockStore.autoHide = false;
                        scene.dockStore.pinned = ["fixture-browser", "fixture-editor", "fixture-files"];
                        scene.dockLabels.values = ({
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
                            });
                    }
                    tick.restart();
                });
            } else if (root.stage >= 32 && root.stage <= 40) {
                const names = ["dock", "dock-list", "dock-menu", "osd-sound", "osd-light", "privacy", "privacy-open", "wifi-portal", "wifi-hidden"];
                root.save(canvas, names[root.stage - 32], () => {
                    ++root.stage;
                    if (root.stage === 33)
                        scene.dock.activate("fixture-editor");
                    if (root.stage === 34)
                        scene.dock.menu("fixture-editor");
                    if (root.stage === 35) {
                        scene.dock.closePopup();
                        // A real value change on the fixture device drives the OSD, not a direct call.
                        systemFixture.output1.audio.volume = .5;
                    }
                    if (root.stage === 36)
                        scene.services.brightness = {
                            state: "ready",
                            percent: 40
                        };
                    if (root.stage === 37) {
                        scene.osd.dismiss();
                        systemFixture.captures = [
                            {
                                kind: "mic",
                                name: "Fixture call"
                            }
                        ];
                        niri.model = Object.assign({}, niri.model, {
                            casts: {
                                "1": {
                                    stream_id: 1,
                                    session_id: 1,
                                    kind: "pipewire",
                                    target: "output",
                                    output: "Fixture",
                                    window_id: null,
                                    is_active: true
                                }
                            }
                        });
                    }
                    if (root.stage === 38)
                        scene.togglePrivacy();
                    if (root.stage === 39) {
                        scene.closeAll();
                        systemFixture.captures = [];
                        niri.model = Object.assign({}, niri.model, {
                            casts: {}
                        });
                        systemFixture.connectivity = "portal";
                        scene.services.vpn = {
                            state: "ready",
                            connections: [
                                {
                                    name: "Home",
                                    kind: "wireguard",
                                    uuid: "11111111-2222-3333-4444-555555555555",
                                    active: false
                                }
                            ]
                        };
                        scene.openSystem("wifi");
                    }
                    if (root.stage === 40) {
                        systemFixture.connectivity = "full";
                        scene.systemBody.hiddenOpen = true;
                    }
                    if (root.stage === 41) {
                        // Recent: the profile's recent.json (written by shell-shots.py) shown in an empty All.
                        scene.closeAll();
                        scene.openLauncher();
                        scene.input.setMode("All");
                    }
                    tick.restart();
                });
            } else if (root.stage === 41) {
                root.save(canvas, "launcher-recent", () => {
                    // Search in All: rows under kind headings, the first one in the bubble.
                    scene.input.setQuery("edit");
                    root.stage = 42;
                    tick.restart();
                });
            } else if (root.stage === 42) {
                root.save(canvas, "launcher-search", () => {
                    scene.closeAll();
                    scene.openDrawer();
                    root.stage = 44;
                    tick.restart();
                });
            } else if (root.stage === 44) {
                // The pointer on the first notification row: the hover drop flows to it.
                const row = root.findTarget(scene.clockPanel, "note-");
                if (!row)
                    throw new Error("No notification row in the drawer");
                pointer.mouseMove(row, row.width / 2, row.height / 2);
                root.stage = 45;
                tick.restart();
            } else if (root.stage === 45) {
                const glass = JSON.parse(scene.status()).clock_glass.panel;
                if (glass.hover.alpha < .99 || !glass.hover_key.startsWith("note-") || glass.today.alpha < .99 || glass.knob.alpha < .99 || glass.player.alpha < .99)
                    throw new Error("Clock panel drops missing: " + JSON.stringify(glass));
                // Today and the chosen player are orange, the hover drop on the row is plain.
                if (glass.selected !== 2)
                    throw new Error("Clock panel: not exactly today and the player selected: " + JSON.stringify(glass));
                console.log("SELECTED_DROPS " + JSON.stringify({
                    shot: "drawer-hover",
                    orange: [glass.today, glass.player],
                    plain: [glass.hover]
                }));
                root.save(canvas, "drawer-hover", () => {
                    pointer.mouseMove(canvas, 100, 900);
                    scene.closeAll();
                    root.stage = 46;
                    tick.restart();
                });
            } else if (root.iconIndex < icons.count) {
                const icon = icons.itemAt(root.iconIndex);
                root.save(icon, "icon-" + icon.kind + "-" + icon.width, () => {
                    ++root.iconIndex;
                    tick.interval = 1;
                    tick.restart();
                });
            } else {
                console.log("SHOTS_COMPLETE");
                Qt.quit();
            }
        }
    }
    Timer {
        id: idle
        interval: 900
        onTriggered: tick.restart()
    }
    Component.onCompleted: Qt.callLater(() => {
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
                        workspace_id: 102,
                        layout: {
                            tile_size: [800, 880]
                        }
                    },
                    "503": {
                        id: 503,
                        workspace_id: 103
                    }
                },
                keyboard_layouts: {
                    names: ["English (US)", "Russian"],
                    current_idx: 0
                }
            }
        }));
        scene.bar.dateTime = new Date(2026, 8, 23, 14, 32, 0);
        scene.panel.visible = Qt.binding(() => scene.launcherPresent);
        scene.input.setMode("Apps");
        tick.start();
    })
}
