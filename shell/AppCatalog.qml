pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: catalog
    // Utility entries that Arch's live-ISO set and its dependencies put in every menu:
    // avahi (avahi-discover, bssh, bvnc), lftp, hwloc (lstopo), v4l-utils (qv4l2,
    // qvidcap), stoken (stoken-gui, stoken-gui-small), vim. Only the launcher skips
    // them: the programs stay installed, gtk-launch and MIME handling are unchanged
    // and the dock still resolves their windows (DECISIONS 2026-10-03).
    // Rich helpers and duplicate indicator entries; KDE Connect and SMS stay visible.
    // NoDisplay entries are excluded by shown(), without duplicate IDs here.
    readonly property var hiddenIds: ["avahi-discover", "bssh", "bvnc", "lftp", "lstopo", "qv4l2", "qvidcap", "stoken-gui", "stoken-gui-small", "vim", "qt6ct", "org.kde.kdeconnect.nonplasma", "mpv", "org.kde.kwrite"]
    readonly property var entries: DesktopEntries.applications.values.filter(entry => catalog.shown(entry)).sort((a, b) => a.name.localeCompare(b.name))
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
    // The launcher could not start the app: id, human name and one plain sentence for the notice.
    signal failed(string id, string name, string reason)
    Component.onCompleted: frequency.start({
        op: "frequent-list"
    })
    // Whether a desktop entry belongs in the launcher's lists (Apps, search, Recent).
    function shown(entry: DesktopEntry): bool {
        return entry !== null && !entry.noDisplay && !hiddenIds.includes(entry.id);
    }
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
    // `state`: the helper's state or "launch_failed"; `detail`: the raw reason (the launcher's
    // stderr line or exit code), which goes to the log only.
    function fail(state: string, detail: string): void {
        launchState = launchState === "timeout" ? "timeout" : "failed";
        console.info("Couldn't open " + pendingId + ": " + (detail || state));
        failed(pendingId, pendingName, failureText(state));
    }
    // What the notice says: an internal code or a program's message never reaches the screen.
    function failureText(state: string): string {
        return ({
                terminal_missing: "The terminal it needs isn’t installed.",
                desktop_missing: "The app is no longer installed."
            })[state] ?? "The app didn’t start.";
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
                catalog.fail(value.state, "");
        }
        onBusyChanged: if (!busy)
            Qt.callLater(() => {
                if (catalog.launchState === "pending")
                    catalog.fail(terminal.state, "terminal helper " + terminal.state);
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
                catalog.fail("launch_failed", catalog.errorLine || "gtk-launch exited with code " + code);
        }
        // exited() may arrive after runningChanged(): decide once the current signals settle.
        onRunningChanged: if (!running)
            Qt.callLater(() => {
                if (catalog.launchState === "pending") {
                    deadline.stop();
                    catalog.fail("launch_failed", catalog.errorLine || "gtk-launch could not be started");
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
