pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

Scope {
    id: root
    property bool sawOff: false
    property bool recovered: false
    property int ticks: 0
    LockEnvironment {
        id: metadata
        onOutputsActiveChanged: {
            if (!outputsActive)
                root.sawOff = true;
            else if (root.sawOff)
                root.recovered = true;
        }
    }
    Timer {
        interval: 20
        repeat: true
        running: true
        onTriggered: {
            if (root.sawOff && root.recovered && metadata.layout === "EN" && metadata.outputsActive) {
                console.log("LOCK_ENVIRONMENT_PASS metadata death clears stale off state and restarts");
                Qt.quit();
            } else if (++root.ticks >= 250) {
                console.error("LOCK_ENVIRONMENT_FAIL metadata recovery deadline");
                Qt.exit(1);
            }
        }
    }
}
