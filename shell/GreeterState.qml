pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// The helper receives account/session metadata only, never authentication input.
Scope {
    id: memory
    property bool ready: false
    property bool failed: false
    property string user: ""
    property string sessionId: ""
    property list<string> command: []
    property string wallpaperRoot: ""
    property bool multipleUsers: false
    property string message: ""
    property var _request: ({})
    property var _response: null
    property string _operation: ""
    property bool _pending: false
    signal loaded
    signal launchRecorded
    signal failedOperation

    function fail(): void {
        if (failed)
            return;
        failed = true;
        ready = false;
        _pending = false;
        _request = ({});
        _response = null;
        deadline.stop();
        if (worker.running)
            worker.signal(9);
        failedOperation();
    }
    function start(request: var): void {
        if (_pending || failed) {
            fail();
            return;
        }
        ready = false;
        _request = request;
        _operation = String(request.op);
        _response = null;
        _pending = true;
        worker.running = true;
        deadline.restart();
    }
    function selectUser(name: string): void {
        start({
            op: "select",
            user: name
        });
    }
    function recordLaunch(): void {
        if (!ready || !user || !sessionId || !command.length) {
            fail();
            return;
        }
        start({
            op: "launch",
            user: user,
            session: sessionId
        });
    }
    function complete(code: int): void {
        if (!_pending || failed)
            return;
        deadline.stop();
        const value = _response;
        if (code !== 0 || !value || value.version !== 1 || value.ok !== true || typeof value.user !== "string" || typeof value.sessionId !== "string" || !Array.isArray(value.command) || !value.command.every(word => typeof word === "string") || typeof value.wallpaperRoot !== "string" || typeof value.multipleUsers !== "boolean" || typeof value.message !== "string") {
            fail();
            return;
        }
        if (_operation === "launch" && (value.user !== user || value.sessionId !== sessionId || JSON.stringify(value.command) !== JSON.stringify(command))) {
            // A desktop entry changed while authenticating: never launch an unrecorded command.
            fail();
            return;
        }
        user = value.user;
        sessionId = value.sessionId;
        command = value.command;
        wallpaperRoot = value.wallpaperRoot;
        multipleUsers = value.multipleUsers;
        message = value.message;
        _pending = false;
        _response = null;
        ready = true;
        if (_operation === "launch")
            launchRecorded();
        else
            loaded();
    }
    Process {
        id: worker
        command: ["python3", "-I", "-B", Quickshell.shellPath("helpers/greeter-state.py")]
        stdinEnabled: true
        onStarted: {
            write(JSON.stringify(memory._request) + "\n");
            memory._request = ({});
        }
        stdout: SplitParser {
            onRead: line => {
                if (!memory._pending || memory.failed)
                    return;
                try {
                    if (memory._response !== null || line.length > 65536)
                        throw new Error("invalid state response");
                    memory._response = JSON.parse(line);
                } catch (_) {
                    memory.fail();
                }
            }
        }
        stderr: SplitParser {
            onRead: _line => {}
        }
        onExited: code => memory.complete(code)
    }
    Timer {
        id: deadline
        interval: 5000
        onTriggered: memory.fail()
    }
    Component.onCompleted: start({
        op: "load"
    })
}
