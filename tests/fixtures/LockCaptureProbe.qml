pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell

// Synthetic red rectangle only; never connects to Wayland or captures a real screen.
// Qt rejects direct GPU sharing across windows. The CPU ItemGrabResult route is
// tested separately by LockCaptureTest and is not disproved by this probe.
ShellRoot {
    Window {
        visible: true
        width: 64
        height: 64
        Rectangle {
            id: standIn
            anchors.fill: parent
            color: "red"
        }
    }
    Window {
        visible: true
        width: 64
        height: 64
        ShaderEffectSource {
            anchors.fill: parent
            sourceItem: standIn
            live: false
        }
    }
    Timer {
        interval: 100
        running: true
        onTriggered: {
            console.log("CAPTURE_PROBE_COMPLETE");
            Qt.quit();
        }
    }
}
