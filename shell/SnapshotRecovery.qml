// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
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
        command: ["emaki-rollback", "status", "--json"]
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
        command: ["pkexec", Platform.binDir + "/emaki-rollback", "keep"]
        stdout: StdioCollector {}
        stderr: StdioCollector {
            id: errors
        }
        onExited: code => {
            recovery.succeeded = code === 0;
            recovery.error = code === 0 ? "" : code === 126 ? qsTranslate("SnapshotRecovery", "Authentication was cancelled. Nothing changed.") : errors.text.trim() || qsTranslate("SnapshotRecovery", "Could not keep this state. You can try again.");
        }
    }
    LazyLoader {
        active: recovery.opened
        FloatingWindow {
            readonly property bool identified: Translation.identify(prompt.Window.window, "org.emaki.recovery")
            objectName: "snapshotRecoveryWindow"
            title: qsTranslate("SnapshotRecovery", "Recovery snapshot")
            color: "transparent"
            implicitWidth: Math.min(prompt.implicitWidth, Math.max(1, (screen?.width ?? 492) - 32))
            implicitHeight: Math.min(prompt.implicitHeight, Math.max(1, (screen?.height ?? 632) - 32))
            minimumSize: Qt.size(0, 0)
            maximumSize: Qt.size(implicitWidth, implicitHeight)
            SnapshotPrompt {
                id: prompt
                objectName: "snapshotRecoveryPrompt"
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
