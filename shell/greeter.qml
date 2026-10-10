pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

// greetd owns authentication and session lifetime; these surfaces only present it.
Scope {
    id: root
    property bool launching: false
    property bool selectionLoaded: false
    property string picturePath: ""
    property bool captured: false
    property bool launchRecorded: false
    signal captureRequested
    function recordLaunch(): void {
        if (launchRecorded)
            return;
        launchRecorded = true;
        captureLimit.stop();
        if (controller.handoffMarker)
            console.log("EMAKI_SESSION_COVER " + JSON.stringify({
                phase: "greeter-frozen",
                handoffAtMs: JSON.parse(controller.handoffMarker).createdMs,
                atMs: Date.now()
            }));
        memory.recordLaunch();
    }
    function createHandoffMarker(): string {
        const createdMs = Date.now();
        // This nonce only identifies decorative startup work, never a login.
        let token = createdMs.toString(16).padStart(12, "0").slice(-12);
        while (token.length < 32)
            token += Math.floor(Math.random() * 0x100000000).toString(16).padStart(8, "0");
        return JSON.stringify({
            v: 1,
            token: token.slice(0, 32),
            createdMs: createdMs,
            idleMode: lockSession.idleMode,
            clock: lockSession.clock,
            introStart: lockSession.handoffIntroStart
        });
    }
    GreeterState {
        id: memory
        onLoaded: {
            root.selectionLoaded = true;
            controller.message = memory.message;
            controller.messageKind = memory.message ? "username" : "";
        }
        onLaunchRecorded: {
            if (root.launching && lockSession.authenticated && lockSession.phase === "handoff")
                controller.launch();
            else
                Qt.exit(1);
        }
        onFailedOperation: Qt.exit(1)
    }
    GreeterAuth {
        id: controller
        user: memory.user
        sessionCommand: memory.command
        selectionReady: memory.ready
        onLaunched: quitCompositor.running = true
        onSelectUser: name => memory.selectUser(name)
        onFatalError: {
            // Keep the existing error line readable on the retained plate.
            if (lockSession.phase === "locked")
                fallbackNotice.start();
            else
                Qt.exit(1);
        }
    }
    Timer {
        id: fallbackNotice
        interval: 1200
        onTriggered: Qt.exit(1)
    }
    LockEnvironment {
        id: lockEnvironment
        isolateHelper: true
    }
    GreeterSession {
        id: lockSession
        auth: controller
        started: root.selectionLoaded
        secure: true
        active: lockEnvironment.outputsActive
        reducedMotion: Quickshell.env("EMAKI_LOCK_REDUCED_MOTION") === "1"
        onHandoffRequested: {
            if (!lockSession.authenticated || lockSession.phase !== "handoff" || root.launching)
                return;
            root.launching = true;
            controller.handoffMarker = memory.sessionId === "niri-emaki.desktop" ? root.createHandoffMarker() : "";
            if (controller.handoffMarker) {
                captureLimit.start();
                preparePicture.command = [Platform.python, "-I", "-B", Quickshell.shellPath("helpers/greeter-handoff.py"), "prepare", JSON.parse(controller.handoffMarker).token];
                preparePicture.running = true;
            } else {
                root.recordLaunch();
            }
        }
    }
    Process {
        id: preparePicture
        stdout: SplitParser {
            onRead: line => {
                try {
                    root.picturePath = JSON.parse(line).path;
                } catch (_) {}
            }
        }
        onExited: (code, status) => {
            if (code === 0 && status === 0 && root.picturePath && !root.launchRecorded)
                root.captureRequested();
            else if (!root.launchRecorded) {
                controller.handoffMarker = "";
                root.recordLaunch();
            }
        }
    }
    Process {
        id: publishPicture
        stdout: SplitParser {
            onRead: _line => {}
        }
        onExited: (code, status) => {
            if (code !== 0 || status !== 0)
                controller.handoffMarker = "";
            root.recordLaunch();
        }
    }
    Timer {
        id: captureLimit
        interval: 2000
        onTriggered: {
            controller.handoffMarker = "";
            root.recordLaunch(); // Failed decoration never blocks an accepted login.
        }
    }
    Process {
        id: quitCompositor
        command: ["emaki-greeter-run", "--success"]
        // Keep QS and its surface alive until the compositor itself exits.
        // IPC EOF during compositor teardown is expected; do not unmap the plate.
    }
    Variants {
        model: Quickshell.screens
        PanelWindow {
            id: panel
            required property ShellScreen modelData
            screen: modelData
            property int handoffFrames: -1
            Connections {
                target: root
                function onCaptureRequested(): void {
                    if (panel.screen === Quickshell.screens[0]) {
                        panel.handoffFrames = 0;
                        panel.contentItem.Window.window?.update();
                    }
                }
            }
            Connections {
                target: panel.contentItem.Window.window
                function onFrameSwapped(): void {
                    if (panel.handoffFrames < 0 || root.captured || root.launchRecorded)
                        return;
                    ++panel.handoffFrames;
                    if (panel.handoffFrames < 2) {
                        panel.contentItem.Window.window?.update();
                        return;
                    }
                    root.captured = true;
                    // Qt multiplies grab targetSize by the window DPR itself.
                    // Pass logical output dimensions to avoid scaling twice.
                    greeterScene.grabToImage(result => {
                        if (!root.launchRecorded && result.saveToFile(root.picturePath) && lockSurface.glassItem) {
                            lockSurface.glassItem.grabToImage(plate => {
                                if (!root.launchRecorded && plate.saveToFile(root.picturePath.replace(".png", "-plate.png"))) {
                                    publishPicture.command = [Platform.python, "-I", "-B", Quickshell.shellPath("helpers/greeter-handoff.py"), "publish", JSON.parse(controller.handoffMarker).token];
                                    publishPicture.running = true;
                                }
                            }, Qt.size(panel.width, panel.height));
                        }
                    }, Qt.size(panel.width, panel.height));
                }
            }
            anchors {
                top: true
                bottom: true
                left: true
                right: true
            }
            color: "black"
            exclusionMode: ExclusionMode.Ignore
            WlrLayershell.namespace: "emaki-greeter"
            WlrLayershell.layer: WlrLayer.Overlay
            WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
            // ProxyWindowContentItem has no QML engine. Capture this QML scene,
            // including the black backing, at the output's physical resolution.
            Item {
                id: greeterScene
                anchors.fill: parent
                LockSurface {
                    id: lockSurface
                    anchors.fill: parent
                    session: lockSession
                    auth: controller
                    environment: lockEnvironment
                    screen: panel.screen
                    wallpaperRoot: memory.wallpaperRoot
                    greeter: true
                    wallpaperSelectionReady: root.selectionLoaded
                }
            }
        }
    }
    Component.onCompleted: Quickshell.watchFiles = false
}
