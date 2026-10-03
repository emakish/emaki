"""C11, on the host: check for a grid on glass over fine detail (27.09, the “stripes”).
  python3 tests/vm/check-launcher-lattice.py <desktop> <screenshot>
login.sh supplies the screenshot: launcher on an empty workspace at scale 1.25, over dense text wallpaper
(make-text-wall.py). Measure an empty plate area to the right of the second tile row (logical
x 600..720, y 226..282 — no tiles there with the VM's app set): variations faster than
~25 px, after subtracting a double 25×25 average. The old blur (first pass sampled the full
capture at 9.4 px intervals) turned each letter into a grid of dots: in simulation, this wallpaper gave
3.2 levels; a real kitty window gave 1.3 (p99−p1 5); correct blur gives 0.1.
Pass when standard deviation is ≤ 0.8 levels. Prints `[<desktop>] launcher-lattice: ok`
or `...: BAD <values>`; exit code 0 / 1."""
import sys

import numpy as np
from PIL import Image

name, path = sys.argv[1], sys.argv[2]
SCALE = 1.25
PATCH = (600, 226, 120, 56)
LIMIT = .8


def box(a, r):
    p = np.pad(a, r + 1, mode="edge")
    c = p.cumsum(0).cumsum(1)
    n = 2 * r + 1
    return (c[n:, n:] - c[:-n, n:] - c[n:, :-n] + c[:-n, :-n])[:a.shape[0], :a.shape[1]] / (n * n)


x, y, w, h = (round(v * SCALE) for v in PATCH)
rgb = np.asarray(Image.open(path).convert("RGB"), dtype=np.float64)[y:y + h, x:x + w]
lum = rgb @ np.array([.2126, .7152, .0722])
high = (lum - box(box(lum, 12), 12))[12:-12, 12:-12]
std = float(high.std())
spread = float(np.percentile(high, 99) - np.percentile(high, 1))
print(f"[{name}]   plate over text: brightness {lum.mean():.1f}, grid {std:.2f} (≤ {LIMIT}), p99−p1 {spread:.2f}")
ok = std <= LIMIT
print(f"[{name}] launcher-lattice: {'ok' if ok else f'BAD lattice={std:.2f}'}")
sys.exit(0 if ok else 1)
