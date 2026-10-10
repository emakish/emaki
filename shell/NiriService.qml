pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import "XkbCodes.js" as XkbCodes

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
    // Only the compositor answering IPC can prove support: the installed CLI may be newer.
    readonly property bool layerFocusSupported: connected && _layerFocusSupported
    property bool _layerFocusSupported: false
    property int _versionEpoch: 0
    property int _versionReadEpoch: -1
    property bool _versionCandidate: false
    property bool _versionTimedOut: false
    function supportsLayerFocus(text: string): bool {
        try {
            const version = JSON.parse(text)?.compositor;
            if (typeof version !== "string")
                return false;
            // A future upstream release needs an explicit compatibility decision.
            const match = /^26\.04 \(v26\.04\+emaki\.([1-9][0-9]*)\)$/.exec(version);
            return !!match && Number.isSafeInteger(Number(match[1])) && Number(match[1]) >= 12;
        } catch (_) {
            return false;
        }
    }
    onConnectedChanged: {
        ++_versionEpoch;
        _layerFocusSupported = false;
        versionDeadline.stop();
        versionRetry.stop();
        if (versionProbe.running)
            versionProbe.signal(9);
        if (connected) {
            versionRetry.interval = 100;
            versionRetry.restart();
        }
    }
    Timer {
        id: versionRetry
        interval: 100
        onTriggered: {
            if (!service.connected)
                return;
            if (versionProbe.running) {
                versionRetry.restart();
                return;
            }
            service._versionReadEpoch = service._versionEpoch;
            service._versionCandidate = false;
            service._versionTimedOut = false;
            versionProbe.running = true;
            versionDeadline.restart();
        }
    }
    Timer {
        id: versionDeadline
        interval: 1500
        onTriggered: {
            service._versionTimedOut = true;
            if (versionProbe.running)
                versionProbe.signal(9);
            // Failed executable startup may not emit exited either.
            if (service.connected) {
                versionRetry.interval = 5000;
                versionRetry.restart();
            }
        }
    }
    Process {
        id: versionProbe
        command: ["niri", "msg", "-j", "version"]
        stdout: StdioCollector {
            onStreamFinished: service._versionCandidate = service.supportsLayerFocus(text)
        }
        stderr: StdioCollector {}
        onExited: (code, status) => {
            versionDeadline.stop();
            if (service._versionReadEpoch !== service._versionEpoch)
                return;
            service._layerFocusSupported = service.connected && !service._versionTimedOut && code === 0 && status === 0 && service._versionCandidate;
            // A transient IPC error must not permanently disable keyboard entry.
            if (service.connected && !service._layerFocusSupported) {
                versionRetry.interval = 5000;
                versionRetry.restart();
            }
        }
    }
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
    // The short code of a layout name (the system island's cell, the keyboard page's rows):
    // the xkb code from evdev.lst, uppercased (Ukrainian -> UA), as on the lock screen.
    property var layoutCodes: ({})
    function layoutCode(name: string): string {
        return XkbCodes.code(name, layoutCodes);
    }
    FileView {
        id: xkbRules
        path: Quickshell.env("EMAKI_XKB_RULES") || Platform.xkbRules
        blockLoading: true
        printErrors: false
        onLoaded: service.layoutCodes = XkbCodes.parse(text())
        onLoadFailed: service.layoutCodes = ({})
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
        pendingRequest = null;
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
        if (!connected || !Number.isSafeInteger(id) || !workspaces.some(w => w.id === id))
            return false;
        return request(["focus-workspace", "--id", String(id)]);
    }
    // Close requests queue behind the single action process (dock "Close all windows").
    property var closeQueue: []
    // Focus/layout requests made while an action runs: only the latest one waits, and it goes
    // before the queued closes, so a click waits for the action in flight, not for all of them.
    property var pendingRequest: null
    function request(args: var): bool {
        pendingRequest = args;
        pump();
        return true;
    }
    // A deferred request is checked again against the model when it starts.
    function stillValid(args: var): bool {
        const value = Number(args[2]);
        if (args[0] === "focus-workspace")
            return workspaces.some(w => w.id === value);
        if (args[0] === "focus-window")
            return windows.some(w => w.id === value);
        return !!layouts && value < layouts.names.length;
    }
    // The action actionState speaks of ("close-window", "switch-layout", ...): one action runs at
    // a time, and a page reports an outcome only for its own kind.
    property string actionKind: ""
    function start(args: var): void {
        actionKind = args[0];
        actionState = "pending";
        actionReason = "";
        action.command = [binary, "niri"].concat(args, ["--json", "--timeout-ms", "1500"]);
        action.running = true;
    }
    function closeWindow(id: double): bool {
        if (!connected || !Number.isSafeInteger(id) || !windows.some(w => w.id === id) || closeQueue.includes(id))
            return false;
        closeQueue = closeQueue.concat([id]);
        pump();
        return true;
    }
    // Serves the pending request first, then closeQueue; invalid ones are dropped silently.
    function pump(): void {
        if (action.running)
            return;
        if (pendingRequest) {
            const args = pendingRequest;
            pendingRequest = null;
            if (connected && stillValid(args))
                start(args);
            else
                pump();
        } else if (closeQueue.length) {
            const id = closeQueue[0];
            closeQueue = closeQueue.slice(1);
            if (!connected || !windows.some(w => w.id === id)) {
                pump();
                return;
            }
            start(["close-window", "--id", String(id)]);
        }
    }
    // Layouts have no niri IDs: the configured index from the current model is the address.
    function switchLayout(index: int): bool {
        if (!connected || !layouts || !Number.isInteger(index) || index < 0 || index >= layouts.names.length)
            return false;
        return request(["switch-layout", "--index", String(index)]);
    }
    function focusWindow(id: double): bool {
        if (!connected || !Number.isSafeInteger(id) || !windows.some(w => w.id === id))
            return false;
        return request(["focus-window", "--id", String(id)]);
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
