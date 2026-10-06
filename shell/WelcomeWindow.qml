pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

FloatingWindow {
    id: window
    property string error: ""
    property bool loadWallpaper: true
    readonly property alias view: view
    signal dismissRequested
    signal shortcutsRequested
    title: "Welcome to Emaki"
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
        view.Window.window?.requestActivate();
        view.takeFocus();
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
    Welcome {
        id: view
        anchors.fill: parent
        wallpaperTexture: wallpaper.texture
        dpr: window.devicePixelRatio
        error: window.error
        onCloseRequested: window.dismissRequested()
        onShortcutsRequested: window.shortcutsRequested()
    }
}
