// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell

ShellRoot {
    SettingsCatalog {
        id: catalog
        active: false
        onStateChanged: console.log("APPEARANCE_CATALOG " + state)
    }
    // Observe bindings only; the production catalog owns all reads and watches.
    Timer {
        property string previous: ""
        interval: 40
        running: true
        repeat: true
        onTriggered: {
            if (catalog.busy || !SettingsBridge.values.length)
                return;
            const snapshot = JSON.stringify({
                theme: Appearance.theme,
                accent: Appearance.accent,
                dark: ShellPalette.dark,
                color: ShellPalette.accent.toString(),
                managerBackground: ShellPalette.managerBackground.toString()
            });
            if (snapshot !== previous) {
                previous = snapshot;
                console.log("APPEARANCE_LIVE " + snapshot);
            }
        }
    }
}
