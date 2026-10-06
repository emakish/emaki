pragma ComponentBehavior: Bound
import QtQuick

// A floating window uses the shell's wallpaper glass, without capturing itself.
Item {
    id: root
    property string wallpaperTexture: ""
    property real dpr: 1
    readonly property var material: gpu.item
    readonly property bool glassReady: material?.ready ?? false
    Rectangle {
        anchors.fill: parent
        radius: 22
        color: Qt.rgba(LiquidPalette.flatPanel.r, LiquidPalette.flatPanel.g, LiquidPalette.flatPanel.b, 1)
    }
    Loader {
        id: gpu
        anchors.fill: parent
        active: GraphicsInfo.api !== GraphicsInfo.Software && root.wallpaperTexture !== ""
        sourceComponent: Item {
            id: material
            readonly property bool ready: wallpaperBackdrop.ready && glass.status !== ShaderEffect.Error
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
                id: glass
                visible: material.ready
                backdrop: wallpaperBackdrop
                plate: Qt.rect(0, 0, material.width, material.height)
                uRadius: 22
                uCover: wallpaperBackdrop.coverFor(Qt.point(0, 0))
                uIcons: empty
                uHasIcons: 0
            }
        }
    }
}
