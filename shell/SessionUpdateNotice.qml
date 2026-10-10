// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: notice
    required property NotificationStore store
    property bool ready: false
    property string generation: Quickshell.env("EMAKI_SESSION_GENERATION")
    property string source: Quickshell.env("EMAKI_SESSION_SOURCE")
    property string watcher: Quickshell.env("EMAKI_SESSION_WATCHER")
    property bool pending: false
    property bool shown: false
    readonly property bool live: Quickshell.env("EMAKI_LIVE_SESSION") === "1"
    function show(): void {
        if (!live && pending && ready && !shown) {
            shown = true;
            store.localNotice("Desktop update", "Sign out and sign in again to finish updating the desktop.", "Save your work first [Emaki].", false, true);
        }
    }
    onPendingChanged: show()
    onReadyChanged: show()
    Process {
        id: observer
        running: !notice.live && (notice.generation !== "" || (notice.source !== "" && notice.watcher !== "")) && !notice.pending
        command: notice.generation !== "" ? [Platform.python, "-I", "-B", notice.generation + "/watch.py", "watch", notice.generation] : [Platform.python, "-I", "-B", notice.watcher, "watch-installed", notice.source]
        stdout: SplitParser {
            onRead: data => {
                try {
                    const state = JSON.parse(data);
                    if (state.schema === 1 && state.restart === true)
                        notice.pending = true;
                } catch (_) {}
            }
        }
        onExited: {
            // Retry a failed observer; a crash is not transaction completion.
            if (!notice.pending && !notice.live)
                retry.start();
        }
    }
    Timer {
        id: retry
        interval: 5000
        onTriggered: observer.running = true
    }
}
