// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick

Item {
    id: root
    width: 320
    height: 300
    property bool gated: true
    property string scenario: "initial"
    property real textureDpr: 1
    property real bufferWidth: 480
    property real bufferHeight: 320
    property real bufferDpr: 1
    Component.onCompleted: bufferDpr = textureDpr
    property bool ready: false
    property bool shown: true
    property bool content: false
    property bool intersects: true
    property int phase: 0
    property int ticks: 0
    property int sourceFrames: 0
    property int insideVersion: 0
    property int outsideVersion: 0
    property real outputWidth: 480
    property real outputHeight: 320
    property rect crop: Qt.rect(80, 70, 240, 80)
    readonly property var renderStats: ({
            graphics_api: GraphicsInfo.api,
            ready: backdrop.ready,
            settled: backdrop.captureSettled,
            content_frames: backdrop.contentFrames,
            source_frames: sourceFrames,
            inside_version: insideVersion,
            phase: phase,
            scenario: scenario,
            crop: [crop.x, crop.y, crop.width, crop.height],
            source_width: outputWidth,
            source_height: outputHeight,
            texture_dpr: textureDpr,
            damage_tracking: backdrop.damageTracking,
            buffer_size: [bufferWidth * bufferDpr, bufferHeight * bufferDpr]
        })
    Rectangle {
        anchors.fill: parent
        color: "#253348"
    }
    DockBackdrop {
        id: backdrop
        testDriver: root
        mode: "capture"
        screen: ({})
        active: root.shown
        damageAwareCapture: root.gated
        screenWidth: root.outputWidth
        screenHeight: root.outputHeight
        region: root.crop
        dpr: root.textureDpr
    }
    // Observe the real production crop and both complete production blur chains.
    Repeater {
        model: [backdrop.sharp, backdrop.clear, backdrop.regular]
        ShaderEffectSource {
            required property var modelData
            required property int index
            x: 20
            y: 20 + index * 90
            width: 280
            height: 80
            sourceItem: modelData
            live: true
            hideSource: true
        }
    }
    Timer {
        interval: 30
        repeat: true
        running: !root.ready
        onTriggered: {
            ++root.ticks;
            if (root.phase === 0) {
                root.content = true;
                root.phase = 1;
                root.ticks = 0;
                return;
            }
            if (root.ticks < 8 || !backdrop.ready)
                return;
            if (root.phase === 1 && root.scenario !== "initial") {
                root.insideVersion = root.scenario === "geometry" || root.scenario === "dpr" ? 0 : 1;
                root.intersects = true;
                if (root.scenario === "reopen") {
                    root.shown = false;
                    root.content = false;
                }
                if (root.scenario === "geometry") {
                    root.intersects = false;
                    root.crop = Qt.rect(60, 50, 260, 100);
                    root.outputWidth = 520;
                    root.outputHeight = 360;
                }
                if (root.scenario === "dpr") {
                    root.intersects = false;
                    root.textureDpr *= 1.5;
                }
                if (root.scenario === "buffer") {
                    root.intersects = false;
                    root.bufferWidth = 560;
                    root.bufferHeight = 400;
                }
                root.phase = 2;
                root.ticks = 0;
                return;
            }
            if (root.phase === 2 && root.scenario === "repeat") {
                // Another completed frame with the same damage decision must refresh.
                root.insideVersion = 2;
                root.phase = 3;
                root.ticks = 0;
                return;
            }
            root.intersects = root.scenario === "unknown";
            root.ready = true;
        }
    }
    Timer {
        interval: 120
        running: root.phase === 2 && !root.shown
        onTriggered: {
            root.shown = true;
            root.content = true;
        }
    }
}
