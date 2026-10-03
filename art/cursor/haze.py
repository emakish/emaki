"""Emaki cursor art: the Haze set (picked 2026-10-01, arrow 7 of the mockup
docs/mockups/cursor/cursor.html; the full set was approved on docs/mockups/cursor/set.html).

Every cursor is drawn on its own cell grid: warm white inside, a dark one-cell outline, then two
rings of pink glow around everything. `cursor(name, frame)` returns ([(x, y, rgba), ...], hotspot
moved to (0, 0)). The mockup page is the same art in JavaScript; this file is the source for the
installed theme (scripts/build-cursors)."""
import math

DARK = (0x14, 0x10, 0x0D, 255)
LIGHT = (0xF2, 0xE6, 0xD8, 255)
HOT = [(0xFF, 0xB6, 0x2E, 255), (0xFF, 0x6A, 0x2A, 255), (0xEC, 0x2A, 0x55, 255), (0xB0, 0x1E, 0x78, 255)]
HALO1 = (0xEC, 0x2A, 0x55, 153)    # rgba(236, 42, 85, .6): the ring touching the cursor
HALO2 = (0xB0, 0x1E, 0x78, 61)     # rgba(176, 30, 120, .24): the outer ring
FRAMES = 8                          # spinner frames for wait and progress
FRAME_MS = 90


def in_poly(pts):
    """A polygon (corners in cell units) as a cell test; a cell on a 45° edge belongs to it."""
    def test(x, y):
        px, py, inside = x + 0.49, y + 0.5, False
        j = len(pts) - 1
        for i, (xi, yi) in enumerate(pts):
            xj, yj = pts[j]
            if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside
    return test


def shape(w, h, test, paint=lambda x, y: LIGHT, dx=0, dy=0):
    """Cells of the shape `test` inside a w×h box: edge cells dark, inner cells painted."""
    def on(x, y):
        return 0 <= x < w and 0 <= y < h and test(x, y)
    out = []
    for y in range(h):
        for x in range(w):
            if on(x, y):
                edge = not (on(x - 1, y) and on(x + 1, y) and on(x, y - 1) and on(x, y + 1))
                out.append((x + dx, y + dy, DARK if edge else paint(x, y)))
    return out


def ascii(rows):
    """o = dark outline, w = warm white, anything else = empty."""
    colours = {'o': DARK, 'w': LIGHT}
    return [(x, y, colours[c]) for y, row in enumerate(rows) for x, c in enumerate(row) if c in colours]


def spinner(c, r_out, r_in, frame, dx=0, dy=0):
    """A white ring around cell centre (c, c) with a hot tail running round it."""
    n = 2 * math.ceil(r_out) + 1
    head = frame / FRAMES * 2 * math.pi

    def ring(x, y):
        return r_in <= math.hypot(x - c, y - c) <= r_out

    def paint(x, y):
        behind = (head - math.atan2(y - c, x - c)) % (2 * math.pi)
        for i, limit in enumerate((0.5, 1.0, 1.5, 2.0)):
            if behind < limit:
                return HOT[i]
        return LIGHT
    return shape(n, n, ring, paint, dx, dy)


def double(length, horizontal):
    """←→ arrow, 9 cells thick (↑↓ when not horizontal)."""
    c, end = 4, length - 1

    def test(x, y):
        return ((5 <= x <= end - 5 and abs(y - c) <= 1) or (x <= 4 and abs(y - c) <= x)
                or (x >= end - 4 and abs(y - c) <= end - x))
    return shape(length, 9, test) if horizontal else shape(9, length, lambda x, y: test(y, x))


def diagonal(mirror):
    """↖↘ arrow in a 17×17 box (↗↙ when mirrored)."""
    n = 16

    def test(x0, y):
        x = n - x0 if mirror else x0
        return x + y <= 6 or (n - x) + (n - y) <= 6 or (abs(x - y) <= 2 and 6 < x + y < 2 * n - 6)
    return shape(n + 1, n + 1, test)


def move(x, y):
    c = 11
    ax, ay = abs(x - c), abs(y - c)
    return ((ax <= 1 and 4 <= y <= 18) or (ay <= 1 and 4 <= x <= 18) or (y <= 3 and ax <= y)
            or (y >= 19 and ax <= 22 - y) or (x <= 3 and ay <= x) or (x >= 19 and ay <= 22 - x))


def not_allowed(x, y):
    r = math.hypot(x - 8, y - 8)
    return 5.2 <= r <= 8.2 or (r < 6 and abs(x - y) <= 1)


def crosshair(x, y):
    return (abs(x - 9) <= 1 and abs(y - 9) >= 3) or (abs(y - 9) <= 1 and abs(x - 9) >= 3)


DART = in_poly([(0, 0), (12.5, 12.5), (5, 10), (0, 18)])

HAND = [
    ".....oo.........", "....owwo........", "....owwo........", "....owwo........", "....owwo........",
    "....owwooo......", "....owwowwooo...", "....owwowwowwoo.", ".oo.owwowwowwowo", "owwoowwwwwwwwowo",
    "owwwowwwwwwwwwwo", ".owwwwwwwwwwwwwo", "..owwwwwwwwwwwwo", "..owwwwwwwwwwwo.", "...owwwwwwwwwwo.",
    "...owwwwwwwwwo..", "....owwwwwwwwo..", "....owwwwwwwwo..", "....oooooooooo..",
]
OPEN = [
    ".......oooo......", ".......owwoooo...", "....oooowwowwo...", "....owwowwowwoooo", "....owwowwowwowwo",
    "....owwowwowwowwo", "....owwowwowwowwo", "....owwowwowwowwo", "ooo.owwowwowwowwo", "owwoowwwwwwwwwwwo",
    "owwwowwwwwwwwwwwo", ".owwwwwwwwwwwwwwo", "..owwwwwwwwwwwwwo", "..owwwwwwwwwwwwo.", "...owwwwwwwwwwwo.",
    "...owwwwwwwwwwo..", "....owwwwwwwwwo..", "....ooooooooooo..",
]
FIST = [
    "....ooooooooooooo", "....owwowwowwowwo", ".oo.owwowwowwowwo", "owwoowwwwwwwwwwwo", "owwwowwwwwwwwwwwo",
    ".owwwwwwwwwwwwwwo", "..owwwwwwwwwwwwwo", "..owwwwwwwwwwwwo.", "...owwwwwwwwwwwo.", "...owwwwwwwwwwo..",
    "....owwwwwwwwwo..", "....ooooooooooo..",
]
IBEAM = ["ooooooo", "owwwwwo", "ooowooo"] + ["..owo.."] * 12 + ["ooowooo", "owwwwwo", "ooooooo"]

# name: (hotspot cell, animated, cells for a frame)
SET = {
    'default': ((0, 0), False, lambda f: shape(13, 18, DART)),
    'pointer': ((5, 0), False, lambda f: ascii(HAND)),
    'text': ((3, 9), False, lambda f: ascii(IBEAM)),
    'wait': ((8, 8), True, lambda f: spinner(8, 8.2, 4.4, f)),
    'progress': ((0, 0), True, lambda f: shape(13, 18, DART) + spinner(5, 5.2, 2.4, f, 9, 11)),
    'not-allowed': ((8, 8), False, lambda f: shape(17, 17, not_allowed)),
    'crosshair': ((9, 9), False, lambda f: shape(19, 19, crosshair)),
    'move': ((11, 11), False, lambda f: shape(23, 23, move)),
    'grab': ((8, 8), False, lambda f: ascii(OPEN)),
    'grabbing': ((8, 5), False, lambda f: ascii(FIST)),
    'ew-resize': ((11, 4), False, lambda f: double(23, True)),
    'ns-resize': ((4, 11), False, lambda f: double(23, False)),
    'nwse-resize': ((8, 8), False, lambda f: diagonal(False)),
    'nesw-resize': ((8, 8), False, lambda f: diagonal(True)),
}


def haze(cells, hot):
    """Later cells win; two glow rings around all of them; hotspot moved to (0, 0)."""
    top = {}
    for x, y, colour in cells:
        top[(x, y)] = colour
    near = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    one = {(x + dx, y + dy) for x, y in top for dx, dy in near} - top.keys()
    two = {(x + dx, y + dy) for x, y in one for dx, dy in near} - top.keys() - one
    out = {**{p: HALO2 for p in two}, **{p: HALO1 for p in one}, **top}
    hx, hy = hot
    return sorted((x - hx, y - hy, colour) for (x, y), colour in out.items())


def cursor(name, frame=0):
    hot, animated, cells = SET[name]
    return haze(cells(frame if animated else 0), hot)


def frames(name):
    return FRAMES if SET[name][1] else 1
