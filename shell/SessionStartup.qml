pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// Only the wrapper's fresh-login token enables this handshake. The cover owns
// the visible transition; a failed helper cannot keep shell animation disabled.
Scope {
    id: startup
    property string token: Quickshell.env("EMAKI_SESSION_START")
    readonly property bool enabled: /^[0-9a-f]{32}$/.test(token)
    readonly property bool skipIntro: enabled && Quickshell.env("EMAKI_SESSION_SKIP_INTRO") === "1" && !_finished
    readonly property bool coverActive: enabled && !_finished
    property bool modelsReady: false
    property string modelRevision: ""
    property bool barMapped: false
    property bool dockMapped: false
    property bool overlayMapped: false
    property bool dockRequired: true
    property bool _finished: false
    property bool _settled: false
    property bool _reportStarted: false
    property bool _reported: false
    property var _frames: ({
            bar: 0,
            dock: 0,
            overlay: 0
        })
    readonly property var frameCounts: _frames
    readonly property bool settled: _settled
    readonly property bool ready: enabled && modelsReady && _settled && barMapped && overlayMapped && (!dockRequired || dockMapped) && _frames.bar >= 2 && _frames.overlay >= 2 && (!dockRequired || _frames.dock >= 2)
    readonly property bool reported: _reported
    signal requestFrames

    function invalidate(): void {
        if (!enabled || _reportStarted || _finished)
            return;
        _settled = false;
        _frames = {
            bar: 0,
            dock: 0,
            overlay: 0
        };
        quiet.stop();
        if (modelsReady && barMapped && overlayMapped && (!dockRequired || dockMapped))
            quiet.restart();
    }
    function painted(name: string): void {
        if (!enabled || !_settled || _reportStarted || _finished || !(name in _frames))
            return;
        const mapped = name === "bar" ? barMapped : name === "dock" ? dockMapped : overlayMapped;
        if (!mapped || _frames[name] >= 2)
            return;
        const next = Object.assign({}, _frames);
        ++next[name];
        _frames = next;
        if (!ready)
            requestFrames();
    }
    function finish(): void {
        _finished = true;
        quiet.stop();
    }
    onModelsReadyChanged: invalidate()
    onModelRevisionChanged: invalidate()
    onBarMappedChanged: invalidate()
    onDockMappedChanged: invalidate()
    onOverlayMappedChanged: invalidate()
    onDockRequiredChanged: invalidate()
    onReadyChanged: {
        if (ready && !_reportStarted && !_finished) {
            _reportStarted = true;
            reporter.command = ["/usr/bin/python3", "-I", "-B", Quickshell.shellPath("helpers/session-start.py"), "shell-ready", token, dockRequired ? "dock" : "no-dock"];
            reporter.running = true;
        }
    }
    Timer {
        id: quiet
        interval: 120
        onTriggered: {
            startup._settled = true;
            startup.requestFrames();
        }
    }
    Process {
        id: reporter
        stdout: SplitParser {
            onRead: _line => {}
        }
        stderr: SplitParser {
            onRead: _line => {}
        }
        onExited: (code, status) => startup._reported = code === 0 && status === 0
    }
    Component.onCompleted: invalidate()
}
