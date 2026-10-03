pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: clips
    required property bool active
    property var entries: []
    property int epoch: 0
    property int readEpoch: 0
    readonly property string state: reader.state
    readonly property string actionState: action.state
    // The shell records the clipboard only when explicitly asked to own the watcher
    // (EMAKI_SHELL_CLIPBOARD_RECORDER=1). Otherwise an external `wl-paste --watch
    // cliphist store` is assumed and Pause/Resume are not offered.
    readonly property bool recorderOwned: Quickshell.env("EMAKI_SHELL_CLIPBOARD_RECORDER") === "1"
    property bool paused: false
    readonly property string recorder: !recorderOwned ? "external" : watcher.running ? "recording" : paused ? "paused" : "failed"
    signal copied
    function refresh(): void {
        if (active && !reader.busy) {
            readEpoch = epoch;
            reader.start({
                op: "clip-list"
            });
        }
    }
    function perform(id: string, remove: bool): void {
        if (active && entries.some(e => e.id === id))
            action.start({
                op: remove ? "clip-delete" : "clip-copy",
                id: id
            });
    }
    function clear(): void {
        if (active)
            action.start({
                op: "clip-clear"
            });
    }
    // Pause stops the watcher: nothing is stored until Resume; the history stays.
    // Resume starts a new watcher; wl-paste also runs the command once for the
    // selection present at start, which cliphist deduplicates.
    function setRecording(value: bool): bool {
        if (!recorderOwned)
            return false;
        paused = !value;
        if (value) {
            if (!watcher.running)
                watcher.running = true;
        } else if (watcher.running)
            watcher.signal(15);
        return true;
    }
    onActiveChanged: {
        ++epoch;
        entries = [];
        if (active)
            refresh();
    }
    Component.onCompleted: {
        if (recorderOwned)
            setRecording(true);
    }
    PrivateJob {
        id: reader
        onCompleted: value => {
            if (clips.active && clips.readEpoch === clips.epoch)
                clips.entries = value.entries || [];
        }
        onBusyChanged: {
            if (!busy && clips.active && clips.readEpoch !== clips.epoch)
                clips.refresh();
        }
    }
    PrivateJob {
        id: action
        onCompleted: value => {
            if (value.state === "copied")
                clips.copied();
            if (value.state === "deleted")
                clips.refresh();
        }
    }
    // The wrapper holds `wl-paste --watch cliphist store` and ends it when this
    // stdin pipe closes, so a dead shell never leaves an orphan recorder.
    Process {
        id: watcher
        command: [Quickshell.env("EMAKI_PYTHON") || "python3", "-B", Quickshell.shellPath("helpers/clip-recorder.py")]
        stdinEnabled: true
        stderr: SplitParser { // Clipboard contents never reach the shell log.
            onRead: _line => {}
        }
    }
}
