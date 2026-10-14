// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// The account picture for the lock and login screens: a private PNG copy made by
// helpers/account-picture.py. The first lookup gets one second before the drop
// falls back to its silhouette; later picture updates remain live.
Scope {
    id: picture
    property string user: ""  // "" = the account running this shell
    property bool active: true
    property bool isolateHelper: false
    property string path: ""
    property bool settled: false
    property bool restartRequested: false
    readonly property bool pending: active && !settled
    property bool halted: false  // The helper refused for good (bad name, no private runtime directory).
    readonly property string url: path ? "file://" + path.split("/").map(encodeURIComponent).join("/") : ""
    // The login screen's helper decodes another account's bytes: give it only what it needs.
    function isolatedEnvironment(): var {
        const environment = {};
        for (const name of ["PATH", "XDG_RUNTIME_DIR", "DBUS_SYSTEM_BUS_ADDRESS"]) {
            const value = Quickshell.env(name);
            if (typeof value === "string" && value !== "")
                environment[name] = value;
        }
        return environment;
    }
    onUserChanged: {
        path = "";
        settled = false;
        if (active)
            firstReply.restart();
        halted = false;
        restartRequested = true;
        worker.running = false;
        Qt.callLater(startRequested);
    }
    function startRequested(): void {
        if (restartRequested && active && !worker.running) {
            restartRequested = false;
            worker.running = true;
        }
    }
    onActiveChanged: {
        settled = false;
        if (active)
            firstReply.restart();
        if (!active) {
            path = "";
            worker.running = false;
        }
    }
    Component.onCompleted: {
        if (active)
            firstReply.restart();
    }
    Timer {
        id: firstReply
        interval: 1000
        onTriggered: picture.settled = true
    }
    Process {
        id: worker
        command: (picture.isolateHelper ? ["python3", "-I", "-B"] : ["python3", "-B"]).concat([Quickshell.shellPath("helpers/account-picture.py")], picture.user ? ["--user", picture.user] : [])
        running: picture.active
        clearEnvironment: picture.isolateHelper
        environment: picture.isolateHelper ? picture.isolatedEnvironment() : ({})
        stdout: SplitParser {
            onRead: line => {
                try {
                    const state = JSON.parse(line);
                    // A line from the previous account's helper can still arrive after a switch.
                    if (state.version === 1 && state.user === picture.user && typeof state.path === "string") {
                        picture.path = state.path.startsWith("/") ? state.path : "";
                        picture.settled = true;
                        firstReply.stop();
                    }
                } catch (_) {}
            }
        }
        stderr: SplitParser {
            onRead: _line => {}
        }
        onExited: code => {
            if (picture.restartRequested) {
                Qt.callLater(picture.startRequested);
                return;
            }
            if (code === 1 || code === 2)
                picture.halted = true;
        }
    }
    Timer {
        interval: 1000
        repeat: true
        running: picture.active && !picture.halted && !worker.running
        onTriggered: worker.running = true
    }
}
