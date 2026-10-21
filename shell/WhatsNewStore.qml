// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import "WhatsNewContent.js" as Content

Scope {
    id: store
    property string releasePath: Platform.prefix + "/lib/emaki-release"
    readonly property string stateRoot: (Quickshell.env("XDG_STATE_HOME") || "").startsWith("/") ? Quickshell.env("XDG_STATE_HOME") : Quickshell.env("HOME") + "/.local/state"
    readonly property string statePath: stateRoot + "/emaki/whatsnew.json"
    property string installedVersion: ""
    property string seenVersion: ""
    property bool restored: false
    property bool initialized: false
    property bool futureState: false
    property bool saveFailed: false
    readonly property bool available: Content.available(installedVersion)
    readonly property bool pending: initialized && !futureState && available && newerThanSeen()

    // Pages are per release series (0.11 for every 0.11.x), so a patch release of a series already seen is not news.
    function newerThanSeen(): bool {
        if (!seenVersion)
            return true;
        const current = installedVersion.split(".").map(Number).slice(0, 2);
        const previous = seenVersion.split(".").map(Number).slice(0, 2);
        for (let index = 0; index < 2; ++index) {
            const difference = (current[index] || 0) - (previous[index] || 0);
            if (difference !== 0)
                return difference > 0;
        }
        return false;
    }
    function initialize(welcomeSeen: bool): void {
        if (!restored || initialized)
            return;
        initialized = true;
        // Welcome covers a fresh installation. Releases without pages only advance state.
        if (!welcomeSeen || !available)
            markSeen();
    }
    function markSeen(): void {
        if (futureState || !installedVersion || !newerThanSeen())
            return;
        seenVersion = installedVersion;
        stateFile.setText(JSON.stringify({version: 1, seenVersion: seenVersion}) + "\n");
    }
    Component.onCompleted: {
        try {
            const match = releaseFile.text().match(/^VERSION=["']?([0-9]+\.[0-9]+(?:\.[0-9]+)?)["']?\s*$/m);
            installedVersion = match ? match[1] : "";
        } catch (_) {}
        try {
            const saved = JSON.parse(stateFile.text());
            futureState = saved.version !== undefined && saved.version !== 1;
            if (saved.version === 1 && typeof saved.seenVersion === "string" && /^[0-9]+\.[0-9]+(?:\.[0-9]+)?$/.test(saved.seenVersion))
                seenVersion = saved.seenVersion;
        } catch (_) {}
        restored = true;
    }
    FileView {
        id: releaseFile
        path: store.releasePath
        blockLoading: true
        printErrors: false
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
