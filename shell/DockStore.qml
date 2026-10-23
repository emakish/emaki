// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// Dock settings and pinned desktop IDs survive restarts (a "position" key from older files is ignored:
// the dock lives at the bottom only, 2026-09-24) in $XDG_STATE_HOME/emaki/dock.json.
// Written only after an explicit change; a missing or damaged file means defaults.
// Pins: until the person pins, unpins or reorders, the dock shows defaultPins (2026-10-14; kitty
// second since 2026-10-15) and the file keeps no list, so a later default reaches them too. Their
// first change saves the whole list with "pins_chosen". Files from before 0.9 have no marker: a
// non-empty list is theirs, an empty one was the old default (the empty dock), so it gives way to
// the default pins.
Scope {
    id: store
    property bool on: true
    property bool autoHide: true
    readonly property var defaultPins: ["firefox", "kitty", "org.kde.dolphin", "emaki-update-manager"]
    property var pinned: defaultPins
    property bool pinsChosen: false
    readonly property string statePath: (Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state") + "/emaki/dock.json"
    property bool restored: false
    // A file written by a newer shell (it outlives a system rollback in /home): its format is
    // unknown here, so it is neither read nor overwritten during this run.
    property bool foreign: false
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
    // The settings reset removed or restored the file: read it again, without saving.
    function reload(): void {
        saveTimer.stop();
        restored = false;
        foreign = false;
        pinned = defaultPins;
        pinsChosen = false;
        // A fresh load: reload() keeps the old text when the file was removed.
        stateFile.path = "";
        stateFile.path = Qt.binding(() => store.statePath);
        restore();
    }
    Connections {
        target: SettingsBridge
        function onStoresHoldRequested(): void {
            saveTimer.stop();
            store.restored = false;
        }
        function onStoresReloadRequested(): void {
            store.reload();
        }
    }
    function restore(): void {
        try {
            const file = JSON.parse(stateFile.text() || "{}");
            foreign = file.version !== undefined && file.version !== 1;
            const saved = foreign ? {} : file;
            if (typeof saved.on === "boolean")
                on = saved.on;
            if (typeof saved.auto_hide === "boolean")
                autoHide = saved.auto_hide;
            if (Array.isArray(saved.pinned) && (saved.pins_chosen === true || saved.pinned.length > 0)) {
                pinned = saved.pinned.filter((id, index, all) => validId(id) && all.indexOf(id) === index).slice(0, 64);
                pinsChosen = true;
            }
        } catch (error) {
            // Missing or damaged file: defaults, the next save replaces it.
        }
        restored = true;
    }
    function choose(next: var): void {
        pinsChosen = true;
        pinned = next;
    }
    function pin(id: string): bool {
        if (!validId(id) || pinned.includes(id) || pinned.length >= 64)
            return false;
        choose(pinned.concat([id]));
        return true;
    }
    function unpin(id: string): bool {
        if (!pinned.includes(id))
            return false;
        choose(pinned.filter(p => p !== id));
        return true;
    }
    // ids: the shown pins in their new order. hidden: pins the dock does not show (the app is not
    // installed); they keep their places so a reorder never drops them.
    function reorder(ids: var, hidden: var): void {
        const shown = ids.slice();
        const merged = [];
        for (const id of pinned) {
            if (hidden && hidden.includes(id))
                merged.push(id);
            else if (shown.length)
                merged.push(shown.shift());
        }
        const next = merged.concat(shown).filter((id, index, all) => validId(id) && all.indexOf(id) === index).slice(0, 64);
        if (JSON.stringify(next) !== JSON.stringify(pinned))
            choose(next);
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
        onTriggered: if (!store.foreign)
            stateFile.setText(JSON.stringify(store.pinsChosen ? {
                version: 1,
                on: store.on,
                auto_hide: store.autoHide,
                pinned: store.pinned,
                pins_chosen: true
            } : {
                version: 1,
                on: store.on,
                auto_hide: store.autoHide
            }))
    }
}
