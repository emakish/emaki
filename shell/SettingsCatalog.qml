// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: settings
    required property bool active
    // A client outside the shell directory (the manager's own qs) names the installed helper.
    property string helper: Quickshell.shellPath("helpers/launcher-tools.py")
    readonly property string profile: Quickshell.env("EMAKI_SETTINGS_PROFILE")
    readonly property var values: SettingsBridge.values
    property var history: []
    property var defaults: []
    property var xkbLayouts: []
    property var wallpapers: []
    property string inheritedWallpaper: ""
    property string state: "idle"
    property string lastAction: "idle"
    property string lastStatus: ""
    property bool sessionApplied: false
    property string currentAction: ""
    property bool replyReceived: false
    property bool inFlight: false
    property var queue: []
    readonly property int applyEpoch: SettingsBridge.applyEpoch
    property int readEpoch: 0
    readonly property bool busy: inFlight || core.busy || queue.length > 0
    readonly property bool writing: (inFlight && ["set", "reset", "undo"].includes(currentAction)) || queue.some(request => ["set", "reset", "undo"].includes(request.action))
    readonly property alias wallpaperChooser: wallpaperChooser
    property string wallpaperChooserError: ""
    function chooseWallpaper(): void {
        if (writing || wallpaperChooser.busy || !row("appearance.wallpaper"))
            return;
        wallpaperChooserError = "";
        wallpaperChooser.start({
            op: "wallpaper-choose"
        });
    }
    PrivateJob {
        id: wallpaperChooser
        timeoutMs: 130000
        onCompleted: result => {
            if (result.state === "ready" && typeof result.path === "string" && result.path.startsWith("/")) {
                settings.wallpaperChooserError = "";
                settings.set("appearance.wallpaper", result.path);
            } else if (result.state !== "cancelled") {
                settings.wallpaperChooserError = "Could not choose a picture. Your wallpaper has not changed.";
            }
        }
        onStateChanged: {
            if (!["idle", "pending", "ready", "cancelled"].includes(state))
                settings.wallpaperChooserError = "Could not choose a picture. Your wallpaper has not changed.";
        }
    }
    function send(action: string, args: var): void {
        queue = queue.concat([
            {
                action: action,
                args: args
            }
        ]);
        Qt.callLater(drain);
    }
    function drain(): void {
        if (!inFlight && !core.busy && queue.length) {
            inFlight = true;
            const next = queue[0];
            queue = queue.slice(1);
            readEpoch = applyEpoch;
            currentAction = next.action;
            replyReceived = false;
            core.start({
                op: "settings",
                action: next.action,
                args: next.args,
                profile: profile,
                binary: Quickshell.env("EMAKI_BIN")
            });
        }
    }
    function finish(): void {
        if (!inFlight || core.busy)
            return;
        if (!replyReceived) {
            state = core.state;
            if (["set", "reset", "undo"].includes(currentAction)) {
                lastStatus = "unconfirmed";
                lastAction = core.state;
            }
        }
        inFlight = false;
        currentAction = "";
        Qt.callLater(drain);
    }
    function refresh(): void {
        send("list", []);
        send("history", []);
        defaultsJob.start({
            op: "defaults"
        });
        xkbJob.start({
            op: "xkb-layouts"
        });
        wallpapersJob.start({
            op: "wallpapers"
        });
    }
    // Effective value of a core key (override or default), null while unknown.
    function value(key: string): var {
        const row = Array.from(settings.values).find(v => v.key === key);
        return row === undefined || row.value === undefined ? null : row.value;
    }
    function set(key: string, value: string): void {
        send("set", [key, value]);
    }
    function reset(key: string): void {
        send("reset", [key]);
    }
    function row(key: string): var {
        return Array.from(settings.values).find(v => v.key === key) || null;
    }
    function setFloating(value: string): void {
        set("keybindings.toggle_window_floating", value);
    }
    function undo(id: string): void {
        send("undo", [id]);
    }
    onActiveChanged: {
        if (active)
            refresh();
    }
    // Start from the durable store without opening a settings page.
    Component.onCompleted: send("list", [])
    // Atomic source replacement is the durable commit notification. The root
    // watch also catches creation of the emaki directory on the first change.
    readonly property string configRoot: profile ? profile + "/config" : (Quickshell.env("XDG_CONFIG_HOME") || Quickshell.env("HOME") + "/.config")
    FileView {
        path: settings.configRoot + "/."
        preload: false
        watchChanges: true
        printErrors: false
        onFileChanged: {
            source.watchChanges = false;
            source.watchChanges = true;
            changed.restart();
        }
    }
    FileView {
        id: source
        path: settings.configRoot + "/emaki/settings.toml"
        preload: false
        watchChanges: true
        printErrors: false
        onFileChanged: changed.restart()
    }
    Timer {
        id: changed
        interval: 50
        onTriggered: settings.send("list", [])
    }
    // Separate shell clients may read the same store at once. A busy read
    // must retry even when no further file change arrives.
    Timer {
        id: readRetry
        interval: 100
        onTriggered: settings.send("list", [])
    }
    // A set/undo the core committed: the page shows "Applied · Undo in History".
    signal applied
    PrivateJob {
        id: core
        helper: settings.helper
        timeoutMs: 35000
        onCompleted: value => {
            const action = settings.currentAction;
            settings.replyReceived = true;
            settings.state = value.state;
            if (action === "list") {
                if (value.state === "settings_busy")
                    readRetry.restart();
                else
                    readRetry.stop();
            }
            if (["set", "reset", "undo"].includes(action)) {
                settings.lastStatus = value.status || "rejected";
                settings.sessionApplied = value.session_applied === true;
            }
            if (value.status === "rejected")
                settings.lastAction = value.state;
            else if (value.status === "committed" || value.status === "unchanged") {
                settings.lastAction = value.state;
                if (value.status === "committed")
                    settings.applied();
                settings.send("list", []);
                settings.send("history", []);
            }
            // A list already in flight must not overwrite a newer IPC apply.
            if (value.settings?.length && settings.readEpoch === settings.applyEpoch)
                SettingsBridge.values = value.settings;
            else if (value.settings?.length && action === "list")
                // An IPC apply carries no titles: read the full rows again.
                settings.send("list", []);
            if (action === "history" && value.history)
                settings.history = value.history;
        }
        onBusyChanged: if (!busy)
            Qt.callLater(settings.finish)
        onStateChanged: if (state !== "pending" && !busy)
            Qt.callLater(settings.finish)
    }
    PrivateJob {
        id: defaultsJob
        helper: settings.helper
        onCompleted: value => settings.defaults = value.entries || []
    }
    PrivateJob {
        id: xkbJob
        helper: settings.helper
        onCompleted: value => settings.xkbLayouts = value.entries || []
    }
    PrivateJob {
        id: wallpapersJob
        helper: settings.helper
        onCompleted: value => {
            settings.wallpapers = value.entries || [];
            settings.inheritedWallpaper = value.inheritedPath || "";
        }
    }
}
