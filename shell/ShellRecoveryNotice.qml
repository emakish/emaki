// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: recovery
    required property NotificationStore store
    property bool ready: false
    property bool checked: false
    property bool registered: false
    readonly property string ownerToken: Date.now().toString(16) + "-" + Math.random().toString(16).slice(2)

    function check(): void {
        if (!ready || !registered || checked)
            return;
        checked = true;
        probe.running = true;
    }
    onReadyChanged: check()
    Component.onCompleted: registration.running = true
    Component.onDestruction: {
        // Commit clean shutdown before the process exits. Detached deletion may
        // finish after the next shell starts, which can read this receipt first.
        if (Quickshell.env("XDG_RUNTIME_DIR")) {
            cleanReceipt.setText(String(Quickshell.processId));
            Quickshell.execDetached(["emaki-shell-health", "clean", String(Quickshell.processId), ownerToken]);
        }
    }

    FileView {
        id: cleanReceipt
        path: Quickshell.env("XDG_RUNTIME_DIR") ? Quickshell.env("XDG_RUNTIME_DIR") + "/emaki-shell-health/clean-" + recovery.ownerToken : ""
        preload: false
        blockWrites: true
    }

    Process {
        id: registration
        command: ["emaki-shell-health", "started", String(Quickshell.processId), recovery.ownerToken]
        onRunningChanged: {
            if (!running) {
                recovery.registered = true;
                recovery.check();
            }
        }
    }

    Process {
        id: probe
        // The helper atomically consumes the pending notice across shell restarts.
        command: ["emaki-shell-health", "notice"]
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const notice = JSON.parse(text);
                    // The action only opens the report window; nothing is sent from a notice.
                    if (typeof notice.backup === "string" && notice.backup.length > 0)
                        recovery.store.offer("Panel recovery", qsTranslate("ShellRecoveryNotice", "Your panel customisation failed to load."), qsTranslate("ShellRecoveryNotice", "It was set aside (%1). Emaki is using its own panel until you sign in again.").arg(notice.backup), "report:shell", qsTranslate("ShellRecoveryNotice", "Report…"));
                    else if (notice.crashed === true)
                        recovery.store.offer("Panel recovery", qsTranslate("ShellRecoveryNotice", "The panel stopped and was restarted."), qsTranslate("ShellRecoveryNotice", "You can report the problem. Nothing is sent until you review the report and choose Send."), "report:shell", qsTranslate("ShellRecoveryNotice", "Report…"));
                } catch (_) {}
            }
        }
    }
}
