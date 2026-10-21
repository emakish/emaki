// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import "WhatsNewContent.js" as ReleaseContent

FloatingWindow {
    id: window
    readonly property bool identified: Translation.identify(viewLoader.Window.window, "org.emaki.welcome")
    property string error: ""
    property bool loadWallpaper: true
    property bool whatsNew: false
    property bool whatsNewAvailable: false
    readonly property var view: viewLoader.item
    signal dismissRequested
    signal shortcutsRequested
    signal languageRequested
    signal whatsNewRequested
    title: whatsNew ? ReleaseContent.title() : qsTranslate("WelcomeWindow", "Welcome to Emaki")
    implicitWidth: Math.min(800, screen ? screen.width - 48 : 800)
    implicitHeight: Math.min(660, screen ? screen.height - 120 : 660)
    minimumSize: Qt.size(560, 420)
    color: "transparent"
    function present(): void {
        if (!backingWindowVisible) {
            visible = false;
            visible = true;
        }
        minimized = false;
        viewLoader.Window.window?.requestActivate();
        view?.takeFocus();
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
    Loader {
        id: viewLoader
        anchors.fill: parent
        sourceComponent: window.whatsNew ? releaseView : welcomeView
        onLoaded: item.takeFocus()
    }
    Component {
        id: welcomeView
        Welcome {
            wallpaperTexture: wallpaper.texture
            dpr: window.devicePixelRatio
            error: window.error
            whatsNewLabel: window.whatsNewAvailable ? ReleaseContent.entryLabel() : ""
            onCloseRequested: window.dismissRequested()
            onShortcutsRequested: window.shortcutsRequested()
            onWhatsNewRequested: window.whatsNewRequested()
        }
    }
    Component {
        id: releaseView
        WhatsNew {
            wallpaperTexture: wallpaper.texture
            dpr: window.devicePixelRatio
            error: window.error
            onCloseRequested: window.dismissRequested()
            onLanguageRequested: window.languageRequested()
        }
    }
}
