// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: settings
    required property bool active
    property var notificationStore: null
    // A client outside the shell directory (the manager's own qs) names the installed helper.
    property string helper: Quickshell.shellPath("helpers/launcher-tools.py")
    readonly property string profile: Quickshell.env("EMAKI_SETTINGS_PROFILE")
    readonly property var values: SettingsBridge.values
    property var history: []
    property var defaults: []
    property var xkbLayouts: []
    property var installedWallpapers: []
    readonly property var wallpapers: styles.map(style => ({
                path: style.wallpaper,
                name: qsTranslate("SettingsThemesPage", style.name),
                thumbnail: style.thumbnail,
                available: style.wallpaper_available
            })).concat(installedWallpapers)
    property var styles: []
    property string inheritedWallpaper: ""
    property string state: "idle"
    property string lastAction: "idle"
    property string lastStatus: ""
    property bool sessionApplied: false
    property string currentAction: ""
    property string currentChoice: ""
    property bool replyReceived: false
    property bool inFlight: false
    property var queue: []
    readonly property int applyEpoch: SettingsBridge.applyEpoch
    property int readEpoch: 0
    readonly property bool busy: inFlight || core.busy || queue.length > 0
    readonly property bool writing: (inFlight && ["set", "reset", "undo"].includes(currentAction)) || queue.some(request => ["set", "reset", "undo"].includes(request.action))
    readonly property alias wallpaperChooser: wallpaperChooserJob
    property string wallpaperChooserError: ""
    function chooseWallpaper(): void {
        if (writing || wallpaperChooser.busy || !row("appearance.wallpaper"))
            return;
        wallpaperChooserError = "";
        wallpaperChooser.start({
            op: "wallpaper-choose"
        });
    }
    function wallpaperChoiceFailed(): void {
        if (wallpaperChooserError)
            return;
        wallpaperChooserError = qsTr("Could not choose a picture. Your wallpaper has not changed.");
        notificationStore?.local("Emaki", wallpaperChooserError, "");
    }
    PrivateJob {
        id: wallpaperChooserJob
        timeoutMs: 130000
        onCompleted: result => {
            if (result.state === "ready" && typeof result.path === "string" && result.path.startsWith("/")) {
                settings.wallpaperChooserError = "";
                settings.applyChoice("wallpaper", "appearance.wallpaper", result.path);
            } else if (result.state !== "cancelled") {
                settings.wallpaperChoiceFailed();
            }
        }
        // A reply sets state before completed; defer transport failures to avoid two notices.
        onStateChanged: {
            if (!["idle", "pending", "ready", "cancelled"].includes(state))
                Qt.callLater(settings.wallpaperChoiceFailed);
        }
    }
    readonly property alias overviewChooser: overviewChooserJob
    property string overviewChooserError: ""
    function chooseOverview(): void {
        if (writing || overviewChooser.busy)
            return;
        overviewChooserError = "";
        overviewChooser.start({
            op: "overview-choose"
        });
    }
    function selectOverview(style): void {
        overviewChooserError = "";
        set("appearance.overview_background", "catalog:" + style.id);
    }
    function resetOverview(): void {
        overviewChooserError = "";
        reset("appearance.overview_background");
    }
    function overviewChoiceFailed(message: string): void {
        if (overviewChooserError)
            return;
        overviewChooserError = message;
        notificationStore?.local("Emaki", overviewChooserError, "");
    }
    PrivateJob {
        id: overviewChooserJob
        timeoutMs: 150000
        onCompleted: result => {
            if (result.state === "ready" && typeof result.path === "string" && result.path.startsWith("/")) {
                const still = result.kind === "picture" ? result.path : settings.value("appearance.overview_still");
                settings.applyChoice("overview", "appearance.overview_background", JSON.stringify({
                    video: result.kind === "video" ? result.path : "",
                    still: still
                }));
            } else if (result.state !== "cancelled") {
                settings.overviewChoiceFailed(qsTr("This file could not be played or opened."));
            }
        }
        onStateChanged: {
            if (!["idle", "pending", "ready", "cancelled"].includes(state))
                Qt.callLater(settings.overviewChoiceFailed, qsTr("Could not choose an overview background. Try again."));
        }
    }
    function applyChoice(choice: string, key: string, value: string): void {
        queue = queue.concat([
            {
                action: "set",
                args: [key, value],
                choice: choice
            }
        ]);
        Qt.callLater(drain);
    }
    function choiceApplyFailed(): void {
        if (currentChoice === "wallpaper")
            wallpaperChoiceFailed();
        else if (currentChoice === "overview")
            overviewChoiceFailed(qsTr("Could not choose an overview background. Try again."));
    }
    function choiceApplyUnconfirmed(): void {
        if (!currentChoice)
            return;
        const message = qsTr("The change could not be confirmed. Check the current value before trying again.");
        if (currentChoice === "wallpaper") {
            wallpaperChooserError = message;
            notificationStore?.local("Emaki", message, "");
        } else {
            overviewChoiceFailed(message);
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
            currentChoice = next.choice || "";
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
            choiceApplyUnconfirmed();
            state = core.state;
            if (["set", "reset", "undo"].includes(currentAction)) {
                lastStatus = "unconfirmed";
                lastAction = core.state;
            }
        }
        inFlight = false;
        currentAction = "";
        currentChoice = "";
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
            if (settings.currentChoice && !["committed", "unchanged"].includes(value.status)) {
                if (value.status === "rejected")
                    settings.choiceApplyFailed();
                else
                    settings.choiceApplyUnconfirmed();
            }
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
            const styles = core.list(value.styles);
            if (value.status !== "rejected" && styles !== null && settings.readEpoch === settings.applyEpoch)
                settings.styles = styles;
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
            settings.installedWallpapers = value.entries || [];
            settings.inheritedWallpaper = value.inheritedPath || "";
        }
    }
}
