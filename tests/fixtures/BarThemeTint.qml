// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window

Item {
    id: root
    width: 1536
    height: 960
    property string firstTexture: ""
    property string secondTexture: ""
    property string creamTexture: ""
    property string backdropMode: "wallpaper"
    property int restarts: 0
    property int completedCaptures: 0
    property string capturedTexture: ""
    property string compositorTexture: scenario === "cream" ? creamTexture : firstTexture
    property string scenario: "initial"
    property string surfaceKind: "launcher"
    property string texture: scenario === "cream" ? creamTexture : firstTexture
    property bool separateWindows: true
    readonly property Item secondarySurface: separateWindows ? overlay : null
    Item {
        id: overlay
        width: root.width
        height: root.height
    }
    property int phase: 0
    property int ticks: 0
    property bool ready: false
    property var history: []
    readonly property var scene: desktop.fixtureScene
    function snapshot() {
        const left = scene.bar.leftIslands;
        const shader = left.workspaceGlass;
        const clock = scene.bar.clockIsland.islandRect;
        const system = scene.bar.systemGlass.islandRect;
        return {
            phase: phase,
            style: SettingsBridge.values.find(row => row.key === "appearance.style")?.value,
            separate_windows: scene.panel.Window.window !== scene.bar.Window.window,
            bar_map_same_window: desktop.fixtureBarMap.Window.window === scene.bar.Window.window,
            clock_rect: [clock.x, clock.y, clock.width, clock.height],
            system_rect: [system.x, system.y, system.width, system.height],
            texture: texture,
            backdrop_ready: desktop.fixtureBarMap.ready,
            glass_ready: left.glassReady,
            handed_over: left.handedOver,
            launcher_open: scene.launcherOpen,
            launcher_present: scene.launcherPresent,
            expansion: scene.expansion,
            settings: scene.input.settingsActive,
            sharp: String(shader.uSharp),
            sharp_width: shader.uSharp ? shader.uSharp.width : -1,
            clear: String(shader.uBlurred),
            regular: String(shader.uRegularLow),
            shader_status: shader.status,
            visible: shader.visible,
            compositor_texture: compositorTexture,
            capture_restarts: restarts,
            completed_captures: completedCaptures,
            captured_texture: capturedTexture,
            capture_settled: desktop.fixtureBarMap.captureSettled,
            content_frames: desktop.fixtureBarMap.contentFrames,
            logo_rect: [left.logoRect.x, left.logoRect.y, left.logoRect.width, left.logoRect.height],
            workspace_rect: [left.workspaceRect.x, left.workspaceRect.y, left.workspaceRect.width, left.workspaceRect.height]
        };
    }
    readonly property var renderStats: ({
            state: snapshot(),
            history: history,
            graphics_api: GraphicsInfo.api
        })
    function palette(accent) {
        // Backend style application is covered separately by test-theme-switching.py.
        SettingsBridge.values = [
            {
                key: "appearance.style",
                value: accent === "rose" ? "emaki" : "07-blue-light"
            },
            {
                key: "appearance.theme",
                value: "light"
            },
            {
                key: "appearance.accent",
                value: accent
            }
        ];
    }
    Component.onCompleted: {
        palette("rose");
        if (separateWindows) {
            scene.panel.parent = overlay;
            desktop.fixtureLauncherMap.parent = overlay;
        }
    }
    ShellRender {
        id: desktop
        anchors.fill: parent
        shot: "bar"
        wallpaper: root.texture
        background: root.firstTexture
        textureScale: 1
        shared: true
        cachedBarBand: true
    }
    Timer {
        interval: 100
        running: !root.ready
        repeat: true
        onTriggered: {
            ++root.ticks;
            if (root.ticks < 2)
                return;
            if (root.phase === 0) {
                if (!desktop.ready)
                    return;
                root.palette("rose");
                root.history = root.history.concat([root.snapshot()]);
                if (root.scenario === "initial" || root.scenario === "cream") {
                    root.ready = true;
                    return;
                }
                if (root.scenario === "surface") {
                    root.scene.openLauncher(false);
                    if (root.surfaceKind === "settings")
                        root.scene.input.showSettings("panel");
                }
                root.phase = 1;
            } else if (root.phase === 1) {
                if (root.scenario === "surface" && root.scene.expansion !== 1)
                    return;
                root.history = root.history.concat([root.snapshot()]);
                root.palette("blue");
                root.compositorTexture = root.creamTexture;
                root.texture = "";
                root.phase = 2;
            } else if (root.phase === 2) {
                root.history = root.history.concat([root.snapshot()]);
                root.texture = root.secondTexture;
                root.phase = 3;
            } else if (root.phase === 3) {
                if (!desktop.fixtureBarMap.ready)
                    return;
                root.history = root.history.concat([root.snapshot()]);
                // The compositor finishes its decode, but the peer consumes this damage.
                root.compositorTexture = root.secondTexture;
                root.phase = 4;
            } else if (root.phase === 4) {
                root.history = root.history.concat([root.snapshot()]);
                root.scene.closeLauncher();
                root.phase = 5;
            } else {
                if (!desktop.ready || root.scene.launcherPresent)
                    return;
                root.history = root.history.concat([root.snapshot()]);
                root.ready = true;
            }
            root.ticks = 0;
        }
    }
}
