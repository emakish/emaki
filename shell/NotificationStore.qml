// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// History and DND survive restarts in $XDG_STATE_HOME/emaki/notifications.json.
// Only text is stored; restored entries are inert (no actions). Never returned in IPC diagnostics.
Scope {
    id: store
    property bool dnd: false
    property var catalog: null
    property bool dndMigrated: false
    property bool dndMigrationRequested: false
    onManagedValuesChanged: Qt.callLater(migrateDnd)
    onRestoredChanged: Qt.callLater(migrateDnd)
    function migrateDnd(): void {
        if (!restored || foreign || dndMigrated || !catalog)
            return;
        const row = managedValues.find(v => v.key === "notifications.dnd");
        if (!row)
            return;
        // An explicit managed value always wins, including a previously saved false.
        if (row.override_value !== null && row.override_value !== undefined || !dnd) {
            dndMigrated = true;
            if (dnd)
                saveTimer.restart();
        } else if (!dndMigrationRequested) {
            dndMigrationRequested = true;
            catalog.set("notifications.dnd", "true");
        }
        // Mark only an observed committed override. A failed or interrupted request
        // keeps the old cache value for a retry on the next shell start.
    }
    readonly property var managedValues: catalog ? catalog.values : []
    readonly property bool effectiveDnd: manualDnd() || Number(policyValue("notifications.until", "0")) > now || scheduleActive(policyJson("notifications.schedule", {}), now)
    readonly property var rules: policyJson("notifications.rules", {})
    readonly property bool systemSounds: policyValue("sound.system_sounds", true) === true
    // A playback provider can query this policy without bypassing quiet rules.
    function soundAllowed(id: string): bool {
        return systemSounds && !effectiveDnd && ruleFor(id) === "allow";
    }
    property var applications: []
    function policyValue(key: string, fallback: var): var {
        const row = managedValues.find(v => v.key === key);
        return row && row.value !== null ? row.value : fallback;
    }
    function policyJson(key: string, fallback: var): var {
        try {
            return JSON.parse(policyValue(key, JSON.stringify(fallback)));
        } catch (error) {
            return fallback;
        }
    }
    function manualDnd(): bool {
        const row = managedValues.find(v => v.key === "notifications.dnd");
        if (!dndMigrated && dnd && (!row || row.override_value === null))
            return true;
        return policyValue("notifications.dnd", dnd) === true;
    }
    function setDnd(enabled: bool): void {
        if (catalog) {
            catalog.set("notifications.dnd", enabled ? "true" : "false");
            if (!enabled)
                catalog.set("notifications.until", "0");
        } else {
            dnd = enabled;
        }
    }
    function scheduleActive(schedule: var, timestamp: double): bool {
        if (!schedule || schedule.enabled !== true)
            return false;
        const minute = value => {
            if (typeof value !== "string" || !/^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/.test(value))
                return -1;
            return Number(value.slice(0, 2)) * 60 + Number(value.slice(3));
        };
        const start = minute(schedule.start), end = minute(schedule.end);
        if (start < 0 || end < 0)
            return false;
        const date = new Date(timestamp);
        const current = date.getHours() * 60 + date.getMinutes();
        return start === end || (start < end ? current >= start && current < end : current >= start || current < end);
    }
    function applicationId(n: var): string {
        const desktop = String(n.desktopEntry || "").slice(0, 240);
        return desktop ? "desktop:" + desktop : "name:" + String(n.appName || "Application").slice(0, 128);
    }
    function rememberApplication(id: string, name: string): void {
        if (applications.some(app => app.id === id))
            return;
        applications = applications.concat([
            {
                id: id,
                name: name.slice(0, 128)
            }
        ]).slice(-256);
    }
    function ruleFor(id: string): string {
        return Object.prototype.hasOwnProperty.call(rules, id) && ["allow", "silent", "off"].includes(rules[id]) ? rules[id] : "allow";
    }
    function peekAllowed(entry: var): bool {
        return entry.critical === true || ruleFor(entry.appId || "name:" + entry.app) === "allow";
    }
    onApplicationsChanged: if (restored)
        saveTimer.restart()
    property var entries: []
    readonly property string statePath: (Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state") + "/emaki/notifications.json"
    property bool restored: false
    // A file written by a newer shell (it outlives a system rollback in /home): its format is
    // unknown here, so it is neither read nor overwritten during this run.
    property bool foreign: false
    onEntriesChanged: if (restored)
        saveTimer.restart()
    onDndChanged: if (restored)
        saveTimer.restart()
    Component.onCompleted: restore()
    function restore(): void {
        try {
            const file = JSON.parse(stateFile.text() || "{}");
            store.foreign = file.version !== undefined && file.version !== 1;
            const saved = store.foreign ? {} : file;
            store.dnd = saved.dnd === true;
            store.dndMigrated = saved.dndMigrated === true;
            // Older caches listed internal warnings even though their rules never apply.
            store.applications = (Array.isArray(saved.applications) ? saved.applications : []).filter(a => a && typeof a.id === "string" && a.id.length <= 512 && typeof a.name === "string" && !["name:Battery low", "name:Battery critical", "name:Desktop update"].includes(a.id)).slice(-256).map(a => ({
                        id: a.id,
                        name: a.name.slice(0, 128)
                    }));
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
        onTriggered: if (!store.foreign)
            stateFile.setText(JSON.stringify({
                version: 1,
                dnd: store.dndMigrated ? store.manualDnd() : store.dnd,
                dndMigrated: store.dndMigrated,
                applications: store.applications,
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
    // Clients sometimes send body markup despite the advertised plain-text capability.
    // Strip tags before decoding entities so escaped device names remain literal text.
    // Consumers must keep Text.PlainText: decoded text is never interpreted as markup.
    function plainBody(value: string): string {
        const text = value.replace(/<!--[\s\S]*?-->|<\/?[a-zA-Z](?:[^"'<>]|"[^"]*"|'[^']*')*>/g, "");
        const named = {
            amp: "&",
            lt: "<",
            gt: ">",
            quot: '"',
            apos: "'",
            nbsp: " "
        };
        return text.replace(/&(#x[0-9a-f]+|#[0-9]+|amp|lt|gt|quot|apos|nbsp);/gi, (entity, name) => {
            if (name[0] !== "#")
                return named[name] === undefined ? entity : named[name];
            const hex = name[1].toLowerCase() === "x";
            const code = parseInt(name.slice(hex ? 2 : 1), hex ? 16 : 10);
            return code > 0 && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff) ? String.fromCodePoint(code) : entity;
        }).slice(0, 4096);
    }
    function snapshot(n: var, time: double): var {
        return {
            id: n.id,
            app: (n.appName || "Application").slice(0, 128),
            appId: n.desktopEntry ? "desktop:" + String(n.desktopEntry).slice(0, 240) : "name:" + (n.appName || "Application").slice(0, 128),
            // Pairing confirmation, authorization and displayed PINs use persistent
            // blueman notices. Match protocol fields, not translated summaries.
            critical: (n.appName === "blueman" && n.appIcon === "blueman" && n.expireTimeout === 0) || (n.appName === "Desktop update" && n.summary === "Sign out and sign in again to finish updating the desktop."),
            // The app's icon for the panel's row: the one it sent, else its desktop entry's.
            // Memory only: not saved with the history (a sent icon may be a private path).
            icon: String(n.appIcon || (n.desktopEntry ? DesktopEntries.byId(n.desktopEntry)?.icon ?? "" : "")).slice(0, 1024),
            summary: n.summary.slice(0, 512),
            body: plainBody(n.body),
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
        // The transaction hook also reaches shells that predate the observer.
        // Both captured and installed-file panels have their own critical notice.
        if ((Quickshell.env("EMAKI_SESSION_GENERATION") || (Quickshell.env("EMAKI_SESSION_SOURCE") && Quickshell.env("EMAKI_SESSION_WATCHER")) || Quickshell.env("EMAKI_LIVE_SESSION") === "1") && n.appName === "Desktop update" && n.summary === "Sign out and sign in again to finish updating the desktop.") {
            n.dismiss();
            return;
        }
        const appId = applicationId(n);
        const rule = ruleFor(appId);
        const entry = snapshot(n, Date.now());
        if (!(entry.critical && n.appName === "Desktop update"))
            rememberApplication(appId, String(n.appName || "Application"));
        if (rule === "off" && !entry.critical) {
            n.dismiss();
            return;
        }
        const id = n.id;
        entries = [entry].concat(entries);
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
        if (rule === "allow" || entry.critical)
            arrived(id);
    }
    property int systemId: -1
    // A notice from the shell itself (no D-Bus object): negative IDs, no actions.
    function local(app: string, summary: string, body: string): int {
        return localNotice(app, summary, body, false, false);
    }
    function localNotice(app: string, summary: string, body: string, batteryWarning: bool, sessionUpdate: bool): int {
        const appId = "name:" + app.slice(0, 128);
        if (!batteryWarning && !sessionUpdate)
            rememberApplication(appId, app);
        const id = systemId--;
        const rule = ruleFor(appId);
        if (rule === "off" && !batteryWarning && !sessionUpdate)
            return id;
        entries = [
            {
                id: id,
                app: app.slice(0, 128),
                critical: batteryWarning || sessionUpdate,
                batteryWarning: batteryWarning,
                sessionUpdate: sessionUpdate,
                summary: summary.slice(0, 512),
                body: body.slice(0, 4096),
                time: Date.now(),
                object: null,
                actions: [],
                deadline: 0
            }
        ].concat(entries);
        if (entries.length > 200)
            dismiss(entries.slice(200).map(e => e.id));
        if (rule === "allow" || batteryWarning || sessionUpdate)
            arrived(id);
        return id;
    }
    function systemBattery(percent: int): void {
        localNotice(percent <= 5 ? "Battery critical" : "Battery low", percent + "% left — plug in soon.", "", true, false);
    }
    // The sleep guard's policy names (scripts/emaki-sleep-guard). Only what the policy is
    // known to have done: the guard does not report whether its second attempt locked.
    function systemSleepLock(policy: string): void {
        local("Screen lock", policy === "end-session" ? "Emaki could not lock the screen before sleep, so it ended the session." : policy === "end-session-failed" ? "Emaki could not lock the screen before sleep and could not end the session." : policy === "sleep-relock" ? "Emaki could not lock the screen before sleep. It tried again after waking." : policy === "stay-awake" ? "Emaki could not lock the screen when sleep was requested." : "Emaki could not lock the screen before sleep.", "");
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
        running: true
        repeat: true
        onTriggered: {
            const current = Date.now();
            store.now = current;
            for (const e of store.entries)
                if (e.deadline && e.deadline <= current && e.object)
                    e.object.expire();
        }
    }
}
