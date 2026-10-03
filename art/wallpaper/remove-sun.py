#!/usr/bin/env python3
"""Paint the sun disc out of a panel: every row of the disc is refilled by interpolating between
the sky just left and right of it (the sky is horizontal bands, so this keeps bands and clouds).

  python3 art/wallpaper/remove-sun.py <in.png> <out.png> [--x 290 --y 245 --r 60]
Without --x/--y the brightest blob in the upper half is taken as the sun."""
import argparse

import numpy as np
from PIL import Image


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('src')
    p.add_argument('out')
    p.add_argument('--x', type=int)
    p.add_argument('--y', type=int)
    p.add_argument('--r', type=int, default=0, help='radius; default = measured + margin')
    p.add_argument('--bright', type=float, default=200, help='mean RGB above which a disc pixel is sun')
    p.add_argument('--grow', type=int, default=3, help='pixels of soft edge taken in around the disc')
    a = p.parse_args()
    img = np.asarray(Image.open(a.src).convert('RGB')).astype(np.float32)
    h, w = img.shape[:2]
    lum = img.mean(axis=2)
    if a.x is None or a.y is None:
        top = lum[: h // 2]
        ys, xs = np.nonzero(top > np.percentile(top, 99.7))
        cy, cx = int(np.median(ys)), int(np.median(xs))
    else:
        cx, cy = a.x, a.y
    r = a.r
    if not r:                                           # grow until the ring is no longer bright
        base = np.median(lum[max(0, cy - 120):cy + 120, max(0, cx - 220):max(1, cx - 160)])
        r = 10
        while r < 150:
            ang = np.linspace(0, 2 * np.pi, 64, endpoint=False)
            ring = lum[np.clip((cy + r * np.sin(ang)).astype(int), 0, h - 1), np.clip((cx + r * np.cos(ang)).astype(int), 0, w - 1)]
            if np.median(ring) < base + 40:
                break
            r += 2
        r += 8
    # Only the disc's own bright pixels are replaced; hills or clouds in front of it stay.
    yy, xx = np.mgrid[0:h, 0:w]
    disc = ((xx - cx) ** 2 + (yy - cy) ** 2 <= r * r) & (lum > a.bright)
    for _ in range(a.grow):                             # take in the soft edge of the disc
        grown = disc.copy()
        grown[1:] |= disc[:-1]; grown[:-1] |= disc[1:]; grown[:, 1:] |= disc[:, :-1]; grown[:, :-1] |= disc[:, 1:]
        disc = grown & ((xx - cx) ** 2 + (yy - cy) ** 2 <= (r + a.grow) ** 2) & (lum > a.bright - 45)
    out = img.copy()
    for y in np.unique(np.nonzero(disc)[0]):
        row = disc[y]
        x = 0
        while x < w:
            if not row[x]:
                x += 1
                continue
            x0 = x
            while x < w and row[x]:
                x += 1
            left, right = img[y, max(0, x0 - 3)], img[y, min(w - 1, x + 2)]
            for i in range(x0, x):
                t = (i - x0 + 1) / (x - x0 + 1)
                out[y, i] = left * (1 - t) + right * t
    Image.fromarray(out.round().astype(np.uint8)).save(a.out)
    print(f'sun at ({cx}, {cy}) r={r} removed -> {a.out}')


if __name__ == '__main__':
    main()
