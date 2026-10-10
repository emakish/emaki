// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: root
    property bool enabled: false
    property var snapshot: ({
            connections: [],
            wired: [],
            wifi_profiles: []
        })
    readonly property var connections: snapshot.connections || []
    readonly property var wired: snapshot.wired || []
    readonly property var wifiProfiles: snapshot.wifi_profiles || []
    property bool busy: false
    property string error: ""
    property string errorOperation: ""
    property string message: ""
    property string operation: ""
    property string reply: ""
    property var arguments: []
    function fail(text): void {
        if (operation === "status" && error && errorOperation !== "status")
            return;
        error = text;
        errorOperation = operation;
    }
    function validSnapshot(value): bool {
        return Array.isArray(value.connections) && Array.isArray(value.wired) && Array.isArray(value.wifi_profiles) && value.connections.every(item => item && typeof item.uuid === "string" && typeof item.name === "string" && Array.isArray(item.dns) && item.dns.every(address => typeof address === "string") && typeof item.dnsAutomatic === "boolean" && ["none", "auto", "manual"].includes(item.proxyMode) && typeof item.proxyValue === "string") && value.wired.every(item => item && typeof item.device === "string" && typeof item.state === "string" && typeof item.connection === "string") && value.wifi_profiles.every(item => item && typeof item.uuid === "string" && typeof item.name === "string" && typeof item.ssid === "string" && Array.isArray(item.addresses));
    }
    function refresh(): void {
        if (enabled && !busy)
            run(["status", "--json"]);
    }
    function run(args): void {
        if (!enabled || busy)
            return;
        operation = args[0];
        root.arguments = args;
        reply = "";
        busy = true;
        if (operation !== "status") {
            error = "";
            errorOperation = "";
            message = "";
        }
        deadline.restart();
        process.running = true;
    }
    onEnabledChanged: {
        if (enabled)
            refresh();
    }
    Component.onCompleted: refresh()
    Process {
        id: process
        command: ["emaki-settings-network"].concat(root.arguments)
        onRunningChanged: {
            if (!running) {
                Qt.callLater(() => {
                    if (root.busy && !process.running) {
                        deadline.stop();
                        root.busy = false;
                        root.fail("Network settings are unavailable. Try again.");
                    }
                });
            }
        }
        stdout: StdioCollector {
            onStreamFinished: root.reply = text
        }
        onExited: code => {
            deadline.stop();
            if (!root.busy)
                return;
            root.busy = false;
            try {
                const response = JSON.parse(root.reply);
                if (!response || response.schema_version !== 1 || typeof response.ok !== "boolean")
                    throw new Error("Invalid response");
                if (code !== 0 || response.ok !== true) {
                    root.fail(typeof response.error === "string" ? response.error : "Network settings could not be changed. Try again.");
                    return;
                }
                if (root.operation === "status") {
                    if (!root.validSnapshot(response))
                        throw new Error("Invalid snapshot");
                    root.snapshot = response;
                    if (root.errorOperation === "status") {
                        root.error = "";
                        root.errorOperation = "";
                    }
                } else {
                    root.message = typeof response.message === "string" && response.message ? response.message : "Network settings updated.";
                    Qt.callLater(root.refresh);
                }
            } catch (_) {
                root.fail("Network settings are unavailable. Try again.");
            }
        }
    }
    Timer {
        id: deadline
        interval: 30000
        onTriggered: {
            root.busy = false;
            root.fail("Network settings did not respond. Try again.");
            if (process.running)
                process.signal(9);
        }
    }
    Timer {
        interval: 15000
        repeat: true
        running: root.enabled
        onTriggered: root.refresh()
    }
}
