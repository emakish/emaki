// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick

Item {
    id: root
    objectName: "settings-glass"
    readonly property var loadedMaterial: materialLoader.item
    readonly property bool ready: loadedMaterial?.ready ?? false
    property string wallpaperTexture: ""
    property real dpr: 1
    Rectangle {
        anchors.fill: parent
        radius: 16
        color: LiquidPalette.flatPanel
    }
    Loader {
        id: materialLoader
        anchors.fill: parent
        active: GraphicsInfo.api !== GraphicsInfo.Software && root.wallpaperTexture !== ""
        sourceComponent: Item {
            id: material
            readonly property bool ready: wallpaperBackdrop.ready
            DockBackdrop {
                id: wallpaperBackdrop
                mode: "wallpaper"
                wallpaperTexture: root.wallpaperTexture
                screenWidth: material.width
                screenHeight: material.height
                dpr: root.dpr
            }
            Item {
                id: blank
                width: 1
                height: 1
            }
            ShaderEffectSource {
                id: empty
                sourceItem: blank
                hideSource: true
                visible: false
                live: false
            }
            IslandGlass {
                visible: wallpaperBackdrop.ready
                backdrop: wallpaperBackdrop
                plate: Qt.rect(0, 0, material.width, material.height)
                uRadius: 16
                uCover: wallpaperBackdrop.coverFor(Qt.point(0, 0))
                uIcons: empty
                uHasIcons: 0
            }
        }
    }
    Rectangle {
        anchors.fill: parent
        radius: 16
        color: Qt.rgba(1, 246 / 255, 240 / 255, .58)
    }
}
