.pragma library
.import "LockWordmarkData.js" as Data

// These functions preserve lock-template.html's motion, including its deterministic hash.
// All drawing coordinates are physical pixels. Particle physics uses logical pixels.
var PIXELS = Data.PIXELS;
var CORNERS = PIXELS.filter(p => p[3] >= 2);
var ROWS = Array.from({ length: Data.H }, () => []);
PIXELS.forEach(p => ROWS[p[1]].push(p));
ROWS.forEach(row => row.sort((a, b) => b[0] - a[0]));
var WIND = [[0,0,0],[0,0,0],[0,0,0],[0,0,0],[0,0,0],[0,0,0],[1,0,0],[2,1,0],[2,1,0],[2,1,0],[2,1,0],[1,0,0],[1,0,0],[0,0,0]];
var HOP = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,-1,-2,-2,-1,0,0];
var SPARK = [0, 1, 2, 2, 1, 0];

function hotIndex(x, y, du) {
    const u = Math.max(0, Math.min(1, x / Data.W * 0.8 + (1 - y / Data.H) * 0.2 + (du || 0))) * (Data.HOT.length - 1);
    const base = Math.min(Math.floor(u), Data.HOT.length - 2);
    return base + (u - base > (Data.BAYER[y % 4][x % 4] + 0.5) / 16 ? 1 : 0);
}
function frameAt(t, fps) { return Math.floor(t * fps); }
function hash(a, b) {
    let x = (a * 374761393 + b * 668265263) | 0;
    x = (x ^ (x >>> 13)) * 1274126177 | 0;
    return ((x ^ (x >>> 16)) >>> 0) / 4294967296;
}
function swell(t, period, peakAt) {
    return 0.5 - 0.5 * Math.cos(2 * Math.PI * (frameAt(t, 8) / 8 - peakAt + period / 2) / period);
}
function breath(p, t) { return { dy: (t % 1.6) < 0.8 ? 0 : -1, du: 0.12 * swell(t % 1.6, 1.6, 1.2) }; }
function wind(p, t) {
    const f = WIND[frameAt(t, 6) % WIND.length];
    const dx = f[Math.min(2, Math.floor(p[1] / 16))];
    return { dx: dx, du: 0.06 * dx };
}
function letters(p, t) {
    const g = p[2] === 5 ? 4 : p[2];
    const tl = (t + g * 0.27) % 1.6;
    return { dy: tl < 0.8 ? 0 : -1, du: 0.12 * swell(tl, 1.6, 1.2) };
}
function dot(p, t) { return { dy: p[2] === 5 ? HOP[frameAt(t, 8) % HOP.length] : 0 }; }
function lake(p, t) {
    const fr = frameAt(t, 15);
    if (hash(p[0] * 97 + p[1], fr) >= 0.3) return {};
    const up = hash(p[1] * 131 + p[0], fr + 7) < 0.5, base = p[4];
    return { cyc: up ? (base < Data.HOT.length - 1 ? base + 1 : base - 1) : (base > 0 ? base - 1 : base + 1) };
}
// Preserve the public motion helpers as the golden reference contract. The
// renderer below uses precomputed colour tables and retained typed buffers.
var BOTH_SCRATCH = { dx: 0, dy: 0, du: 0 };
function both(a, b) {
    BOTH_SCRATCH.dx = (a.dx || 0) + (b.dx || 0);
    BOTH_SCRATCH.dy = (a.dy || 0) + (b.dy || 0);
    BOTH_SCRATCH.du = (a.du || 0) + (b.du || 0);
    return BOTH_SCRATCH;
}
function idle(mode, p, t) {
    if (mode === "lake") return lake(p, t);
    if (mode === "letters") return letters(p, t);
    if (mode === "breath") return both(breath(p, t), dot(p, t));
    if (mode === "wind") return both(wind(p, t), dot(p, t));
    return {};
}
function sparks(t) {
    const out = [], slot = 0.45;
    for (let k = Math.floor(t / slot) - 2; k <= Math.floor(t / slot); k++) {
        for (let lane = 0; lane < 2; lane++) {
            if (hash(k, lane + 7) > 0.5) continue;
            const c = CORNERS[Math.floor(hash(k, lane + 101) * CORNERS.length)];
            const f = frameAt(t - (k * slot + hash(k, lane + 31) * 0.3), 8);
            if (f >= 0 && f < SPARK.length) out.push([c[0], c[1], SPARK[f]]);
        }
    }
    return out;
}
// Integer logical output geometry can include a final partial physical pixel at
// fractional scale. Placement uses the fully covered physical grid, never that
// outward-rounded extra column (for example 1707 logical px at DPR 1.5).
function placement(width, height, dpr) {
    const ratio = Math.max(0.1, dpr), dw = Math.floor(width * ratio + 0.0000001), dh = Math.floor(height * ratio + 0.0000001);
    const margin = Math.round(40 * ratio);
    return { x: dw - margin - Data.W, y: dh - margin - Data.H, dw: dw, dh: dh, dpr: ratio };
}
function canvasBounds(logo, phase, intro) {
    let x = logo.x - 2, y = logo.y - 4;
    let right = logo.x + Data.W + 2, bottom = logo.y + Data.H + 2;
    if (phase === "drain") {
        x = logo.x - Math.ceil(170 * logo.dpr);
        y = logo.y - Math.ceil(170 * logo.dpr);
        right = logo.dw; bottom = logo.dh;
    } else if (intro) {
        right = logo.dw;
    }
    x = Math.max(0, x); y = Math.max(0, y);
    return Qt.rect(x, y, Math.max(1, Math.min(logo.dw, right) - x), Math.max(1, Math.min(logo.dh, bottom) - y));
}
function introX(p, index, row, t, logo) {
    const fly = 0.5 - 0.15, t0 = p[1] * 0.15 / (Data.H - 1);
    if (t < t0) return logo.dw;
    const head0 = Math.ceil(logo.dw - logo.x) + 1;
    const velocity = (head0 + row.length - 1 - row[row.length - 1][0]) / fly;
    const moving = head0 + index - velocity * (t - t0);
    return logo.x + (moving <= p[0] ? p[0] : Math.ceil(moving));
}
function waveAt(v, amp, clock) {
    return amp * (0.55 * Math.sin(v * 0.0061 + clock * 2.3)
                 + 0.30 * Math.sin(v * 0.0137 - clock * 3.1 + 1.3)
                 + 0.15 * Math.sin(v * 0.029 + clock * 4.7 + 2.1));
}
function drainEdge(height, elapsed) {
    const k = Math.max(0, Math.min(1, elapsed));
    return { b: -70 + (height + 140) * (0.35 * k + 0.65 * k * k), amp: 24 };
}
function spawnParticles(logo) {
    return PIXELS.map(p => ({
        x: (logo.x + p[0] + 0.5) / logo.dpr,
        y: (logo.y + p[1] + 0.5) / logo.dpr,
        vx: 0, vy: 0,
        rel: 0.3 * (Data.H - 1 - p[1]) / Data.H + 0.08 * hash(p[0] * 7 + 1, p[1] * 13 + 5),
        margin: 3 + hash(p[0] + 11, p[1] + 17) * 30,
        jx: (hash(p[0] + 3, p[1] + 29) - 0.5) * 50,
        jy: (hash(p[1] + 5, p[0] + 41) - 0.5) * 50,
        c: p[4], alive: true
    }));
}
function stepParticles(particles, dt, elapsed, clock, width, height) {
    const e = drainEdge(height, elapsed);
    for (const q of particles) {
        if (!q.alive) continue;
        if (elapsed < q.rel && q.y < e.b + waveAt(q.x, e.amp, clock) + q.margin)
            q.rel = elapsed;
        const tau = elapsed - q.rel;
        if (tau < 0) continue;
        const drift = 40 + 700 * tau * tau, swirl = 90;
        const fx = q.jx + swirl * (Math.sin(q.y * 0.012 + clock * 1.9)
                                  + 0.5 * Math.sin((q.x + q.y) * 0.008 - clock * 2.6));
        const fy = drift + q.jy + swirl * (Math.cos(q.x * 0.011 - clock * 1.6)
                                          + 0.5 * Math.cos((q.x - q.y) * 0.009 + clock * 2.2));
        const k = Math.min(1, dt * 5);
        q.vx += (fx - q.vx) * k;
        q.vy += (fy - q.vy) * k;
        q.x += q.vx * dt;
        q.y += q.vy * dt;
        const b = e.b + waveAt(q.x, e.amp, clock);
        if (q.y < b + q.margin) q.y = b + q.margin;
        if (q.x < -30 || q.x > width + 30 || q.y < -30 || q.y > height + 30) q.alive = false;
    }
}
function drawIntro(ctx, age, logo, ox, oy) {
    for (const row of ROWS) {
        for (let i = 0; i < row.length; i++) {
            const p = row[i], x = introX(p, i, row, age, logo);
            if (x >= logo.dw) continue;
            ctx.fillStyle = Data.HOT[p[4]];
            ctx.fillRect(x - ox, logo.y + p[1] - oy, 1, 1);
        }
    }
}
var RGB = [[176,30,120], [236,42,85], [255,106,42], [255,182,46]];
// Every non-lake colour shift is quantised by the approved 8 Hz/6 Hz motion.
// Calculate these finite tables once, rather than 3,475 cosines and Hot gradients
// on every paint. Runtime buffers and the image are retained by each renderer.
var BREATH_COLORS = Array.from({ length: 13 }, (_, frame) => {
    const du = 0.12 * swell(frame / 8, 1.6, 1.2);
    return new Uint8Array(PIXELS.map(p => hotIndex(p[0], p[1], du)));
});
var WIND_COLORS = Array.from({ length: 3 }, (_, dx) =>
    new Uint8Array(PIXELS.map(p => dx ? hotIndex(p[0], p[1], 0.06 * dx) : p[4])));
function createIdleRaster(ctx) {
    return { image: ctx.createImageData(Data.W + 4, Data.H + 6),
        colors: new Uint8Array(PIXELS.length),
        positions: new Uint16Array(PIXELS.length),
        previousColors: new Int8Array(PIXELS.length).fill(-1),
        previousPositions: new Uint16Array(PIXELS.length),
        letterDy: new Int8Array(5), letterFrame: new Uint8Array(5) };
}
function sampleIdleRaster(mode, age, raster) {
    const lakeFrame = frameAt(age, 15), dotDy = HOP[frameAt(age, 8) % HOP.length];
    const windFrame = WIND[frameAt(age, 6) % WIND.length];
    if (mode === "letters" || mode === "breath") {
        for (let g = 0; g < 5; g++) {
            const tl = mode === "letters" ? (age + g * 0.27) % 1.6 : age % 1.6;
            raster.letterDy[g] = tl < .8 ? 0 : -1;
            raster.letterFrame[g] = frameAt(tl, 8);
        }
    }
    for (let i = 0; i < PIXELS.length; i++) {
        const p = PIXELS[i];
        let dx = 0, dy = 0, color = p[4];
        if (mode === "lake") {
            if (hash(p[0] * 97 + p[1], lakeFrame) < 0.3) {
                const up = hash(p[1] * 131 + p[0], lakeFrame + 7) < 0.5;
                color = up ? (color < Data.HOT.length - 1 ? color + 1 : color - 1) : (color > 0 ? color - 1 : color + 1);
            }
        } else if (mode === "letters" || mode === "breath") {
            const g = p[2] === 5 ? 4 : p[2];
            dy = raster.letterDy[g];
            color = BREATH_COLORS[raster.letterFrame[g]][i];
            if (mode === "breath" && p[2] === 5) dy += dotDy;
        } else if (mode === "wind") {
            dx = windFrame[Math.min(2, Math.floor(p[1] / 16))];
            color = WIND_COLORS[dx][i];
            if (p[2] === 5) dy = dotDy;
        }
        raster.colors[i] = color;
        raster.positions[i] = (p[1] + dy + 4) * (Data.W + 4) + p[0] + dx + 2;
    }
}
function drawIdle(ctx, mode, age, logo, ox, oy, raster) {
    const image = raster.image, bytes = image.data;
    sampleIdleRaster(mode, age, raster);
    // The four approved idles preserve distinct pixel positions: letters translate
    // as units; wind shifts whole rows; the dot only moves away from the stem.
    // Clear old positions first, then update only changed pixels in the retained image.
    for (let i = 0; i < PIXELS.length; i++) {
        if (raster.previousPositions[i] !== raster.positions[i])
            bytes[raster.previousPositions[i] * 4 + 3] = 0;
    }
    for (let i = 0; i < PIXELS.length; i++) {
        if (raster.previousPositions[i] === raster.positions[i] && raster.previousColors[i] === raster.colors[i]) continue;
        const n = raster.positions[i] * 4, rgb = RGB[raster.colors[i]];
        bytes[n] = rgb[0]; bytes[n + 1] = rgb[1]; bytes[n + 2] = rgb[2]; bytes[n + 3] = 255;
        raster.previousPositions[i] = raster.positions[i];
        raster.previousColors[i] = raster.colors[i];
    }
    // Qt Canvas requires the explicit dirty rectangle to submit this image.
    ctx.putImageData(image, logo.x - 2 - ox, logo.y - 4 - oy, 0, 0, image.width, image.height);
    if (mode !== "lake") return;
    ctx.fillStyle = "#ffffff";
    for (const spark of sparks(age)) {
        const x = logo.x + spark[0] - ox, y = logo.y + spark[1] - oy;
        ctx.globalAlpha = 1;
        ctx.fillRect(x, y, 1, 1);
        for (let d = 1; d <= spark[2]; d++) {
            ctx.globalAlpha = d === 1 ? 0.9 : 0.55;
            ctx.fillRect(x + d, y, 1, 1);
            ctx.fillRect(x - d, y, 1, 1);
            ctx.fillRect(x, y + d, 1, 1);
            ctx.fillRect(x, y - d, 1, 1);
        }
    }
    ctx.globalAlpha = 1;
}
function drawParticles(ctx, particles, logo, ox, oy) {
    for (const q of particles) {
        if (!q.alive) continue;
        ctx.fillStyle = Data.HOT[q.c];
        ctx.fillRect(Math.round(q.x * logo.dpr - 0.5) - ox, Math.round(q.y * logo.dpr - 0.5) - oy, 1, 1);
    }
}
