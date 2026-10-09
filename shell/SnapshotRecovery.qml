pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: recovery
    property string snapshot: ""
    property string automaticMessage: ""
    property bool opened: false
    property bool succeeded: false
    property string error: ""
    Process {
        id: probe
        command: ["/usr/bin/emaki-rollback", "status", "--json"]
        running: true
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const status = JSON.parse(text);
                    if (status.mode === "snapshot" && /^[1-9][0-9]*$/.test(status.snapshot)) {
                        recovery.snapshot = status.snapshot;
                        recovery.automaticMessage = status.automatic === true ? status.message : "";
                        recovery.opened = true;
                    }
                } catch (_) {}
            }
        }
    }
    Process {
        id: keep
        command: ["/usr/bin/pkexec", "/usr/bin/emaki-rollback", "keep"]
        stdout: StdioCollector {}
        stderr: StdioCollector {
            id: errors
        }
        onExited: code => {
            recovery.succeeded = code === 0;
            recovery.error = code === 0 ? "" : code === 126 ? "Authentication was cancelled. Nothing changed." : errors.text.trim() || "Could not keep this state. You can try again.";
        }
    }
    LazyLoader {
        active: recovery.opened
        FloatingWindow {
            title: "Recovery snapshot"
            color: "transparent"
            implicitWidth: prompt.implicitWidth
            implicitHeight: prompt.implicitHeight
            minimumSize: Qt.size(implicitWidth, implicitHeight)
            maximumSize: Qt.size(implicitWidth, implicitHeight)
            SnapshotPrompt {
                id: prompt
                anchors.fill: parent
                snapshot: recovery.snapshot
                automaticMessage: recovery.automaticMessage
                busy: keep.running
                succeeded: recovery.succeeded
                error: recovery.error
                Component.onCompleted: Qt.callLater(takeFocus)
                onKeepRequested: {
                    recovery.error = "";
                    keep.running = true;
                }
                onDismissed: recovery.opened = false
            }
        }
    }
}
