// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

ShellRoot {
    id: root
    property bool sawRussian: false
    property double readyAt: 0
    LockEnvironment {
        onLayoutChanged: {
            if (layout === "RU") {
                root.sawRussian = true;
                root.readyAt = Date.now();
                console.log("LOCK_LAYOUT_READY");
            }
            if (layout === "US" && root.sawRussian) {
                // Include switch-helper startup so a full polling interval cannot pass.
                const elapsed = Date.now() - root.readyAt;
                if (elapsed < 500)
                    console.log("LOCK_LAYOUT_PASS", elapsed);
                else
                    console.error("LOCK_LAYOUT_TOO_SLOW", elapsed);
                Qt.quit();
            }
        }
    }
    Timer {
        interval: 5000
        running: true
        onTriggered: {
            console.error("LOCK_LAYOUT_TIMEOUT");
            Qt.quit();
        }
    }
}
