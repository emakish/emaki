// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Wayland

PanelWindow {
    id: surface
    property string videoPath: ""
    property string stillPath: ""
    property bool playing: false
    // Hide immediately when the theme clears, before deferred surface destruction.
    visible: stillPath !== ""
    anchors {
        top: true
        bottom: true
        left: true
        right: true
    }
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Background
    WlrLayershell.namespace: "emaki-overview-video"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    mask: Region {}
    color: "#151515"
    OverviewVideoContent {
        anchors.fill: parent
        videoPath: surface.videoPath
        stillPath: surface.stillPath
        playing: surface.playing
        fallbackPath: Platform.dataDir + "/wallpaper/fallback.png"
    }
}
