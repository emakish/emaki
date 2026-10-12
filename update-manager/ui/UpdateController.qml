// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell.Io
import "file:@EMAKI_DATADIR@/shell" as Shell

Item {
    id: root
    property var data: ({updates: [], groups: [], news: [], warnings: [], downloadSize: 0})
    property bool externalBusy: false
    property bool hideAfterDismiss: true
    property string phase: "idle"
    property string message: ""
    property string output: ""
    property bool restart: false
    property bool reminder: false
    property var restartPackages: []
    property var restorePoints: []
    property string restoreError: ""
    property int restoreRefreshes: 0
    property string countError: ""
    readonly property bool countRefreshing: completedCheck.running
    property int generation: 0
    property var selectedRestore: null
    property bool restoreOpen: false
    property bool restorePending: false
    property var restoredPoint: null
    signal hideReady
    property bool signOut: false
    property bool succeeded: false
    property var applyingUpdates: []
    property var completedUpdates: []
    property string completedAt: ""
    property bool received: false
    property bool notStarted: false
    property string dismissalPhase: "idle"
    readonly property bool busy: phase === "checking" || phase === "applying" || phase === "restarting" || phase === "restoring" || phase === "restoreChecking" || phase === "dismissing"
    readonly property int repositoryCount: (data.updates || []).filter(row => !row.aur && row.kind !== "flatpak").length
    readonly property int managedCount: (data.updates || []).filter(row => !row.aur).length
    function start(operation: string, argument: var): void {
        if (busy || (externalBusy && ["--apply", "--restore", "--restart"].includes(operation))) return;
        const dismissing = operation === "--dismiss-restart";
        if (dismissing) dismissalPhase = phase;
        if (operation === "--apply") {
            applyingUpdates = (data.updates || []).filter(row => !row.aur);
            completedUpdates = [];
            completedAt = "";
        }
        phase = operation === "--apply" ? "applying" : operation === "--restart" ? "restarting" : operation === "--restore" ? "restoring" : operation === "--restore-points" ? "restoreChecking" : operation === "--dismiss-restart" ? "dismissing" : "checking";
        if (!dismissing) {
            generation += 1;
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
    function apply(): void { if (managedCount > 0 && !data.error && !restorePending && !countRefreshing) start("--apply"); }
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
    function refreshRestorePoints(): void {
        if (!restoreIndex.running) {
            restoreIndex.received = false;
            restoreError = "";
            restoreIndex.running = true;
            restoreWatch.restart();
        }
    }
    function refreshCompletedUpdates(): void {
        if (completedCheck.running) return;
        completedCheck.requestGeneration = generation;
        completedCheck.received = false;
        countError = "";
        completedCheck.running = true;
        countWatch.restart();
    }
    function notifyRestart(): void {
        if (restart && reminder && !restartNotice.running) restartNotice.running = true;
    }
    function dismissReminder(): void {
        if (reminder && !busy) { hideAfterDismiss = false; start("--dismiss-restart"); }
    }
    function hide(): void {
        hideAfterDismiss = true;
        if (reminder && !busy) start("--dismiss-restart");
        else hideReady();
    }
    function finishDismiss(ok: bool): void {
        phase = dismissalPhase;
        if (ok) {
            reminder = false;
            if (hideAfterDismiss) hideReady();
        } else {
            message += "\nThe reminder could not be hidden. Press Later again.";
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
                restoreError = event.error || "";
                message = restoreError;
            } else if (event.type === "restoreFinished") {
                received = true;
                succeeded = event.ok === true;
                message = event.message || "";
                output = event.details || "";
                if (succeeded || event.restart === true) {
                    restorePending = true;
                    restoredPoint = selectedRestore;
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
            if (wasApply && root.succeeded) {
                root.completedUpdates = root.applyingUpdates;
                root.completedAt = new Date().toISOString();
            }
            root.phase = wasApply && !root.notStarted ? "finished" : "idle";
            root.refreshRestorePoints();
            if (wasApply && !root.notStarted) {
                root.refreshCompletedUpdates();
                root.restoreRefreshes = 6;
                restoreRefresh.restart();
            }
        }
    }
    Timer {
        id: restoreRefresh
        interval: 10000
        repeat: true
        onTriggered: {
            root.refreshRestorePoints();
            root.restoreRefreshes -= 1;
            if (root.restoreRefreshes <= 0) stop();
        }
    }
    Process {
        id: restoreIndex
        property bool received: false
        onStarted: restoreWatch.stop()
        onExited: code => {
            if ((!received || code !== 0) && !root.restoreError)
                root.restoreError = "The restore dates could not be loaded. Try again.";
        }
        command: [Shell.Platform.libexecDir + "/update-manager-backend", "--restore-points"]
        stdout: SplitParser {
            onRead: line => {
                try {
                    const event = JSON.parse(line);
                    if (event.type === "restorePoints" && Array.isArray(event.points)) {
                        restoreIndex.received = true;
                        root.restoreError = event.error || "";
                        root.restorePoints = event.points;
                        root.restorePending = root.restorePending || event.restorePending === true;
                        if (root.restorePending) root.restart = true;
                    }
                } catch (_) { root.restoreError = "The restore dates could not be loaded. Try again."; }
            }
        }
    }
    Timer {
        id: restoreWatch
        interval: 2000
        onTriggered: if (!restoreIndex.running && !restoreIndex.received)
            root.restoreError = "The restore dates could not be loaded. Try again.";
    }
    Process {
        id: completedCheck
        property bool received: false
        property int requestGeneration: 0
        command: [Shell.Platform.libexecDir + "/update-manager-backend", "--check"]
        onStarted: countWatch.stop()
        stdout: SplitParser {
            onRead: line => {
                if (completedCheck.requestGeneration !== root.generation) return;
                try {
                    const event = JSON.parse(line);
                    if (event.type === "result" && event.data && Array.isArray(event.data.updates)) {
                        completedCheck.received = true;
                        root.countError = event.data.error || "";
                        root.data = event.data;
                    }
                } catch (_) { root.countError = "The updates could not be checked. Press Check again."; }
            }
        }
        onExited: code => {
            if (requestGeneration === root.generation && (!received || code !== 0) && !root.countError)
                root.countError = "The updates could not be checked. Press Check again.";
        }
    }
    Timer {
        id: countWatch
        interval: 2000
        onTriggered: if (!completedCheck.running && !completedCheck.received)
            root.countError = "The updates could not be checked. Press Check again.";
    }
    Process {
        id: restartNotice
        command: [Shell.Platform.libexecDir + "/update-manager-backend", "--notify-restart"]
    }
    Component.onCompleted: { start("--check"); refreshRestorePoints(); }
}
