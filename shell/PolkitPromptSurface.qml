// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell
import Quickshell.Wayland

PanelWindow {
    id: surface
    required property PolkitPromptController controller
    required property ShellScene scene
    property bool presented: false
    screen: scene?.output ?? null
    visible: body.present
    color: "transparent"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.namespace: "emaki-auth-prompt"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: visible ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.None
    anchors {
        top: true
        bottom: true
        left: true
        right: true
    }
    readonly property alias body: body
    WallpaperSource {
        id: wallpaper
        refreshManaged: true
        variant: "sharp"
        outputWidth: Math.round(surface.scene?.viewportWidth ?? 0)
        outputHeight: Math.round(surface.scene?.viewportHeight ?? 0)
        outputScale: surface.scene?.output?.devicePixelRatio ?? 1
        outputName: surface.scene?.outputName ?? ""
    }
    // Only the wallpaper URL crosses surfaces; each window owns its GPU textures.
    DockBackdrop {
        id: promptBackdrop
        parent: surface.contentItem
        readonly property real reach: 560 + 2 * 120
        mode: "wallpaper"
        screen: surface.screen
        wallpaperTexture: wallpaper.texture
        sharedWallpaper: true
        screenWidth: body.width
        screenHeight: body.height
        dpr: surface.scene?.output?.devicePixelRatio ?? 1
        region: Qt.rect(Math.max(0, Math.floor((screenWidth - reach) / 2)), 0, Math.min(screenWidth, reach), screenHeight)
        active: body.present && body.card.contentAlpha < 1
    }
    PolkitPromptBody {
        id: body
        controller: surface.controller
        scene: surface.scene
        presented: surface.presented
        backdrop: promptBackdrop
    }
    Connections {
        target: surface.contentItem.Window.window
        function onFrameSwapped(): void {
            if (surface.visible)
                surface.presented = true;
        }
    }
    onVisibleChanged: {
        presented = false;
        if (visible)
            Qt.callLater(body.takeFocus);
    }
}
