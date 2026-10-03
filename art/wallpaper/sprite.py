#!/usr/bin/env python3
"""Turn a sprite drawn on a flat #00FF00 background into an indexed sprite in the ring's palette.

  python3 art/wallpaper/sprite.py train.png --palette art/wallpaper/palette-64.gpl --width 372 \
      --out art/wallpaper/train.png

The green is keyed out, the sprite is cropped to its bounding box and area-averaged down to
`--width` cells (colour weighted by coverage, so the green never bleeds into the edges), cells
covered less than half become transparent, the rest take the nearest palette colour in OKLab.
The output is an indexed PNG with the palette plus one transparent index after it."""
import argparse

import numpy as np
from PIL import Image

from pixelize import load_palette, nearest, srgb_to_oklab


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('image')
    p.add_argument('--palette', required=True, help='.gpl or indexed PNG')
    p.add_argument('--width', type=int, required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args()

    rgb = np.asarray(Image.open(a.image).convert('RGB')).astype(np.float64)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    alpha = 1 - np.clip((g - np.maximum(r, b) - 60) / 80, 0, 1)      # pure green → 0
    ys, xs = np.nonzero(alpha > 0.5)
    rgb, alpha = rgb[ys.min():ys.max() + 1, xs.min():xs.max() + 1], alpha[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = alpha.shape
    height = round(h * a.width / w)

    def shrink(plane):
        return np.asarray(Image.fromarray(plane.astype(np.float32), 'F').resize((a.width, height), Image.BOX))

    cover = shrink(alpha)
    colour = np.stack([shrink(rgb[..., c] * alpha) for c in range(3)], -1) / np.maximum(cover, 1e-6)[..., None]

    pal = load_palette(a.palette)
    clear = len(pal)
    idx = nearest(srgb_to_oklab(colour.reshape(-1, 3).clip(0, 255)), srgb_to_oklab(pal.astype(np.float64)))
    idx = idx.reshape(height, a.width).astype(np.uint8)
    idx[cover < 0.5] = clear
    out = Image.fromarray(idx, 'P')
    out.putpalette(pal.reshape(-1).tolist() + [0, 0, 0])
    out.save(a.out, transparency=clear, optimize=True)
    print(f'{a.out}: {a.width}x{height} cells')


if __name__ == '__main__':
    main()
