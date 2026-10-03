pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// Only this object owns the lock lifetime. Visual components cannot authenticate.
Scope {
    id: session
    required property AuthController auth
    // The animation starts with lock acquisition, after the bounded optional capture.
    property bool started: true
    property bool secure: false
    property bool active: true
    property bool reducedMotion: false
    property bool introComplete: false
    property bool wasSecure: false
    property bool releasing: false
    property var visualStates: ({})
    property real preparationStarted: Date.now()
    property real sampledAt: Date.now()
    property string phase: "pour"
    property real clock: 0
    property real elapsed: 0
    property real wrongAt: -100
    property real referenceHeight: 1080
    property string selectedOutput: ""
    readonly property string idleMode: ["lake", "letters", "breath", "wind"][Math.floor(Math.random() * 4)]
    property var surfaces: ({})
    property bool presentationSettled: false
    property int acceptedAttempt: -1
    readonly property bool authenticated: acceptedAttempt >= 0 && acceptedAttempt === auth.attemptId && auth.succeeded
    readonly property bool poured: secure && phase === "locked" && Object.keys(surfaces).length > 0 && (presentationSettled || Object.keys(surfaces).every(name => surfaces[name] >= 2))
    readonly property bool accepting: phase === "pour" || phase === "locked"
    readonly property real wrongAge: clock - wrongAt
    signal releaseLock
    signal privacyChanged
    signal lockLost

    // Visual preparation never delays acquiring ext-session-lock. It can delay
    // only the animation, and for at most 2 s before using the flat wave. This
    // covers the measured 1.81 s cold wallpaper crop; warm cache loads are shorter.
    function visualReady(name: string, ready: bool): void {
        const next = Object.assign({}, visualStates);
        next[name] = ready;
        visualStates = next;
    }
    function sample(now: real): void {
        const dt = Math.max(0, Math.min(.05, (now - sampledAt) / 1000));
        sampledAt = now;
        if (!started || !secure)
            return;
        if (phase === "pour" && now - preparationStarted < 2000 && (!Object.keys(surfaces).length || !Object.keys(surfaces).every(name => visualStates[name] === true)))
            return;
        advance(dt);
    }

    function bubbleAge(height: real): real {
        if (reducedMotion)
            return 10;
        const fraction = (height / 2 + 100) / (height + 130);
        const birth = .9 * Math.acos(1 - 2 * fraction) / Math.PI;
        return clock < birth ? -1 : clock - birth;
    }
    function registerOutput(name: string): void {
        resetPresentation();
        const next = Object.assign({}, surfaces);
        next[name] = 0;
        surfaces = next;
        const visuals = Object.assign({}, visualStates);
        delete visuals[name];
        visualStates = visuals;
        if (!selectedOutput)
            selectedOutput = name;
    }
    function removeOutput(name: string): void {
        const next = Object.assign({}, surfaces);
        delete next[name];
        surfaces = next;
        const visuals = Object.assign({}, visualStates);
        delete visuals[name];
        visualStates = visuals;
        if (selectedOutput === name)
            selectedOutput = Object.keys(next)[0] || "";
    }
    function painted(name: string): void {
        if (phase !== "locked" || !(name in surfaces) || surfaces[name] >= 2)
            return;
        const next = Object.assign({}, surfaces);
        // The first queued frameSwapped may belong to the preceding animation
        // frame. Request one more full frame before acknowledging private coverage.
        next[name] += 1;
        surfaces = next;
    }
    function chooseOutput(name: string, height: real): void {
        if (name in surfaces) {
            selectedOutput = name;
            referenceHeight = height;
        }
    }
    function go(next: string): void {
        elapsed = 0;
        phase = next;
        updateReadiness();
    }
    function updateReadiness(): void {
        // Initial property notifications can run before required auth is assigned.
        if (!auth)
            return;
        auth.enabled = accepting;
        auth.fieldReady = started && secure && phase === "locked" && bubbleAge(referenceHeight) >= .35;
    }
    function advance(dt: real): void {
        clock += dt;
        elapsed += dt;
        if (phase === "locked" && elapsed >= .5)
            introComplete = true;
        if (phase === "pour" && (reducedMotion || elapsed >= .9))
            go("locked");
        else if (phase === "melt" && (reducedMotion || elapsed >= .22))
            go("drain");
        else if (phase === "drain" && (reducedMotion || elapsed >= 1)) {
            if (authenticated && secure) {
                go("finished");
                releaseLock();
            }
        }
        updateReadiness();
    }
    function acceptAuthentication(attempt: int): void {
        if (!secure || !accepting || attempt !== auth.attemptId || !auth.succeeded)
            return;
        acceptedAttempt = attempt;
        go("melt");
        // A powered-off output has no animation to wait for. Authentication still gates exit.
        if (reducedMotion || !active) {
            advance(.22);
            advance(1);
        }
    }
    function prepareSleep(): void {
        // A pending drain cannot release the lock after a sleep request.
        acceptedAttempt = -1;
        if (phase === "melt" || phase === "drain" || phase === "finished")
            introComplete = true;
        auth.suspend();
        if (phase === "melt" || phase === "drain" || phase === "finished")
            go("locked");
        // Prefer two submitted private frames; secure remains the compositor
        // coverage guarantee when DPMS prevents presentation callbacks.
        resetPresentation();
        const next = {};
        for (const name of Object.keys(surfaces))
            next[name] = 0;
        surfaces = next;
        updateReadiness();
    }
    function resetPresentation(): void {
        presentationSettled = false;
        if (presentationGrace)
            presentationGrace.restart();
    }
    onSecureChanged: {
        resetPresentation();
        if (secure) {
            wasSecure = true;
            preparationStarted = Date.now();
            sampledAt = preparationStarted;
        } else if (wasSecure && !releasing) {
            acceptedAttempt = -1;
            auth.suspend();
            phase = "lost";
            lockLost();
        }
        updateReadiness();
    }
    onStartedChanged: {
        resetPresentation();
        preparationStarted = Date.now();
        sampledAt = preparationStarted;
        updateReadiness();
    }
    onPouredChanged: privacyChanged()
    onPhaseChanged: {
        resetPresentation();
        privacyChanged();
    }
    Connections {
        target: session.auth
        function onAttemptIdChanged(): void {
            if (session.acceptedAttempt >= 0 && session.acceptedAttempt !== session.auth.attemptId) {
                session.acceptedAttempt = -1;
                session.go("locked");
            }
        }
        function onAuthenticated(attemptId: int): void {
            session.acceptAuthentication(attemptId);
        }
        function onRejected(): void {
            session.wrongAt = session.clock;
        }
    }
    Timer {
        interval: 16
        running: session.started && (session.active || session.phase !== "locked") && !session.reducedMotion && session.phase !== "finished" && session.phase !== "lost"
        repeat: true
        onRunningChanged: session.sampledAt = Date.now()
        onTriggered: session.sample(Date.now())
    }
    // Output mode metadata cannot tell DPMS-off from a stalled presentation.
    // Never infer it: after the full logical plate has settled for an event-loop
    // grace period, secure itself proves compositor coverage. The next rendered
    // frame uses that plate, including after prepare-sleep revokes a drain.
    Timer {
        id: presentationGrace
        interval: 250
        onTriggered: {
            if (session.started && session.secure && session.phase === "locked" && Object.keys(session.surfaces).length > 0)
                session.presentationSettled = true;
        }
    }
    Component.onCompleted: {
        if (reducedMotion)
            go("locked");
        updateReadiness();
    }
}
