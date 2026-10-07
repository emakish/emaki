pragma ComponentBehavior: Bound
import QtQuick
import "file:///usr/share/emaki/shell" as Shell

Item {
    id: root
    // The floating surface uses wallpaper glass; it never captures its own window.
    // The same published wallpaper helper as the shell supplies this texture.
    property string wallpaperTexture: ""
    // Match tokens.toml and niri's window geometry clip for this floating window.
    readonly property real cornerRadius: 16
    readonly property bool software: GraphicsInfo.api === GraphicsInfo.Software
    Rectangle {
        anchors.fill: parent
        radius: root.cornerRadius
        color: Shell.LiquidPalette.flatPanel
    }
    Loader {
        anchors.fill: parent
        active: !root.software && root.wallpaperTexture !== ""
        sourceComponent: Component {
            Item {
                id: gpu
                anchors.fill: parent
                Shell.DockBackdrop {
                    id: wallpaperBackdrop
                    mode: "wallpaper"
                    wallpaperTexture: root.wallpaperTexture
                    screenWidth: gpu.width
                    screenHeight: gpu.height
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
                }
                Shell.IslandGlass {
                    visible: wallpaperBackdrop.ready
                    backdrop: wallpaperBackdrop
                    plate: Qt.rect(0, 0, gpu.width, gpu.height)
                    uRadius: root.cornerRadius
                    uCover: wallpaperBackdrop.coverFor(Qt.point(0, 0))
                    uIcons: empty
                    uHasIcons: 0
                    fragmentShader: "file:///usr/share/emaki/shell/shaders/dock.frag.qsb"
                }
            }
        }
    }
}
