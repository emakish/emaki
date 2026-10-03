pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: service
    readonly property var items: (receiver.item as TrayItems)?.items ?? []
    readonly property bool requested: Quickshell.env("EMAKI_SHELL_TRAY") === "1"
    property string state: requested ? "checking" : "disabled"
    property bool verifying: false
    property bool finished: false
    // No SystemTray singleton (nor registration attempt) before this check.
    Loader {
        id: receiver
    }
    Process {
        id: check
        command: ["busctl", "--user", "--timeout=2", "--json=short", "call", "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", service.verifying ? "GetConnectionUnixProcessID" : "NameHasOwner", "s", "org.kde.StatusNotifierWatcher"]
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const value = JSON.parse(text).data[0];
                    if (service.verifying) {
                        service.state = value === Quickshell.processId ? "active" : "owned_elsewhere";
                    } else if (value === true) {
                        service.state = "owned_elsewhere";
                    } else if (value === false) {
                        receiver.setSource(Qt.resolvedUrl("TrayItems.qml"));
                        service.state = "registering";
                        verify.start();
                    } else
                        service.state = "unavailable";
                } catch (_) {
                    service.state = "unavailable";
                }
                service.finished = true;
            }
        }
        stderr: SplitParser {
            onRead: _data => {}
        }
        onRunningChanged: {
            if (!running) {
                deadline.stop();
                if (!service.finished)
                    service.state = "unavailable";
            }
        }
    }
    Timer {
        id: verify
        interval: 150
        onTriggered: {
            service.verifying = true;
            service.finished = false;
            check.running = true;
            deadline.start();
        }
    }
    Timer {
        id: deadline
        interval: 3000
        onTriggered: {
            service.state = "unavailable";
            check.signal(9);
        }
    }
    // The name was taken (or the bus did not answer) at start: look again every few seconds
    // and register once it is free — another daemon dying must not leave the shell deaf.
    // Never replaces a living owner (busctl NameHasOwner, no REPLACE_EXISTING).
    Timer {
        id: recheck
        interval: 5000
        repeat: true
        running: service.requested && (service.state === "owned_elsewhere" || service.state === "unavailable") && !check.running
        onTriggered: {
            service.verifying = false;
            service.finished = false;
            check.running = true;
            deadline.start();
        }
    }
    Component.onCompleted: {
        if (requested) {
            check.running = true;
            deadline.start();
        }
    }
}
