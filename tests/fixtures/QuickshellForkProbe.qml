// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell
import Quickshell.Io

// Reports what the qs in PATH does where the packaged quickshell-emaki patches change it
// (tests/test-quickshell-fork.py).
ShellRoot {
    id: root
    readonly property string ids: DesktopEntries.applications.values.map(e => e.id).sort().join(" ")
    property int scans: 0
    onIdsChanged: console.info("FORK_IDS [" + ids + "]")
    Connections {
        target: DesktopEntries
        function onApplicationsChanged(): void {
            root.scans++;
            console.info("FORK_SCANS " + root.scans);
        }
    }
    Process {
        id: child
        command: ["sh", "-c", "echo \"FORK_CHILD_SOCKET ${WAYLAND_SOCKET-unset}\""]
        stdout: SplitParser {
            onRead: line => console.info(line)
        }
    }
    Component.onCompleted: {
        Quickshell.watchFiles = false;
        // 0009: no key is held while the probe starts, so the query answers no modifiers.
        console.info("FORK_MODIFIERS " + typeof Quickshell.queryKeyboardModifiers + (typeof Quickshell.queryKeyboardModifiers === "function" ? " " + Quickshell.queryKeyboardModifiers() : ""));
        console.info("FORK_REVISION " + typeof Quickshell.iconThemeRevision);
        console.info("FORK_IDS [" + ids + "]");
        child.running = true;
    }
}
