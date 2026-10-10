pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: settings
    required property bool active
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
    // A set/undo the core committed: the page shows "Applied · Undo in History".
    signal applied
    PrivateJob {
        id: core
        timeoutMs: 35000
        onCompleted: value => {
            const action = settings.currentAction;
            settings.replyReceived = true;
            settings.state = value.state;
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
        onCompleted: value => settings.defaults = value.entries || []
    }
    PrivateJob {
        id: xkbJob
        onCompleted: value => settings.xkbLayouts = value.entries || []
    }
    PrivateJob {
        id: wallpapersJob
        onCompleted: value => {
            settings.wallpapers = value.entries || [];
            settings.inheritedWallpaper = value.inheritedPath || "";
        }
    }
}
