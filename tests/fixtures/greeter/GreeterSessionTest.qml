pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// This fixture tests only the session state machine. Real greetd authentication
// remains covered separately by the compiled-module socket scenarios.
ShellRoot {
    id: root
    property int handoffs: 0
    property int releases: 0
    AuthController {
        id: auth
    }
    GreeterSession {
        id: session
        auth: auth
        started: true
        secure: true
        onHandoffRequested: ++root.handoffs
        onReleaseLock: ++root.releases
    }
    function check(ok: bool, detail: string): void {
        if (!ok)
            throw new Error(detail);
    }
    Timer {
        interval: 1
        running: true
        onTriggered: {
            try {
                session.clock = 2;
                session.go("locked");
                root.check(auth.enabled && auth.fieldReady, "input never became ready");
                auth.attemptId = 1;
                auth.authenticated(1);
                root.check(!session.authenticated && root.handoffs === 0, "a signal without success was accepted");
                auth._terminal = true;
                auth.authenticated(0);
                root.check(!session.authenticated && root.handoffs === 0, "a stale attempt was accepted");
                session.secure = false;
                auth.authenticated(1);
                root.check(!session.authenticated && root.handoffs === 0, "an insecure attempt was accepted");
                session.secure = true;
                session.go("locked");
                auth.authenticated(1);
                root.check(session.authenticated && session.acceptedAttempt === 1 && root.handoffs === 0, "inherited success connection did not reach the greeter override");
                root.check(session.phase === "melt" && !auth.enabled && !auth.fieldReady, "success drained the plate or left input enabled");
                session.advance(.22);
                const frozenClock = session.clock;
                root.check(session.phase === "handoff" && root.handoffs === 1, "melt did not finish before handoff");
                session.advance(2);
                root.check(session.clock === frozenClock, "handoff visual clock kept moving");
                auth.authenticated(1);
                session.reducedMotion = true;
                session.active = false;
                session.advance(2);
                root.check(session.phase === "handoff" && root.handoffs === 1 && root.releases === 0, "greeter released, drained, or requested duplicate handoff");
                console.log("GREETER_SESSION_CONTRACT_OK");
                Qt.exit(0);
            } catch (error) {
                console.error(error.message);
                Qt.exit(1);
            }
        }
    }
    Component.onCompleted: Quickshell.watchFiles = false
}
