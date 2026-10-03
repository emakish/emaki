pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell
import Quickshell.Wayland
import Quickshell.Io

// Separate from shell.qml: a shell crash cannot destroy the lock's protocol object.
Scope {
    id: root
    property int sleepGeneration: 0
    property bool exiting: false
    LockCapture {
        id: capture
        onReady: {
            if (!root.exiting) {
                lock.locked = true;
            }
        }
    }
    LockAuth {
        id: pamController
    }
    LockEnvironment {
        id: lockEnvironment
    }
    LockSession {
        id: lockSession
        auth: pamController
        // QS 0.3.1 does not notify locked on acquisition; secure does notify.
        // Start only after compositor confirmation, never from the request getter.
        started: lock.secure
        secure: lock.secure
        active: lockEnvironment.outputsActive
        reducedMotion: Quickshell.env("EMAKI_LOCK_REDUCED_MOTION") === "1"
        onPrivacyChanged: root.sendState()
        onLockLost: {
            capture.clear();
            // A lost compositor lock is terminal. Never park a disabled input
            // surface behind a healthy heartbeat; supervision owns recovery.
            if (!root.exiting)
                Qt.exit(1);
        }
        onReleaseLock: {
            // This signal is reachable only after the current PAM success and completed drain.
            if (!lockSession.authenticated || !lock.secure)
                return;
            root.exiting = true;
            lockSession.releasing = true;
            capture.clear();
            supervisor.write(JSON.stringify({
                version: 1,
                event: "authenticated-exit"
            }) + "\n");
            supervisor.flush();
            lock.locked = false;
            exitDelay.start();
        }
    }
    WlSessionLock {
        id: lock
        locked: false
        surface: WlSessionLockSurface {
            id: surface
            color: Qt.rgba(LiquidPalette.flatPlate.r, LiquidPalette.flatPlate.g, LiquidPalette.flatPlate.b, 1)
            LockSurface {
                anchors.fill: parent
                session: lockSession
                auth: pamController
                environment: lockEnvironment
                screen: surface.screen
                captureUrl: capture.urlFor(surface.screen)
            }
        }
        onSecureStateChanged: root.sendState()
    }
    function sendState(): void {
        if (supervisor && supervisor.connected) {
            supervisor.write(JSON.stringify({
                version: 1,
                event: "state",
                secure: lock.secure,
                poured: lockSession.poured,
                phase: lockSession.phase,
                sleepGeneration: root.sleepGeneration,
                surfaceCount: Object.keys(lockSession.surfaces).length,
                outputsActive: lockEnvironment.outputsActive
            }) + "\n");
            supervisor.flush();
        }
    }
    Socket {
        id: supervisor
        path: Quickshell.env("EMAKI_LOCK_SOCKET")
        connected: path !== ""
        onConnectionStateChanged: root.sendState()
        parser: SplitParser {
            onRead: line => {
                try {
                    const message = JSON.parse(line);
                    if (message.version === 1 && message.event === "prepare-sleep" && !root.exiting && lockSession.phase !== "finished" && Number.isSafeInteger(message.generation) && message.generation > root.sleepGeneration) {
                        lockSession.prepareSleep();
                        capture.clear();
                        root.sleepGeneration = message.generation;
                        root.sendState();
                    }
                } catch (_) {}
            }
        }
    }
    Timer {
        interval: 500
        running: true
        repeat: true
        onTriggered: root.sendState()
    }
    Timer {
        id: exitDelay
        interval: 100
        onTriggered: Qt.quit()
    }
    Connections {
        target: Quickshell
        function onScreensChanged(): void {
            capture.prune(Quickshell.screens);
        }
    }
    Component.onCompleted: {
        Quickshell.watchFiles = false;
        capture.begin(Quickshell.screens);
    }
}
