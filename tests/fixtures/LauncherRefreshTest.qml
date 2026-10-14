// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell

// Prints the launcher's app list each time it changes (tests/test-launcher-refresh.py).
ShellRoot {
    AppCatalog {
        id: catalog
        readonly property string ids: entries.map(e => e.id).sort().join(" ")
        onIdsChanged: console.info("CATALOG [" + ids + "]")
        Component.onCompleted: console.info("CATALOG [" + ids + "]")
    }
}
