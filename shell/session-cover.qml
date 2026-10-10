pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

// This is loaded only by the main shell after its one-shot fresh-login claim.
Scope {
    id: root
    readonly property string token: Quickshell.env("EMAKI_SESSION_START")
    readonly property bool valid: /^[0-9a-f]{32}$/.test(token)
    property var context: ({})
    property var observation: ({})
    property bool closed: false
    property bool completionEmitted: false
    property int observerAttempts: 0
    property string observerStderr: ""
    readonly property bool visibleCover: valid && context.active === true && !closed
    signal revealing
    signal completed
    function event(phase: string, detail: var): void {
        console.log("EMAKI_SESSION_COVER " + JSON.stringify(Object.assign({
            phase: phase,
            handoffAtMs: root.context.visual?.createdMs || 0,
            atMs: Date.now(),
            observation: observation,
            frames: transition.surfaces
        }, detail || {})));
    }
    function startObserver(): void {
        const expires = Number(context.expiresAtMs);
        if (!valid || closed || context.active !== true || !Number.isFinite(expires) || expires <= Date.now() || observer.running || observerAttempts >= 3)
            return;
        // Process snapshots command when running is set. Sibling bindings can
        // still contain the pre-context expiry at that point (QS 0.3.1).
        observer.command = [Platform.python, "-I", "-B", Quickshell.shellPath("helpers/session-start.py"), "observe", token, String(expires)];
        observerStderr = "";
        ++observerAttempts;
        observer.running = true;
    }
    onContextChanged: startObserver()
    function complete(): void {
        if (completionEmitted)
            return;
        closed = true;
        expiry.stop();
        observerRetry.stop();
        if (loadContext.running)
            loadContext.signal(9);
        if (release.running)
            release.signal(9);
        completionEmitted = true;
        if (observer.running)
            observer.signal(9);
        event("finished", {});
        finishReport.running = true;
        completed();
    }
    Timer {
        id: observerRetry
        interval: 150
        onTriggered: root.startObserver()
    }
    // Context itself is bounded; after a reply use its original expiry even
    // when no output/image becomes ready or the release process stops replying.
    Timer {
        id: expiry
        interval: 3000
        running: root.valid && !root.closed
        onTriggered: root.complete()
    }
    SessionCoverState {
        id: transition
        running: root.visibleCover
        // Reserve the drain and an IPC allowance inside the reported lifetime.
        capInterval: Math.max(1, (Number(root.context.remainingMs) || 1) - 1250)
        expiresAtMs: Number(root.context.expiresAtMs) || 0
        initialClock: Number(root.context.visual?.clock) || 0
        desktopReady: root.observation.ready === true
        onReleaseCover: release.running = true
        onDrainStarted: reason => {
            root.revealing();
            root.event("drain-" + reason, {});
            drainReport.command = [Platform.python, "-I", "-B", Quickshell.shellPath("helpers/session-start.py"), "drain", root.token, reason];
            drainReport.running = true;
        }
        onFinished: root.complete()
    }
    Process {
        id: loadContext
        command: [Platform.python, "-I", "-B", Quickshell.shellPath("helpers/session-start.py"), "context", root.token]
        running: root.valid
        stdout: SplitParser {
            onRead: line => {
                try {
                    const value = JSON.parse(line);
                    // A delayed pipe reply must not start a fresh cover budget.
                    const remaining = Math.min(Number(value.remainingMs), Number(value.expiresAtMs) - Date.now());
                    if (value.version === 1 && value.active === true && Number.isFinite(Number(value.expiresAtMs)) && Number.isFinite(remaining) && remaining > 0 && !root.closed) {
                        value.remainingMs = remaining;
                        root.context = value;
                        expiry.interval = Math.max(1, value.expiresAtMs - Date.now() + 250);
                        expiry.restart();
                        root.event("loaded", {});
                    }
                } catch (_) {}
            }
        }
        onExited: {
            if (!root.context.active)
                root.complete();
        }
    }
    Process {
        id: observer
        stdout: SplitParser {
            onRead: line => {
                try {
                    const value = JSON.parse(line);
                    if (root.closed || value.version !== 1)
                        return;
                    const next = {
                        ready: value.ready === true,
                        shellReady: value.shellReady === true,
                        coverMapped: value.coverMapped === true,
                        barMapped: value.barMapped === true,
                        dockMapped: value.dockMapped === true,
                        wallpaperMapped: value.wallpaperMapped === true,
                        autostartSettled: value.autostartSettled === true,
                        windowCount: Number.isFinite(value.windowCount) ? value.windowCount : null
                    };
                    if (JSON.stringify(next) !== JSON.stringify(root.observation)) {
                        root.observation = next;
                        root.event("observation", {});
                    }
                } catch (_) {}
            }
        }
        stderr: SplitParser {
            onRead: line => root.observerStderr = (root.observerStderr + line + "\n").slice(-4096)
        }
        onExited: (code, status) => {
            if (root.closed)
                return; // complete() intentionally kills the observer.
            const retry = root.observerAttempts < 3 && Number(root.context.expiresAtMs) - Date.now() > observerRetry.interval;
            root.event("observer-exited", {
                code: code,
                status: status,
                stderr: root.observerStderr,
                attempt: root.observerAttempts,
                retry: retry
            });
            if (retry)
                observerRetry.restart();
        }
    }
    Process {
        id: release
        command: ["niri-emaki", "msg", "action", "emaki-startup-cover-release"]
        onExited: (code, status) => {
            if (code === 0 && status === 0 && !root.closed)
                transition.beginDrain();
            else
                root.complete();
        }
    }
    Process {
        id: drainReport
    }
    Process {
        id: finishReport
        command: [Platform.python, "-I", "-B", Quickshell.shellPath("helpers/session-start.py"), "finished", root.token]
    }
    Variants {
        model: root.visibleCover ? Quickshell.screens.filter(s => (root.context.outputs || []).includes(s.name)) : []
        PanelWindow {
            id: panel
            required property ShellScreen modelData
            screen: modelData
            anchors {
                top: true
                bottom: true
                left: true
                right: true
            }
            color: "transparent"
            exclusionMode: ExclusionMode.Ignore
            WlrLayershell.namespace: "emaki-session-cover"
            WlrLayershell.layer: WlrLayer.Overlay
            WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
            mask: Region {}
            SessionCoverSurface {
                anchors.fill: parent
                session: transition
                screen: panel.screen
                imagePath: root.context.image || ""
                idleMode: root.context.visual?.idleMode || "lake"
                wordmarkOrigin: Number(root.context.visual?.introStart) || 0
            }
        }
    }
    Component.onCompleted: {
        if (!valid)
            Qt.callLater(complete);
    }
}
