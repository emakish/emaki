// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

FloatingWindow {
    id: window
    readonly property bool identified: Translation.identify(viewObject.Window.window, "emaki-report")
    required property var controller
    property bool loadWallpaper: true
    readonly property alias view: viewObject
    title: qsTranslate("ReportWindow", "Report a problem")
    implicitWidth: Math.min(720, screen ? screen.width - 48 : 720)
    implicitHeight: Math.min(700, screen ? screen.height - 120 : 700)
    minimumSize: Qt.size(480, 420)
    color: "transparent"
    function present(): void {
        if (!backingWindowVisible) {
            visible = false;
            visible = true;
        }
        minimized = false;
        viewObject.Window.window?.requestActivate();
        viewObject.takeFocus();
    }
    WallpaperSource {
        id: wallpaper
        enabled: window.loadWallpaper
        outputWidth: window.width
        outputHeight: window.height
        outputScale: window.devicePixelRatio
        outputName: window.screen?.name ?? ""
        variant: "sharp"
    }
    ReportView {
        id: viewObject
        anchors.fill: parent
        controller: window.controller
        wallpaperTexture: wallpaper.texture
        dpr: window.devicePixelRatio
        onCloseRequested: window.controller.close()
    }
}
