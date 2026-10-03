pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: environment
    property string layout: "Layout unavailable"
    property bool capsLock: false
    property bool capsKnown: false
    property bool outputsActive: true
    property bool isolateHelper: false
    Process {
        id: worker
        command: environment.isolateHelper ? ["python3", "-I", "-B", Quickshell.shellPath("helpers/lock-environment.py")] : ["python3", "-B", Quickshell.shellPath("helpers/lock-environment.py")]
        stdinEnabled: true
        running: true
        onRunningChanged: {
            // Metadata loss must not leave a stale "outputs off" value suppressing
            // idle visuals when an output returns. Transitions do not rely on it.
            if (!running) {
                environment.outputsActive = true;
                environment.layout = "Layout unavailable";
                environment.capsKnown = false;
                environment.capsLock = false;
            }
        }
        stdout: SplitParser {
            onRead: line => {
                try {
                    const state = JSON.parse(line);
                    environment.layout = String(state.layout);
                    environment.outputsActive = state.outputs_active !== false;
                    environment.capsKnown = typeof state.caps === "boolean";
                    environment.capsLock = environment.capsKnown && state.caps;
                } catch (_) {}
            }
        }
        stderr: SplitParser {
            onRead: _line => {}
        }
    }
    Timer {
        interval: 1000
        repeat: true
        running: !worker.running
        onTriggered: worker.running = true
    }
}
