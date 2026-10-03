#!/usr/bin/env python3
"""Turn a source image into Emaki pixel art: resample to the art grid (area averaging when
shrinking, Lanczos when growing), then map every cell to a palette, so the picture keeps the
look of the source but every pixel gets a colour index.

  python3 art/wallpaper/pixelize.py loop.png --width 0 --height 1080 \
      --palette art/wallpaper/palette-64.gpl --cells art/wallpaper/loop-cells.png
  python3 art/wallpaper/pixelize.py 01.png --width 960 --height 540 --scale 2 --preview 01-pixel.png

`--width 0` keeps the whole width (a strip); otherwise the centre is cropped to width:height.
`--palette` takes a fixed palette (GIMP .gpl or an indexed PNG); without it `--colours` shades
are taken from the image itself (k-means in OKLab, sorted dark to light).
`--cells` writes the art grid as an indexed PNG, which is what palette cycling and masks work
on. `--preview` writes it ×`--scale`."""
import argparse

import numpy as np
from PIL import Image, ImageFilter


def srgb_to_oklab(rgb):
    c = rgb / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = c[:, 0], c[:, 1], c[:, 2]
    l = np.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = np.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = np.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return np.stack([0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
                     1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
                     0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s], 1)


def oklab_to_srgb(lab):
    L, a, b = lab[:, 0], lab[:, 1], lab[:, 2]
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    c = np.stack([4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
                  -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
                  -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s], 1).clip(0, 1)
    c = np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055)
    return (c * 255).round().astype(np.uint8)


def nearest(lab, centres, chunk=200_000):
    out = np.empty(len(lab), dtype=np.int64)
    for i in range(0, len(lab), chunk):
        part = lab[i:i + chunk]
        out[i:i + chunk] = ((part[:, None, :] - centres[None]) ** 2).sum(2).argmin(1)
    return out


def palette(lab, colours, seed=1, rounds=30):
    """k-means in OKLab over a fixed sample of cells; deterministic for the same image."""
    rng = np.random.default_rng(seed)
    sample = lab[rng.choice(len(lab), min(len(lab), 300_000), replace=False)]
    centres = sample[rng.choice(len(sample), colours, replace=False)]
    for _ in range(rounds):
        idx = nearest(sample, centres)
        for k in range(colours):
            members = sample[idx == k]
            centres[k] = members.mean(0) if len(members) else sample[rng.integers(len(sample))]
    return centres[np.argsort(centres[:, 0])]          # dark to light


def grid(path, width, height):
    src = Image.open(path).convert('RGB')
    w, h = src.size
    if width <= 0:                                    # strip: keep the whole width
        width = round(w * height / h)
    elif w / h > width / height:                      # crop the centre to the screen aspect
        cw = round(h * width / height)
        src = src.crop(((w - cw) // 2, 0, (w - cw) // 2 + cw, h))
    else:
        ch = round(w * height / width)
        src = src.crop((0, (h - ch) // 2, w, (h - ch) // 2 + ch))
    if height >= src.height:                          # 1 px cells: the grid is finer than the source
        return src.resize((width, height), Image.LANCZOS)
    return src.filter(ImageFilter.MedianFilter(3)).resize((width, height), Image.BOX)


def load_palette(path):
    """sRGB palette (n, 3) from a GIMP .gpl file or an indexed PNG."""
    if path.endswith('.gpl'):
        rows = [line.split()[:3] for line in open(path)
                if line[:1].isdigit() or line[:1] == ' ']
        return np.array(rows, dtype=np.uint8)
    im = Image.open(path)
    return np.array(im.getpalette()).reshape(-1, 3).astype(np.uint8)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('image')
    p.add_argument('--width', type=int, default=960)
    p.add_argument('--height', type=int, default=540)
    p.add_argument('--colours', type=int, default=64)
    p.add_argument('--palette', help='fixed palette: .gpl or indexed PNG')
    p.add_argument('--scale', type=int, default=2)
    p.add_argument('--cells', help='indexed PNG of the art grid')
    p.add_argument('--preview', help='RGB PNG of the grid ×scale')
    a = p.parse_args()
    small = grid(a.image, a.width, a.height)
    rgb = np.asarray(small).reshape(-1, 3).astype(np.float64)
    lab = srgb_to_oklab(rgb)
    if a.palette:
        pal = load_palette(a.palette)
        centres = srgb_to_oklab(pal.astype(np.float64))
    else:
        centres = palette(lab, a.colours)
        pal = oklab_to_srgb(centres)
    idx = nearest(lab, centres).reshape(small.height, small.width).astype(np.uint8)
    cells = Image.fromarray(idx, 'P')
    cells.putpalette(pal.reshape(-1).tolist())
    print(f'{small.width}x{small.height} cells, {len(pal)} colours')
    if a.cells:
        cells.save(a.cells, optimize=True)
        print(a.cells)
    if a.preview:
        cells.convert('RGB').resize((small.width * a.scale, small.height * a.scale), Image.NEAREST).save(a.preview)
        print(a.preview)


if __name__ == '__main__':
    main()
