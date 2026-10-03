"""C11, on the host: check whether bar glass flickers when the image underneath changes abruptly.
  python3 tests/vm/check-bar-flicker.py <desktop> <frames-directory> <scale>
login.sh (guest-frames.sh) supplies frames: the top of the screen while switching from workspace 1 (black
kitty) to empty workspace 2 with wallpaper; the black window slides under the islands. Measure brightness
in a strip of the launcher button plate left of the arrow (x 12..18, y 10..42, logical pixels) in each frame.
On 27.09 before the fix, the plate jumped light → almost black → light → dark within 150 ms (jumps of up to 215
levels, with reversals): glass tone and ink colors were recalculated each frame.
Pass when the largest jump between adjacent frames is ≤ 130 (a black window arriving under glass
with a stable tone gives ≈ 90) and there are no reversals: a jump > 60 followed within 3 frames
by an opposite jump > 60. Prints `[<desktop>] bar-flicker: ok` or `...: BAD <values>`."""
import glob
import os
import sys

from PIL import Image

name, folder, scale = sys.argv[1], sys.argv[2], float(sys.argv[3])
frames = sorted(glob.glob(f"{folder}/*.ppm"))
if len(frames) < 20:
    print(f"[{name}] bar-flicker: NO-FRAMES ({len(frames)})")
    sys.exit(1)
series = []
for path in frames:
    im = Image.open(path).convert("RGB")
    px = im.load()
    x0, y0, x1, y1 = round(12 * scale), round(10 * scale), round(18 * scale), round(42 * scale)
    total = 0.0
    for y in range(y0, y1):
        for x in range(x0, x1):
            r, g, b = px[x, y]
            total += .2126 * r + .7152 * g + .0722 * b
    series.append(total / ((x1 - x0) * (y1 - y0)))
steps = [series[i + 1] - series[i] for i in range(len(series) - 1)]
max_jump = max(abs(s) for s in steps)
reversals = 0
for i, s in enumerate(steps):
    if abs(s) <= 60:
        continue
    if any(abs(t) > 60 and (t > 0) != (s > 0) for t in steps[i + 1:i + 4]):
        reversals += 1
lo, hi = min(series), max(series)
print(f"[{name}]   frames {len(series)}, plate brightness {lo:.0f}..{hi:.0f}, largest jump {max_jump:.0f} (≤ 130), reversals {reversals} (0)")
bad = []
if max_jump > 130:
    bad.append(f"jump={max_jump:.0f}")
if reversals:
    bad.append(f"reversals={reversals}")
print(f"[{name}] bar-flicker: {'ok' if not bad else 'BAD ' + ' '.join(bad)}")
sys.exit(0 if not bad else 1)
