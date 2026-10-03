#!/usr/bin/env python3
"""Continuation canvas for the next wallpaper panel: the right part of the previous panel on the
left, flat key green (#00FF00, never in the palette) everywhere else, same size as the panel.

  python3 art/wallpaper/make-canvas.py <previous.png> <canvas.png> [--keep 0.33]
  python3 art/wallpaper/make-canvas.py <previous.png> <canvas.png> --close <first.png>

--close makes the loop closer: previous panel's right part on the left, the first panel's left
part on the right, green gap in between."""
import argparse

from PIL import Image

KEY = (0, 255, 0)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('previous')
    p.add_argument('canvas')
    p.add_argument('--keep', type=float, default=0.33, help='share of the width taken from the previous panel')
    p.add_argument('--close', help='first panel, for the loop-closing canvas')
    a = p.parse_args()
    prev = Image.open(a.previous).convert('RGB')
    w, h = prev.size
    keep = round(w * a.keep)
    canvas = Image.new('RGB', (w, h), KEY)
    canvas.paste(prev.crop((w - keep, 0, w, h)), (0, 0))
    if a.close:
        first = Image.open(a.close).convert('RGB').resize((w, h), Image.LANCZOS)
        canvas.paste(first.crop((0, 0, keep, h)), (w - keep, 0))
    canvas.save(a.canvas)
    print(f'{a.canvas}: {w}x{h}, kept {keep} px')


if __name__ == '__main__':
    main()
