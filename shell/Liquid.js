.pragma library
// Motion of the launcher and the left bar islands, docs/mockups/liquid-glass/launcher.js
// line for line: tweens, springs and the liquid body (a rectangle whose four sides are
// springs, so a tile, a row and a word are one bubble changing shape while it flows).
// Plain objects mutated in place; the owner runs them from a FrameAnimation and bumps a
// counter so QML bindings see the new values. Time is Date.now() in milliseconds.

const BUBBLE = Object.freeze({
    pad: 6,
    restUnion: 18,
    motionUnion: 30,
    stiffness: 620,
    hideDelay: 300,
    thickness: 9,
    edge: 18,
    dispersion: 16,
    refraction: 14,
    rimLight: .6,
    rimWidth: 3.5
});

function clamp(x, a, b) {
    return Math.max(a, Math.min(b, x));
}
function mix(a, b, t) {
    return a + (b - a) * t;
}
function smooth(t) {
    t = clamp(t, 0, 1);
    return t * t * (3 - 2 * t);
}
function easeOut(t) {
    return 1 - Math.pow(1 - t, 3);
}
function tween(x) {
    return {
        x: x,
        from: x,
        target: x,
        start: 0,
        duration: 160
    };
}
function aim(t, target, duration, now) {
    if (t.target === target)
        return false;
    t.from = t.x;
    t.target = target;
    t.start = now;
    t.duration = duration;
    return true;
}
function tick(t, now, easing) {
    const f = clamp((now - t.start) / t.duration, 0, 1);
    t.x = mix(t.from, t.target, (easing || easeOut)(f));
    return f < 1 && t.from !== t.target;
}
function spring(x) {
    return {
        x: x,
        v: 0,
        target: x
    };
}
function step(s, dt, k, zeta) {
    k = k || BUBBLE.stiffness;
    zeta = zeta || .66;
    const c = 2 * zeta * Math.sqrt(k), n = Math.max(1, Math.ceil(dt / .004)), h = dt / n;
    for (let i = 0; i < n; i++) {
        s.v += (k * (s.target - s.x) - c * s.v) * h;
        s.x += s.v * h;
    }
    if (Math.abs(s.x - s.target) < .05 && Math.abs(s.v) < .8) {
        s.x = s.target;
        s.v = 0;
        return false;
    }
    return true;
}
// One liquid body per role; velocity survives retargeting.
function liquid() {
    return {
        x: spring(0),
        y: spring(0),
        w: spring(0),
        h: spring(0),
        alpha: tween(0),
        radius: 16,
        hideAt: 0,
        placed: false
    };
}
function place(b, rect, radius, now) {
    if (!b.placed || b.alpha.x < .001) {
        b.x.x = rect[0];
        b.y.x = rect[1];
        b.w.x = rect[2];
        b.h.x = rect[3];
        b.x.v = b.y.v = b.w.v = b.h.v = 0;
        b.placed = true;
    }
    const moved = b.x.target !== rect[0] || b.y.target !== rect[1] || b.w.target !== rect[2] || b.h.target !== rect[3];
    b.x.target = rect[0];
    b.y.target = rect[1];
    b.w.target = rect[2];
    b.h.target = rect[3];
    b.radius = radius;
    b.hideAt = 0;
    return aim(b.alpha, 1, 160, now) || moved;
}
// Wait (the pointer only wandered off), then melt in place.
function dissolve(b, now) {
    if (!b.hideAt && b.alpha.target !== 0)
        b.hideAt = now + BUBBLE.hideDelay;
}
// The target is gone (filter, mode, page): the bubble sinks with it at once.
function vanish(b, now) {
    b.hideAt = 0;
    return aim(b.alpha, 0, 110, now);
}
// Advances alpha, the pending dissolve and the four sides. True while still moving.
function advance(b, now, dt) {
    let active = false;
    if (b.hideAt) {
        if (now >= b.hideAt) {
            b.hideAt = 0;
            aim(b.alpha, 0, 220, now);
        }
        active = true;
    }
    active = tick(b.alpha, now, smooth) || active;
    if (b.alpha.x > .001)
        for (const s of [b.x, b.y, b.w, b.h])
            active = step(s, dt) || active;
    return active;
}
// The body as a shader drop: a tail behind the motion, a nose in front; it stretches, it
// does not teleport. `union` is the smooth union with other liquid members (3: distinct
// bodies never fuse, 24.09). Null while invisible.
function drop(b, dx, dy) {
    const a = smooth(b.alpha.x);
    if (a < .001)
        return null;
    const vx = b.x.v + b.w.v / 2, vy = b.y.v + b.h.v / 2;
    const sx = Math.min(28, Math.abs(vx) * .035), sy = Math.min(16, Math.abs(vy) * .035);
    const x = b.x.x - (vx < 0 ? sx : sx * .35) + (dx || 0), y = b.y.x - (vy < 0 ? sy : sy * .35) + (dy || 0);
    const w = b.w.x + sx * 1.35, h = b.h.x + sy * 1.35;
    return {
        rect: Qt.vector4d(x, y, w, h),
        params: Qt.vector4d(Math.min(b.radius, Math.min(w, h) / 2), 3, 1, a)
    };
}
function pad(r, p) {
    return [r[0] - p, r[1] - p, r[2] + 2 * p, r[3] + 2 * p];
}
// dual() by coverage (LauncherBody.coverOf, 27.09): how much of r = [x, y, w, h] the body's
// drop covers, 0..1 — the overlap on each axis against the smaller of the two, eased; times the
// body's alpha, so it also melts with it. What a drop covers is drawn on the glass by this much
// and under it by the rest: its rim then never bends and splits what it flows over or away from.
function cover(b, r) {
    const d = drop(b, 0, 0);
    if (!d || r[2] <= 0 || r[3] <= 0)
        return 0;
    const q = d.rect;
    const ox = Math.max(0, Math.min(r[0] + r[2], q.x + q.z) - Math.max(r[0], q.x)) / Math.max(1, Math.min(r[2], q.z));
    const oy = Math.max(0, Math.min(r[1] + r[3], q.y + q.w) - Math.max(r[1], q.y)) / Math.max(1, Math.min(r[3], q.w));
    return smooth(b.alpha.x) * smooth(Math.min(1, ox) * Math.min(1, oy));
}
// Shadow and flow margin around a glass shape: glassRect() in launcher.js.
function glassPad() {
    return Math.ceil(28 * 1.8 + 5 + BUBBLE.motionUnion + 6);
}
// regularPalette(avg) > .5 in launcher.js, from a probe's light value.
function light(value) {
    return value > .5;
}
