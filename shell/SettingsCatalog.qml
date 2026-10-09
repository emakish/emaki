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
    property string state: "idle"
    property string lastAction: "idle"
    property var queue: []
    readonly property int applyEpoch: SettingsBridge.applyEpoch
    property int readEpoch: 0
    readonly property bool busy: core.busy || queue.length > 0
    function send(action: string, args: var): void {
        queue = queue.concat([
            {
                action: action,
                args: args
            }
        ]);
        drain();
    }
    function drain(): void {
        if (!core.busy && queue.length) {
            const next = queue[0];
            queue = queue.slice(1);
            readEpoch = applyEpoch;
            core.start({
                op: "settings",
                action: next.action,
                args: next.args,
                profile: profile,
                binary: Quickshell.env("EMAKI_BIN")
            });
        }
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
            settings.state = value.state;
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
            if (value.history)
                settings.history = value.history;
        }
        onBusyChanged: {
            if (!busy) {
                if (settings.state === "idle")
                    settings.state = core.state;
                settings.drain();
            }
        }
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
        onCompleted: value => settings.wallpapers = value.entries || []
    }
}
