#!/usr/bin/env python3
"""Hand fixes on the pixelized ring, kept as code so the ring can be rebuilt from loop-cells.png.

  python3 art/wallpaper/touchup.py art/wallpaper/loop-cells.png art/wallpaper/ring.png

1. The painted train and its smoke in panel 01 are replaced by landscape from further along the
   ring (the train becomes an animated sprite); the patch is cut along least-difference seams.
2. Foreground spruces whose tops cross the railway were drawn behind the rails; the rails are
   painted over with the tree, its silhouette interpolated between the rows above and below.
3. The sky is extended upwards by SKY rows, so the 16:9 picture fills a 16:10 screen: an ordered
   dither from the top row's colour towards a darker magenta.
Input and output are indexed PNGs with the same palette."""
import sys

import numpy as np
from PIL import Image

from pixelize import srgb_to_oklab
from stitch import seam

# Train patches, applied in order; dx = where the donor lies (found by a search over the ring for
# the lowest seam difference and the closest colour histogram). The first takes forest, rails
# and poles from just right of the train; the second covers a lake glimpse the first brought in.
TRAIN = [dict(x0=592, x1=1012, y0=578, y1=714, dx=462, margin=12),
         dict(x0=700, x1=1036, y0=556, y1=636, dx=-404, margin=10)]
# (centre column, first and last rail row there, brightest mean RGB that still counts as the tree)
TREES = [(5668, 690, 697, 100)]
SKY = 120
SKY_TOP = 14                 # palette index (#8d1d44 in the current palette) the zenith fades to
SKY_RAMP = (11, 14, 16, 17, 21, 23, 24, 29, 33)
BAYER = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]]) / 16 - 0.5


def patch(idx, dist, x0, x1, y0, y1, dx, margin):
    src = np.roll(idx, -dx, axis=1)
    cost = lambda ys, xs: dist[idx[ys, xs], src[ys, xs]]
    rows, cols = slice(y0 - margin, y1 + margin), slice(x0 - margin, x1 + margin)
    left = seam(cost(rows, slice(x0 - margin, x0 + margin))) + x0 - margin
    right = seam(cost(rows, slice(x1 - margin, x1 + margin))) + x1 - margin
    top = seam(cost(slice(y0 - margin, y0 + margin), cols).T) + y0 - margin
    bottom = seam(cost(slice(y1 - margin, y1 + margin), cols).T) + y1 - margin
    ys, xs = np.mgrid[rows, cols]
    inside = ((xs >= left[ys - rows.start]) & (xs < right[ys - rows.start])
              & (ys >= top[xs - cols.start]) & (ys < bottom[xs - cols.start]))
    idx[ys[inside], xs[inside]] = src[ys[inside], xs[inside]]


def run_at(dark_row, x):
    """Columns of the contiguous dark run in one row that contains or is nearest to x."""
    near = np.flatnonzero(dark_row[x - 4:x + 5])
    if not len(near):
        return None
    x = x - 4 + near[np.argmin(abs(near - 4))]
    a = b = x
    while a > 0 and dark_row[a - 1]:
        a -= 1
    while b + 1 < len(dark_row) and dark_row[b + 1]:
        b += 1
    return a, b


def span(dark, rows, xc):
    """Widest extent of the tree's dark runs over a few rows (lit branches break single rows)."""
    runs = [run for run in (run_at(dark[r], xc) for r in rows) if run]
    return (min(a for a, _ in runs), max(b for _, b in runs)) if runs else (xc, xc)


def tree_over_rails(idx, light, xc, top, bottom, limit):
    """Paint rows top-1..bottom+1 (the rails and their edges) with the tree in column range
    interpolated between its extent above and below; the upper half copies the tree's texture
    from just above the rails, the lower half from just below."""
    dark = light <= limit
    above = span(dark, range(top - 4, top - 1), xc)
    below = span(dark, range(bottom + 2, bottom + 5), xc)
    first, last = top - 1, bottom + 1
    shift = last - first + 1
    fallback = np.bincount(idx[last + 1:last + 6, below[0]:below[1] + 1].ravel()).argmax()
    for r in range(first, last + 1):
        f = (r - first + 1) / (shift + 1)
        a = round(above[0] + (below[0] - above[0]) * f)
        b = round(above[1] + (below[1] - above[1]) * f)
        near, far = (r - shift, r + shift) if f < 0.5 else (r + shift, r - shift)
        for x in range(a, b + 1):
            idx[r, x] = (idx[near, x] if dark[near, x] else
                         idx[far, x] if dark[far, x] else fallback)


def extend_sky(idx, lab):
    w = idx.shape[1]
    base = lab[idx[:4]].mean(0)                              # (w, 3), the top rows' colour
    k = 64
    pad = np.concatenate([base[-k:], base, base[:k]])        # the ring wraps around
    base = np.stack([np.convolve(pad[:, c], np.ones(2 * k + 1) / (2 * k + 1), 'same')[k:-k]
                     for c in range(3)], 1)
    ramp = np.array(SKY_RAMP)
    ramp_l = lab[ramp, 0]
    out = np.empty((SKY, w), dtype=idx.dtype)
    for y in range(SKY):
        t = (SKY - y) / SKY                                  # 1 at the new top row
        target = base[:, 0] + (lab[SKY_TOP, 0] - base[:, 0]) * t
        step = np.diff(np.sort(ramp_l)).mean()
        target = target + BAYER[y % 4, np.arange(w) % 4] * step
        out[y] = ramp[np.abs(target[:, None] - ramp_l[None]).argmin(1)]
    return np.concatenate([out, idx])


def main():
    src, dst = sys.argv[1:3]
    im = Image.open(src)
    pal = np.array(im.getpalette()[:192]).reshape(64, 3)
    lab = srgb_to_oklab(pal.astype(float))
    dist = np.sqrt(((lab[:, None] - lab[None]) ** 2).sum(2))
    idx = np.array(im)
    for p in TRAIN:
        patch(idx, dist, **p)
    for xc, top, bottom, limit in TREES:
        tree_over_rails(idx, pal.mean(1)[idx], xc, top, bottom, limit)
    idx = extend_sky(idx, lab)
    out = Image.fromarray(idx, 'P')
    out.putpalette(pal.reshape(-1).tolist())
    out.save(dst, optimize=True)
    print(f'{dst}: {idx.shape[1]}x{idx.shape[0]}')


if __name__ == '__main__':
    main()
