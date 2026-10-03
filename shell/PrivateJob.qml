pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

Scope {
    id: job
    property int timeoutMs: 5000
    property string helper: Quickshell.shellPath("helpers/launcher-tools.py")
    property var request: ({})
    property string state: "idle"
    property bool launchRequest: false
    readonly property bool busy: process.running
    signal completed(var response)
    function start(value: var): bool {
        if (busy)
            return false;
        request = value;
        launchRequest = ["terminal", "web", "portal-open", "clip-copy"].includes(value.op);
        state = "pending";
        process.running = true;
        deadline.restart();
        return true;
    }
    Process {
        id: process
        command: [Quickshell.env("EMAKI_PYTHON") || "python3", "-B", job.helper]
        stdinEnabled: true
        onStarted: {
            write(JSON.stringify(job.request) + "\n");
            job.request = ({});
        }
        stdout: StdioCollector {
            onStreamFinished: {
                if (job.state !== "pending")
                    return;
                try {
                    const value = JSON.parse(text);
                    if (value.schema_version !== 1 || !value.state)
                        throw new Error("protocol");
                    job.state = value.state;
                    job.completed(value);
                } catch (_) {
                    job.state = "invalid_response";
                }
            }
        }
        stderr: SplitParser {
            onRead: line => AppLaunch.diagnostic(line)
        }
        onRunningChanged: {
            if (!running) {
                deadline.stop();
                job.request = ({});
                if (job.state === "pending")
                    job.state = "helper_failed";
            }
        }
    }
    Timer {
        id: deadline
        interval: job.launchRequest ? Math.max(job.timeoutMs, AppLaunch.timeoutMs) : job.timeoutMs
        onTriggered: {
            job.state = job.launchRequest ? "requested" : "timeout";
            process.signal(9);
            if (job.launchRequest)
                job.completed({
                    schema_version: 1,
                    state: job.state
                });
        }
    }
}
