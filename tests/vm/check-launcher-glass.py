"""C11, on the host: launcher glass on the REAL Qt path (capture → ShaderEffectSource → dock.frag).
  python3 tests/vm/check-launcher-glass.py <desktop> <run-directory>
login.sh (glass_scene) supplies screenshots: empty workspace over wallpaper-neo-pink,
launcher-s1.png / wall-s1.png (scale 1) and launcher-s125.png / wall-s125.png (1.25), where
wall-* shows the same workspace without the launcher. Measure an empty plate area — the header gap between
the search field and modes, x 228..318, y 22..52 in logical screen pixels (panel at (10, 8)).
Pass when, at each scale:
  - the plate is light: area brightness ≥ 0.8 of the wallpaper brightness below (light glass mixes in
    white; 1.06 at acceptance); before the fix on 27.09 it was 0.26 — the glass showed a frame captured
    when the shell started on another workspace (a black terminal);
  - no horizontal stripes: mean absolute second difference of row means ≤ 0.6
    (smooth glass: 0.04; a stripe with a 1-level brightness difference every other row gives ≈ 2);
and brightness ratios at 1 and 1.25 differ by no more than 0.12.
Prints `[<desktop>] launcher-glass: ok` or `...: BAD <values>`; exit code 0 / 1."""
import os
import sys

from PIL import Image

name, out = sys.argv[1], sys.argv[2]
PATCH = (228, 22, 90, 30)


def patch(path, scale):
    im = Image.open(path).convert("RGB")
    x, y, w, h = PATCH
    x0, y0, x1, y1 = round(x * scale), round(y * scale), round((x + w) * scale), round((y + h) * scale)
    px = im.load()
    rows = []
    for yy in range(y0, y1):
        rows.append(sum(.2126 * px[xx, yy][0] + .7152 * px[xx, yy][1] + .0722 * px[xx, yy][2] for xx in range(x0, x1)) / (x1 - x0))
    lum = sum(rows) / len(rows)
    stripes = sum(abs(rows[i - 1] - 2 * rows[i] + rows[i + 1]) for i in range(1, len(rows) - 1)) / (len(rows) - 2)
    return lum, stripes


bad = []
ratios = {}
for tag, scale in (("s1", 1.0), ("s125", 1.25)):
    plate_path, wall_path = f"{out}/launcher-{tag}.png", f"{out}/wall-{tag}.png"
    if not (os.path.exists(plate_path) and os.path.exists(wall_path)):
        print(f"[{name}] launcher-glass: NO-SHOT ({tag})")
        sys.exit(1)
    lum, stripes = patch(plate_path, scale)
    wall, _ = patch(wall_path, scale)
    ratio = lum / max(1.0, wall)
    ratios[tag] = ratio
    print(f"[{name}]   {tag}: plate {lum:.1f}, wallpaper {wall:.1f}, ratio {ratio:.2f} (≥ 0.8), stripes {stripes:.3f} (≤ 0.6)")
    if ratio < .8:
        bad.append(f"{tag}-dark={ratio:.2f}")
    if stripes > .6:
        bad.append(f"{tag}-stripes={stripes:.3f}")
gap = abs(ratios["s125"] - ratios["s1"])
print(f"[{name}]   difference between scales: {gap:.2f} (≤ 0.12)")
if gap > .12:
    bad.append(f"scale-gap={gap:.2f}")
print(f"[{name}] launcher-glass: {'ok' if not bad else 'BAD ' + ' '.join(bad)}")
sys.exit(0 if not bad else 1)
