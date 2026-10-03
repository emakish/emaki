pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import LockNotifyFixture 1.0

Scope {
    id: root
    property int stage: 0
    property int lockedNotifies: 0
    property int secureNotifies: 0
    property int releases: 0
    property real began: Date.now()
    property real confirmedAt: 0
    // This binding deliberately demonstrates the broken acquisition notification.
    readonly property bool cachedLocked: lock.locked
    function check(ok: bool, label: string): void {
        if (!ok)
            throw new Error(label);
        console.log("LOCK_NOTIFY_PASS " + label);
    }
    SessionLock {
        id: lock
        onLockStateChanged: ++root.lockedNotifies
        onSecureStateChanged: ++root.secureNotifies
    }
    LockAuth {
        id: authController
    }
    LockSession {
        id: session
        auth: authController
        // The harness replaces this expression with the binding from lock.qml.
        started: false // PRODUCTION_STARTED_BINDING
        secure: lock.secure
        onReleaseLock: {
            ++root.releases;
            session.releasing = true;
            lock.locked = false;
        }
    }
    Component.onCompleted: {
        session.registerOutput("fixture");
        session.visualReady("fixture", true);
        lock.locked = true;
        authController.edit("fixture");
        authController.submit();
    }
    Timer {
        interval: 10
        running: true
        repeat: true
        onTriggered: {
            try {
                if (Date.now() - root.began > 5000)
                    throw new Error("notification regression deadline");
                if (root.stage === 0 && Date.now() - root.began >= 150) {
                    root.check(lock.locked && !root.cachedLocked && root.lockedNotifies === 0, "acquisition changes native getter without refreshing the locked binding");
                    root.check(!session.started && session.clock === 0 && authController.queued && !authController.checking, "request alone never advances the pour or starts queued PAM");
                    lock.confirm();
                    root.confirmedAt = Date.now();
                    root.stage = 1;
                } else if (root.stage === 1 && Date.now() - root.confirmedAt >= 250) {
                    root.check(root.secureNotifies === 1 && root.lockedNotifies === 0 && !root.cachedLocked, "secure notifies independently while locked binding stays stale");
                    root.check(session.started && session.clock > 0, "secure notification must start the real session timer");
                    root.stage = 2;
                } else if (root.stage === 2 && session.poured) {
                    root.check(session.phase === "locked" && authController.checking && !authController.queued && root.releases === 0, "real timer completes pour and dispatches early queued Enter without releasing lock");
                    authController._attempt.finished(authController.attemptId, "success");
                    root.stage = 3;
                } else if (root.stage === 3 && root.releases === 1) {
                    root.check(session.phase === "finished" && !lock.locked && !lock.secure && !session.started, "current PAM success alone completes drain and clears secure start state");
                    root.check(root.lockedNotifies === 1 && root.secureNotifies === 2, "unlock emits the first locked notify as Quickshell does");
                    console.log("LOCK_NOTIFY_COMPLETE");
                    Qt.quit();
                }
            } catch (error) {
                console.error("LOCK_NOTIFY_FAILED " + error);
                Qt.exit(1);
            }
        }
    }
}
