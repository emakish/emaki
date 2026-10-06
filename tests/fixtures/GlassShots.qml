pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell
import QtTest

// Run only through tests/glass-shots.py. Software Qt cannot draw a ShaderEffect, so for each
// state this dumps what the GPU would get instead: every visible glass draw of the left
// islands and the launcher (item rect, uniforms), the content layer under the glass, the
// layer drawn on it, and the other islands of the bar. The harness page renders
// shaders/dock.frag from it in WebGL2.
ShellRoot {
    id: root
    readonly property string destination: Quickshell.env("EMAKI_GLASS_SHOT_DIR")
    readonly property real shotScale: Number(Quickshell.env("QT_SCALE_FACTOR") || "1")
    // EMAKI_GLASS_MOTION=1: instead of the still states, frames of drops in flight (motionSteps).
    readonly property bool motion: Quickshell.env("EMAKI_GLASS_MOTION") === "1"
    readonly property var states: motion ? ["closed"].concat(Object.keys(motionSteps)) : ["closed", "logo-hover", "open", "selected", "search", "clock-hover", "clock-open", "clock-row-hover", "clock-day-hover", "clock-player-hover", "clock-dnd", "clock-closed-dnd", "clock-peek", "system-hover", "system-sound", "system-sound-hover", "system-wifi", "system-bt", "system-bt-cell-hover", "system-power", "system-power-confirm", "system-osd", "privacy", "privacy-open", "lock-pour-50", "lock-locked", "lock-wrong", "lock-melt", "lock-drain-50"]
    // ---- Motion: each state is one exact frame. The owner's FrameAnimation is stopped and its
    // frame() is called on a clock of our own (60 fps), so a dump shows the drop where it is
    // after that many frames, not wherever the offscreen timer happened to leave it. `roi` is
    // the region glass-shots.py measures.
    property real clock: 0
    property var roi: null
    function freeze(owner: Item): void {
        for (let i = 0; i < owner.data.length; ++i) {
            const o = owner.data[i];
            if (o && o.frameTime !== undefined && o.running !== undefined) {
                o.running = false;
                root.clock = Date.now();
                return;
            }
        }
        throw new Error("no FrameAnimation in " + owner);
    }
    function frames(owner: var, count: int): void {
        for (let i = 0; i < count; ++i) {
            root.clock += 1000 / 60;
            try {
                owner.frame(root.clock, 1 / 60);
            } catch (e) {
                console.log("FRAME ERROR " + e + " " + e.stack);
                throw e;
            }
        }
    }
    function islandRoi(): var {
        const h = scene.bar.systemGlass.hit, p = h.mapToItem(scene, 0, 0);
        return [p.x - 6, p.y - 6, h.width + 12, h.height + 12];
    }
    readonly property var motionSteps: {
        const steps = {};
        const island = () => scene.bar.systemGlass;
        const cellPoint = page => {
            const c = island().row.cell(page);
            return c.mapToItem(scene, c.width / 2, c.height / 2);
        };
        const name = (prefix, i) => prefix + "-" + (i < 10 ? "0" : "") + i;
        // The named reference: the pointer sweeps the launcher's frequent tiles the same
        // way (10 px a frame), where no flicker is seen.
        const tilePoint = i => {
            const r = scene.input.geometryOf(i).rect;
            return scene.input.mapToItem(scene, r.x + r.width / 2, r.y + r.height / 2);
        };
        steps[name("launcher", 0)] = () => {
            root.roi = null;
            scene.input.appCatalog.counts = root.frequent;
            scene.openLauncher();
            pointer.mouseMove(scene, 1100, 900);
        };
        steps[name("launcher", 1)] = () => {
            root.freeze(scene.input);
            const p = tilePoint(0);
            pointer.mouseMove(scene, p.x, p.y);
            root.frames(scene.input, 60);
            const a = scene.input.geometryOf(0).rect, b = scene.input.geometryOf(3).rect, o = scene.input.mapToItem(scene, a.x, a.y);
            root.roi = [o.x - 10, o.y - 10, b.x + b.width - a.x + 20, a.height + 20];
        };
        for (let i = 1; i <= 24; ++i)
            steps[name("launcher", i + 1)] = () => {
                const p = tilePoint(0);
                pointer.mouseMove(scene, p.x + i * 10, p.y);
                root.frames(scene.input, 1);
            };
        // (a) The pointer sweeps the system island from the tray cell to the battery at 10 px a
        // frame (600 px/s), one frame per state.
        steps[name("sweep", 0)] = () => {
            scene.closeAll();
            scene.endPeek();
            root.freeze(island());
            const p = cellPoint("tray");
            pointer.mouseMove(scene, p.x, p.y);
            root.frames(island(), 60);
            root.roi = root.islandRoi();
        };
        for (let i = 1; i <= 24; ++i)
            steps[name("sweep", i)] = () => {
                const p = cellPoint("tray");
                pointer.mouseMove(scene, p.x + i * 10, p.y);
                root.frames(island(), 1);
            };
        // (b) The drop rests on the sound cell, the pointer leaves the island: it waits 300 ms,
        // then melts in 220 ms. Frames counted from the leave.
        steps[name("melt", 0)] = () => {
            const p = cellPoint("sound");
            pointer.mouseMove(scene, p.x, p.y);
            root.frames(island(), 60);
        };
        const melt = [1, 6, 12, 18, 20, 22, 24, 26, 28, 30, 32];
        melt.forEach((frame, i) => {
            steps[name("melt", i + 1)] = () => {
                if (i === 0)
                    pointer.mouseMove(scene, 700, 600);
                root.frames(island(), frame - (i ? melt[i - 1] : 0));
            };
        });
        // The open panel (sound page): the pointer on a row, then off the panel — the row's
        // drop waits and melts; then a click on the Wi-Fi cell — the orange tab flows over to it.
        steps[name("panelmelt", 0)] = () => {
            root.roi = null;
            scene.openSystem("sound");
            pointer.mouseMove(scene, 1100, 500);
        };
        steps[name("panelmelt", 1)] = () => {
            root.freeze(scene.systemPanel);
            const row = root.findTarget(scene.systemPanel, "output-2");
            root.pointAt(row);
            root.frames(scene.systemPanel, 60);
            const o = row.mapToItem(scene, 0, 0);
            root.roi = [o.x - 12, o.y - 12, row.width + 24, row.height + 24];
        };
        melt.forEach((frame, i) => {
            steps[name("panelmelt", i + 2)] = () => {
                if (i === 0)
                    pointer.mouseMove(scene, 300, 700);
                root.frames(scene.systemPanel, frame - (i ? melt[i - 1] : 0));
            };
        });
        const tab = [1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 18, 22, 26, 30, 40];
        steps[name("tabflow", 0)] = () => {
            root.frames(scene.systemPanel, 60);
            const t = root.findTarget(scene.systemPanel, "cell-tray"), o = t.mapToItem(scene, 0, 0);
            root.roi = [o.x - 14, o.y - 11, 290, 48];
        };
        tab.forEach((frame, i) => {
            steps[name("tabflow", i + 1)] = () => {
                if (i === 0) {
                    const c = root.findTarget(scene.systemPanel, "cell-wifi");
                    pointer.mouseClick(c, c.width / 2, c.height / 2);
                }
                root.frames(scene.systemPanel, frame - (i ? tab[i - 1] : 0));
            };
        });
        // (c) The drawer with three players; the pointer on Telegram, then a click: the chosen
        // player's resting drop flows from Firefox to it.
        steps[name("player", 0)] = () => {
            root.roi = null;
            scene.openDrawer();
            pointer.mouseMove(scene, 1300, 900);
        };
        steps[name("player", 1)] = () => {
            const panel = scene.clockPanel;
            root.freeze(panel);
            root.pointAt(root.findTarget(panel, "player-telegram"));
            root.frames(panel, 60);
            const first = root.findTarget(panel, "player-firefox"), p = first.mapToItem(scene, 0, 0);
            root.roi = [p.x - 8, p.y - 8, 420, 26 + 16];
        };
        const player = [1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 18, 22, 26, 30, 40];
        player.forEach((frame, i) => {
            steps[name("player", i + 2)] = () => {
                const panel = scene.clockPanel;
                if (i === 0) {
                    const t = root.findTarget(panel, "player-telegram");
                    pointer.mouseClick(t, t.width / 2, t.height / 2);
                }
                root.frames(panel, frame - (i ? player[i - 1] : 0));
            };
        });
        return steps;
    }
    // The system island's data of the mockup (system.js), never a real device.
    SystemMockup {
        id: systemMockup
    }
    property int stage: -1
    property var frequent: ({})
    property int waits: 0
    property int noteId: 0
    // The mockup's players (clock.js PLAYERS), as MPRIS would describe them.
    Component {
        id: fixtureMedia
        MediaBlock {
            players: [
                {
                    dbusName: "firefox",
                    identity: "Firefox",
                    desktopEntry: "firefox",
                    trackTitle: "Тестовая дорожка с длинным названием",
                    trackArtist: "Исполнитель",
                    isPlaying: true,
                    canTogglePlaying: true,
                    canGoNext: true,
                    canGoPrevious: true
                },
                {
                    dbusName: "telegram",
                    identity: "Telegram · Pixel",
                    desktopEntry: "org.telegram.desktop",
                    trackTitle: "Voice message",
                    trackArtist: "Alex",
                    isPlaying: false,
                    canTogglePlaying: true,
                    canGoNext: false,
                    canGoPrevious: false
                },
                {
                    dbusName: "youtube",
                    identity: "YouTube · Pixel",
                    desktopEntry: "firefox",
                    trackTitle: "niri 25.08 release notes",
                    trackArtist: "Sample channel",
                    isPlaying: false,
                    canTogglePlaying: true,
                    canGoNext: true,
                    canGoPrevious: true
                }
            ]
        }
    }
    // The mockup's notifications (clock.js NOTES): five apps and a burst of 63 from kitty.
    function fixtureNotes(): void {
        const now = Date.now();
        const rows = [["kitty", "kitty", "Build", "make check finished: all tests passed", 2], ["Telegram", "org.telegram.desktop", "Alex", "Dinner at 7? I will bring the bread", 14], ["Thunderbird", "org.mozilla.Thunderbird", "Library · loan #1042", "Book returned · thank you for visiting", 48], ["Signal", "signal", "Sam", "Photos from the garden", 95], ["Firefox", "firefox", "Download finished", "emaki-0.3.pkg.tar.zst · 184 MB", 130]];
        for (let i = 0; i < 63; ++i)
            rows.push(["kitty", "kitty", "Build", "make check finished: all tests passed", 11 + i * 7]);
        const entries = rows.map((r, i) => ({
                    id: 7000 + i,
                    app: r[0],
                    icon: r[1],
                    summary: r[2],
                    body: r[3],
                    time: now - r[4] * 60000,
                    object: null,
                    actions: [],
                    deadline: 0
                }));
        root.noteId = 7001;
        scene.notifications.now = now;
        scene.notifications.entries = entries.sort((a, b) => b.time - a.time);
    }
    // The uniforms of shaders/dock.frag (the buf block without qt_Matrix/qt_Opacity).
    readonly property var uniforms: ["uOrigin", "uItemSize", "uMaterial", "uRegularMix", "uRegularBlur", "uRegularLight", "uRegularDark", "uRegularContrast", "uRegularTint", "uRegularSaturation", "uRegularDock", "uIsDock", "uFlat", "uFlatColor", "uSceneSize", "uCover", "uRect", "uRectB", "uRadius", "uRadiusB", "uUnion", "uHasB", "uEdge", "uThickness", "uRefraction", "uDispersion", "uLightAngle", "uSpecular", "uShininess", "uRim", "uRimWidth", "uInnerShadow", "uInnerWidth", "uShadow", "uShadowSoftness", "uShadowOffset", "uTint", "uTintColor", "uAdaptation", "uContrast", "uSaturation", "uBlur", "uActivity", "uOpacity", "uTrack", "uTrackValue", "uTrackEnabled", "uOnlyTrack", "uRectC", "uHasC", "uRadiusC", "uUnionC", "uDropCount", "uDrop0", "uDrop1", "uDrop2", "uDrop3", "uDrop4", "uDrop5", "uDrop6", "uDrop7", "uDropParam0", "uDropParam1", "uDropParam2", "uDropParam3", "uDropParam4", "uDropParam5", "uDropParam6", "uDropParam7", "uBulge", "uDematerialize", "uHasIcons", "uDropDome", "uRimLight", "uRimLightWidth", "uRegularBase", "uLiveTop", "uRegularBaseB", "uSelectedColor", "uDropSelected0", "uDropSelected1", "uWaveEnabled", "uWaveEdge", "uSharpRect", "uSharpBounds", "uWallSharpRect", "uWallSharpBounds", "uHasDynamicIcons", "uDynamicRect"]

    function value(v: var): var {
        if (typeof v === "number" || typeof v === "boolean")
            return Number(v);
        if (v.w !== undefined)
            return [v.x, v.y, v.z, v.w];
        if (v.z !== undefined)
            return [v.x, v.y, v.z];
        return [v.x, v.y];
    }
    // `layer` is the surface (bar, launcher, clock: the order they are composited in), `region`
    // the backdrop region it refracts, `ink` the name of the grab under its glass.
    function glassDraw(shape: Item, layer: string, region: string, ink: string): var {
        const p = shape.mapToItem(null, 0, 0);
        const u = {};
        for (const name of uniforms)
            u[name] = value(shape[name]);
        for (const name of ["uSharpRect", "uSharpBounds", "uWallSharpRect", "uWallSharpBounds", "uDynamicRect"])
            u[name] = [0, 0, 1, 1];
        u.uHasDynamicIcons = 0;
        return {
            layer: layer,
            region: region,
            ink: ink,
            rect: [p.x, p.y, shape.width, shape.height],
            // Where the glass's scene origin lies on the screen (the OSD's scene is its own).
            origin: [p.x - u.uOrigin[0], p.y - u.uOrigin[1]],
            uniforms: u
        };
    }
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
    function pointAt(item: Item): void {
        const p = item.mapToItem(scene, item.width / 2, item.height / 2);
        pointer.mouseMove(scene, p.x, p.y);
    }
    // Software Qt drops the alpha of a grab whose subtree holds a fully opaque node (a plain
    // Rectangle, an icon without alpha): the image comes back RGB on black. So every layer is
    // grabbed twice over a backing of its own, black then white, and the driver recovers
    // straight alpha from the pair (a = 1 − (white − black)).
    function grab(item: Item, name: string, list: var, done: var): void {
        const p = item.mapToItem(null, 0, 0);
        const file = name + "@" + shotScale + "x.png";
        const one = (colour, suffix, then) => {
            const backing = Qt.createQmlObject('import QtQuick; Rectangle { z: -100000; color: "' + colour + '" }', item);
            backing.width = item.width;
            backing.height = item.height;
            const accepted = item.grabToImage(result => {
                backing.destroy();
                if (!result.saveToFile(destination + "/" + name + suffix + "@" + shotScale + "x.png"))
                    throw new Error("Grab save failed");
                then();
            }, Qt.size(item.width, item.height));
            if (!accepted)
                throw new Error("Grab failed: " + name);
        };
        one("black", "", () => one("white", "-white", () => {
                list.push({
                    name: name,
                    file: file,
                    white: name + "-white@" + shotScale + "x.png",
                    rect: [p.x, p.y, item.width, item.height]
                });
                done();
            }));
    }
    // Grabs one after another, then writes the state's JSON.
    // The production lock shader, isolated from PAM and session-lock. No screen capture.
    function dumpLock(state: string, next: var): void {
        const draws = [glassDraw(lockGlass.plateGlass, "lock", "lock", "lock-ink")];
        const grabs = [];
        // All lock moments share the same completed pour at t=.9. Pin the intro origin
        // explicitly so QML binding evaluation order cannot shift these fixed shots.
        lockWordmark.introStart = .9;
        lockWordmark.requestFrame();
        grab(lockInk, state + "-lock-ink", grabs, () => {
            grabs[0].kind = "on-lock";
            // Independent opacity: controls sink during melt; the wordmark persists and
            // is still painted during drain, when the field's ink has disappeared.
            grab(lockWordmarkLayer, state + "-lock-wordmark", grabs, () => {
                grabs[1].kind = "on-lock";
                console.log("GLASS " + JSON.stringify({
                    state: state,
                    scale: shotScale,
                    viewport: [window.width, window.height],
                    regions: {
                        lock: [0, 0, window.width, window.height]
                    },
                    draws: draws,
                    grabs: grabs,
                    islands: {},
                    roi: null,
                    launcher: {},
                    left: {},
                    clock: {},
                    system: {},
                    lock: {
                        phase: lockGlass.phase,
                        elapsed: lockGlass.elapsed,
                        fieldLife: lockGlass.fieldLife,
                        avatarLife: lockGlass.avatarLife,
                        wordmark: {
                            visible: lockWordmark.visible,
                            idleMode: lockWordmark.idleMode,
                            introAge: lockWordmark.introAge,
                            simulatedElapsed: lockWordmark.simulatedElapsed
                        }
                    }
                }));
                next();
            });
        });
    }
    function dump(state: string, next: var): void {
        if (state.startsWith("lock-")) {
            dumpLock(state, next);
            return;
        }
        const left = scene.bar.leftIslands;
        const body = scene.input;
        const clock = scene.bar.clockIsland;
        const clockPanel = scene.clockPanel;
        const draws = [];
        if (!scene.launcherPresent && left.logoOpacity > .001)
            draws.push(glassDraw(left.logoGlass, "bar", "bar", "bar-ink"));
        if (left.workspaceOpacity > .001)
            draws.push(glassDraw(left.workspaceGlass, "bar", "bar", "bar-ink"));
        if (clock.shown)
            draws.push(glassDraw(clock.glass, "bar", "bar", "clock-ink"));
        if (scene.launcherPresent && body.morph > 0)
            draws.push(glassDraw(body.panelGlass, "launcher", "launcher", "launcher-ink"));
        if (scene.clockPresent)
            draws.push(glassDraw(clockPanel.panelGlass, "clock", "clock", "clockpanel-ink"));
        const system = scene.bar.systemGlass;
        if (system.shown)
            draws.push(glassDraw(system.glass, "bar", "bar", "system-ink"));
        if (scene.systemPresent)
            draws.push(glassDraw(scene.systemPanel.panelGlass, "system", "system", "systempanel-ink"));
        if (scene.osd.shown)
            draws.push(glassDraw(scene.osd.glass, "system", "system", "osd-ink"));
        const privacy = scene.privacy;
        if (privacy.visible && privacy.shown)
            draws.push(glassDraw(privacy.glass, "bar", "bar", "privacy-ink"));
        if (scene.privacyPresent)
            draws.push(glassDraw(scene.privacyPopup.panelGlass, "system", "system", "privacypanel-ink"));
        const grabs = [];
        const queue = [[left.inkLayer, state + "-bar-ink", "under"], [body.contentLayer, state + "-launcher-ink", "under"], [clock.inkLayer, state + "-clock-ink", "under"], [system.inkLayer, state + "-system-ink", "under"]];
        if (scene.clockPresent)
            queue.push([clockPanel.contentLayer, state + "-clockpanel-ink", "under"]);
        if (scene.systemPresent)
            queue.push([scene.systemPanel.contentLayer, state + "-systempanel-ink", "under"]);
        if (scene.osd.shown)
            queue.push([scene.osd.inkLayer, state + "-osd-ink", "under"]);
        if (privacy.visible)
            queue.push([privacy.inkLayer, state + "-privacy-ink", "under"]);
        if (scene.privacyPresent)
            queue.push([scene.privacyPopup.contentLayer, state + "-privacypanel-ink", "under"]);
        if (privacy.visible && privacy.lineOnGlass.visible)
            queue.push([privacy.lineOnGlass, state + "-privacy-line", "on-bar"]);
        if (scene.privacyPresent)
            queue.push([scene.privacyPopup.onGlassLayer, state + "-privacypanel-on", "on-system"]);
        if (left.arrowOnGlass.visible)
            queue.push([left.arrowOnGlass, state + "-bar-arrow", "on-bar"]);
        if (clock.lineOnGlass.visible)
            queue.push([clock.lineOnGlass, state + "-clock-line", "on-bar"]);
        if (system.shown)
            queue.push([system.onGlassLayer, state + "-system-on", "on-bar"]);
        if (scene.launcherPresent && body.arrowOnGlass.visible)
            queue.push([body.arrowOnGlass, state + "-launcher-arrow", "on-launcher"]);
        if (scene.launcherPresent && body.onGlassLayer.visible)
            queue.push([body.onGlassLayer, state + "-launcher-on", "on-launcher"]);
        if (scene.clockPresent)
            queue.push([clockPanel.onGlassLayer, state + "-clockpanel-on", "on-clock"]);
        if (scene.systemPresent)
            queue.push([scene.systemPanel.onGlassLayer, state + "-systempanel-on", "on-system"]);
        const kinds = {};
        const step = () => {
            if (!queue.length) {
                console.log("GLASS " + JSON.stringify({
                    state: state,
                    scale: shotScale,
                    viewport: [scene.viewportWidth, scene.viewportHeight],
                    // The backdrop regions of Surfaces.qml (bar band, launcher corner).
                    regions: {
                        bar: [0, 0, scene.viewportWidth, Math.min(scene.viewportHeight, 240)],
                        launcher: [0, 0, Math.min(scene.viewportWidth, Metrics.logoX + scene.panelWidth + 120), Math.min(scene.viewportHeight, Metrics.top + Metrics.launcherHeader + Metrics.launcherBodyMax + Metrics.launcherFoot + 120)],
                        clock: [Math.max(0, Math.floor((scene.viewportWidth - 800) / 2)), 0, Math.min(scene.viewportWidth, 800), scene.viewportHeight],
                        system: [Math.max(0, scene.viewportWidth - 800), 0, Math.min(scene.viewportWidth, 800), scene.viewportHeight]
                    },
                    draws: draws,
                    grabs: grabs.map(g => Object.assign(g, {
                            kind: kinds[g.name]
                        })),
                    islands: {},
                    roi: root.roi,
                    launcher: JSON.parse(scene.status()).launcher_glass,
                    left: JSON.parse(scene.status()).left_glass,
                    clock: JSON.parse(scene.status()).clock_glass,
                    system: JSON.parse(scene.status()).system_glass
                }));
                next();
                return;
            }
            const [item, name, kind] = queue.shift();
            kinds[name] = kind;
            grab(item, name, grabs, step);
        };
        step();
    }

    NiriService {
        id: niri
        binary: ""
    }
    Window {
        id: window
        visible: true
        width: 1536
        height: 960
        color: "transparent"
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
    // Deterministic moments from the approved mockup. Ink is intentionally a fixture;
    // the real text input/auth are exercised independently and never fed to GPU shots.
    LockGlass {
        id: lockGlass
        parent: window.contentItem
        anchors.fill: parent
        visible: root.stage >= 0 && root.stage < root.states.length && root.states[root.stage].startsWith("lock-")
        phase: root.states[root.stage] === "lock-pour-50" ? "pour" : root.states[root.stage] === "lock-melt" ? "melt" : root.states[root.stage] === "lock-drain-50" ? "drain" : "locked"
        elapsed: phase === "pour" ? .45 : phase === "drain" ? .5 : phase === "melt" ? .11 : 1
        clock: phase === "pour" ? .45 : phase === "drain" ? 3.72 : phase === "melt" ? 3.11 : 2
        bubbleAge: phase === "pour" ? -1 : 1
        wrongAge: root.states[root.stage] === "lock-wrong" ? .09 : 1000
        dpr: root.shotScale
    }
    Item {
        id: lockInk
        parent: window.contentItem
        anchors.fill: parent
        visible: lockGlass.visible
        opacity: lockGlass.phase === "drain" ? 0 : Math.max(0, Math.min(1, (lockGlass.fieldLife - .4) / .6))
        Text {
            x: lockGlass.fieldRect.x
            y: lockGlass.fieldRect.y
            width: lockGlass.fieldRect.z
            height: lockGlass.fieldRect.w
            text: "Password"
            color: LiquidPalette.dimOnLight
            font.pixelSize: 15
            font.weight: Font.Medium
            verticalAlignment: Text.AlignVCenter
            horizontalAlignment: Text.AlignHCenter
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            y: lockGlass.fieldRect.y + 78
            text: root.states[root.stage] === "lock-wrong" ? "Wrong password" : "EN"
            color: root.states[root.stage] === "lock-wrong" ? LiquidPalette.dangerOnLight : LiquidPalette.dimOnLight
            font.pixelSize: 13
        }
        Rectangle {
            x: lockGlass.avatarRect.x + lockGlass.avatarSize / 2 - width / 2
            y: lockGlass.avatarRect.y + lockGlass.avatarSize * .28
            width: lockGlass.avatarSize * .27
            height: width
            radius: width / 2
            color: "#4d241018"
        }
        Rectangle {
            x: lockGlass.avatarRect.x + lockGlass.avatarSize / 2 - width / 2
            y: lockGlass.avatarRect.y + lockGlass.avatarSize * .59
            width: lockGlass.avatarSize * .52
            height: lockGlass.avatarSize * .23
            radius: height / 2
            color: "#4d241018"
        }
    }
    Item {
        id: lockWordmarkLayer
        parent: window.contentItem
        anchors.fill: parent
        visible: lockGlass.visible
        LockWordmark {
            id: lockWordmark
            anchors.fill: parent
            phase: lockGlass.phase
            elapsed: lockGlass.elapsed
            clock: lockGlass.clock
            dpr: root.shotScale
            active: lockGlass.visible
            idleMode: "lake"
        }
    }
    TestCase {
        id: pointer
        when: false
    }
    Timer {
        id: tick
        interval: 900
        onTriggered: {
            if (root.stage < 0) {
                if (!scene.wallpaper.ready && ++root.waits < 12) {
                    tick.restart();
                    return;
                }
                root.stage = 0;
            }
            const state = root.states[root.stage];
            root.dump(state, () => {
                ++root.stage;
                if (root.stage >= root.states.length) {
                    console.log("GLASS_COMPLETE");
                    Qt.quit();
                    return;
                }
                const nextState = root.states[root.stage];
                // A stepped frame needs no settling; the drawer's morph (380 ms) does.
                tick.interval = root.motion && !["player-00", "launcher-00", "panelmelt-00"].includes(nextState) ? 40 : 900;
                if (root.motion)
                    root.motionSteps[nextState]();
                else if (nextState === "logo-hover")
                    pointer.mouseMove(scene.bar.logo, 18, 18);
                else if (nextState === "open") {
                    pointer.mouseMove(scene, 1100, 900);
                    scene.input.appCatalog.counts = root.frequent;
                    scene.openLauncher();
                } else if (nextState === "selected") {
                    for (const key of [Qt.Key_Right, Qt.Key_Right, Qt.Key_Down])
                        pointer.keyClick(key);
                } else if (nextState === "search") {
                    scene.input.setMode("All");
                    scene.input.setQuery(Quickshell.env("EMAKI_GLASS_QUERY") || "tele");
                } else if (nextState === "clock-hover") {
                    scene.closeAll();
                    root.pointAt(scene.bar.clock);
                } else if (nextState === "clock-open") {
                    pointer.mouseClick(scene.bar.clock, scene.bar.clock.width / 2, scene.bar.clock.height / 2);
                    pointer.mouseMove(scene, 1300, 900);
                } else if (nextState === "clock-row-hover") {
                    // clock.js shots: the pointer on Alex's message.
                    root.pointAt(root.findTarget(scene.clockPanel, "note-" + root.noteId));
                } else if (nextState === "clock-day-hover") {
                    root.pointAt(root.findTarget(scene.clockPanel, "day-2026-8-10"));
                } else if (nextState === "clock-player-hover") {
                    // Another player under the pointer: a plain drop beside the chosen one's orange.
                    root.pointAt(root.findTarget(scene.clockPanel, "player-telegram"));
                } else if (nextState === "clock-dnd") {
                    const toggle = root.findTarget(scene.clockPanel, "dnd");
                    pointer.mouseClick(toggle, toggle.width / 2, toggle.height / 2);
                    pointer.mouseMove(scene, 1300, 900);
                } else if (nextState === "clock-closed-dnd") {
                    scene.closeAll();
                    pointer.mouseMove(scene, 700, 600);
                } else if (nextState === "clock-peek") {
                    scene.notifications.dnd = false;
                    scene.notifications.local("Telegram", "Alex", "Dinner at 7? I will bring the bread");
                } else if (nextState === "system-hover") {
                    // system.js shots: the pointer on the sound cell, then the pages.
                    scene.closeAll();
                    scene.endPeek();
                    root.pointAt(scene.bar.systemGlass.row.cell("sound"));
                } else if (nextState === "system-sound") {
                    const cell = scene.bar.systemGlass.row.cell("sound");
                    pointer.mouseClick(cell, cell.width / 2, cell.height / 2);
                    pointer.mouseMove(scene, 700, 600);
                } else if (nextState === "system-sound-hover") {
                    root.pointAt(root.findTarget(scene.systemPanel, "output-2"));
                } else if (nextState === "system-bt-cell-hover") {
                    // The open panel's head: the pointer on the cell next to the tab.
                    root.pointAt(root.findTarget(scene.systemPanel, "cell-sound"));
                } else if (["system-wifi", "system-bt", "system-power"].includes(nextState)) {
                    const cell = root.findTarget(scene.systemPanel, "cell-" + nextState.slice(7));
                    pointer.mouseClick(cell, cell.width / 2, cell.height / 2);
                    pointer.mouseMove(scene, 700, 600);
                } else if (nextState === "system-power-confirm") {
                    const off = root.findTarget(scene.systemPanel, "act-off");
                    pointer.mouseClick(off, off.width / 2, off.height / 2);
                    pointer.mouseMove(scene, 700, 600);
                } else if (nextState === "system-osd") {
                    scene.closeAll();
                    scene.osd.show("sound", 72, false, "Speaker");
                } else if (nextState === "privacy") {
                    // The pill lit by a call (microphone, camera) and a screen share, then opened
                    // by a click (no mockup of its own: the call of clock.js, the fixture output).
                    scene.osd.dismiss();
                    systemMockup.captures = [
                        {
                            kind: "mic",
                            name: "Telegram"
                        },
                        {
                            kind: "cam",
                            name: "Telegram"
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
                    pointer.mouseMove(scene, 700, 600);
                } else if (nextState === "privacy-open") {
                    pointer.mouseClick(scene.privacy, scene.privacy.width / 2, scene.privacy.height / 2);
                    pointer.mouseMove(scene, 700, 600);
                }
                tick.restart();
            });
        }
    }
    Component.onCompleted: Qt.callLater(() => {
        Quickshell.watchFiles = false;
        const workspaces = {};
        for (let i = 1; i <= 4; ++i)
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
                    // A tile size on the active workspace: the screen is known to be clear,
                    // so a notification may peek (NiriService.presentationState).
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
                    // system.js: the island shows "RU".
                    current_idx: 1
                }
            }
        }));
        scene.bar.dateTime = new Date(2026, 8, 27, 9, 41, 0);
        scene.panel.visible = Qt.binding(() => scene.launcherPresent);
        // Frequent: the mockup's six, as far as they are installed here.
        const counts = {};
        const wanted = (Quickshell.env("EMAKI_GLASS_FREQUENT") || "firefox,kitty,org.telegram.desktop,org.kde.kate,org.kde.dolphin,org.kde.gwenview").split(",");
        wanted.forEach((id, i) => {
            if (DesktopEntries.byId(id))
                counts[id] = 10 - i;
        });
        root.frequent = counts;
        scene.input.appCatalog.counts = counts;
        // The system island as in the mockup: its devices, networks, streams, battery 70 %.
        scene.services.backend = systemMockup;
        scene.services.brightness = {
            state: "ready",
            percent: 100
        };
        scene.services.profiles = {
            state: "ready",
            profiles: ["power-saver", "balanced", "performance"],
            current: "balanced"
        };
        scene.services.vpn = {
            state: "ready",
            connections: [
                {
                    name: "Home",
                    kind: "wireguard",
                    uuid: "00000000-0000-0000-0000-000000000001",
                    active: false
                }
            ]
        };
        // The clock panel as in the mockup: its notifications and players, a working server.
        root.fixtureNotes();
        scene.clockBody.serverState = "active";
        scene.clockBody.mediaSource = fixtureMedia;
        scene.clockBody.mediaEnabled = true;
        tick.start();
    })
}
