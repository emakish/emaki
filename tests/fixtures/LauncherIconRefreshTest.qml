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
    Component.onCompleted: {
        // Quickshell.iconThemeRevision is quickshell-emaki's 0008 (since 0.3.1-12): without it
        // nothing can tell a tile that its icon arrived, and the test says so instead of failing.
        console.info("ICON_REVISION " + typeof Quickshell.iconThemeRevision);
        console.info("TILES " + icons);
    }

    Item {
        LauncherItem {
            id: first
            shape: "tile"
            row: ({
                    kind: "app",
                    label: "First",
                    entry: {
                        icon: "org.example.First"
                    }
                })
        }
        LauncherItem {
            id: second
            shape: "tile"
            row: ({
                    kind: "app",
                    label: "Second",
                    entry: {
                        icon: "org.example.Second"
                    }
                })
        }
    }
}
