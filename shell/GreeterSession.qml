pragma ComponentBehavior: Bound
import QtQuick

// Login keeps the poured plate until greetd replaces the greeter. The new
// session owns the decorative handoff; the C8 lock still owns its own drain.
LockSession {
    id: greeterSession
    property real handoffIntroStart: 0
    signal handoffRequested

    function updateReadiness(): void {
        if (!auth)
            return;
        auth.enabled = accepting && !authenticated;
        auth.fieldReady = started && secure && phase === "locked" && bubbleAge(referenceHeight) >= .35 && !authenticated;
    }
    function acceptAuthentication(attempt: int): void {
        if (!secure || !accepting || acceptedAttempt >= 0 || attempt !== auth.attemptId || !auth.succeeded)
            return;
        handoffIntroStart = Math.max(0, clock - elapsed);
        acceptedAttempt = attempt;
        go("melt");
        if (reducedMotion || !active)
            advance(.22);
    }
    function advance(dt: real): void {
        if (phase === "handoff")
            return; // Freeze the final visual clock before grabbing its pixels.
        clock += dt;
        elapsed += dt;
        if (phase === "locked" && elapsed >= .5)
            introComplete = true;
        if (phase === "pour" && (reducedMotion || elapsed >= .9))
            go("locked");
        else if (phase === "melt" && (reducedMotion || elapsed >= .22)) {
            go("handoff");
            handoffRequested();
        }
        updateReadiness();
    }
}
