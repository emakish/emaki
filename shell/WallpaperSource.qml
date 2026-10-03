pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// Pixels or a hashed private wallpaper-cache URL cross this pipe, never source filenames.
Scope {
    id: wallpaper
    property int outputWidth: 0
    property int outputHeight: 0
    property real outputScale: 1
    property string outputName: ""
    // "glass": pre-blurred for the island lens; "sharp": untouched, device pixels (dock glass).
    property string variant: "glass"
    // Empty preserves the user's existing wallpaper contract. The greeter only
    // reads a published copy, under a root derived from a validated system user.
    property string publishedRoot: ""
    property bool isolateHelper: false
    property bool enabled: true
    property string state: "idle"
    property string texture: ""
    property int epoch: 0
    property int readEpoch: 0
    readonly property bool ready: state === "ready" && texture !== ""
    onOutputWidthChanged: reload()
    onOutputHeightChanged: reload()
    onOutputScaleChanged: reload()
    onOutputNameChanged: reload()
    onVariantChanged: reload()
    onPublishedRootChanged: reload()
    onIsolateHelperChanged: reload()
    onEnabledChanged: reload()
    function helperCommand(): var {
        const command = [Quickshell.env("EMAKI_PYTHON") || "python3"];
        if (isolateHelper || publishedRoot !== "")
            command.push("-I");
        command.push("-B", Quickshell.shellPath("helpers/wallpaper.py"), String(outputWidth), String(outputHeight), String(outputScale), outputName, variant);
        return command;
    }
    function helperEnvironment(): var {
        if (publishedRoot === "")
            return {};
        return {
            "EMAKI_GREETER_WALLPAPER_ROOT": publishedRoot,
            "XDG_CONFIG_HOME": publishedRoot
        };
    }
    function reload(): void {
        ++epoch;
        texture = "";
        if (worker.running)
            worker.signal(9);
        if (!enabled) {
            state = "disabled";
            schedule.stop();
            deadline.stop();
            return;
        }
        state = "loading";
        schedule.restart();
    }
    Timer {
        id: schedule
        interval: wallpaper.variant === "sharp" ? 0 : 100
        onTriggered: {
            if (worker.running) {
                restart();
            } else if (wallpaper.enabled && wallpaper.outputWidth > 0 && wallpaper.outputHeight > 0) {
                wallpaper.readEpoch = wallpaper.epoch;
                worker.running = true;
                deadline.restart();
            }
        }
    }
    Timer {
        id: deadline
        interval: 8000
        onTriggered: {
            wallpaper.state = "timeout";
            worker.signal(9);
        }
    }
    Process {
        id: worker
        command: wallpaper.helperCommand()
        environment: wallpaper.helperEnvironment()
        stdout: StdioCollector {
            onStreamFinished: {
                if (wallpaper.state === "timeout" || wallpaper.readEpoch !== wallpaper.epoch)
                    return;
                try {
                    const result = JSON.parse(text);
                    wallpaper.state = result.state;
                    wallpaper.texture = result.state === "ready" ? result.texture : "";
                } catch (_) {
                    wallpaper.state = "invalid_response";
                }
            }
        }
        stderr: SplitParser { // Do not forward helper diagnostics or paths.
            onRead: _data => {}
        }
        onRunningChanged: {
            if (!running) {
                deadline.stop();
                if (wallpaper.readEpoch === wallpaper.epoch && wallpaper.state === "loading")
                    wallpaper.state = "unavailable";
            }
        }
    }
    Component.onCompleted: reload()
}
