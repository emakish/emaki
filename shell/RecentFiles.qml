pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: files
    required property bool active
    readonly property string python: Quickshell.env("EMAKI_PYTHON") || "python3"
    readonly property string helper: Quickshell.shellPath("helpers/recent-files.py")
    property var entries: []
    property string state: "idle"
    property bool limited: false
    property string openState: "idle"
    readonly property bool opening: opener.running
    property string pendingPath: ""
    property int epoch: 0
    property int readEpoch: -1
    property int openEpoch: -1
    property bool reloadPending: false
    signal launched

    onActiveChanged: {
        ++epoch;
        entries = [];
        limited = false;
        if (active) {
            state = "loading";
            openState = "idle";
            if (reader.running)
                reloadPending = true;
            else
                refresh();
        } else {
            state = "idle";
            reloadPending = false;
            if (reader.running)
                reader.signal(9);
        }
    }
    function refresh(): void {
        if (!active || reader.running)
            return;
        reloadPending = false;
        readEpoch = epoch;
        reader.running = true;
    }
    function open(path: string): void {
        if (!active || opener.running || !entries.some(entry => entry.path === path))
            return;
        pendingPath = path;
        openEpoch = epoch;
        openState = "pending";
        opener.running = true;
    }
    Process {
        id: reader
        command: [files.python, "-B", files.helper, "list"]
        stdout: SplitParser {
            onRead: line => {
                if (!files.active || files.readEpoch !== files.epoch)
                    return;
                try {
                    const value = JSON.parse(line);
                    if (value.schema_version !== 1 || !value.state)
                        throw new Error("protocol");
                    files.entries = value.state === "ready" ? value.entries : [];
                    files.limited = value.limited === true;
                    files.state = value.state;
                } catch (_) {
                    files.entries = [];
                    files.state = "invalid_response";
                }
            }
        }
        stderr: SplitParser {
            onRead: line => AppLaunch.diagnostic(line)
        }
        onStarted: readDeadline.restart()
        onRunningChanged: {
            if (!running) {
                readDeadline.stop();
                if (files.active && files.reloadPending)
                    Qt.callLater(() => files.refresh());
                else if (files.active && files.state === "loading")
                    files.state = "helper_failed";
            }
        }
    }
    Timer {
        id: readDeadline
        interval: 3500
        onTriggered: {
            files.state = "timeout";
            reader.signal(9);
        }
    }
    Process {
        id: opener
        command: [files.python, "-B", files.helper, "open"]
        stdinEnabled: true
        onStarted: {
            write(JSON.stringify({
                path: files.pendingPath
            }) + "\n");
            files.pendingPath = "";
            openDeadline.restart();
        }
        stdout: SplitParser {
            onRead: line => {
                if (files.openEpoch !== files.epoch || files.openState !== "pending")
                    return;
                try {
                    const value = JSON.parse(line);
                    if (value.schema_version !== 1 || !value.state)
                        throw new Error("protocol");
                    files.openState = value.state;
                    if (value.state === "requested" && files.active)
                        files.launched();
                } catch (_) {
                    files.openState = "invalid_response";
                }
            }
        }
        stderr: SplitParser {
            onRead: line => AppLaunch.diagnostic(line)
        }
        onRunningChanged: {
            if (!running) {
                openDeadline.stop();
                files.pendingPath = "";
                if (files.openState === "pending")
                    files.openState = "helper_failed";
            }
        }
    }
    Timer {
        id: openDeadline
        interval: AppLaunch.timeoutMs
        onTriggered: {
            files.openState = "requested";
            opener.signal(9);
            if (files.active)
                files.launched();
        }
    }
}
