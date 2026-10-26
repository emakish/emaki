// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick

Item {
    id: root
    width: 640
    height: 420
    property real textureDpr: 1
    property string firstTexture: ""
    property string secondTexture: ""
    property string scenario: "initial"
    property string materialKind: "welcome"
    property string texture: firstTexture
    property int phase: 0
    property bool ready: false
    property bool shown: true
    property var shaderStates: []
    function shaders(item) {
        let states = [];
        if (item.fragmentShader !== undefined)
            states.push(item.status);
        for (const child of item.children || [])
            states = states.concat(shaders(child));
        return states;
    }
    readonly property var renderStats: ({
        graphics_api: GraphicsInfo.api,
        material_ready: surface.item && (materialKind === "welcome" ? surface.item.glassReady : surface.item.ready),
        material_kind: materialKind,
        shader_states: shaderStates,
        scenario: scenario,
        phase: phase,
        shown: shown,
        texture: texture,
        texture_dpr: textureDpr
    })
    Rectangle { anchors.fill: parent; color: "#213047" }
    Loader {
        id: surface
        x: 32
        y: 32
        width: root.width - 64
        height: root.height - 64
        visible: root.shown
        sourceComponent: root.materialKind === "welcome" ? welcome : settings
    }
    Component {
        id: welcome
        WelcomeGlass {
            wallpaperTexture: root.texture
            dpr: root.textureDpr
        }
    }
    Component {
        id: settings
        SettingsGlass {
            wallpaperTexture: root.texture
            dpr: root.textureDpr
        }
    }
    Timer {
        interval: 40
        running: !root.ready
        repeat: true
        onTriggered: {
            if (root.phase === 1) {
                root.shown = true;
                root.phase = 2;
                return;
            }
            if (!surface.item || !(root.materialKind === "welcome" ? surface.item.glassReady : surface.item.ready))
                return;
            root.shaderStates = root.shaders(surface.item);
            // Reused RHI shaders can remain Uncompiled in QML despite drawing.
            // Require error-free passes and compiled work; Python checks pixels too.
            if (root.shaderStates.length !== 7 || root.shaderStates.some(s => s === ShaderEffect.Error)
                    || !root.shaderStates.some(s => s === ShaderEffect.Compiled))
                return;
            if (root.phase === 0 && root.scenario !== "initial") {
                root.texture = root.secondTexture;
                root.shown = root.scenario !== "reopen";
                root.phase = 1;
                return;
            }
            root.ready = true;
        }
    }
}
