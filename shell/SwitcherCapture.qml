// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Wayland

// What the window switcher cuts its live pictures from: one capture of its output while the
// switcher is open, none otherwise. Emaki's niri leaves the shell's own surfaces out of the
// shell's capture, so the windows under the panel are in it. A fresh capture each opening (a
// reused one waits for damage that a still screen never makes, GOTCHAS "copy_with_damage"),
// used two frames after its first content (GOTCHAS "A fresh screencopy's first frame").
// Nothing here is drawn on screen: the capture is hidden and only the cards' copies show it.
Item {
    id: root
    property ShellScreen screen: null
    property bool active: false
    property real screenWidth: 1
    property real screenHeight: 1
    readonly property Item source: capture
    readonly property bool ready: active && capture.hasContent && frames >= 2
    property int frames: 0
    onActiveChanged: frames = 0
    width: screenWidth
    height: screenHeight
    enabled: false
    ScreencopyView {
        id: capture
        width: root.screenWidth
        height: root.screenHeight
        captureSource: root.active ? root.screen : null
        live: root.active
        paintCursor: false
        onHasContentChanged: root.frames = 0
    }
    // Hides the capture from the window it sits in; the cards render it into their own copies.
    ShaderEffectSource {
        sourceItem: capture
        hideSource: true
        visible: false
        live: false
        width: 1
        height: 1
    }
    FrameAnimation {
        running: root.active && capture.hasContent && root.frames < 2
        onTriggered: ++root.frames
    }
}
