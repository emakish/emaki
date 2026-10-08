#!/usr/bin/env python3
"""Check the Emaki xcursor theme: reproducible, valid Xcursor files, exact pixel cells, every name
niri and apps ask for, and the niri configs that select it."""
from pathlib import Path
import os
import re
import runpy
import struct
import subprocess
import sys
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
THEME = ROOT / 'cursors/Emaki'
subprocess.run([sys.executable, str(ROOT / 'scripts/build-cursors'), '--check'], check=True)
build = runpy.run_path(str(ROOT / 'scripts/build-cursors'))
art = runpy.run_path(str(ROOT / 'art/cursor/haze.py'))
SIZES, cell_size = build['SIZES'], build['cell_size']

# Every CSS cursor name (the cursor-shape-v1 set niri knows through the cursor-icon crate).
CSS = ('default context-menu help pointer progress wait cell crosshair text vertical-text alias copy move '
       'no-drop not-allowed grab grabbing e-resize n-resize ne-resize nw-resize s-resize se-resize sw-resize '
       'w-resize ew-resize ns-resize nesw-resize nwse-resize col-resize row-resize all-scroll zoom-in zoom-out '
       'dnd-ask all-resize').split()
HOLLOW = {'wait', 'crosshair'}          # the hotspot sits in the hole on purpose


def images(path):
    data = path.read_bytes()
    magic, header, version, count = struct.unpack_from('<4s3I', data)
    assert (magic, header, version) == (b'Xcur', 16, 0x10000), path
    out = []
    for i in range(count):
        kind, nominal, position = struct.unpack_from('<3I', data, 16 + 12 * i)
        assert kind == 0xFFFD0002, path
        fields = struct.unpack_from('<9I', data, position)
        assert fields[:4] == (36, 0xFFFD0002, nominal, 1), path
        w, h, xhot, yhot, delay = fields[4:]
        pixels = struct.unpack_from(f'<{w * h}I', data, position + 36)
        out.append((nominal, w, h, xhot, yhot, delay, pixels))
    return out


def premultiplied(rgba):
    r, g, b, a = rgba
    return (a << 24) | ((r * a + 127) // 255 << 16) | ((g * a + 127) // 255 << 8) | (b * a + 127) // 255


drawn = set(art['SET'])
for name in sorted(drawn):
    frames = art['frames'](name)
    found = images(THEME / 'cursors' / name)
    assert len(found) == len(SIZES) * frames, name
    assert sorted({n for n, *_ in found}) == sorted(SIZES), name
    for index, (nominal, w, h, xhot, yhot, delay, pixels) in enumerate(found):
        k = cell_size(nominal)
        frame = index % frames
        assert delay == (art['FRAME_MS'] if frames > 1 else build['STATIC_DELAY']), name
        assert w % k == 0 and h % k == 0 and 0 <= xhot < w and 0 <= yhot < h, (name, nominal)
        assert (xhot % k, yhot % k) == (0, 0), (name, nominal)
        cells = art['cursor'](name, frame)
        x0, y0 = min(c[0] for c in cells), min(c[1] for c in cells)
        assert (xhot, yhot) == (-x0 * k, -y0 * k), (name, nominal)
        expected = [0] * (w * h)
        for x, y, rgba in cells:
            for dy in range(k):
                for dx in range(k):
                    expected[((y - y0) * k + dy) * w + (x - x0) * k + dx] = premultiplied(rgba)
        assert list(pixels) == expected, f'{name}@{nominal} frame {frame}: pixels are not exact {k}×{k} cells'
        if name not in HOLLOW:
            assert pixels[yhot * w + xhot] >> 24 == 255, f'{name}@{nominal}: hotspot is not on the cursor'
    # niri keeps every image with the dimensions of the nearest nominal size: those must be one
    # size's frames only, or a static cursor turns "animated" and niri redraws every frame.
    dims = {}
    for nominal, w, h, *_ in found:
        dims.setdefault((w, h), set()).add(nominal)
    assert all(len(sizes) == 1 for sizes in dims.values()), f'{name}: one image size under several nominals'

# The pick: at scale 1.25 (24 × 1.25 = 30) one art cell is 2×2 pixels; nearest nominal per request.
nearest = lambda request: min(SIZES, key=lambda n: abs(n - request))
assert [cell_size(nearest(24 * s)) for s in (1, 1.25, 1.5, 1.75, 2, 2.5, 3)] == [2, 2, 2, 3, 3, 4, 5]

names = {p.name for p in (THEME / 'cursors').iterdir()}
missing = [n for n in CSS if n not in names]
assert not missing, f'missing cursor names: {missing}'
for path in (THEME / 'cursors').iterdir():
    if path.is_symlink():
        target = os.readlink(path)
        assert '/' not in target and target in drawn, (path.name, target)
assert 'Inherits=' not in (THEME / 'index.theme').read_text()

for kdl in ('niri/default.kdl', 'greetd/niri.kdl'):
    block = re.search(r'cursor \{(.*?)\}', (ROOT / kdl).read_text(), re.S).group(1)
    assert re.search(r'xcursor-theme "Emaki"', block), f'{kdl} does not select the Emaki cursors'
print(f'PASS: {len(drawn)} cursors × {len(SIZES)} sizes exact, {len(names)} names, niri configs select Emaki')
