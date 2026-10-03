#!/usr/bin/env python3
"""Stitch wallpaper panels into one strip. Each panel's left `keep` share repeats the previous
panel's right part (make-canvas.py); the cut runs along the vertical path of least difference
inside that overlap (image quilting), softened by a narrow feather.

  python3 art/wallpaper/stitch.py 01.png 02.png ... --out strip.png [--keep 0.33] [--feather 12]
  python3 art/wallpaper/stitch.py 01.png ... 15.png@0.30 --loop 0.30 --out loop.png

`panel@share` overrides the overlap for that join. --loop closes the ring: the strip's last
`share` columns repeat the first panel's first columns (make-canvas.py --close); they are cut
into the start along the best seam, so the result wraps around without a seam."""
import argparse

import numpy as np
from PIL import Image


def seam(cost):
    """Column index per row of the cheapest top-to-bottom path (steps of -1/0/+1 column)."""
    h, w = cost.shape
    acc = cost.copy()
    for y in range(1, h):
        prev = acc[y - 1]
        left = np.r_[np.inf, prev[:-1]]
        right = np.r_[prev[1:], np.inf]
        acc[y] += np.minimum(np.minimum(left, prev), right)
    path = np.empty(h, dtype=int)
    path[-1] = int(np.argmin(acc[-1]))
    for y in range(h - 2, -1, -1):
        x = path[y + 1]
        lo, hi = max(0, x - 1), min(w, x + 2)
        path[y] = lo + int(np.argmin(acc[y, lo:hi]))
    return path


def join(strip, panel, keep, feather):
    h = strip.shape[0]
    a = strip[:, -keep:].astype(np.float32)
    b = panel[:, :keep].astype(np.float32)
    cost = np.abs(a - b).sum(axis=2)
    margin = keep // 6                                    # keep the cut away from the overlap's ends
    cost[:, :margin] = cost[:, -margin:] = 1e9
    cut = seam(cost)
    xs = np.arange(keep)[None, :]
    t = np.clip((xs - cut[:, None] + feather / 2) / max(feather, 1), 0, 1)[..., None]   # 0 = strip, 1 = panel
    mixed = (a * (1 - t) + b * t).round().astype(np.uint8)
    return np.concatenate([strip[:, :-keep], mixed, panel[:, keep:]], axis=1), cut


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('panels', nargs='+')
    p.add_argument('--out', required=True)
    p.add_argument('--keep', type=float, default=0.33)
    p.add_argument('--feather', type=int, default=12)
    p.add_argument('--loop', type=float, help='close the ring; share of the width repeated at the end')
    a = p.parse_args()
    strip = np.asarray(Image.open(a.panels[0]).convert('RGB'))
    h, w = strip.shape[:2]
    for spec in a.panels[1:]:
        path, _, share = spec.partition('@')
        keep = round(w * (float(share) if share else a.keep))
        panel = Image.open(path).convert('RGB')
        if panel.size != (w, h):
            panel = panel.resize((w, h), Image.LANCZOS)
        strip, cut = join(strip, np.asarray(panel), keep, a.feather)
        print(f'{path}: cut at overlap columns {cut.min()}..{cut.max()} of {keep}')
    if a.loop:
        keep = round(w * a.loop)
        # The tail repeats the head: cut the tail into the head, drop the duplicate tail.
        merged, cut = join(strip[:, -keep:], strip[:, :keep], keep, a.feather)
        strip = np.concatenate([merged, strip[:, keep:-keep]], axis=1)
        print(f'loop: cut at overlap columns {cut.min()}..{cut.max()} of {keep}')
    Image.fromarray(strip).save(a.out)
    print(f'{a.out}: {strip.shape[1]}x{strip.shape[0]}')


if __name__ == '__main__':
    main()
