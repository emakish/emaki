// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import DamageCadence

Item {
    id: root
    width: 400
    height: 100
    property bool gated: true
    property bool ready: capture.frames >= 20
    readonly property var renderStats: ({
            capture_frames: capture.frames,
            gated: gated
        })
    CaptureView {
        id: capture
        width: 400
        height: 300
        onFrameCaptured: {
            if (frames === 1 || damageIntersects(crop.sourceRect))
                crop.scheduleUpdate();
        }
    }
    ShaderEffectSource {
        id: crop
        anchors.fill: parent
        sourceItem: capture
        sourceRect: Qt.rect(0, 0, 400, 100)
        textureSize: Qt.size(400, 100)
        hideSource: true
        live: !root.gated
    }
}
