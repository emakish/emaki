pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// Private presentation channel, never serialized by CLI or diagnostic IPC.
// Enumeration, workspace IDs and all actions still come from the core model.
Scope {
    id: labels
    required property NiriService niri
    required property bool active
    property var values: ({})
    property string state: "closed"
    property bool acknowledged: false
    // A window the compositor opened after the initial snapshot (never the snapshot itself).
    signal appeared(int id, string appId)
    readonly property bool wanted: active && niri.connected && Quickshell.env("NIRI_SOCKET") !== ""
    function reset(): void {
        socket.connected = false;
        values = {};
        acknowledged = false;
        state = wanted ? "connecting" : "closed";
        initial.stop();
        retry.restart();
    }
    onWantedChanged: reset()
    Connections {
        target: labels.niri
        function onGenerationChanged(): void {
            labels.reset();
        }
    }
    function receive(line: string): void {
        if (!wanted || !socket.connected)
            return;
        try {
            const event = JSON.parse(line);
            if (!acknowledged) {
                if (event.Ok !== "Handled")
                    throw new Error("ack");
                acknowledged = true;
                return;
            }
            let next = Object.assign({}, values);
            if (event.WindowsChanged) {
                next = {};
                for (const w of event.WindowsChanged.windows)
                    if (Number.isSafeInteger(w.id))
                        next[w.id] = ({
                                title: String(w.title ?? ""),
                                appId: String(w.app_id ?? "")
                            });
                state = "ready";
                initial.stop();
            } else if (event.WindowOpenedOrChanged) {
                const w = event.WindowOpenedOrChanged.window;
                if (Number.isSafeInteger(w.id)) {
                    const fresh = state === "ready" && !(w.id in values);
                    next[w.id] = ({
                            title: String(w.title ?? ""),
                            appId: String(w.app_id ?? "")
                        });
                    if (fresh)
                        appeared(w.id, next[w.id].appId);
                }
            } else if (event.WindowClosed) {
                delete next[event.WindowClosed.id];
            } else {
                return; // Layout/keyboard/cast events cannot change private labels.
            }
            values = next;
        } catch (_) {
            reset();
            state = "invalid_response";
        }
    }
    Socket {
        id: socket
        path: Quickshell.env("NIRI_SOCKET")
        onConnectionStateChanged: {
            if (connected) {
                write('"EventStream"\n');
                flush();
                initial.restart();
            } else {
                labels.values = {};
                labels.acknowledged = false;
                labels.state = labels.wanted ? "unavailable" : "closed";
                retry.restart();
            }
        }
        onError: {
            labels.values = {};
            labels.state = labels.wanted ? "unavailable" : "closed";
            retry.restart();
        }
        parser: SplitParser {
            onRead: line => labels.receive(line)
        }
    }
    Timer {
        id: retry
        interval: 500
        onTriggered: {
            if (labels.wanted && !socket.connected)
                socket.connected = true;
        }
    }
    Timer {
        id: initial
        interval: 2000
        onTriggered: {
            labels.reset();
            labels.state = "timeout";
        }
    }
}
