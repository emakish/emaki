// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import "CaptureText.js" as CaptureText

Scope {
    id: controller
    property bool enabled: true
    property bool locked: true
    property var screens: Quickshell.screens
    property string preferredOutput: ""
    property var notificationStore: null
    property string wallpaperTexture: ""
    property bool opened: false
    property bool ready: false
    property bool busy: false
    property bool recording: false
    // The PipeWire node this recording streams to (-1: none): its cast is the shell's own.
    property int streamNode: -1
    property bool stopping: false
    property string mode: "picture"
    property string target: "area"
    property string outputName: ""
    property double windowId: -1
    property rect area: Qt.rect(0, 0, 0, 0)
    property var outputs: []
    property var windows: []
    property string error: ""
    property string reason: ""
    property string lastPath: ""
    property bool completed: false
    property bool receivedEvent: false
    property var pendingRequest: null
    property var helperCommand: [Platform.python, "-I", "-B", Qt.resolvedUrl("helpers/capture.py").toString().replace(/^file:\/\//, "")]
    readonly property var screen: screens.find(s => s.name === outputName) ?? screens.find(s => s.name === preferredOutput) ?? screens[0] ?? null
    readonly property var selectedOutput: outputs.find(o => o.name === outputName) ?? null
    readonly property bool canCapture: enabled && !locked && opened && ready && !busy && !!selectedOutput && (target !== "window" || windows.some(w => w.id === windowId)) && (target !== "area" || area.width >= 2 && area.height >= 2)
    signal opening

    function open(): bool {
        if (!enabled || locked || busy || inventory.running)
            return false;
        opening();
        error = "";
        reason = "";
        ready = false;
        opened = true;
        outputName = preferredOutput || screen?.name || "";
        inventory.launched = false;
        inventory.running = true;
        inventoryDeadline.restart();
        return true;
    }
    function close(): void {
        opened = false;
        ready = false;
        inventoryDeadline.stop();
        if (inventory.running)
            inventory.signal(15);
    }
    // Super+Alt+R (2026-10-15): open the overlay set to recording, or stop the recording that
    // runs. A picture in progress is left alone.
    function record(): bool {
        if (busy)
            return mode === "recording" && stop();
        if (!opened && !open())
            return false;
        return selectMode("recording");
    }
    function selectMode(value: string): bool {
        if (busy || !["picture", "recording"].includes(value))
            return false;
        mode = value;
        return true;
    }
    function selectTarget(value: string): bool {
        if (busy || !["area", "window", "screen"].includes(value))
            return false;
        target = value;
        return true;
    }
    function selectOutput(name: string): bool {
        const output = outputs.find(o => o.name === name);
        if (busy || !output)
            return false;
        outputName = name;
        area = Qt.rect(Math.round(output.width / 4), Math.round(output.height / 4), Math.round(output.width / 2), Math.round(output.height / 2));
        return true;
    }
    function selectWindow(id: double): bool {
        if (busy || !windows.some(w => w.id === id))
            return false;
        windowId = id;
        return true;
    }
    function setArea(x: real, y: real, width: real, height: real): void {
        if (busy || !selectedOutput || ![x, y, width, height].every(Number.isFinite))
            return;
        const left = Math.max(0, Math.min(Math.round(x), selectedOutput.width - 2));
        const top = Math.max(0, Math.min(Math.round(y), selectedOutput.height - 2));
        area = Qt.rect(left, top, Math.max(2, Math.min(Math.round(width), selectedOutput.width - left)), Math.max(2, Math.min(Math.round(height), selectedOutput.height - top)));
    }
    function capture(): bool {
        if (!canCapture)
            return false;
        pendingRequest = {
            target: target,
            output: outputName,
            window_id: windowId,
            area: {
                x: area.x,
                y: area.y,
                width: area.width,
                height: area.height
            }
        };
        opened = false;
        busy = true;
        recording = false;
        streamNode = -1;
        stopping = false;
        completed = false;
        receivedEvent = false;
        error = "";
        reason = "";
        // Unmap the controls before requesting pixels on the compositor connection.
        unmapDelay.restart();
        return true;
    }
    function stop(): bool {
        if (!busy || stopping)
            return false;
        stopping = true;
        recording = false;
        if (unmapDelay.running) {
            unmapDelay.stop();
            busy = false;
            stopping = false;
            return true;
        }
        if (worker.running)
            worker.write("stop\n");
        finishDeadline.restart();
        return true;
    }
    function fail(code: string): void {
        reason = code;
        error = CaptureText.failed;
        if (opened)
            ready = false;
        else
            notificationStore?.local("Emaki", CaptureText.failed, "");
    }
    function sampleInventory(data: string): void {
        if (!opened)
            return;
        try {
            const result = JSON.parse(data);
            if (result.event !== "inventory") {
                if (result.event === "error")
                    fail(result.reason || "inventory_failed");
                return;
            }
            outputs = result.outputs;
            windows = result.windows;
            if (!Array.isArray(outputs) || !Array.isArray(windows))
                throw new Error("invalid_inventory");
            const chosen = outputs.find(o => o.name === outputName) ?? outputs[0];
            if (!chosen)
                throw new Error("no_outputs");
            selectOutput(chosen.name);
            windowId = (windows.find(w => w.is_focused) ?? windows[0])?.id ?? -1;
            ready = true;
            inventoryDeadline.stop();
        } catch (_) {
            fail("invalid_inventory");
        }
    }
    function sampleCapture(data: string): void {
        try {
            const result = JSON.parse(data);
            receivedEvent = true;
            if (result.event === "stream") {
                streamNode = Number.isInteger(result.node) && result.node >= 0 ? result.node : -1;
            } else if (result.event === "recording") {
                startDeadline.stop();
                recording = !stopping;
                if (stopping)
                    worker.write("stop\n");
            } else if (result.event === "saved") {
                completed = true;
                recording = false;
                lastPath = result.path;
                notificationStore?.local("Emaki", mode === "recording" ? CaptureText.savedRecording : result.clipboard ? CaptureText.savedPicture : CaptureText.savedPictureNoClipboard, lastPath);
            } else if (result.event === "error") {
                completed = true;
                recording = false;
                fail(result.reason || "capture_failed");
            }
        } catch (_) {
            fail("invalid_capture_reply");
            stop();
        }
    }
    function status(): string {
        return JSON.stringify({
            schema_version: 1,
            opened: opened,
            ready: ready,
            busy: busy,
            recording: recording,
            stream_node: streamNode,
            stopping: stopping,
            mode: mode,
            target: target,
            output: outputName,
            window_id: windowId,
            area: {
                x: area.x,
                y: area.y,
                width: area.width,
                height: area.height
            },
            reason: reason,
            path: lastPath
        });
    }
    onLockedChanged: {
        if (locked) {
            close();
            stop();
        }
    }
    onEnabledChanged: {
        if (!enabled) {
            close();
            stop();
        }
    }
    onScreensChanged: {
        if (opened && !screens.some(s => s.name === outputName))
            close();
    }
    Timer {
        id: unmapDelay
        interval: 180
        onTriggered: {
            if (controller.locked || !controller.enabled) {
                controller.busy = false;
                return;
            }
            worker.command = controller.helperCommand.concat([controller.mode === "recording" ? "--record" : "--picture", JSON.stringify(controller.pendingRequest)]);
            worker.launched = false;
            worker.running = true;
            startDeadline.restart();
        }
    }
    Timer {
        id: inventoryDeadline
        interval: 6000
        onTriggered: {
            if (inventory.running)
                inventory.signal(15);
            controller.fail("inventory_timeout");
        }
    }
    Timer {
        id: startDeadline
        // Inventory, stream, portal and recorder startup each have their own bounds.
        interval: controller.mode === "recording" ? 90000 : 20000
        onTriggered: {
            controller.fail("capture_timeout");
            controller.stop();
        }
    }
    Timer {
        id: finishDeadline
        // Allow the worker to drain, finalize and close its compositor session.
        interval: 100000
        onTriggered: {
            // The helper handles TERM with bounded EOS/session cleanup.
            if (worker.running)
                worker.signal(15);
            else {
                controller.busy = false;
                controller.stopping = false;
            }
        }
    }
    Process {
        id: inventory
        property bool launched: false
        command: controller.helperCommand.concat(["--inventory"])
        onStarted: launched = true
        onRunningChanged: if (!running && !launched) {
            Qt.callLater(() => {
                if (controller.opened && !inventory.running && !inventory.launched) {
                    inventoryDeadline.stop();
                    controller.fail("inventory_failed");
                }
            });
        }
        stdout: SplitParser {
            onRead: data => controller.sampleInventory(data)
        }
        stderr: StdioCollector {}
        onExited: {
            inventoryDeadline.stop();
            if (controller.opened && !controller.ready && !controller.error)
                controller.fail("inventory_failed");
        }
    }
    Process {
        id: worker
        property bool launched: false
        stdinEnabled: true
        onStarted: launched = true
        onRunningChanged: if (!running && !launched) {
            Qt.callLater(() => {
                if (controller.busy && !worker.running && !worker.launched && !unmapDelay.running) {
                    startDeadline.stop();
                    finishDeadline.stop();
                    controller.fail("capture_failed");
                    controller.busy = false;
                    controller.recording = false;
                    controller.stopping = false;
                }
            });
        }
        stdout: SplitParser {
            onRead: data => controller.sampleCapture(data)
        }
        stderr: StdioCollector {}
        onExited: {
            startDeadline.stop();
            finishDeadline.stop();
            controller.streamNode = -1;
            if (!controller.completed && !controller.error)
                controller.fail("capture_failed");
            controller.busy = false;
            controller.recording = false;
            controller.stopping = false;
        }
    }
}
