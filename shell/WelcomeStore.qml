pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: store
    readonly property string stateRoot: (Quickshell.env("XDG_STATE_HOME") || "").startsWith("/") ? Quickshell.env("XDG_STATE_HOME") : Quickshell.env("HOME") + "/.local/state"
    readonly property string statePath: stateRoot + "/emaki/welcome.json"
    property bool restored: false
    property bool seen: false
    property bool saveFailed: false
    Component.onCompleted: {
        try {
            const saved = JSON.parse(stateFile.text());
            // A file written by a newer shell (it outlives a system rollback in /home) counts as
            // seen: the welcome does not come back and the file is not rewritten.
            seen = saved.version === 1 ? saved.seen === true : saved.version !== undefined;
        } catch (_) {
            // Missing or damaged state gets one welcome, then a fresh record.
        }
        restored = true;
    }
    function markSeen(): void {
        if (seen)
            return;
        seen = true;
        // Save at presentation, including when the user later closes with Esc or Super+C.
        stateFile.setText(JSON.stringify({
            version: 1,
            seen: true
        }) + "\n");
    }
    FileView {
        id: stateFile
        path: store.statePath
        blockLoading: true
        atomicWrites: true
        printErrors: false
        onSaveFailed: store.saveFailed = true
        onSaved: store.saveFailed = false
    }
}
