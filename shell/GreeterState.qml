// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
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
    // The people the login screen lists (login, full name), or none for one person.
    property var accounts: []
    // The chosen person's published look as settings rows: theme, accent, switch times.
    property var lookRows: []
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
        // Optional fields: a list or look that does not parse shows the default screen.
        const listed = Array.isArray(value.accounts) && value.accounts.length <= 64 && value.accounts.every(entry => entry && typeof entry.login === "string" && /^[A-Za-z_][A-Za-z0-9_.-]{0,62}\$?$/.test(entry.login) && typeof entry.name === "string" && entry.name.length <= 64);
        const look = value.look && typeof value.look === "object" && !Array.isArray(value.look) ? value.look : ({});
        const rows = [];
        for (const key of ["theme", "accent", "light_time", "dark_time"]) {
            if (typeof look[key] === "string" && look[key].length <= 16)
                rows.push({
                    key: "appearance." + key,
                    value: look[key]
                });
        }
        accounts = listed ? value.accounts.map(entry => ({
                    login: entry.login,
                    name: entry.name || entry.login
                })) : [];
        lookRows = rows;
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
