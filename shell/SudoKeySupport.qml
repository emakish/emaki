// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma Singleton
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: support
    property bool installed: false
    property var backend: null
    property bool warningDismissed: false
    property bool warningRequested: false
    readonly property bool warningAccepted: !!backend && backend.warningAccepted
    readonly property bool warningNeeded: installed && !!backend && backend.connected && (warningRequested || (!warningAccepted && !warningDismissed))
    readonly property var keys: installed && backend && backend.connected ? backend.keys : []
    onInstalledChanged: {
        warningDismissed = false;
        warningRequested = false;
    }
    FileView {
        path: Platform.sudoKeyMarker
        watchChanges: true
        printErrors: false
        onLoaded: support.installed = true
        onLoadFailed: support.installed = false
        onFileChanged: reload()
    }
}
