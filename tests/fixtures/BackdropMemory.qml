pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell

ShellRoot {
    id: root
    property string texture: ""
    property int stage: 0
    Component.onCompleted: Qt.callLater(() => texture = Quickshell.env("EMAKI_FIXTURE_WALLPAPER"))
    component Backdrop: DockBackdrop {
        property bool bandCrop: false
        mode: "wallpaper"
        wallpaperTexture: root.texture
        screenWidth: 1536
        screenHeight: 960
        dpr: 2
        Component.onCompleted: {
            if ("sharedWallpaper" in this)
                this["sharedWallpaper"] = !(bandCrop && "cacheWallpaper" in this);
            if ("cacheWallpaper" in this)
                this["cacheWallpaper"] = bandCrop;
        }
    }
    Window {
        visible: true
        width: 1536
        height: 557
        Backdrop {
            id: dock
        }
    }
    Window {
        visible: true
        width: 1536
        height: 84
        Backdrop {
            id: bar
            bandCrop: true
            region: Qt.rect(0, 0, 1536, 240)
        }
    }
    Window {
        visible: true
        width: 1536
        height: 960
        Backdrop {
            id: launcher
            region: Qt.rect(0, 0, 850, 704)
        }
        Backdrop {
            id: clock
            region: Qt.rect(368, 0, 800, 960)
        }
        Backdrop {
            id: system
            region: Qt.rect(736, 0, 800, 960)
        }
    }
    Timer {
        interval: 2000
        running: true
        repeat: true
        onTriggered: {
            const ready = dock.ready && bar.ready && launcher.ready && clock.ready && system.ready;
            if (root.stage < 3 && !ready)
                throw new Error("Backdrop was not ready");
            console.log("BACKDROP_SAMPLE", root.stage, ready);
            if (root.stage === 1)
                root.texture = Quickshell.env("EMAKI_FIXTURE_WALLPAPER_NEXT");
            if (root.stage === 2)
                root.texture = "";
            if (root.stage === 3) {
                if (dock.ready || bar.ready || launcher.ready || clock.ready || system.ready)
                    throw new Error("Revoked backdrop remained ready");
                console.log("BACKDROP_COMPLETE");
                Qt.quit();
            }
            ++root.stage;
        }
    }
}
