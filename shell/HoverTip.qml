pragma ComponentBehavior: Bound
import QtQuick

Item {
    id: tip
    z: 40
    property Item owner: null
    property string label: ""
    property string shortcut: ""
    property bool shown: false
    property real viewportWidth: 1536
    property real anchorX: 0
    property real anchorY: 0
    // "bottom" places the hint above the owner (dock), "left"/"right" beside it.
    property string side: "top"
    property point ownerOffset: Qt.point(0, 0)
    function offer(item: Item, text: string, key: string): void {
        offerFrom(item, text, key, "top", Qt.point(0, 0));
    }
    function offerFrom(item: Item, text: string, key: string, where: string, offset: point): void {
        hide();
        owner = item;
        label = text;
        shortcut = key;
        side = where;
        ownerOffset = offset;
        delay.start();
    }
    function leave(item: Item): void {
        if (owner === item)
            hide();
    }
    function hide(): void {
        delay.stop();
        shown = false;
        owner = null;
        label = "";
        shortcut = "";
        side = "top";
        ownerOffset = Qt.point(0, 0);
    }
    Timer {
        id: delay
        interval: 450
        onTriggered: {
            if (tip.owner && tip.owner.visible) {
                const point = tip.owner.mapToItem(null, tip.side === "left" ? tip.owner.width : tip.side === "right" ? 0 : tip.owner.width / 2, tip.side === "bottom" ? 0 : tip.side === "top" ? tip.owner.height : tip.owner.height / 2);
                tip.anchorX = point.x + tip.ownerOffset.x;
                tip.anchorY = point.y + tip.ownerOffset.y;
                tip.shown = true;
            }
        }
    }
    visible: shown
    width: Math.min(viewportWidth - 16, text.implicitWidth + keyBox.width + 24)
    height: 30
    x: side === "left" ? anchorX + 10 : side === "right" ? anchorX - width - 10 : Math.max(8, Math.min(viewportWidth - width - 8, anchorX - width / 2))
    y: side === "bottom" ? anchorY - height - 10 : side === "top" ? anchorY + 10 : anchorY - height / 2
    Rectangle {
        anchors.fill: parent
        radius: 8
        color: ShellPalette.surface
    }
    Text {
        id: text
        x: 10
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width - keyBox.width - 24
        text: tip.label
        textFormat: Text.PlainText
        elide: Text.ElideRight
        font.family: ShellPalette.uiFont
        font.pixelSize: 12
        color: ShellPalette.text
    }
    Rectangle {
        id: keyBox
        anchors.right: parent.right
        anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        width: tip.shortcut ? key.implicitWidth + 10 : 0
        height: 22
        radius: 5
        color: ShellPalette.surfaceHigh
        visible: width > 0
        Text {
            id: key
            anchors.centerIn: parent
            text: tip.shortcut
            font.family: ShellPalette.uiFont
            font.pixelSize: 11
            color: ShellPalette.muted
        }
    }
}
