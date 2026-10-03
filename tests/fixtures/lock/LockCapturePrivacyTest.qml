pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

ShellRoot {
    id: root
    property int stage: 0
    property real stageStarted: Date.now()
    property var captureScreen: ({
            name: "A",
            delay: 30,
            color: "#ff0000",
            witness: {
                created: 0,
                stopped: 0,
                destroyed: 0
            }
        })
    property var glass: null
    function check(ok: bool, name: string): void {
        if (!ok)
            throw new Error(name);
        console.log("LOCK_CAPTURE_PASS " + name);
    }
    function waitFor(ok: bool, name: string): bool {
        if (!ok && Date.now() - stageStarted > 5000)
            throw new Error("Deadline waiting for " + name + " at stage " + stage);
        return ok;
    }
    function nextStage(): void {
        stage++;
        stageStarted = Date.now();
    }
    function hidden(): bool {
        return !surface.revealCapture && surface.visualItem.captureUrl === "";
    }
    function attempt(): int {
        authentication.edit("synthetic test response");
        authentication.submit();
        return authentication.attemptId;
    }
    LockCapture {
        id: capture
    }
    LockAuth {
        id: authentication
    }
    LockEnvironment {
        id: environment
        outputsActive: false
    }
    LockSession {
        id: session
        auth: authentication
        active: true
    }
    FloatingWindow {
        visible: true
        implicitWidth: 640
        implicitHeight: 480
        color: "black"
        LockSurface {
            id: surface
            anchors.fill: parent
            session: session
            auth: authentication
            environment: environment
            captureUrl: capture.urlFor(root.captureScreen)
        }
    }
    Component.onCompleted: {
        // Drive the real state machine deterministically without a production
        // fixture flag. Authentication timers and fake PAM still use real Qt.
        for (const resource of session.children) {
            if (resource.interval !== undefined && resource.running !== undefined)
                resource.running = false;
        }
        capture.begin([captureScreen]);
    }
    function step(): void {
        switch (stage) {
        case 0:
            if (!waitFor(capture.finished && surface.visualItem !== null, "capture and production visual loading"))
                return;
            check(surface.captureUrl !== "" && surface.revealCapture && surface.visualItem.captureUrl === surface.captureUrl, "initial pour displays the retained memory capture under the moving edge");
            session.advance(.9);
            session.secure = true;
            check(session.phase === "locked" && hidden(), "locked wallpaper never receives the desktop capture URL");
            const wrong = attempt();
            check(authentication.checking && hidden(), "PAM checking cannot reveal a retained desktop");
            authentication.complete(wrong, "rejected");
            check(authentication.messageKind === "wrong" && hidden(), "wrong password leaves the capture hidden");
            attempt();
            authentication.cancel();
            check(!authentication.checking && hidden(), "cancel leaves the capture hidden");
            const current = attempt();
            authentication.complete(current - 1, "success");
            check(authentication.checking && !session.authenticated && hidden(), "stale PAM success cannot reveal captured pixels");
            authentication.complete(current, "success");
            check(session.authenticated && session.phase === "melt" && hidden(), "current success still keeps the snapshot hidden during melt");
            session.advance(.22);
            check(session.phase === "drain" && surface.revealCapture && surface.visualItem.captureUrl === surface.captureUrl, "current authenticated drain alone hands the memory URL to the visual");
            for (const child of surface.visualItem.children) {
                if (child.captureReady !== undefined && child.plateGlass !== undefined)
                    glass = child;
            }
            check(glass !== null, "privacy test exercises the production LockGlass tree");
            nextStage();
            break;
        case 1:
            if (!waitFor(glass.captureReady, "same-window captured-frame Image loading"))
                return;
            check(glass.captureUrl === surface.captureUrl, "production glass loads the retained cross-window memory image");
            capture.clear();
            session.prepareSleep();
            check(Object.keys(capture.retained).length === 0 && surface.captureUrl === "" && glass.captureUrl === "" && !glass.captureReady && hidden() && session.phase === "locked", "prepare-sleep drops retained images and revokes an active reveal");
            const resumed = attempt();
            authentication.complete(resumed, "success");
            session.advance(.22);
            check(session.authenticated && session.phase === "drain" && surface.visualItem.captureUrl === "" && !glass.captureReady, "successful unlock after sleep drains to wallpaper fallback");
            session.advance(1);
            check(session.phase === "finished" && !surface.revealCapture && surface.visualItem.captureUrl === "", "finished unlock removes the visual capture binding");
            console.log("LOCK_CAPTURE_COMPLETE");
            Qt.quit();
            break;
        }
    }
    Timer {
        interval: 10
        repeat: true
        running: true
        onTriggered: {
            try {
                root.step();
            } catch (error) {
                console.error("LOCK_CAPTURE_FAILED " + error);
                Qt.exit(1);
            }
        }
    }
}
