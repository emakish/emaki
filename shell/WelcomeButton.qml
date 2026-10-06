pragma ComponentBehavior: Bound
import QtQuick

Rectangle {
    id: button
    required property string text
    property bool primary: false
    property bool selected: false
    signal clicked
    implicitWidth: label.implicitWidth + 28
    implicitHeight: 42
    radius: 12
    color: primary || selected ? LiquidPalette.selectedDrop : pointer.containsMouse ? LiquidPalette.flatDropRim : LiquidPalette.flatDrop
    border.width: activeFocus ? 2 : 1
    border.color: activeFocus ? LiquidPalette.inkOnLight : LiquidPalette.flatDropRim
    opacity: enabled ? 1 : .4
    activeFocusOnTab: true
    Accessible.role: Accessible.Button
    Accessible.name: text
    Accessible.onPressAction: if (enabled)
        clicked()
    Keys.onReturnPressed: clicked()
    Keys.onEnterPressed: clicked()
    Keys.onSpacePressed: clicked()
    Text {
        id: label
        anchors.centerIn: parent
        text: button.text
        color: LiquidPalette.inkOnLight
        font.family: ShellPalette.uiFont
        font.pixelSize: 14
        font.weight: button.primary || button.selected ? Font.DemiBold : Font.Normal
    }
    MouseArea {
        id: pointer
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: {
            button.forceActiveFocus();
            button.clicked();
        }
    }
}
