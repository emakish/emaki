pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

Scope {
    id: root
    property int stage: 0
    property real startedAt: Date.now()
    property real resetAt: 0
    LockAuth {
        id: offAuth
    }
    LockAuth {
        id: unknownAuth
    }
    LockAuth {
        id: absentAuth
    }
    LockSession {
        id: absentSession
        auth: absentAuth
        active: false
        reducedMotion: true
    }
    LockSession {
        id: offSession
        auth: offAuth
        active: false
    }
    LockSession {
        id: unknownSession
        auth: unknownAuth
        // A missing niri socket yields this advisory value, even with DPMS off.
        active: true
    }
    function check(ok: bool, label: string): void {
        if (!ok)
            throw new Error(label);
        console.log("LOCK_PRESENTATION_PASS " + label);
    }
    Component.onCompleted: {
        absentSession.secure = true;
        for (const session of [offSession, unknownSession]) {
            session.registerOutput("no-frame-callbacks");
            session.visualReady("no-frame-callbacks", true);
            session.secure = true;
        }
    }
    Timer {
        interval: 10
        running: true
        repeat: true
        onTriggered: {
            try {
                if (Date.now() - root.startedAt > 5000)
                    throw new Error("presentation deadline");
                if (root.stage === 0 && offSession.poured && unknownSession.poured) {
                    root.check(offSession.clock >= .9 && unknownSession.clock >= .9, "both inactive and unknown output states complete the full logical pour");
                    root.check(offSession.surfaces["no-frame-callbacks"] === 0 && unknownSession.surfaces["no-frame-callbacks"] === 0, "secure confirmation does not require DPMS frame callbacks");
                    root.check(offAuth.fieldReady && unknownAuth.fieldReady, "missing presentation frames cannot strand queued authentication");
                    root.check(absentSession.phase === "locked" && !absentSession.poured && !absentSession.presentationSettled, "secure and elapsed grace cannot confirm a lock with no registered surface");
                    absentSession.registerOutput("late-output");
                    root.check(!absentSession.poured, "a late first surface requires a fresh presentation barrier");
                    offSession.prepareSleep();
                    unknownSession.prepareSleep();
                    root.resetAt = Date.now();
                    root.check(!offSession.poured && !unknownSession.poured, "prepare-sleep revokes old presentation readiness");
                    root.stage = 1;
                } else if (root.stage === 1 && offSession.poured && unknownSession.poured) {
                    root.check(Date.now() - root.resetAt >= 240, "sleep waits for the fresh event-loop settling grace without waking monitors");
                    root.check(absentSession.poured, "a late first surface can confirm after its own settling grace");
                    absentSession.removeOutput("late-output");
                    root.check(!absentSession.poured, "removing the last surface revokes poured immediately");
                    offSession.secure = false;
                    unknownSession.secure = false;
                    root.stage = 2;
                    root.resetAt = Date.now();
                } else if (root.stage === 2 && Date.now() - root.resetAt >= 300) {
                    root.check(!offSession.poured && !unknownSession.poured, "neither elapsed time nor absent frames can replace compositor secure");
                    root.check(absentSession.secure && !absentSession.poured, "zero surfaces remain unconfirmed after the grace period despite secure");
                    console.log("LOCK_PRESENTATION_COMPLETE");
                    Qt.quit();
                }
            } catch (error) {
                console.error("LOCK_PRESENTATION_FAILED " + error);
                Qt.exit(1);
            }
        }
    }
}
