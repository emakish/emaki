pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import "LockWordmark.js" as Motion

// Optional decoration, with no authentication or input dependency.
Item {
    id: wordmark
    property string phase: "pour"
    property real elapsed: 0
    property real clock: 0
    property string idleMode: "lake"
    property real dpr: 1
    property bool active: true
    property bool reducedMotion: false
    property bool introComplete: false
    property int idleTick: -1
    property real introStart: -1
    property var particles: null
    property var idleRaster: null
    property real simulatedElapsed: 0
    property bool framePending: false
    readonly property var logo: Motion.placement(width, height, dpr)
    readonly property real introAge: introStart < 0 ? .5 : Math.max(0, clock - introStart)
    readonly property bool fullRate: phase === "drain" || (!introComplete && introAge < .5)
    readonly property bool animating: active && visible && !reducedMotion && !framePending
    readonly property rect canvasRect: Motion.canvasBounds(logo, phase, fullRate && !reducedMotion)
    visible: active && (phase === "locked" || phase === "handoff" || phase === "melt" || (phase === "drain" && !reducedMotion))

    function requestFrame(): void {
        if (active && visible && !framePending) {
            framePending = true;
            idleTick = Math.floor(Math.max(0, introAge - .5) * 15);
            canvas.requestPaint();
        }
    }
    function refreshFrame(): void {
        // Geometry/visibility changes may discard an outstanding render request.
        // Replace it once; an ordinary animation tick never bypasses backpressure.
        framePending = false;
        requestFrame();
    }
    function framePresented(): void {
        framePending = false;
        // Let the window's presentation cadence drive the short full-rate phases.
        // Idle is driven only by the shared session clock, at most fifteen times a second.
        if (active && visible && !reducedMotion && fullRate)
            requestFrame();
    }
    function syncPhase(): void {
        particles = null;
        simulatedElapsed = 0;
        if (phase === "pour" || phase === "finished")
            introStart = -1;
        else if (phase === "locked" && !introComplete)
            introStart = clock - elapsed;
        else if (phase === "locked" && introStart < 0)
            introStart = clock - elapsed - .5;
        refreshFrame();
    }
    function geometryChanged(): void {
        particles = null;
        simulatedElapsed = 0;
        refreshFrame();
    }
    function advanceParticles(): void {
        if (particles === null) {
            particles = Motion.spawnParticles(logo);
            simulatedElapsed = 0;
        }
        // Fixed 60 Hz integration reproduces the mockup and catches up after output sleep.
        const target = Math.min(1, Math.max(0, elapsed));
        while (simulatedElapsed + 1 / 60 <= target + 0.0000001) {
            simulatedElapsed += 1 / 60;
            Motion.stepParticles(particles, 1 / 60, simulatedElapsed, clock - elapsed + simulatedElapsed, width, height);
        }
    }
    onPhaseChanged: syncPhase()
    onClockChanged: {
        if (active && visible && !reducedMotion && (fullRate || Math.floor(Math.max(0, introAge - .5) * 15) !== idleTick))
            requestFrame();
    }
    onIntroCompleteChanged: refreshFrame()
    onActiveChanged: refreshFrame()
    onVisibleChanged: refreshFrame()
    onReducedMotionChanged: refreshFrame()
    onIdleModeChanged: refreshFrame()
    onLogoChanged: geometryChanged()
    Component.onCompleted: syncPhase()

    Connections {
        target: wordmark.Window.window
        function onFrameSwapped(): void {
            wordmark.framePresented();
        }
    }
    Canvas {
        id: canvas
        // Idle needs only the wordmark, its two-pixel sparks and the dot's three-pixel
        // upward movement. Ribbons extend to the right; only drain needs the eddy margin.
        x: wordmark.canvasRect.x / wordmark.logo.dpr
        y: wordmark.canvasRect.y / wordmark.logo.dpr
        width: wordmark.canvasRect.width / wordmark.logo.dpr
        height: wordmark.canvasRect.height / wordmark.logo.dpr
        renderTarget: Canvas.Image
        renderStrategy: Canvas.Immediate
        antialiasing: false
        smooth: false
        onAvailableChanged: wordmark.refreshFrame()
        onPaint: {
            if (!wordmark.active || !wordmark.visible)
                return;
            const c = getContext("2d");
            c.reset();
            c.clearRect(0, 0, width, height);
            // Qt Canvas has a DPR-aware backing image. Draw integer physical pixels,
            // including on fractional output scales, instead of scaling a 48-logical-px image.
            c.scale(1 / wordmark.logo.dpr, 1 / wordmark.logo.dpr);
            const ox = Math.round(x * wordmark.logo.dpr), oy = Math.round(y * wordmark.logo.dpr);
            if (wordmark.phase === "drain") {
                wordmark.advanceParticles();
                Motion.drawParticles(c, wordmark.particles, wordmark.logo, ox, oy);
            } else if (!wordmark.reducedMotion && !wordmark.introComplete && wordmark.introAge < .5) {
                Motion.drawIntro(c, wordmark.introAge, wordmark.logo, ox, oy);
            } else {
                if (wordmark.idleRaster === null)
                    wordmark.idleRaster = Motion.createIdleRaster(c);
                Motion.drawIdle(c, wordmark.reducedMotion ? "still" : wordmark.idleMode, Math.max(0, wordmark.introAge - .5), wordmark.logo, ox, oy, wordmark.idleRaster);
            }
        }
    }
}
