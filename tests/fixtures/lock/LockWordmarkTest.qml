pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Window
import Quickshell
import "LockWordmark.js" as Motion
import "LockWordmarkData.js" as Pixels
import "WordmarkReference.js" as Reference

ShellRoot {
    id: root
    property int stage: 0
    property int paints: 0
    property int priorPaints: 0
    property bool saved: false
    property bool driveClock: false
    property real stageStarted: 0
    function check(ok: bool, name: string): void {
        if (!ok)
            throw new Error(name);
        console.log("LOCK_WORDMARK_PASS " + name);
    }
    function near(actual: var, expected: var): bool {
        if (typeof actual === "number")
            return typeof expected === "number" && Math.abs(actual - expected) < 0.00000001;
        if (actual !== null && typeof actual === "object") {
            if (JSON.stringify(Object.keys(actual).sort()) !== JSON.stringify(Object.keys(expected).sort()))
                return false;
            for (const key of Object.keys(actual)) {
                if (!near(actual[key], expected[key]))
                    return false;
            }
            return true;
        }
        return actual === expected;
    }
    function mathTests(): void {
        check(Pixels.W === 200 && Pixels.H === 48, "canonical 200 by 48 wordmark");
        const times = [0, .067, .125, .45, .79, .8, 1.02, 1.2, 1.6, 1.875, 2.4, 11.73, 107.125];
        let compared = 0;
        for (const p of Pixels.PIXELS) {
            checkColor(p);
            for (const t of times) {
                for (const mode of ["lake", "letters", "breath", "wind"]) {
                    if (!near(Motion.idle(mode, p, t), Reference.idleAt(mode, p, t)))
                        throw new Error("Mockup idle mismatch: " + mode + " t=" + t + " pixel=" + p);
                    compared++;
                }
            }
        }
        check(compared > 100000, "all four idles match approved HTML per pixel at 13 times");
        check(times.every(t => near(Motion.sparks(t), Reference.sparkAt(t))), "lake sparks match approved HTML");
        rasterTests(times);
        for (const ratio of [1, 1.25, 1.5, 2, 3]) {
            const logo = Motion.placement(1536, 960, ratio);
            check(logo.dh - logo.y - Pixels.H === Math.round(40 * ratio), "physical size and logical margin at DPR " + ratio);
            for (const age of [0, .025, .15, .3, .49, .5]) {
                const rectangles = [], context = {
                    fillStyle: "",
                    fillRect: function (x, y, w, h) {
                        rectangles.push([x, y, w, h, this.fillStyle]);
                    }
                };
                Motion.drawIntro(context, age, logo, 0, 0);
                if (!near(rectangles, Reference.introAt(age, logo)))
                    throw new Error("Mockup mirrored intro mismatch at DPR " + ratio + " t=" + age);
            }
        }
        check(Motion.placement(1707, 960, 1.5).dw === 2560, "fractional geometry does not place pixels in an outward-rounded extra column");
        check(true, "mirrored ribbons match approved HTML through final row arrival");
        const logo = Motion.placement(1536, 960, 1.25);
        const particles = Motion.spawnParticles(logo);
        Reference.beginDrain(logo, 1536, 960, 7.2);
        check(near(particles, Reference.particleState()), "crumble spawn order, jitter and colour match mockup");
        for (let frame = 1; frame <= 60; frame++) {
            const elapsed = frame / 60;
            Motion.stepParticles(particles, 1 / 60, elapsed, 7.2 + elapsed, 1536, 960);
            Reference.drainFrame(1 / 60, elapsed, 7.2 + elapsed);
            if (!near(particles, Reference.particleState()))
                throw new Error("Mockup particle integration mismatch at frame " + frame);
            const edge = Motion.drainEdge(960, elapsed);
            if (particles.some(q => q.alive && elapsed >= q.rel && q.y < edge.b + Motion.waveAt(q.x, edge.amp, 7.2 + elapsed) + q.margin - 0.00000001))
                throw new Error("Particle left behind the draining edge");
        }
        check(particles.every(q => !q.alive), "all particles swept away by end of downward drain");
        const caught = [
            {
                x: 20,
                y: -100,
                vx: 0,
                vy: 0,
                rel: .3,
                margin: 10,
                jx: 0,
                jy: 0,
                c: 0,
                alive: true
            }
        ];
        Motion.stepParticles(caught, 1 / 60, .05, 7.25, 1536, 960);
        check(caught[0].rel === .05, "moving edge releases a pixel before its scheduled crumble");
    }
    function rasterTests(times: var): void {
        let uploads = 0, rectangles = 0;
        const context = {
            createImageData: (w, h) => ({
                        width: w,
                        height: h,
                        data: new Uint8ClampedArray(w * h * 4)
                    }),
            putImageData: () => {
                uploads++;
            },
            fillRect: () => {
                rectangles++;
            }
        };
        const raster = Motion.createIdleRaster(context), retained = raster.image;
        const logo = Motion.placement(640, 360, 1);
        let samples = 0;
        for (const mode of ["lake", "letters", "breath", "wind", "still"]) {
            for (const t of times) {
                const before = uploads, oldRectangles = rectangles, expected = new Uint8ClampedArray(retained.data.length);
                const occupied = new Set();
                Motion.drawIdle(context, mode, t, logo, 0, 0, raster);
                for (const p of Pixels.PIXELS) {
                    const m = mode === "still" ? {} : Reference.idleAt(mode, p, t);
                    const color = m.cyc === undefined ? (m.du ? Motion.hotIndex(p[0], p[1], m.du) : p[4]) : m.cyc;
                    const offset = ((p[1] + (m.dy || 0) + 4) * 204 + p[0] + (m.dx || 0) + 2) * 4;
                    if (occupied.has(offset))
                        throw new Error("Approved idle pixels overlap");
                    occupied.add(offset);
                    const rgb = Motion.RGB[color];
                    expected[offset] = rgb[0];
                    expected[offset + 1] = rgb[1];
                    expected[offset + 2] = rgb[2];
                    expected[offset + 3] = 255;
                }
                for (let i = 0; i < expected.length; i += 4) {
                    if (retained.data[i + 3] !== expected[i + 3] || (expected[i + 3] && (retained.data[i] !== expected[i] || retained.data[i + 1] !== expected[i + 1] || retained.data[i + 2] !== expected[i + 2])))
                        throw new Error("Retained raster differs from mockup: " + mode + " t=" + t + " byte=" + i);
                }
                if (raster.image !== retained || uploads !== before + 1 || (mode !== "lake" && rectangles !== oldRectangles))
                    throw new Error("Idle must retain one image and use one upload, with rectangles only for lake sparks");
                samples++;
            }
        }
        check(samples === times.length * 5, "retained typed raster matches original pixels across mode changes without stale pixels or per-pixel Canvas calls");
    }
    function checkColor(p: var): void {
        if (p[4] !== Motion.hotIndex(p[0], p[1], 0) || p[4] !== Reference.colorAt(p[0], p[1]))
            throw new Error("Generated Bayer colour disagrees with approved HTML");
    }
    Window {
        width: 640
        height: 360
        visible: true
        LockWordmark {
            id: visual
            anchors.fill: parent
            dpr: Screen.devicePixelRatio
        }
        LockWordmark {
            id: late
            anchors.fill: parent
            phase: "locked"
            clock: 10
            elapsed: 4
            active: false
        }
    }
    Connections {
        target: visual.children[0]
        function onPainted(): void {
            root.paints++;
        }
    }
    function nextStage(): void {
        stage++;
        stageStarted = Date.now();
    }
    function waitFor(ok: bool, name: string): bool {
        if (!ok && Date.now() - stageStarted > 5000)
            throw new Error("Deadline waiting for " + name + " at stage " + stage);
        return ok;
    }
    function benchmark(): void {
        const c = visual.children[0].getContext("2d"), logo = visual.logo;
        const ox = visual.canvasRect.x, oy = visual.canvasRect.y, raster = Motion.createIdleRaster(c);
        for (const mode of ["lake", "letters", "breath", "wind"]) {
            let oldTime = 0, newTime = 0;
            for (let pass = 0; pass < 2; pass++) {
                const started = Date.now();
                for (let frame = 0; frame < 40; frame++) {
                    c.reset();
                    c.clearRect(0, 0, 204, 54);
                    if (pass === 0)
                        Reference.paintIdle(c, mode, frame / 15, logo, ox, oy);
                    else
                        Motion.drawIdle(c, mode, frame / 15, logo, ox, oy, raster);
                }
                // Flush queued Canvas commands so the comparison includes rendering.
                c.getImageData(0, 0, 1, 1);
                if (pass === 0)
                    oldTime = Date.now() - started;
                else
                    newTime = Date.now() - started;
            }
            console.log("LOCK_WORDMARK_BENCH " + mode + " original_ms=" + (oldTime / 40).toFixed(3) + " raster_ms=" + (newTime / 40).toFixed(3));
        }
    }
    function frameAcknowledgements(): var {
        for (const resource of visual.resources) {
            if (resource.target !== undefined && resource.enabled !== undefined)
                return resource;
        }
        throw new Error("Window frame acknowledgement connection missing");
    }
    function step(): void {
        switch (stage) {
        case 0:
            mathTests();
            check(!visual.visible && !visual.animating, "no wordmark or work during pour");
            visual.clock = .912;
            visual.elapsed = 0;
            visual.phase = "locked";
            check(visual.introStart === .912 && visual.introAge === 0, "pour to locked starts intro at shared clock with zero phase elapsed");
            check(late.introStart === 6 && late.introAge === 4, "late-created output joins existing intro timeline");
            visual.phase = "pour";
            visual.clock = 2;
            visual.elapsed = 1;
            visual.reducedMotion = true;
            visual.phase = "locked";
            nextStage();
            break;
        case 1:
            // Canvas painting and grabToImage are asynchronous. Advance only after their
            // completion signals, never because another fixture timer interval elapsed.
            if (!waitFor(paints > 0, "first Canvas paint"))
                return;
            check(visual.visible && !visual.animating, "reduced motion renders one static wordmark");
            visual.grabToImage(result => {
                root.saved = result.saveToFile(Quickshell.shellPath("wordmark.png"));
            });
            nextStage();
            break;
        case 2:
            if (!waitFor(saved, "completed Qt image grab"))
                return;
            check(true, "Qt Canvas screenshot saved for physical pixel checks");
            check(visual.canvasRect.width === 204 && visual.canvasRect.height === 54, "idle Canvas is a tight physical 204 by 54 pixels at every DPR");
            benchmark();
            priorPaints = paints;
            visual.clock = 99;
            visual.elapsed = 98;
            nextStage();
            break;
        case 3:
            // Observe a quiet interval spanning two normal 67 ms idle updates.
            if (Date.now() - stageStarted < 150)
                return;
            check(paints === priorPaints, "reduced mode ignores animation clock without repaint");
            visual.active = false;
            visual.reducedMotion = false;
            priorPaints = paints;
            nextStage();
            break;
        case 4:
            if (Date.now() - stageStarted < 150)
                return;
            check(!visual.animating && paints === priorPaints, "inactive output stops timers and Canvas redraws");
            visual.clock = 102;
            visual.elapsed = 0;
            visual.phase = "pour";
            visual.phase = "locked";
            visual.active = true;
            priorPaints = paints;
            driveClock = true;
            nextStage();
            break;
        case 5:
            if (!waitFor(paints - priorPaints >= 2, "intro frames"))
                return;
            check(visual.fullRate, "intro uses presentation frame rate after lock");
            visual.clock = 103;
            visual.elapsed = 1;
            priorPaints = paints;
            nextStage();
            break;
        case 6:
            if (!waitFor(paints - priorPaints >= 2, "idle frames"))
                return;
            check(!visual.fullRate && !visual.resources.some(r => r.interval !== undefined), "idle is driven by the shared clock without a second timer");
            // Withhold acknowledgements without a production test switch or real DPMS.
            frameAcknowledgements().enabled = false;
            priorPaints = paints;
            visual.refreshFrame();
            nextStage();
            break;
        case 7:
            if (!waitFor(paints > priorPaints, "outstanding unacknowledged frame"))
                return;
            check(visual.framePending && !visual.animating, "outstanding frame stops repeated paint requests");
            priorPaints = paints;
            nextStage();
            break;
        case 8:
            if (Date.now() - stageStarted < 150)
                return;
            check(paints === priorPaints && visual.framePending && !visual.animating, "withheld frame acknowledgements stop repeated Canvas work");
            frameAcknowledgements().enabled = true;
            visual.refreshFrame();
            nextStage();
            break;
        case 9:
            if (!waitFor(paints > priorPaints && !visual.framePending, "resumed window presentation"))
                return;
            check(true, "rendering resumes when window acknowledgements resume");
            driveClock = false;
            visual.introComplete = true;
            visual.phase = "melt";
            check(visual.canvasRect.width === 204 && visual.canvasRect.height === 54, "melt retains the tight Canvas");
            visual.phase = "locked";
            visual.elapsed = 0;
            check(!visual.fullRate, "sleep during melt does not replay a completed ribbon intro");
            visual.clock = 104;
            visual.elapsed = 0;
            visual.phase = "drain";
            nextStage();
            break;
        case 10:
            if (!waitFor(visual.particles !== null, "drain particle creation"))
                return;
            check(visual.fullRate && visual.canvasRect.width > 204 && visual.canvasRect.height > 54, "only drain grows the Canvas for a fresh particle set");
            visual.clock = 104.5;
            visual.elapsed = .5;
            nextStage();
            break;
        case 11:
            if (!waitFor(Math.abs(visual.simulatedElapsed - .5) < .00001, "particle clock catch-up"))
                return;
            check(true, "Canvas catches particles up to shared session clock");
            visual.phase = "finished";
            nextStage();
            break;
        case 12:
            check(!visual.visible && !visual.animating && visual.particles === null, "finished lock drops particles and animation work");
            console.log("LOCK_WORDMARK_COMPLETE");
            Qt.quit();
            break;
        }
    }
    Timer {
        interval: 20
        repeat: true
        running: true
        onTriggered: {
            try {
                if (root.driveClock) {
                    visual.clock += .02;
                    visual.elapsed += .02;
                }
                root.step();
            } catch (error) {
                console.error("LOCK_WORDMARK_FAILED " + error);
                Qt.exit(1);
            }
        }
    }
}
