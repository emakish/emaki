// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io

QtObject {
    id: service
    property string helper: Quickshell.env("EMAKI_NOTES_HELPER") || Platform.libexecDir + "/notes-backend"
    property bool active: true
    property var notes: []
    property var events: []
    readonly property var pinnedNotes: notes.filter(note => note.pinned)
    property string error: ""
    property bool ready: false
    property int serial: 0
    property var pending: ({})
    signal completed(var result)
    function find(noteId): var { return notes.find(note => note.id === noteId) || null; }
    function request(action, fields): string {
        if (!ready || !worker.running) { error = "Notes is reconnecting. Your draft is kept here."; return ""; }
        const id = String(++serial);
        pending[id] = true;
        worker.write(JSON.stringify(Object.assign({}, fields || {}, {id: id, action: action})) + "\n");
        error = "";
        return id;
    }
    function accept(line): void {
        try {
            const value = JSON.parse(line);
            if (value.type === "snapshot") {
                if (!ready) error = "";
                notes = value.notes || [];
                events = value.events || [];
                ready = true;
                retryMs = 2000;
                if (value.errors && value.errors.length) error = value.errors.join("\n");
            } else if (value.type === "error") {
                error = value.error || "Notes could not be loaded.";
            } else if (value.type === "result") {
                delete pending[value.id];
                if (!value.ok) error = value.error || "The change could not be saved.";
                completed(value);
            }
        } catch (_) { error = "Notes returned an unreadable response."; }
    }
    // Restart delay after the backend stops: doubles up to 5 min, back to 2 s on a snapshot.
    property int retryMs: 2000
    // A restart assigns worker.running and so breaks its binding to active.
    onActiveChanged: {
        if (!active) reconnect.stop();
        worker.running = active;
    }
    function retry(): void {
        if (!active || reconnect.running) return;
        reconnect.interval = retryMs;
        reconnect.start();
        retryMs = Math.min(retryMs * 2, 300000);
    }
    property Process worker: Process {
        command: [Platform.python, "-B", service.helper, "serve"]
        running: service.active
        stdinEnabled: true
        stdout: SplitParser { onRead: line => service.accept(line) }
        stderr: SplitParser { onRead: _line => {} }
        // Not only exited(): a failed start emits runningChanged alone.
        onRunningChanged: {
            if (running) return;
            service.ready = false;
            service.error = "Notes stopped. Your draft is kept here.";
            const pending = service.pending;
            service.pending = ({});
            for (const id in pending) service.completed({id: id, ok: false, error: service.error});
            service.retry();
        }
    }
    property Timer startupTimeout: Timer {
        interval: 5000; running: service.active && !service.ready && service.worker.running
        onTriggered: service.error = "Notes could not connect. Your draft is kept here."
    }
    property Timer reconnect: Timer { onTriggered: if (service.active && !service.worker.running) service.worker.running = true }
}
