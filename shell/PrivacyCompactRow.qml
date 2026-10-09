pragma ComponentBehavior: Bound
import QtQuick

// The privacy pill's line: a pulsing dot and one symbol per kind in use (microphone, camera,
// screen) in the alarm colour of the glass. Drawn by the pill and by its panel (whose closed
// state is the pill, pixel for pixel), under and on the glass. `pulse` is the dot's opacity:
// one value for every copy, so the dot does not jump when the panel takes the pill over.
// `naturalWidth` is the pill's width.
Item {
    id: line
    property var kinds: []
    property real pulse: 1
    property color alarm: LiquidPalette.dangerOnLight
    readonly property var symbols: ({
            mic: "audio-input-microphone-symbolic",
            cam: "camera-web-symbolic",
            cast: "screen-shared-symbolic"
        })
    readonly property int naturalWidth: 20 + 7 + kinds.length * 22
    width: naturalWidth
    height: Metrics.islandHeight
    Rectangle {
        visible: line.kinds.length > 0
        x: 10
        y: (line.height - 7) / 2
        width: 7
        height: 7
        radius: 3.5
        color: line.alarm
        opacity: line.pulse
    }
    Row {
        x: 23
        y: 10
        height: 16
        spacing: 6
        Repeater {
            model: line.kinds
            Item {
                id: kindIcon
                required property string modelData
                width: 16
                height: 16
                SymbolIcon {
                    id: symbol
                    width: 16
                    height: 16
                    name: line.symbols[kindIcon.modelData] ?? ""
                    ink: line.alarm
                }
                Icon {
                    visible: !symbol.found
                    width: 16
                    height: 16
                    kind: kindIcon.modelData
                    ink: line.alarm
                }
            }
        }
    }
}
