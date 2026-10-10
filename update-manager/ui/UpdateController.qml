// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell.Io
import "@EMAKI_DATADIR@/shell" as Shell

Item {
    id: root
    property var data: ({updates: [], groups: [], news: [], warnings: [], downloadSize: 0})
    property string phase: "idle"
    property string message: ""
    property string output: ""
    property bool restart: false
    property bool signOut: false
    property bool succeeded: false
    property bool received: false
    property bool notStarted: false
    readonly property bool busy: phase === "checking" || phase === "applying" || phase === "restarting"
    readonly property int repositoryCount: (data.updates || []).filter(row => !row.aur).length
    function start(operation: string): void {
        if (busy) return;
        phase = operation === "--apply" ? "applying" : operation === "--restart" ? "restarting" : "checking";
        message = "";
        output = "";
        received = false;
        notStarted = false;
        process.command = [Shell.Platform.libexecDir + "/update-manager-backend", operation];
        process.running = true;
        startWatch.restart();
    }
    function check(): void { start("--refresh"); }
    function apply(): void { if (repositoryCount > 0 && !data.error) start("--apply"); }
    function reboot(): void { start("--restart"); }
    function accept(line: string): void {
        try {
            const event = JSON.parse(line);
            if (event.type === "result") {
                data = event.data;
                received = true;
                message = data.error || "";
            } else if (event.type === "progress") {
                output = (output + event.message + "\n").slice(-32768);
            } else if (event.type === "finished") {
                received = true;
                succeeded = event.ok === true;
                notStarted = event.notStarted === true;
                if (notStarted) output = "";
                message = event.message || "";
                restart = event.restart === true;
                signOut = event.signOut === true;
            }
        } catch (_) {
            message = "The update service sent an unreadable response.";
        }
    }
    Timer {
        id: startWatch
        interval: 2000
        onTriggered: if (root.busy && !process.running) {
            root.succeeded = false;
            root.message = "The update service could not start. Check that Emaki updates are installed correctly.";
            root.phase = "idle";
        }
    }
    Process {
        id: process
        onStarted: startWatch.stop()
        stdout: SplitParser { onRead: line => root.accept(line) }
        stderr: SplitParser { onRead: line => root.output = (root.output + line + "\n").slice(-32768) }
        onExited: code => {
            const wasApply = root.phase === "applying";
            if (!root.received || code !== 0) {
                root.succeeded = false;
                if (!root.message) root.message = "The update service could not finish. Check the details and try again.";
            }
            root.phase = wasApply && !root.notStarted ? "finished" : "idle";
        }
    }
    Component.onCompleted: start("--check")
}
