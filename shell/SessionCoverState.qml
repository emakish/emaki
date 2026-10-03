pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// Drain on readiness, or at the last safe point in the compositor budget.
Scope {
    id: cover
    property bool running: true
    property int capInterval: 1
    property real expiresAtMs: 0
    property int drainDuration: 1000
    property string drainReason: "ready"
    property bool deadlineReached: false
    property real initialClock: 0
    readonly property real clock: initialClock + elapsed
    property real elapsed: 0
    property string phase: "locked"
    property bool desktopReady: false
    property bool releaseRequested: false
    property var surfaces: ({})
    property var visuals: ({})
    readonly property bool presented: Object.keys(surfaces).length > 0 && Object.keys(surfaces).every(name => surfaces[name] >= 2)
    readonly property bool visualsReady: Object.keys(surfaces).length > 0 && Object.keys(surfaces).every(name => visuals[name] === true)
    signal releaseCover
    signal drainStarted(string reason)
    signal finished
    signal requestFrames
    function registerOutput(name: string): void {
        surfaces = Object.assign({}, surfaces, {
            [name]: 0
        });
    }
    function removeOutput(name: string): void {
        const next = Object.assign({}, surfaces);
        delete next[name];
        surfaces = next;
    }
    function visualReady(name: string, ready: bool): void {
        visuals = Object.assign({}, visuals, {
            [name]: ready
        });
        surfaces = Object.assign({}, surfaces, {
            [name]: 0
        });
        requestFrames();
    }
    function painted(name: string): void {
        if (name in surfaces && visuals[name] && surfaces[name] < 2) {
            surfaces = Object.assign({}, surfaces, {
                [name]: surfaces[name] + 1
            });
            requestFrames();
        }
        maybeRelease();
    }
    function maybeRelease(): void {
        if (running && phase === "locked" && (desktopReady || deadlineReached) && presented && visualsReady && !releaseRequested) {
            drainReason = desktopReady ? "ready" : "deadline";
            releaseRequested = true;
            releaseCover();
        }
    }
    function beginDrain(): void {
        if (phase !== "locked" || !releaseRequested)
            return;
        drainDuration = Math.max(1, Math.min(1000, expiresAtMs - Date.now()));
        phase = "drain";
        drainStarted(drainReason);
    }
    onDesktopReadyChanged: maybeRelease()
    Timer {
        interval: cover.capInterval
        running: cover.running && cover.phase === "locked"
        onTriggered: {
            cover.deadlineReached = true;
            cover.maybeRelease();
        }
    }
    NumberAnimation {
        target: cover
        property: "elapsed"
        from: 0
        to: 1
        duration: cover.drainDuration
        running: cover.running && cover.phase === "drain"
        onFinished: {
            cover.phase = "finished";
            cover.finished();
        }
    }
}
