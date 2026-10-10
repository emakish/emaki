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
    property var controller: null
    // The core holds its transaction lock here. Applying must stay synchronous
    // and must never call back into the core before acknowledging the values.
    IpcHandler {
        target: "settings"
        function open(page: string): string {
            return bridge.controller ? bridge.controller.open(page) : JSON.stringify({
                schema_version: 1,
                status: "unavailable"
            });
        }
        function close(): string {
            bridge.controller?.dismiss();
            return JSON.stringify({
                schema_version: 1,
                status: "closed"
            });
        }
        function status(): string {
            return JSON.stringify({
                schema_version: 1,
                opened: bridge.controller?.opened ?? false,
                page: bridge.controller?.page ?? "panel"
            });
        }
        function apply(rows: string): string {
            let next;
            try {
                next = JSON.parse(rows).rows;
            } catch (error) {
                return "invalid_settings";
            }
            if (!Array.isArray(next) || !next.every(row => row && typeof row.key === "string"))
                return "invalid_settings";
            const keys = ["bar.autohide", "bar.overview_workspaces", "dock.on", "dock.auto_hide"];
            for (const key of keys) {
                const matches = next.filter(row => row && row.key === key);
                if (matches.length !== 1 || typeof matches[0].value !== "boolean")
                    return "invalid_settings";
            }
            // The core sends key, value and a set override only; titles and other
            // descriptive fields stay from the last full list.
            const known = new Map(Array.from(bridge.values).map(row => [row.key, row]));
            bridge.applyEpoch += 1;
            bridge.values = next.map(row => Object.assign({}, known.get(row.key), {
                    override_value: null
                }, row));
            return "applied";
        }
    }
}
