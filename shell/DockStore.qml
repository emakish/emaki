pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// Dock settings and pinned desktop IDs survive restarts (a "position" key from older files is ignored:
// the dock lives at the bottom only, 2026-09-24) in $XDG_STATE_HOME/emaki/dock.json.
// Written only after an explicit change; a missing or damaged file means defaults.
Scope {
    id: store
    property bool on: true
    property bool autoHide: true
    property var pinned: []
    readonly property string statePath: (Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state") + "/emaki/dock.json"
    property bool restored: false
    onOnChanged: if (restored)
        saveTimer.restart()
    onAutoHideChanged: if (restored)
        saveTimer.restart()
    onPinnedChanged: if (restored)
        saveTimer.restart()
    Component.onCompleted: restore()
    function validId(id: var): bool {
        return typeof id === "string" && id.length > 0 && id.length <= 256 && !id.includes("/") && !id.includes("\n");
    }
    function restore(): void {
        try {
            const saved = JSON.parse(stateFile.text() || "{}");
            if (typeof saved.on === "boolean")
                on = saved.on;
            if (typeof saved.auto_hide === "boolean")
                autoHide = saved.auto_hide;
            if (Array.isArray(saved.pinned))
                pinned = saved.pinned.filter((id, index, all) => validId(id) && all.indexOf(id) === index).slice(0, 64);
        } catch (error) {
            // Missing or damaged file: defaults, the next save replaces it.
        }
        restored = true;
    }
    function pin(id: string): bool {
        if (!validId(id) || pinned.includes(id) || pinned.length >= 64)
            return false;
        pinned = pinned.concat([id]);
        return true;
    }
    function unpin(id: string): bool {
        if (!pinned.includes(id))
            return false;
        pinned = pinned.filter(p => p !== id);
        return true;
    }
    function reorder(ids: var): void {
        const next = ids.filter((id, index, all) => validId(id) && all.indexOf(id) === index).slice(0, 64);
        if (JSON.stringify(next) !== JSON.stringify(pinned))
            pinned = next;
    }
    FileView {
        id: stateFile
        path: store.statePath
        blockLoading: true
        atomicWrites: true
        printErrors: false
    }
    Timer {
        id: saveTimer
        interval: 500
        onTriggered: stateFile.setText(JSON.stringify({
            version: 1,
            on: store.on,
            auto_hide: store.autoHide,
            pinned: store.pinned
        }))
    }
}
