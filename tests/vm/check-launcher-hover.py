"""C11, on the host: check for a launcher tile disappearing for one frame as the selection bubble follows the pointer.
  python3 tests/vm/check-launcher-hover.py <desktop> <frames-directory> <scale>
login.sh (guest-hover-frames.sh) supplies frames: the first tile row (logical x 0..740, starting
10 above the row, height 110; row top in row.txt), while the pointer moves steadily across it from left to right.
For each of 7 tiles, measure brightness variation in the icon square (center x 82 + 96·i, from row top
+6 to +38): an icon gives high variation; an empty area shows the plate. The bubble must traverse the row
(bubble x before and after movement is also in row.txt), otherwise the check observed nothing. Before the fix on 27.09, the newly selected tile
disappeared entirely for one frame (`selected` already hid it below the glass, while the next frame's
`selection` had not yet drawn its copy above) — the “flickering” bubble. Fail on a frame with variation below
0.35 of the tile's median, while adjacent frames exceed 0.7. Prints `[<desktop>] launcher-hover: ok`
or `...: BAD vanish=<n>`."""
import glob
import statistics
import sys

from PIL import Image, ImageChops, ImageStat

name, folder, scale = sys.argv[1], sys.argv[2], float(sys.argv[3])
frames = []
prev = None
for path in sorted(glob.glob(f"{folder}/*.ppm")):
    im = Image.open(path).convert("L")
    if prev is None or ImageStat.Stat(ImageChops.difference(im, prev)).mean[0] > .01:
        frames.append(im)
    prev = im
if len(frames) < 30:
    print(f"[{name}] launcher-hover: NO-FRAMES ({len(frames)})")
    sys.exit(1)
try:
    _, x0, x1 = (int(v) for v in open(f"{folder}/row.txt").read().split())
except (OSError, ValueError):
    x0 = x1 = 0
if x1 - x0 < 300:
    print(f"[{name}] launcher-hover: NO-MOTION (bubble x {x0} → {x1})")
    sys.exit(1)
vanish = []
for i in range(7):
    cx = 82 + 96 * i
    box = (round((cx - 16) * scale), round(16 * scale), round((cx + 16) * scale), round(48 * scale))
    spread = [ImageStat.Stat(f.crop(box)).stddev[0] for f in frames]
    med = statistics.median(spread)
    for k in range(1, len(spread) - 1):
        if spread[k] < .35 * med and spread[k - 1] > .7 * med and spread[k + 1] > .7 * med:
            vanish.append((i, k, round(spread[k], 1), round(med, 1)))
print(f"[{name}]   frames {len(frames)}, single-frame tile disappearances: {len(vanish)} {vanish[:6]}")
print(f"[{name}] launcher-hover: {'ok' if not vanish else f'BAD vanish={len(vanish)}'}")
sys.exit(0 if not vanish else 1)
