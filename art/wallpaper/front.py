#!/usr/bin/env python3
"""The layer in front of the train: everything that hides the rails (spruces, birches, poles,
the Japanese house). A player draws ring.png, then the train and its smoke, then this layer.

  python3 art/wallpaper/front.py art/wallpaper/ring.png art/wallpaper/ring-front.png

1. Rails: a column shows the rails when, near the rail rows, a dark row (the rail) has a row at
   least 45 lighter right above it. Columns without that are hidden by something in front.
2. Each hidden run seeds a region at the rail rows; the region grows upwards over pixels close
   to the object's main colours, touching the row below, never more than SPREAD columns beyond
   the run (POLE_SPREAD for a pole, so its crossbar comes in), so it follows the object's
   silhouette instead of spilling into the forest behind.
3. The Japanese house with its pine is too big and too patterned for that (its roof tiles look
   like rails): it is filled whole, everything not light that connects to the rail rows.
Output: an indexed PNG with the ring's palette plus a transparent index; only the objects are
opaque. Every spot was checked by eye with the train composited behind it."""
import sys

import numpy as np
from PIL import Image

from pixelize import srgb_to_oklab

RAIL_ROWS = range(805, 817)          # where the rail head can be (ring.png rows)
SEED_ROWS = range(806, 818)
BOTTOM = 821                         # nothing below the wheels matters
REACH = 110                          # rows above the rails an object may hide (train 36, smoke above)
COLOUR = 0.05                        # OKLab distance to the seed colours that still counts
SPREAD = 1                           # columns an object may reach beyond the hidden rails
POLE_WIDTH, POLE_SPREAD = 4, 5       # a run this narrow is a telegraph pole; its crossbar is wider
IGNORE = [(241, 258)]                # runs where the rails change style, nothing stands there
SKY_LIGHT = 140                      # mean RGB from which the sky and lake behind the house start
# (first column, last column, top row): big objects drawn whole, see building()
TALL = [(16738, 17446, 690)]         # the Japanese house and its pine: they also hide the smoke


def rails_visible(light):
    seen = np.zeros(light.shape[1], bool)
    for r in RAIL_ROWS:
        under = np.minimum(np.minimum(light[r + 1], light[r + 2]), light[r + 3])
        seen |= (light[r] - under >= 45) & (under < 35)
    return seen


def hidden_runs(seen):
    runs, start = [], None
    for x, s in enumerate(list(seen) + [True]):
        if not s and start is None:
            start = x
        elif s and start is not None:
            runs.append((start, x))
            start = None
    return runs


def grow(idx, dist, a, b, reach):
    """Region of the object hiding columns a..b-1."""
    w = idx.shape[1]
    seeds = idx[SEED_ROWS.start:SEED_ROWS.stop, a:b].ravel()
    counts = np.bincount(seeds, minlength=64)
    colours = np.flatnonzero(counts >= max(2, 0.08 * len(seeds)))   # the object's main colours
    near = dist[:, colours].min(1) <= COLOUR            # palette entries close to the object
    mask = np.zeros_like(idx, bool)
    spread = POLE_SPREAD if b - a <= POLE_WIDTH else SPREAD        # poles: let the crossbar in
    left, right = max(a - spread, 0), min(b + spread, w)
    for r in range(BOTTOM - 1, RAIL_ROWS.start - reach, -1):
        row = np.zeros(w, bool)
        if r >= SEED_ROWS.start:
            row[a:b] = near[idx[r, a:b]]
        else:
            cand = np.zeros(w, bool)
            cand[left:right] = near[idx[r, left:right]]
            below = mask[r + 1]
            reach_below = below | np.r_[below[1:], False] | np.r_[False, below[:-1]]
            x = left                                    # keep runs that touch the row below
            while x < right:
                if cand[x]:
                    e = x
                    while e < right and cand[e]:
                        e += 1
                    if reach_below[x:e].any():
                        row[x:e] = True
                    x = e
                else:
                    x += 1
        if not row.any():
            break
        mask[r] = row
    return mask


def flood(seed, allowed):
    """Grow seed over allowed pixels (4-connected) until it stops changing."""
    region = seed & allowed
    while True:
        grown = region.copy()
        grown[1:] |= region[:-1]
        grown[:-1] |= region[1:]
        grown[:, 1:] |= region[:, :-1]
        grown[:, :-1] |= region[:, 1:]
        grown &= allowed
        if (grown == region).all():
            return region
        region = grown


def building(light, seed_row):
    """A big object in front (the house with its pine): everything not light that connects to
    the rail rows, plus light details enclosed by it (lanterns, lit edges). The light sky and
    lake around it stay behind."""
    sky = light >= SKY_LIGHT
    seed = np.zeros_like(sky)
    seed[seed_row:] = True
    body = flood(seed, ~sky)
    border = np.zeros_like(sky)
    border[0], border[:, 0], border[:, -1] = True, True, True
    outside = flood(border, sky)
    return body | (sky & ~outside)


def main():
    src, dst = sys.argv[1:3]
    im = Image.open(src)
    pal = np.array(im.getpalette()[:192]).reshape(64, 3)
    lab = srgb_to_oklab(pal.astype(float))
    dist = np.sqrt(((lab[:, None] - lab[None]) ** 2).sum(2))
    idx = np.asarray(im)
    light = pal.mean(1)[idx]
    runs = hidden_runs(rails_visible(light))
    mask = np.zeros_like(idx, bool)
    for a, b in runs:
        if (a, b) not in IGNORE and not any(x0 <= a < x1 for x0, x1, _ in TALL):
            mask |= grow(idx, dist, a, b, REACH)
    for x0, x1, top in TALL:
        mask[top:BOTTOM, x0:x1] |= building(light[top:BOTTOM, x0:x1], SEED_ROWS.start - top)
    out = np.where(mask, idx, 64).astype(np.uint8)
    img = Image.fromarray(out, 'P')
    img.putpalette(pal.reshape(-1).tolist() + [0, 0, 0])
    img.save(dst, transparency=64, optimize=True)
    print(f'{dst}: {len(runs)} hidden runs, {mask.sum()} pixels')


if __name__ == '__main__':
    main()
