pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Wayland

// A short-lived pre-lock capture window. The owner starts the lock after all
// helpers finish or its 300 ms deadline expires, and retains each ItemGrabResult.
// The image provider URL and pixels remain in memory; this helper never saves or
// logs them. Keep the tiny panel and capture item visible so the compositor
// establishes the screencopy context (the live-proven Quickshell 0.3.1 route).
PanelWindow {
    id: panel
    property string outputName: ""
    property int generation: 0
    property bool _ready: false
    property bool _stopped: false
    property bool _grabbing: false
    property bool _completed: false
    signal completed(string outputName, int generation, var result)

    function stop(): void {
        _stopped = true;
        capture.captureSource = null;
        visible = false;
    }
    function finish(result): void {
        if (_stopped || _completed)
            return;
        _completed = true;
        completed(outputName, generation, result);
    }
    function grab(): void {
        if (_stopped || _grabbing || _completed || !capture.hasContent)
            return;
        _grabbing = true;
        // Wait until the first automatically delivered screencopy frame has been
        // applied to the item. captureFrame() before this context exists fails.
        Qt.callLater(() => {
            if (panel._stopped)
                return;
            if (!panel.screen || panel.screen.name !== panel.outputName) {
                panel.finish(null);
                return;
            }
            const dpr = panel.screen.devicePixelRatio;
            const size = Qt.size(Math.max(1, Math.floor(panel.screen.width * dpr)), Math.max(1, Math.floor(panel.screen.height * dpr)));
            const accepted = capture.grabToImage(result => {
                if (panel._stopped)
                    return;
                if (!panel.screen || panel.screen.name !== panel.outputName) {
                    panel.finish(null);
                    return;
                }
                panel.finish(result);
            }, size);
            if (!accepted)
                panel.finish(null);
        });
    }

    implicitWidth: 8
    implicitHeight: 8
    visible: true
    color: "transparent"
    exclusiveZone: 0
    WlrLayershell.namespace: "emaki-lock-capture"
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    anchors {
        top: true
        left: true
    }
    mask: Region {}

    ScreencopyView {
        id: capture
        visible: true
        width: panel.screen ? panel.screen.width : 1
        height: panel.screen ? panel.screen.height : 1
        captureSource: panel._ready && !panel._stopped && panel.screen && panel.screen.name === panel.outputName ? panel.screen : null
        live: false
        paintCursor: false
        onHasContentChanged: panel.grab()
    }

    Component.onCompleted: _ready = true
    Component.onDestruction: stop()
}
