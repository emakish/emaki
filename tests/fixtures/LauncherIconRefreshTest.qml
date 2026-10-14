// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell

// Two launcher tiles whose icons are missing at start. Prints both icon sources each time one
// changes (tests/test-launcher-icon-refresh.py). Nothing here looks the icons up again.
ShellRoot {
    id: root
    readonly property string icons: (first.icon || "-") + " " + (second.icon || "-")
    onIconsChanged: console.info("TILES " + icons)
    Component.onCompleted: console.info("TILES " + icons)

    Item {
        LauncherItem {
            id: first
            shape: "tile"
            row: ({ kind: "app", label: "First", entry: { icon: "org.example.First" } })
        }
        LauncherItem {
            id: second
            shape: "tile"
            row: ({ kind: "app", label: "Second", entry: { icon: "org.example.Second" } })
        }
    }
}
