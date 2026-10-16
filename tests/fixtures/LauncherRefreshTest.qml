// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell

// Prints the launcher's app list each time it changes (tests/test-launcher-refresh.py).
ShellRoot {
    // Quickshell.iconThemeRevision is quickshell-emaki's 0008: present means the 0.3.1-12 the
    // packages pin, which also watches application directories created later (0007).
    Component.onCompleted: console.info("ICON_REVISION " + typeof Quickshell.iconThemeRevision)
    AppCatalog {
        id: catalog
        readonly property string ids: entries.map(e => e.id).sort().join(" ")
        onIdsChanged: console.info("CATALOG [" + ids + "]")
        Component.onCompleted: console.info("CATALOG [" + ids + "]")
    }
}
