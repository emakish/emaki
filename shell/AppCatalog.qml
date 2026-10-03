pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: catalog
    readonly property var entries: DesktopEntries.applications.values.filter(entry => !entry.noDisplay).sort((a, b) => a.name.localeCompare(b.name))
    readonly property string launcher: Quickshell.env("EMAKI_GTK_LAUNCH") || "gtk-launch"
    property string launchState: "idle"
    // A launch in flight; frequency bookkeeping never blocks the next click (it queues).
    readonly property bool busy: process.running || terminal.busy
    property string pendingId: ""
    property string pendingName: ""
    // Last stderr line of the launcher for the failure notice.
    property string errorLine: ""
    property var recordQueue: []
    property var counts: ({})
    readonly property var frequent: entries.filter(e => (counts[e.id] || 0) > 0).sort((a, b) => counts[b.id] - counts[a.id] || a.name.localeCompare(b.name)).slice(0, 6)
    readonly property string frequentState: frequency.state
    signal launched
    // The launcher could not start the app: id, human name, one-line reason.
    signal failed(string id, string name, string reason)
    Component.onCompleted: frequency.start({
        op: "frequent-list"
    })
    function accepted(): void {
        recordQueue = recordQueue.concat([pendingId]);
        flushRecords();
        launched();
    }
    function flushRecords(): void {
        if (frequency.busy || recordQueue.length === 0)
            return;
        const id = recordQueue[0];
        recordQueue = recordQueue.slice(1);
        frequency.start({
            op: "frequent-record",
            id: id
        });
    }
    function fail(reason: string): void {
        launchState = launchState === "timeout" ? "timeout" : "failed";
        failed(pendingId, pendingName, reason);
    }
    PrivateJob {
        id: frequency
        onCompleted: value => {
            if (value.counts)
                catalog.counts = value.counts;
        }
        onBusyChanged: if (!busy)
            catalog.flushRecords()
    }
    PrivateJob {
        id: terminal
        onCompleted: value => {
            catalog.launchState = value.state;
            if (value.state === "requested")
                catalog.accepted();
            else
                catalog.fail("Terminal launch: " + value.state);
        }
        onBusyChanged: if (!busy)
            Qt.callLater(() => {
                if (catalog.launchState === "pending")
                    catalog.fail("Terminal helper: " + terminal.state);
            })
    }
    // false: another launch is still in flight (the dock shows the pulse on that icon).
    function launch(entry: DesktopEntry): bool {
        if (!entry || busy)
            return false;
        pendingId = entry.id;
        pendingName = entry.name;
        errorLine = "";
        if (entry.runInTerminal) {
            launchState = "pending";
            terminal.start({
                op: "terminal",
                id: entry.id,
                terminal: Quickshell.env("EMAKI_TERMINAL") || ShellTools.terminal
            });
            return true;
        }
        // GLib resolves the ID in XDG directories and handles Exec field codes,
        // working directory and D-Bus activation. Never parse or shell-evaluate Exec/query.
        // Always pass the file name with ".desktop": gtk-launch keeps a name that already
        // ends in ".desktop" as is, so a bare "org.telegram.desktop" names a missing file.
        launchState = "pending";
        process.command = AppLaunch.command(entry.id, [launcher, "--", entry.id + ".desktop"]);
        process.running = true;
        deadline.restart();
        return true;
    }
    Process {
        id: process
        stdout: SplitParser {
            onRead: _line => {}
        }
        stderr: SplitParser {
            onRead: line => {
                const text = String(line).trim();
                if (text && !AppLaunch.diagnostic(text))
                    catalog.errorLine = text.slice(0, 200);
            }
        }
        onExited: (code, status) => {
            deadline.stop();
            if (catalog.launchState !== "pending")
                return;
            if (code === 0 && status === 0) {
                catalog.launchState = "requested";
                catalog.accepted();
            } else
                catalog.fail(catalog.errorLine || "gtk-launch exited with code " + code);
        }
        // exited() may arrive after runningChanged(): decide once the current signals settle.
        onRunningChanged: if (!running)
            Qt.callLater(() => {
                if (catalog.launchState === "pending") {
                    deadline.stop();
                    catalog.fail(catalog.errorLine || "gtk-launch could not be started");
                }
            })
    }
    Timer {
        id: deadline
        interval: AppLaunch.timeoutMs
        onTriggered: {
            // The independent worker may still complete. Requested is acceptance,
            // not confirmation of a window; a timeout must not invite a retry.
            catalog.launchState = "requested";
            process.signal(9);
            catalog.accepted();
        }
    }
}
