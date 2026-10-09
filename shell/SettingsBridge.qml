// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma Singleton
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: bridge
    // One session-wide snapshot makes every output observe the same apply.
    property var values: []
    property int applyEpoch: 0
    // The core holds its transaction lock here. Applying must stay synchronous
    // and must never call back into the core before acknowledging the values.
    IpcHandler {
        target: "settings"
        function apply(rows: string): string {
            let next;
            try {
                next = JSON.parse(rows).rows;
            } catch (error) {
                return "invalid_settings";
            }
            if (!Array.isArray(next))
                return "invalid_settings";
            const keys = ["bar.autohide", "bar.overview_workspaces", "dock.on", "dock.auto_hide"];
            for (const key of keys) {
                const matches = next.filter(row => row && row.key === key);
                if (matches.length !== 1 || typeof matches[0].value !== "boolean")
                    return "invalid_settings";
            }
            bridge.applyEpoch += 1;
            bridge.values = next;
            return "applied";
        }
    }
}
