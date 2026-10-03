pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: service
    required property string binary
    property string connection: "connecting"
    property string reason: "starting"
    property var model: null
    property double generation: 0
    property string actionState: "idle"
    property string actionReason: ""
    readonly property bool connected: connection === "connected" && model !== null
    readonly property bool overviewOpen: connected && model.overview_open
    readonly property var workspaces: connected ? Object.values(model.workspaces) : []
    readonly property var windows: connected ? Object.values(model.windows) : []
    readonly property var layouts: connected ? model.keyboard_layouts : null
    // Screencasts (26.04 CastsState); an older core without the field means "none known".
    // by_parent: the capture is this shell's own (live glass under the bar, dock, panels);
    // it is not screen sharing and must not light the privacy pill. Other captures do.
    readonly property var casts: connected && model.casts ? Object.values(model.casts).filter(c => !c.by_parent) : []
    readonly property string layoutName: layouts ? layouts.names[layouts.current_idx] : ""
    readonly property string layoutLabel: layoutCode(layoutName)
    // The short code of a layout name (the system island's cell, the keyboard page's rows).
    function layoutCode(name: string): string {
        return name === "English (US)" ? "US" : name === "Russian" ? "RU" : name.slice(0, 2).toUpperCase();
    }

    Component.onCompleted: {
        if (!binary)
            invalidate("core_binary_not_configured");
    }

    // IPC 26.4.0 has geometry, not an explicit fullscreen bit. Fail quiet if unknown.
    function presentationState(outputName: string): string {
        if (!model || !connected || model.links_pending)
            return "unknown";
        const output = model.outputs?.[outputName]?.logical;
        const workspace = workspaces.find(w => w.output === outputName && w.is_active);
        if (!output || !workspace)
            return "unknown";
        // niri 26.04 fills tile_pos_in_workspace_view only for floating tiles (layout/scrolling.rs
        // leaves the template's None), so a position is never required here. A fullscreen tile is
        // at least the output's logical size (Tile::tile_size clamps to view_size); nothing else is.
        const shown = windows.filter(w => w.workspace_id === workspace.id);
        for (const w of shown) {
            const size = w.layout?.tile_size;
            if (!size)
                return "unknown";
            if (size[0] >= output.width - 1 && size[1] >= output.height - 1)
                return "covered";
        }
        return "clear";
    }
    function invalidate(why: string): void {
        closeQueue = [];
        model = null;
        connection = "disconnected";
        reason = why;
    }
    function receive(line: string): void {
        try {
            const value = JSON.parse(line);
            if (value.schema_version !== 1 || value.ipc_release !== "26.04" || !value.connection)
                throw new Error("protocol");
            if (value.connection.status === "connected") {
                const m = value.model;
                if (!m || !m.workspaces || !m.windows || !m.keyboard_layouts || typeof m.overview_open !== "boolean")
                    throw new Error("model");
                // QML JS numbers cannot address the entire u64 range. Fail closed,
                // never issue a rounded ID. A string-ID UI protocol is deferred.
                const ids = Object.values(m.workspaces).concat(Object.values(m.windows));
                if (ids.some(item => !Number.isSafeInteger(item.id))) {
                    invalidate("id_out_of_js_range");
                    return;
                }
                model = m;
            } else {
                model = null;
            }
            connection = value.connection.status;
            reason = value.connection.reason;
            generation = value.generation;
        } catch (_) {
            invalidate("invalid_core_response");
        }
    }
    function focusWorkspace(id: double): bool {
        if (!connected || action.running || !Number.isSafeInteger(id) || !workspaces.some(w => w.id === id))
            return false;
        actionState = "pending";
        actionReason = "";
        action.command = [binary, "niri", "focus-workspace", "--id", String(id), "--json", "--timeout-ms", "1500"];
        action.running = true;
        return true;
    }
    // Close requests queue behind the single action process (dock "Close all windows").
    property var closeQueue: []
    function closeWindow(id: double): bool {
        if (!connected || !Number.isSafeInteger(id) || !windows.some(w => w.id === id) || closeQueue.includes(id))
            return false;
        closeQueue = closeQueue.concat([id]);
        pump();
        return true;
    }
    function pump(): void {
        if (action.running || !closeQueue.length)
            return;
        const id = closeQueue[0];
        closeQueue = closeQueue.slice(1);
        if (!connected || !windows.some(w => w.id === id)) {
            pump();
            return;
        }
        actionState = "pending";
        actionReason = "";
        action.command = [binary, "niri", "close-window", "--id", String(id), "--json", "--timeout-ms", "1500"];
        action.running = true;
    }
    // Layouts have no niri IDs: the configured index from the current model is the address.
    function switchLayout(index: int): bool {
        if (!connected || action.running || !layouts || !Number.isInteger(index) || index < 0 || index >= layouts.names.length)
            return false;
        actionState = "pending";
        actionReason = "";
        action.command = [binary, "niri", "switch-layout", "--index", String(index), "--json", "--timeout-ms", "1500"];
        action.running = true;
        return true;
    }
    function focusWindow(id: double): bool {
        if (!connected || action.running || !Number.isSafeInteger(id) || !windows.some(w => w.id === id))
            return false;
        actionState = "pending";
        actionReason = "";
        action.command = [binary, "niri", "focus-window", "--id", String(id), "--json", "--timeout-ms", "1500"];
        action.running = true;
        return true;
    }
    Process {
        id: watcher
        command: [service.binary, "niri", "watch", "--json"]
        running: service.binary !== ""
        stdout: SplitParser {
            onRead: line => service.receive(line)
        }
        stderr: SplitParser {
            onRead: _line => {}
        }
        onRunningChanged: {
            if (!running) {
                service.invalidate("core_process_stopped");
                restart.restart();
            }
        }
    }
    Timer {
        id: restart
        interval: 1500
        onTriggered: {
            if (service.binary !== "")
                watcher.running = true;
        }
    }
    Process {
        id: action
        // CLI action --json promises one compact object + newline (docs/cli.md).
        stdout: SplitParser {
            onRead: line => {
                try {
                    const reply = JSON.parse(line);
                    service.actionState = reply.outcome ?? "error";
                    service.actionReason = reply.reason ?? "invalid_action_response";
                } catch (_) {
                    service.actionState = "error";
                    service.actionReason = "invalid_action_response";
                }
            }
        }
        stderr: SplitParser {
            onRead: _line => {}
        }
        onRunningChanged: {
            if (!running && service.actionState === "pending") {
                service.actionState = "error";
                service.actionReason = "action_helper_stopped";
            }
            if (!running)
                Qt.callLater(() => service.pump());
        }
    }
}
