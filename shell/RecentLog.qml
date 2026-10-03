pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// What was last opened through the launcher (an empty All = Recent). The list lives
// in $XDG_STATE_HOME/emaki/recent.json through launcher-tools.py, like Frequent; refs only
// (desktop id, path, page id, app id, clip id), never titles or query text.
Scope {
    id: log
    required property bool active
    property var entries: []
    property string state: "idle"
    readonly property bool busy: job.busy
    property var queue: []
    function refresh(): void {
        queue = queue.concat([
            {
                op: "recent-list"
            }
        ]);
        drain();
    }
    function record(kind: string, ref: string): void {
        if (!ref)
            return;
        queue = queue.concat([
            {
                op: "recent-record",
                kind: kind,
                ref: ref
            }
        ]);
        drain();
    }
    function drain(): void {
        if (!job.busy && queue.length) {
            const next = queue[0];
            queue = queue.slice(1);
            job.start(next);
        }
    }
    onActiveChanged: {
        if (active)
            refresh();
    }
    PrivateJob {
        id: job
        onCompleted: value => {
            log.state = value.state;
            if (Array.isArray(value.entries))
                log.entries = value.entries;
        }
        onBusyChanged: {
            if (!busy)
                log.drain();
        }
    }
}
