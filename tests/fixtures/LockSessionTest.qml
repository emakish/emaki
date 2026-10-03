pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import QtTest
import Quickshell

ShellRoot {
    id: root
    property int releases: 0
    property int lostLocks: 0
    function check(ok: bool, name: string): void {
        if (!ok)
            throw new Error(name);
        console.log("LOCK_SESSION_PASS " + name);
    }
    LockAuth {
        id: authController
    }
    LockEnvironment {
        id: metadata
    }
    LockAuth {
        id: preflightAuth
    }
    LockSession {
        id: preflightClock
        auth: preflightAuth
        started: false
        active: true
    }
    LockSession {
        id: sessionController
        auth: authController
        active: false
        onReleaseLock: ++root.releases
        onLockLost: ++root.lostLocks
    }
    Window {
        id: window
        visible: true
        width: 1000
        height: 700
        LockSurface {
            id: lockSurface
            anchors.fill: parent
            session: sessionController
            auth: authController
            environment: metadata
        }
        TestCase {
            id: keyboard
            when: false
        }
    }
    Timer {
        interval: 150
        running: true
        onTriggered: {
            root.check(preflightClock.clock === 0 && preflightClock.elapsed === 0, "capture preflight does not consume the pour animation clock");
            root.check(!lockSurface.glassReady, "flat input independent of broken visual components");
            keyboard.keyClick(Qt.Key_A);
            root.check(authController.buffer.length === 1, "real Qt input once delivered to the lock surface");
            keyboard.keyClick(Qt.Key_Backspace);
            root.check(authController.buffer.length === 0, "real Qt Backspace clears early input");
            keyboard.keyClick(Qt.Key_A);
            keyboard.keyClick(Qt.Key_Return);
            root.check(authController.queued && !authController.checking, "real Enter queued during pour");
            const initial = authController.attemptId;
            sessionController.acceptAuthentication(initial - 1);
            root.check(sessionController.phase === "pour" && root.releases === 0, "stale success cannot change lock lifetime");
            sessionController.advance(.9);
            root.check(!authController.checking && !sessionController.poured, "elapsed animation cannot impersonate compositor confirmation");
            sessionController.started = false;
            sessionController.secure = true;
            root.check(authController.queued && !authController.checking, "capture preflight cannot enable queued authentication");
            sessionController.started = true;
            root.check(authController.checking, "queued Enter starts only after secure and full pour");
            const current = authController.attemptId;
            sessionController.registerOutput("B");
            sessionController.painted("offscreen");
            sessionController.painted("B");
            root.check(!sessionController.poured, "a queued pre-transition frame cannot acknowledge privacy");
            sessionController.painted("offscreen");
            root.check(!sessionController.poured, "all outputs must commit a private frame");
            sessionController.painted("B");
            root.check(sessionController.poured, "all private frames establish readiness");
            sessionController.removeOutput("offscreen");
            root.check(authController.checking && authController.attemptId === current, "output removal preserves active PAM conversation");
            sessionController.registerOutput("C");
            root.check(!sessionController.poured, "hotplug revokes readiness until new safe frame");
            sessionController.removeOutput("C");
            authController._attempt.finished(current, "rejected");
            root.check(root.releases === 0 && sessionController.phase === "locked", "PAM rejection never releases lock");
            keyboard.keyClick(Qt.Key_Escape);
            root.check(authController.buffer.length === 0 && authController.message === "", "Escape cancels without releasing lock");
            authController.edit("fixture");
            authController.submit();
            const success = authController.attemptId;
            sessionController.active = true;
            authController._attempt.finished(success, "success");
            sessionController.active = false;
            root.check(sessionController.phase === "melt" && root.releases === 0, "current success starts melt while compositor stays locked");
            sessionController.advance(.22);
            root.check(sessionController.phase === "drain" && root.releases === 0, "drain must finish before unlock");
            sessionController.prepareSleep();
            root.check(sessionController.introComplete, "sleep during drain does not replay ribbon intro on resume");
            sessionController.advance(2);
            root.check(sessionController.phase === "locked" && root.releases === 0 && !sessionController.authenticated, "sleep revokes in-flight drain and authentication");
            authController.edit("fixture");
            authController.submit();
            const again = authController.attemptId;
            authController._attempt.finished(again, "success");
            root.check(root.releases === 1 && sessionController.phase === "finished", "powered-off output still needs fresh PAM success");
            authController.reset();
            sessionController.acceptedAttempt = -1;
            sessionController.reducedMotion = true;
            sessionController.go("pour");
            sessionController.advance(0);
            root.check(sessionController.phase === "locked", "reduced motion keeps readiness and input contract");
            authController.edit("fixture");
            authController.submit();
            authController._attempt.finished(authController.attemptId, "success");
            root.check(root.releases === 2, "reduced motion unlock remains PAM gated");
            sessionController.go("drain");
            sessionController.secure = false;
            root.check(root.lostLocks === 1 && sessionController.phase === "lost" && !sessionController.accepting, "lost secure state is terminal rather than a disabled input with healthy heartbeat");
            preflightClock.active = false;
            preflightClock.registerOutput("timing");
            preflightClock.started = true;
            preflightClock.secure = true;
            const epoch = preflightClock.sampledAt;
            preflightClock.sample(epoch + 20);
            root.check(preflightClock.clock === 0, "pour waits for visual readiness after secure acquisition");
            preflightClock.visualReady("timing", true);
            preflightClock.sample(epoch + 55);
            root.check(Math.abs(preflightClock.clock - .035) < .0001, "timeline uses measured delta rather than timer interval");
            preflightClock.sample(epoch + 255);
            root.check(Math.abs(preflightClock.clock - .085) < .0001, "GUI stall delta is capped at 50 milliseconds");
            preflightClock.go("pour");
            preflightClock.visualReady("timing", false);
            preflightClock.preparationStarted = epoch + 255;
            const beforeFallback = preflightClock.clock;
            preflightClock.sample(epoch + 270);
            root.check(preflightClock.clock === beforeFallback, "unready visuals still wait within the bounded warmup");
            preflightClock.sample(epoch + 2280);
            root.check(preflightClock.clock > beforeFallback, "broken visuals use flat pour after the two-second deadline");
            preflightClock.go("finished");
            preflightClock.releasing = true;
            preflightClock.secure = false;
            root.check(preflightClock.phase === "finished", "deliberate release retains finished state so a concurrent lock request is queued");
            console.log("LOCK_SESSION_COMPLETE");
            Qt.quit();
        }
    }
}
