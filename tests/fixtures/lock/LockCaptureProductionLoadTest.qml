pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

Scope {
    // Compile the real panel, without a Wayland screencopy context. The synthetic
    // transfer fixture replaces it only in its own separate offscreen profile.
    Timer {
        interval: 1
        running: true
        onTriggered: {
            const component = Qt.createComponent("LockCapturePanel.qml");
            if (component.status !== Component.Ready) {
                // QS does not install a PanelWindow backend on the offscreen QPA.
                // Strict qmllint checks the production properties separately;
                // actual panel creation and screencopy remain a Wayland VM check.
                if (!component.errorString().trim().endsWith("No PanelWindow backend loaded.")) {
                    console.error("LOCK_CAPTURE_FAILED production panel load: " + component.errorString());
                    Qt.exit(1);
                    return;
                }
                console.log("LOCK_CAPTURE_PASS production load reaches required Wayland PanelWindow backend (creation requires live verification)");
            } else {
                console.log("LOCK_CAPTURE_PASS unchanged production panel compiles with Wayland imports");
            }
            console.log("LOCK_CAPTURE_COMPLETE");
            Qt.quit();
        }
    }
}
