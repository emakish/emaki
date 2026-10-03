#!/usr/bin/env python3
"""Animation frames for the train sprite: the source train keeps its body; the locomotive's
three driving wheels, the coupling rod, the main rod and the crosshead are drawn here in FRAMES
positions per turn of a driving wheel; the small wheels get a turning bolt.

  python3 art/wallpaper/train-anim.py art/wallpaper/train.png art/wallpaper/train-frames.png \
      art/wallpaper/train-anim.js art/wallpaper/train.json

train-frames.png stacks the frames vertically (indexed, the palette plus a transparent index).
train-anim.js holds what a player needs: frame size and count, the driving wheel radius (frame
= travelled distance / (2 pi R) * FRAMES), the chimney and cylinder positions, the vehicles'
column ranges and the palette. Wheel positions were measured on the 2508x627 source image and
scaled to the 372x36 sprite."""
import json
import math
import sys

import numpy as np
from PIL import Image

FRAMES = 16
WHEEL_BOTTOM_ROW = 818                                    # in ring.png, where the wheels touch the rails
SPEED = 60                                                # ring pixels per second
DRIVERS = [(298.8, 29.7), (315.5, 29.7), (333.7, 29.7)]   # centres, sprite pixels
R = 6.0                                                   # driving wheel radius
CRANK = 3.0                                               # crank pin radius
MAIN = 1                                                  # the main rod drives the middle wheel
ROD_LENGTH = 26.0
SMALL = [(14.3, 32.0), (26.4, 32.0), (62.2, 32.0), (74.5, 32.0), (99.0, 32.0), (111.0, 32.0),
         (146.5, 32.0), (158.8, 32.0), (183.1, 32.0), (195.7, 32.0), (225.2, 32.0), (237.5, 32.0),
         (259.0, 31.5), (275.9, 31.5), (346.0, 32.4), (357.8, 32.4)]
SMALL_R = 3.0
# palette indices (palette-64.gpl)
RIM_SHADOW, RIM, RIM_LIT, FACE = 4, 10, 19, 2
SPOKE, HUB, HUB_LIT = 14, 17, 61
ROD, ROD_LOW, PIN = 51, 27, 63
BOLT = 52


def disc(cx, cy, r):
    """Pixels whose centres lie inside the circle."""
    for y in range(math.floor(cy - r), math.ceil(cy + r) + 1):
        for x in range(math.floor(cx - r), math.ceil(cx + r) + 1):
            if (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= r * r:
                yield x, y


def put(img, x, y, c):
    if 0 <= y < img.shape[0] and 0 <= x < img.shape[1]:
        img[y, x] = c


def driver(img, cx, cy, angle):
    """A spoked wheel: lit rim towards the low sun on the right, eight spokes, a hub."""
    for x, y in disc(cx, cy, R + 0.5):
        dx, dy = x + 0.5 - cx, y + 0.5 - cy
        d = math.hypot(dx, dy)
        if d >= R - 0.9:
            light = (dx - dy * 0.5) / max(d, 1e-6)        # +1 towards the right and up
            put(img, x, y, RIM_LIT if light > 0.55 else RIM_SHADOW if light < -0.55 else RIM)
        elif d <= 1.2:
            put(img, x, y, HUB)
        else:
            a = math.atan2(dy, dx) - angle
            off = (a + math.pi / 8) % (math.pi / 4) - math.pi / 8
            put(img, x, y, SPOKE if abs(d * math.sin(off)) < 0.55 else FACE)
    put(img, round(cx - 0.5), round(cy - 0.5), HUB_LIT)


def line(img, x0, y0, x1, y1, c):
    n = max(abs(x1 - x0), abs(y1 - y0), 1)
    for i in range(int(n) + 1):
        put(img, round(x0 + (x1 - x0) * i / n - 0.5), round(y0 + (y1 - y0) * i / n - 0.5), c)


def frame(base, k):
    img = base.copy()
    angle = 2 * math.pi * k / FRAMES          # clockwise on screen: the top moves forward (right)
    for cx, cy in DRIVERS:
        driver(img, cx, cy, angle)
    pins = [(cx + CRANK * math.cos(angle + math.pi), cy + CRANK * math.sin(angle + math.pi))
            for cx, cy in DRIVERS]
    # coupling rod: two rows, the lit one on top
    (x0, y0), (x1, _) = pins[0], pins[-1]
    for x in range(round(x0 - 1), round(x1 + 1)):
        put(img, x, round(y0 - 0.5), ROD)
        put(img, x, round(y0 + 0.5), ROD_LOW)
    # main rod from the middle pin forward to the crosshead, which slides at the axle height
    px, py = pins[MAIN]
    cy = DRIVERS[MAIN][1]
    hx = px + math.sqrt(ROD_LENGTH ** 2 - (py - cy) ** 2)
    line(img, px, py, hx, cy, ROD_LOW)
    for dx in (0, 1):
        for dy in (-1, 0):
            put(img, round(hx - 0.5) + dx, round(cy - 0.5) + dy, ROD)
    for x, y in pins:
        put(img, round(x - 0.5), round(y - 0.5), PIN)
    # small wheels: one bolt turning at radius 2 (they turn R / SMALL_R times as fast)
    for cx, cy in SMALL:
        a = angle * R / SMALL_R
        put(img, round(cx + 2 * math.cos(a) - 0.5), round(cy + 2 * math.sin(a) - 0.5), BOLT)
    return img


def vehicles(alpha):
    """Column ranges of the cars, tender and locomotive, split at the thin couplings."""
    body = alpha.sum(0) > 4
    runs, start = [], None
    for x, b in enumerate(list(body) + [False]):
        if b and start is None:
            start = x
        elif not b and start is not None:
            if x - start > 20:
                runs.append([start, x])
            start = None
    for left, right in zip(runs, runs[1:]):              # couplings go to the halfway point
        left[1] = right[0] = (left[1] + right[0]) // 2
    runs[0][0], runs[-1][1] = 0, len(body)
    return runs


def main():
    src, sheet, meta = sys.argv[1:4]
    im = Image.open(src)
    clear = im.info['transparency']
    pal = np.array(im.getpalette()).reshape(-1, 3)[:clear]
    base = np.array(im)
    # take out the painted driving wheels and the painted rods
    for cx, cy in DRIVERS:
        for x, y in disc(cx, cy, R + 0.8):
            put(base, x, y, clear)
    light = np.where(base == clear, 0, pal.mean(1)[np.minimum(base, clear - 1)])
    for y in range(27, 33):
        for x in range(288, 346):
            if light[y, x] > 110:
                base[y, x] = clear
    frames = [frame(base, k) for k in range(FRAMES)]
    out = Image.fromarray(np.concatenate(frames), 'P')
    out.putpalette(pal.reshape(-1).tolist() + [0, 0, 0])
    out.save(sheet, transparency=clear, optimize=True)

    alpha = np.array(im) != clear
    top = [y for y in range(alpha.shape[0]) if alpha[y, 340:].any()][0]
    chimney = int(np.flatnonzero(alpha[top, 340:]).mean()) + 340
    info = dict(width=base.shape[1], height=base.shape[0], frames=FRAMES, wheelRadius=R,
                chimney=[chimney, top], cylinder=[round(DRIVERS[-1][0] + ROD_LENGTH - 2), 30],
                vehicles=vehicles(alpha), clear=int(clear), palette=pal.tolist())
    with open(meta, 'w') as f:
        f.write('// Generated by art/wallpaper/train-anim.py\nconst TRAIN = ' + json.dumps(info) + ';\n')
    if len(sys.argv) > 4:                     # the same numbers for niri-emaki, without the palette
        engine = {k: v for k, v in info.items() if k != 'palette'}
        engine.update(wheelBottomRow=WHEEL_BOTTOM_ROW, speed=SPEED)
        with open(sys.argv[4], 'w') as f:
            json.dump(engine, f, indent=1)
            f.write('\n')
    print(f'{sheet}: {FRAMES} frames; vehicles {info["vehicles"]}; chimney {info["chimney"]}')


if __name__ == '__main__':
    main()
