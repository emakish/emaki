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

    function check(): void {
        if (!ready || checked)
            return;
        checked = true;
        probe.running = true;
    }
    onReadyChanged: check()
    Component.onCompleted: check()

    Process {
        id: probe
        // The helper atomically consumes the pending notice across shell restarts.
        command: ["emaki-shell-health", "notice"]
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const notice = JSON.parse(text);
                    if (typeof notice.backup === "string" && notice.backup.length > 0)
                        recovery.store.local("Panel recovery", "Your panel customisation failed to load.", "It was set aside (" + notice.backup + "). Emaki is using its own panel until you sign in again.");
                } catch (_) {}
            }
        }
    }
}
