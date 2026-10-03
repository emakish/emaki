pragma ComponentBehavior: Bound
import QtQuick

Text {
    id: label
    property bool liquid: false
    property color fallbackColor: ShellPalette.text
    property color glassColor: LiquidPalette.text
    color: liquid ? glassColor : fallbackColor
    textFormat: Text.PlainText
    // A small software-renderable shadow kernel around offset (0,1).
    // Unlike MultiEffect this also works in offscreen Software.
    Repeater {
        model: label.liquid && label.color !== LiquidPalette.accentText ? [Qt.point(0, 1), Qt.point(-1, 1), Qt.point(1, 1), Qt.point(0, 0), Qt.point(0, 2)] : []
        Text {
            required property int index
            required property point modelData
            z: -1
            x: modelData.x
            y: modelData.y
            width: label.width
            height: label.height
            text: label.text
            textFormat: Text.PlainText
            visible: label.textFormat === Text.PlainText
            font: label.font
            wrapMode: label.wrapMode
            elide: label.elide
            maximumLineCount: label.maximumLineCount
            horizontalAlignment: label.horizontalAlignment
            verticalAlignment: label.verticalAlignment
            color: Qt.rgba(20 / 255, 8 / 255, 2 / 255, index === 0 ? .18 : .0675)
        }
    }
}
