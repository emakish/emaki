pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// Test-only helper: captures synthetic pixels from its own FloatingWindow. No
// ScreencopyView, real screen, file, Wayland connection or production test switch.
Scope {
    id: root
    required property var screen
    required property string outputName
    required property int generation
    property bool stopped: false
    signal completed(string name, int generation, var result)
    function stop(): void {
        if (stopped)
            return;
        stopped = true;
        delay.stop();
        window.visible = false;
        screen.witness.stopped++;
    }
    Component.onCompleted: screen.witness.created++
    Component.onDestruction: screen.witness.destroyed++
    FloatingWindow {
        id: window
        visible: true
        implicitWidth: 32
        implicitHeight: 32
        color: "transparent"
        Rectangle {
            id: pixels
            visible: false
            anchors.fill: parent
            color: root.screen.color
            Rectangle {
                width: 8
                height: 8
                color: "#00ff00"
            }
            Rectangle {
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                width: 8
                height: 8
                color: "#ffff00"
            }
        }
    }
    Timer {
        id: delay
        interval: root.screen.delay
        running: true
        onTriggered: {
            if (root.stopped)
                return;
            pixels.grabToImage(result => {
                if (!root.stopped)
                    root.completed(root.outputName, root.generation, result);
            }, Qt.size(32, 32));
        }
    }
}
