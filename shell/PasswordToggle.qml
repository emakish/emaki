pragma ComponentBehavior: Bound
import QtQuick

Item {
    id: toggle
    property bool revealed: false
    property color ink: "white"
    signal toggled
    width: 32
    height: 32
    activeFocusOnTab: true
    Accessible.role: Accessible.CheckBox
    Accessible.name: revealed ? "Hide password" : "Show password"
    Accessible.checkable: true
    Accessible.checked: revealed
    Accessible.onToggleAction: if (enabled)
        toggled()
    Accessible.onPressAction: if (enabled)
        toggled()
    Keys.onSpacePressed: event => {
        if (!event.isAutoRepeat)
            toggled();
        event.accepted = true;
    }
    Keys.onReturnPressed: event => {
        if (!event.isAutoRepeat)
            toggled();
        event.accepted = true;
    }
    Rectangle {
        anchors.fill: parent
        radius: 8
        color: "transparent"
        border.width: toggle.activeFocus ? 1 : 0
        border.color: toggle.ink
    }
    Canvas {
        id: eye
        anchors.centerIn: parent
        width: 22
        height: 18
        onPaint: {
            const ctx = getContext("2d");
            ctx.reset();
            ctx.strokeStyle = toggle.ink;
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            ctx.moveTo(1, 9);
            ctx.quadraticCurveTo(11, -3, 21, 9);
            ctx.quadraticCurveTo(11, 21, 1, 9);
            ctx.stroke();
            ctx.beginPath();
            ctx.arc(11, 9, 3, 0, Math.PI * 2);
            ctx.stroke();
            if (toggle.revealed) {
                ctx.beginPath();
                ctx.moveTo(3, 2);
                ctx.lineTo(19, 16);
                ctx.stroke();
            }
        }
    }
    onRevealedChanged: eye.requestPaint()
    onInkChanged: eye.requestPaint()
    MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: toggle.toggled()
    }
}
