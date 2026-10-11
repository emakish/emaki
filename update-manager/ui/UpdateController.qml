// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell.Io
import "file:@EMAKI_DATADIR@/shell" as Shell

Item {
    id: root
    property var data: ({updates: [], groups: [], news: [], warnings: [], downloadSize: 0})
    property string phase: "idle"
    property string message: ""
    property string output: ""
    property bool restart: false
    property bool reminder: false
    property var restartPackages: []
    property var restorePoints: []
    property var selectedRestore: null
    property bool restoreOpen: false
    property bool restorePending: false
    signal hideReady
    property bool signOut: false
    property bool succeeded: false
    property bool received: false
    property bool notStarted: false
    property string dismissalPhase: "idle"
    readonly property bool busy: phase === "checking" || phase === "applying" || phase === "restarting" || phase === "restoring" || phase === "restoreChecking" || phase === "dismissing"
    readonly property int repositoryCount: (data.updates || []).filter(row => !row.aur && row.kind !== "flatpak").length
    readonly property int managedCount: (data.updates || []).filter(row => !row.aur).length
    function start(operation: string, argument: var): void {
        if (busy) return;
        const dismissing = operation === "--dismiss-restart";
        if (dismissing) dismissalPhase = phase;
        phase = operation === "--apply" ? "applying" : operation === "--restart" ? "restarting" : operation === "--restore" ? "restoring" : operation === "--restore-points" ? "restoreChecking" : operation === "--dismiss-restart" ? "dismissing" : "checking";
        if (!dismissing) {
            message = "";
            output = "";
            received = false;
            notStarted = false;
        }
        process.command = [Shell.Platform.libexecDir + "/update-manager-backend", operation];
        if (argument) process.command = process.command.concat([argument]);
        process.running = true;
        startWatch.restart();
    }
    function check(): void { start("--refresh"); }
    function apply(): void { if (managedCount > 0 && !data.error && !restorePending) start("--apply"); }
    function reboot(): void { if (restart) start("--restart"); }
    function loadRestorePoints(): void {
        if (busy || restorePending) return;
        restoreOpen = true;
        selectedRestore = null;
        restorePoints = [];
        start("--restore-points");
    }
    function selectRestore(point: var): void { if (!busy && !restorePending) selectedRestore = point; }
    function cancelRestore(): void { if (!busy) { selectedRestore = null; restoreOpen = false; } }
    function restore(): void {
        if (selectedRestore && !restorePending) start("--restore", String(selectedRestore.id));
    }
    function hide(): void {
        if (reminder && !busy) start("--dismiss-restart");
        else hideReady();
    }
    function finishDismiss(ok: bool): void {
        phase = dismissalPhase;
        if (ok) {
            reminder = false;
            hideReady();
        } else {
            message += "\n[PLACEHOLDER] The restart reminder could not be dismissed. Try Later again.";
        }
    }
    function updateReminder(event: var): void {
        restart = event.restart === true || restorePending;
        reminder = event.reminder === undefined ? restart : event.reminder === true;
        restartPackages = event.restartPackages || [];
    }
    function accept(line: string): void {
        try {
            const event = JSON.parse(line);
            if (event.type === "result") {
                data = event.data;
                received = true;
                message = data.error || "";
                restorePending = restorePending || data.restorePending === true;
                updateReminder(data);
            } else if (event.type === "restorePoints") {
                received = true;
                restorePoints = event.points || [];
                restorePending = restorePending || event.restorePending === true;
                if (restorePending) restart = true;
                message = event.error || "";
            } else if (event.type === "restoreFinished") {
                received = true;
                succeeded = event.ok === true;
                message = event.message || "";
                output = event.details || "";
                if (succeeded || event.restart === true) {
                    restorePending = true;
                    selectedRestore = null;
                    restoreOpen = false;
                    restart = true;
                }
            } else if (event.type === "progress") {
                output = (output + event.message + "\n").slice(-32768);
            } else if (event.type === "finished") {
                received = true;
                succeeded = event.ok === true;
                notStarted = event.notStarted === true;
                if (notStarted) output = "";
                message = event.message || "";
                restorePending = restorePending || event.restorePending === true;
                updateReminder(event);
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
            if (root.phase === "dismissing") { root.finishDismiss(false); return; }
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
            const wasDismiss = root.phase === "dismissing";
            if (wasDismiss) {
                root.finishDismiss(code === 0);
                return;
            }
            if (!root.received || code !== 0) {
                root.succeeded = false;
                if (!root.message) root.message = "The update service could not finish. Check the details and try again.";
            }
            root.phase = wasApply && !root.notStarted ? "finished" : "idle";
        }
    }
    Component.onCompleted: start("--check")
}
