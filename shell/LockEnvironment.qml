// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: environmentObject
    property string layout: qsTranslate("LockEnvironment", "Layout unavailable")
    property bool capsLock: false
    property bool capsKnown: false
    property bool outputsActive: true
    property bool isolateHelper: false
    Process {
        id: worker
        command: environmentObject.isolateHelper ? ["python3", "-I", "-B", Quickshell.shellPath("helpers/lock-environment.py")] : ["python3", "-B", Quickshell.shellPath("helpers/lock-environment.py")]
        stdinEnabled: true
        running: true
        onRunningChanged: {
            // Metadata loss must not leave a stale "outputs off" value suppressing
            // idle visuals when an output returns. Transitions do not rely on it.
            if (!running) {
                environmentObject.outputsActive = true;
                environmentObject.layout = qsTranslate("LockEnvironment", "Layout unavailable");
                environmentObject.capsKnown = false;
                environmentObject.capsLock = false;
            }
        }
        stdout: SplitParser {
            onRead: line => {
                try {
                    const state = JSON.parse(line);
                    environmentObject.layout = String(state.layout);
                    environmentObject.outputsActive = state.outputs_active !== false;
                    environmentObject.capsKnown = typeof state.caps === "boolean";
                    environmentObject.capsLock = environmentObject.capsKnown && state.caps;
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
