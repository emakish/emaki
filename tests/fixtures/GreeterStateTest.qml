pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

Scope {
    id: root
    property int step: 0
    readonly property string mode: Quickshell.env("EMAKI_GREETER_STATE_TEST")
    function check(condition: bool, detail: string): void {
        if (!condition) {
            console.error("GREETER_STATE_FAILED " + detail);
            Qt.exit(1);
        }
    }
    GreeterState {
        id: memory
        onLoaded: {
            root.check(root.mode === "success", "unexpected helper success");
            root.check(memory.ready && !memory.failed, "ready after completion");
            if (root.step === 0) {
                root.check(memory.user === "alice" && memory.sessionId === "niri-emaki.desktop", "initial selection");
                root.step = 1;
                memory.selectUser("");
            } else if (root.step === 1) {
                root.check(memory.user === "" && memory.command.length === 0, "username step");
                root.step = 2;
                memory.selectUser("unknown");
            } else if (root.step === 2) {
                root.check(memory.user === "" && memory.message === "Unknown user", "unknown user remains recoverable");
                root.step = 3;
                memory.selectUser("alice");
            } else if (root.step === 3) {
                root.check(memory.user === "alice" && memory.message === "", "valid user recovers");
                memory.recordLaunch();
            }
        }
        onLaunchRecorded: {
            root.check(root.step === 3 && memory.ready, "recorded selected launch");
            console.log("GREETER_STATE_PASS success");
            Qt.quit();
        }
        onFailedOperation: {
            root.check(root.mode !== "success", "unexpected helper failure");
            root.check(memory.failed && !memory.ready, "terminal failure state");
            console.log("GREETER_STATE_PASS " + root.mode);
            Qt.quit();
        }
    }
    Timer {
        interval: 9000
        running: true
        onTriggered: {
            console.error("GREETER_STATE_FAILED fixture timed out");
            Qt.exit(1);
        }
    }
}
