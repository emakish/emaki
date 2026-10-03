pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell

Scope {
    id: root
    property int stage: 0
    property bool capturing: false
    property bool selected: false
    readonly property bool late: Quickshell.env("EMAKI_VISUAL_LATE") === "1"
    function capture(name: string): void {
        capturing = true;
        surface.grabToImage(result => {
            if (!result.saveToFile(Quickshell.env("EMAKI_VISUAL_OUTPUT") + "/" + name + ".png"))
                Qt.exit(1);
            capturing = false;
            stage += 1;
        });
    }
    AuthController {
        id: auth
    }
    LockEnvironment {
        id: environment
        isolateHelper: true
    }
    LockSession {
        id: session
        auth: auth
        secure: true
        started: root.selected
    }
    Window {
        width: 640
        height: 480
        visible: true
        color: "black"
        LockSurface {
            id: surface
            anchors.fill: parent
            auth: auth
            session: session
            environment: environment
            greeter: true
            wallpaperSelectionReady: root.selected
            wallpaperRoot: Quickshell.env("EMAKI_VISUAL_OUTPUT")
        }
    }
    Timer {
        interval: 20
        repeat: true
        running: true
        onTriggered: {
            if (root.capturing)
                return;
            if (root.stage === 0) {
                if (surface.visualItem !== null)
                    Qt.exit(1);
                root.capture("before-selection");
            } else if (root.stage === 1) {
                root.selected = true;
                root.stage += 1;
            } else if (root.stage === 2 && surface.visualItem !== null && !surface.readyForPour) {
                root.capture("decoding");
            } else if (root.stage === 3 && session.phase === "pour" && session.elapsed >= .40) {
                root.capture("pour");
            } else if (root.stage === 4 && session.phase === "locked" && session.elapsed >= 1.2) {
                if (surface.visualItem.preparationExpired !== root.late)
                    Qt.exit(1);
                root.capture("locked");
            } else if (root.stage === 5) {
                session.phase = "finished";
                root.stage += 1;
            } else if (root.stage === 6) {
                root.capture("finished");
            } else if (root.stage === 7) {
                console.log("GREETER_VISUAL_PASS");
                Qt.quit();
            }
        }
    }
    Timer {
        interval: 9000
        running: true
        onTriggered: {
            console.error("GREETER_VISUAL_TIMEOUT", root.stage, session.phase, session.elapsed);
            Qt.exit(1);
        }
    }
}
