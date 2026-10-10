// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: service
    // Only the installed, visible shell enables polling; render tests stay offline.
    property bool enabled: false
    property int pending: 0
    property string checkedAt: ""
    property string error: ""
    signal opening
    function acceptStatus(text: string): bool {
        try {
            const state = JSON.parse(text);
            if (!Number.isInteger(state.pending) || state.pending < 0 || typeof state.checkedAt !== "string" || typeof state.error !== "string")
                return false;
            pending = state.pending;
            checkedAt = state.checkedAt;
            error = state.error;
            return true;
        } catch (_) {
            return false;
        }
    }
    function refresh(): void {
        if (enabled && !status.running)
            status.running = true;
    }
    function check(): void {
        if (enabled && !checker.running)
            checker.running = true;
    }
    function open(): void {
        if (!launcher.running) {
            opening();
            launcher.running = true;
        }
    }
    onEnabledChanged: {
        if (enabled) {
            refresh();
            check();
        }
    }
    Process {
        id: status
        command: ["emaki-update-manager", "--status"]
        stdout: StdioCollector {
            onStreamFinished: service.acceptStatus(text)
        }
        stderr: StdioCollector {}
    }
    Process {
        id: checker
        command: ["emaki-update-manager", "--check"]
        stdout: StdioCollector {}
        stderr: StdioCollector {}
        onRunningChanged: if (!running)
            service.refresh()
    }
    Process {
        id: launcher
        command: AppLaunch.command("emaki-update-manager", ["emaki-update-manager"])
        stderr: SplitParser {
            onRead: line => AppLaunch.diagnostic(line)
        }
    }
    Timer {
        interval: 60000
        running: service.enabled
        repeat: true
        onTriggered: service.refresh()
    }
    Timer {
        interval: 24 * 60 * 60 * 1000
        running: service.enabled
        repeat: true
        onTriggered: service.check()
    }
}
