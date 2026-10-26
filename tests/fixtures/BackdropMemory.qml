// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell

ShellRoot {
    id: root
    property string texture: ""
    property int stage: 0
    property var retainedMaterials: []
    // Only bypass the software-renderer guard here: exercise production image
    // loading and lifetime without claiming to test GPU rendering.
    function enableMaterial(surface) {
        const loaders = Array.from(surface.children).filter(child => "sourceComponent" in child);
        if (loaders.length !== 1)
            throw new Error("Expected one glass material Loader");
        loaders[0].active = Qt.binding(() => surface.wallpaperTexture !== "");
    }
    function showGlass(shown) {
        welcomeWindow.visible = shown;
        settingsWindow.visible = shown;
        secondSettingsWindow.visible = shown;
    }
    Component.onCompleted: Qt.callLater(() => {
        enableMaterial(welcome);
        enableMaterial(settings);
        enableMaterial(secondSettings);
        texture = Quickshell.env("EMAKI_FIXTURE_WALLPAPER");
    })
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
        id: welcomeWindow
        visible: false
        width: 800
        height: 600
        WelcomeGlass {
            id: welcome
            anchors.fill: parent
            wallpaperTexture: root.texture
            dpr: 1
        }
    }
    Window {
        id: settingsWindow
        visible: false
        width: 960
        height: 720
        SettingsGlass {
            id: settings
            anchors.fill: parent
            wallpaperTexture: root.texture
            dpr: 1
        }
    }
    Window {
        id: secondSettingsWindow
        visible: false
        width: 1200
        height: 800
        SettingsGlass {
            id: secondSettings
            anchors.fill: parent
            wallpaperTexture: root.texture
            dpr: 2
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
            const ready = dock.ready && bar.ready && launcher.ready && clock.ready && system.ready
                && welcome.glassReady && settings.ready && secondSettings.ready;
            if (root.stage < 5 && !ready)
                throw new Error("Backdrop was not ready");
            const materials = [welcome.material, settings.loadedMaterial, secondSettings.loadedMaterial];
            if (root.stage === 0)
                root.retainedMaterials = materials;
            if (root.stage > 0 && root.stage < 5
                    && materials.some((material, index) => material !== root.retainedMaterials[index]))
                throw new Error("Hidden glass material was recreated");
            console.log("BACKDROP_SAMPLE", root.stage, ready);
            if (root.stage === 0)
                root.showGlass(true);
            if (root.stage === 1)
                root.showGlass(false);
            if (root.stage === 2)
                root.texture = Quickshell.env("EMAKI_FIXTURE_WALLPAPER_NEXT");
            if (root.stage === 3)
                root.showGlass(true);
            if (root.stage === 4)
                root.texture = "";
            if (root.stage === 5) {
                if (dock.ready || bar.ready || launcher.ready || clock.ready || system.ready
                        || welcome.glassReady || settings.ready || secondSettings.ready
                        || materials.some(material => material !== null))
                    throw new Error("Revoked backdrop remained ready");
                console.log("BACKDROP_COMPLETE");
                Qt.quit();
            }
            ++root.stage;
        }
    }
}
