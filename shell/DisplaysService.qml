// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import Quickshell
import Quickshell.Io

QtObject {
    id: root
    property bool active: true
    property var niri: null
    // The shared compositor model already follows events and output changes.
    readonly property string topology: JSON.stringify(niri?.model?.outputs ?? {})
    property bool refreshQueued: false
    property var outputs: []
    property string main: ""
    property bool pending: false
    property int seconds: 0
    property string message: ""
    property bool busy: true
    property bool ready: false
    property bool closing: false
    property string helper: Quickshell.shellPath("helpers/displays.py")
    function send(request): bool {
        if (busy || !worker.running || closing)
            return false;
        busy = true;
        if (request.op !== "state")
            message = "";
        worker.write(JSON.stringify(request) + "\n");
        return true;
    }
    function set(output, key, value): bool {
        return send({
            op: "set",
            output,
            key,
            value
        });
    }
    function reset(output, key): bool {
        return send({
            op: "reset",
            output,
            key
        });
    }
    function keep(): bool {
        return send({
            op: "keep"
        });
    }
    function revert(): bool {
        return send({
            op: "revert"
        });
    }
    function close(): void {
        closing = true;
        worker.stdinEnabled = false;
    }
    function refreshState(): void {
        if (closing)
            return;
        if (!worker.running) {
            busy = true;
            worker.running = true;
        } else if (!send({
            op: "state"
        })) {
            refreshQueued = true;
        }
    }
    onActiveChanged: {
        if (active)
            refreshState();
    }
    onTopologyChanged: {
        // Retain the last-screen rescue after a session-only disable, even off-page.
        if (active || pending || outputs.some(output => output.enabled === false))
            refreshState();
    }
    Component.onDestruction: close()
    property Process worker: Process {
        id: worker
        command: [Quickshell.env("EMAKI_PYTHON") || "python3", "-B", root.helper]
        running: true
        stdinEnabled: true
        stdout: SplitParser {
            onRead: line => {
                try {
                    const reply = JSON.parse(line);
                    if (reply.schema_version !== 1 || !["ready", "pending", "error"].includes(reply.state))
                        throw new Error("protocol");
                    if (Array.isArray(reply.outputs))
                        root.outputs = reply.outputs;
                    if (reply.main !== undefined)
                        root.main = reply.main || "";
                    if (reply.pending !== undefined)
                        root.pending = !!reply.pending;
                    root.seconds = reply.seconds || 0;
                    if (reply.message)
                        root.message = reply.message;
                    root.ready = Array.isArray(reply.outputs) || root.ready;
                } catch (_) {
                    root.message = "Display settings returned an invalid response.";
                }
                root.busy = false;
                if (root.refreshQueued) {
                    root.refreshQueued = false;
                    root.refreshState();
                }
            }
        }
        stderr: SplitParser {
            onRead: _line => {}
        }
        onExited: _code => {
            root.busy = false;
            root.ready = false;
            if (!root.closing && !root.message)
                root.message = "Display settings stopped. Trying to reconnect.";
        }
    }
    property Timer countdown: Timer {
        interval: 1000
        repeat: true
        running: root.active && root.pending && root.seconds > 0
        onTriggered: root.seconds -= 1
    }
    property Timer refresh: Timer {
        interval: 2000
        repeat: true
        // Legacy/standalone clients without the shared model need an open-page fallback.
        running: !root.closing && root.active && (!root.niri || !root.niri.connected || !worker.running)
        onTriggered: root.refreshState()
    }
}
