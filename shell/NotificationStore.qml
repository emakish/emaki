pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// History and DND survive restarts in $XDG_STATE_HOME/emaki/notifications.json.
// Only text is stored; restored entries are inert (no actions). Never returned in IPC diagnostics.
Scope {
    id: store
    property bool dnd: false
    property var entries: []
    readonly property string statePath: (Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state") + "/emaki/notifications.json"
    property bool restored: false
    onEntriesChanged: if (restored)
        saveTimer.restart()
    onDndChanged: if (restored)
        saveTimer.restart()
    Component.onCompleted: restore()
    function restore(): void {
        try {
            const saved = JSON.parse(stateFile.text() || "{}");
            store.dnd = saved.dnd === true;
            store.entries = (Array.isArray(saved.entries) ? saved.entries : []).filter(e => e && typeof e.summary === "string" && typeof e.time === "number").slice(0, 200).map(e => ({
                        id: store.systemId--,
                        app: String(e.app || "Application").slice(0, 128),
                        summary: e.summary.slice(0, 512),
                        body: String(e.body || "").slice(0, 4096),
                        time: e.time,
                        object: null,
                        actions: [],
                        deadline: 0
                    }));
        } catch (error) {
            // Missing or damaged file: start empty, the next save replaces it.
        }
        store.restored = true;
    }
    FileView {
        id: stateFile
        path: store.statePath
        blockLoading: true
        atomicWrites: true
        printErrors: false
    }
    Timer {
        id: saveTimer
        interval: 500
        onTriggered: stateFile.setText(JSON.stringify({
            version: 1,
            dnd: store.dnd,
            entries: store.entries.map(e => ({
                        app: e.app,
                        summary: e.summary,
                        body: e.body,
                        time: e.time
                    }))
        }))
    }
    property var expanded: ({})
    property double now: Date.now()
    readonly property int count: entries.length
    property string actionState: "idle"
    signal arrived(int id)
    function bucket(time: double): string {
        const age = (now - time) / 60000;
        return age < 5 ? "Now" : age < 720 ? "Today" : "Earlier";
    }
    function groups(): var {
        const result = [];
        for (const bucketName of ["Now", "Today", "Earlier"]) {
            const rows = entries.filter(n => bucket(n.time) === bucketName);
            const apps = [...new Set(rows.map(n => n.app))];
            for (const app of apps) {
                const notes = rows.filter(n => n.app === app);
                const key = JSON.stringify([bucketName, app]);
                result.push({
                    key: key,
                    bucket: bucketName,
                    app: app,
                    count: notes.length,
                    ids: notes.map(n => n.id),
                    expanded: !!expanded[key],
                    notes: expanded[key] ? notes : notes.slice(0, 1)
                });
            }
        }
        return result;
    }
    function expand(key: string, value: bool): void {
        const next = Object.assign({}, expanded);
        if (value)
            next[key] = true;
        else
            delete next[key];
        expanded = next;
    }
    function snapshot(n: var, time: double): var {
        return {
            id: n.id,
            app: (n.appName || "Application").slice(0, 128),
            // The app's icon for the panel's row: the one it sent, else its desktop entry's.
            // Memory only: not saved with the history (a sent icon may be a private path).
            icon: String(n.appIcon || (n.desktopEntry ? DesktopEntries.byId(n.desktopEntry)?.icon ?? "" : "")).slice(0, 1024),
            summary: n.summary.slice(0, 512),
            body: n.body.slice(0, 4096),
            time: time,
            object: n,
            actions: n.actions.map(a => ({
                        id: a.identifier,
                        text: a.text.slice(0, 128)
                    })),
            deadline: n.expireTimeout > 0 ? Date.now() + Math.min(n.expireTimeout, 86400000) : 0
        };
    }
    function accept(n: var): void {
        n.tracked = true;
        const id = n.id;
        entries = [snapshot(n, Date.now())].concat(entries);
        function update() {
            const old = store.entries.find(e => e.id === id);
            if (old?.object)
                store.entries = store.entries.map(e => e.id === id ? store.snapshot(n, old.time) : e);
        }
        n.summaryChanged.connect(update);
        n.bodyChanged.connect(update);
        n.appNameChanged.connect(update);
        n.actionsChanged.connect(update);
        n.expireTimeoutChanged.connect(update);
        n.closed.connect(reason => {
            if (reason === 1) // expired: inert copy remains in history
                store.entries = store.entries.map(e => e.id === id ? Object.assign({}, e, {
                        object: null,
                        actions: [],
                        deadline: 0
                    }) : e);
            else
                store.entries = store.entries.filter(e => e.id !== id);
        });
        if (entries.length > 200)
            dismiss(entries.slice(200).map(e => e.id));
        arrived(id);
    }
    property int systemId: -1
    // A notice from the shell itself (no D-Bus object): negative IDs, no actions.
    function local(app: string, summary: string, body: string): int {
        const id = systemId--;
        entries = [
            {
                id: id,
                app: app.slice(0, 128),
                summary: summary.slice(0, 512),
                body: body.slice(0, 4096),
                time: Date.now(),
                object: null,
                actions: [],
                deadline: 0
            }
        ].concat(entries).slice(0, 200);
        arrived(id);
        return id;
    }
    function systemBattery(percent: int): void {
        local("Battery low", percent + "% left — plug in soon.", "");
    }
    function dismiss(ids: var): void {
        const removed = entries.filter(e => ids.includes(e.id));
        entries = entries.filter(e => !ids.includes(e.id));
        for (const e of removed)
            if (e.object)
                e.object.dismiss();
    }
    function activate(id: int, action: string): bool {
        const entry = entries.find(e => e.id === id);
        const selected = entry?.object?.actions.find(a => a.identifier === action);
        if (!selected) {
            actionState = "action_unavailable";
            return false;
        }
        selected.invoke();
        actionState = "requested";
        // NotificationAction.invoke() closes nonresident notifications itself.
        return true;
    }
    Timer {
        interval: 1000
        running: store.entries.length > 0
        repeat: true
        onTriggered: {
            const current = Date.now();
            if (Math.floor(current / 60000) !== Math.floor(store.now / 60000))
                store.now = current;
            for (const e of store.entries)
                if (e.deadline && e.deadline <= current && e.object)
                    e.object.expire();
        }
    }
}
