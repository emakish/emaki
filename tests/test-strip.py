#!/usr/bin/env python3
"""Workspace strip pixels at scale 1, 1.25 and 2: only the existing workspaces, every digit on
the optical centre of its cell, every mark a crisp device-pixel rectangle centred under the
digit with the same gap. Flat stand-in (no GPU offscreen): digits in the glass ink."""
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)
DEST = Path(tempfile.mkdtemp(prefix='strip-', dir=CACHE))
INK = (36, 16, 24)             # LiquidPalette.inkOnLight: digits of occupied/current cells, current mark


def ink(frame, x0, y0, x1, y1, color):
    return [(x, y) for x in range(x0, x1) for y in range(y0, y1) if frame.getpixel((x, y))[:3] == color]


def distance(a, b):
    return sum((p - q) ** 2 for p, q in zip(a[:3], b[:3]))


def glyph_ink(frame, x0, y0, x1, y1, color):
    # Antialiased text never reaches the exact colour at 9 px: count a pixel as ink when it is
    # closer to the digit colour than to the cell background (just under the top edge, centre
    # column: the accent pill for the current cell, the island for the others).
    background = frame.getpixel(((x0 + x1) // 2, y0 + 2))
    return [(x, y) for x in range(x0, x1) for y in range(y0, y1)
            if distance(frame.getpixel((x, y)), color) < distance(frame.getpixel((x, y)), background)]


def bbox(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


for scale in ('1', '1.25', '2'):
    profile = Path(tempfile.mkdtemp(prefix='sp-', dir=CACHE))
    for name in ('runtime', 'cache', 'config', 'state', 'data', 'tmp'):
        (profile / name).mkdir(mode=0o700)
    shutil.copytree(ROOT / 'shell', profile / 'qml')
    shutil.copyfile(ROOT / 'tests/fixtures/StripShots.qml', profile / 'qml/shell.qml')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', QT_SCALE_FACTOR=scale, PYTHONDONTWRITEBYTECODE='1',
               EMAKI_SHELL_TRAY='0', EMAKI_TEST_SYSTEM='0', EMAKI_BIN='', EMAKI_SETTINGS_PROFILE='',
               EMAKI_TEST_MPRIS='0', EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_SHOT_DIR=str(DEST),
               XDG_RUNTIME_DIR=str(profile / 'runtime'), XDG_CACHE_HOME=str(profile / 'cache'),
               XDG_CONFIG_HOME=str(profile / 'config'), XDG_STATE_HOME=str(profile / 'state'),
               XDG_DATA_HOME=str(profile / 'data'), XDG_DATA_DIRS=str(profile / 'data'),
               TMPDIR=str(profile / 'tmp'), NIRI_SOCKET='',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session.sock'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system.sock'))
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_SCREEN_SCALE_FACTORS', 'QT_LOGGING_RULES'):
        env.pop(name, None)
    with (profile / 'qs.log').open('w') as log:
        subprocess.run(['qs', '-p', str(profile / 'qml'), '--no-color'], env=env, text=True,
                       stdout=log, stderr=subprocess.STDOUT, timeout=60)
    text = (profile / 'qs.log').read_text()
    assert 'STRIP_COMPLETE' in text, text
    assert not any(w in text for w in ('WARN', 'ERROR', 'ReferenceError', 'TypeError')), text
    geometry = json.loads(next(line.split('CELLS ', 1)[1] for line in text.splitlines() if 'CELLS ' in line))
    dpr = float(scale)
    assert abs(geometry['dpr'] - dpr) < 1e-6, geometry['dpr']
    # The strip origin (and so every snapped child) sits on a whole device pixel.
    for axis in ('x', 'y'):
        v = geometry['strip'][axis] * dpr
        assert abs(v - round(v)) < 1e-6, (scale, axis, v)
    # The whole scene, so screen-space snapping is measured where it applies (an island grab
    # would start at the island's own half-pixel origin).
    frame = Image.open(DEST / f'strip@{scale}x.png').convert('RGB')
    assert frame.size == (round(1536 * dpr), round(960 * dpr)), frame.size
    gaps, mark_sizes = [], []
    # Only existing workspaces: five in the fixture, 1..5, active 2, windows on 1..3.
    assert [c['current'] for c in geometry['cells']] == [False, True, False, False, False], geometry['cells']
    assert [c['occupied'] for c in geometry['cells']] == [True, True, True, False, False], geometry['cells']
    assert geometry['island']['width'] == 5 * 30 - 2 + 16, geometry['island']
    for cell in geometry['cells']:
        # Cell rect in device pixels.
        cx = cell['x'] * dpr
        cy = cell['y'] * dpr
        x0, x1 = int(round(cx)), int(round(cx + cell['width'] * dpr))
        y0, y1 = int(round(cy)), int(round(cy + cell['height'] * dpr))
        split = int(math.floor(cell['mark'][1] * dpr)) - 1   # digits end above the mark row
        # Central 60 % of the cell: keeps the drop's rim out of the digit ink.
        inset = round((x1 - x0) * 0.2)
        background = frame.getpixel(((x0 + x1) // 2, y0 + round(4 * dpr)))
        digit_color = INK if cell['current'] or cell['occupied'] else None
        if digit_color is None:
            # An empty workspace: the faint ink, darker than the plate but far from full ink.
            digit = [(x, y) for x in range(x0 + inset, x1 - inset) for y in range(y0, split)
                     if distance(frame.getpixel((x, y)), background) > 900]
        else:
            digit = glyph_ink(frame, x0 + inset, y0, x1 - inset, split, digit_color)
        assert digit, (scale, cell, 'digit ink not found')
        dx0, dy0, dx1, dy1 = bbox(digit)
        digit_cx = (dx0 + dx1) / 2
        digit_cy = (dy0 + dy1) / 2
        cell_cx = (x0 + x1) / 2
        cell_cy = (y0 + y1) / 2
        # Optical centre within one device pixel of the cell centre, both axes, mark or not.
        assert abs(digit_cx - cell_cx) <= 1.0, (scale, cell, 'digit x', digit_cx, cell_cx)
        assert abs(digit_cy - cell_cy) <= 1.0, (scale, cell, 'digit y', digit_cy, cell_cy)
        # The mark: the pixels of the lower band under the digit that differ from the plate
        # there (the band stops short of the drop's rounded rim).
        mx_lo, mx_hi = int(digit_cx - 6 * dpr), int(math.ceil(digit_cx + 6 * dpr))
        band = [(x, y) for x in range(mx_lo, mx_hi) for y in range(split + 1, y1 - round(3 * dpr))]
        plate = frame.getpixel((mx_lo, split + 2))
        mark = [(x, y) for x, y in band if distance(frame.getpixel((x, y)), plate) > 1500]
        if not cell['occupied']:
            assert not mark, (scale, cell, 'unexpected mark')
            continue
        mx0, my0, mx1, my1 = bbox(mark)
        colors = {frame.getpixel(p)[:3] for p in mark}
        # Crisp: the mark is a full device-pixel rectangle of one colour, no soft edge, and
        # its item geometry sits on whole device pixels (what the GPU renderer paints). The
        # offscreen software renderer ceils a far edge that floating point puts at
        # 46.00000000000001, so at 1.25 it may paint one extra device row/column.
        assert len(mark) == (mx1 - mx0) * (my1 - my0) and len(colors) == 1, (scale, cell, 'mark not a solid rectangle', colors)
        if cell['current']:
            assert colors == {INK}, (scale, cell, colors)
        expected = (math.floor(6 * dpr + .5), math.floor(2 * dpr + .5))
        slack = 0 if dpr == int(dpr) else 1
        assert expected[0] <= mx1 - mx0 <= expected[0] + slack and expected[1] <= my1 - my0 <= expected[1] + slack, (scale, cell, (mx1 - mx0, my1 - my0), expected)
        gx, gy, gw, gh = cell['glyph']
        rx, ry, rw, rh = cell['mark']
        for value in (gx * dpr, gy * dpr, rx * dpr, ry * dpr):
            assert abs(value - round(value)) < 1e-6, (scale, cell, 'not on a device pixel', value)
        assert abs(rw * dpr - expected[0]) < .02 and abs(rh * dpr - expected[1]) < .02, (scale, cell, (rw * dpr, rh * dpr))
        mark_sizes.append((mx1 - mx0, my1 - my0))
        # Centred under the digit's ink within half a device pixel; the gap is the same everywhere.
        assert abs((mx0 + mx1) / 2 - digit_cx) <= 0.5, (scale, cell, 'mark off centre', (mx0 + mx1) / 2, digit_cx)
        # Same gap everywhere: the mark row comes from the font's ink box, identical for lining
        # digits, so every mark shares one y; the rasterised gap is at least 3 logical px
        # (round bottoms like "3" lose a faint antialiased row to the ink threshold).
        gaps.append(ry)
        assert my0 - dy1 >= round(3 * dpr) - 1, (scale, cell, 'gap', my0 - dy1)
    assert len(set(gaps)) == 1 and len(gaps) == 3, (scale, gaps)
    print(f'scale {scale}: {len(geometry["cells"])} cells, digits on centre, marks {mark_sizes[0]} device px, mark row y={gaps[0]}; {DEST.relative_to(ROOT)}')
print('PASS: workspace strip digits optically centred and marks crisp at 1x, 1.25x, 2x')
