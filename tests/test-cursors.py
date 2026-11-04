#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check the white and dark Xcursor themes, complete names, pixels and installed defaults."""
from pathlib import Path
import os
import re
import runpy
import struct
import subprocess
import sys
import tempfile
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
subprocess.run([sys.executable, str(ROOT / 'scripts/build-cursors'), '--check'], check=True)
build = runpy.run_path(str(ROOT / 'scripts/build-cursors'))

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


drawn = set(build['ALIASES'])
names = drawn | {alias for aliases in build['ALIASES'].values() for alias in aliases.split()}
assert set(CSS) <= names, 'missing standard cursor names'
assert set(build['THEMES']) == {'Emaki-White', 'Emaki-Dark'}
assert not (ROOT / 'cursors/Emaki').exists(), 'retired cursor theme is still shipped'

for kdl in ('niri/default.kdl', 'greetd/niri.kdl'):
    block = re.search(r'cursor \{(.*?)\}', (ROOT / kdl).read_text(), re.S).group(1)
    assert re.search(r'xcursor-theme "Emaki-White"', block), f'{kdl} does not select the rounded white cursors'
# Validate the installed format and each requested integer/fractional size.
rounded_sizes = build['ROUNDED_SIZES']
assert len(rounded_sizes) == len(set(rounded_sizes)), 'duplicate nominal sizes'
assert {24, 30, 32, 36, 42, 45, 48, 60, 64, 72, 75, 96} == set(rounded_sizes)
centered = {'text', 'wait', 'crosshair', 'move', 'not-allowed', 'grab', 'grabbing',
            'ew-resize', 'ns-resize', 'nwse-resize', 'nesw-resize'}
rounded_images = {}
for theme_name in ('Emaki-White', 'Emaki-Dark'):
    theme = ROOT / 'cursors' / theme_name
    paths = {path.name: path for path in (theme / 'cursors').iterdir()}
    assert set(paths) == names, f'{theme_name}: cursor names differ from the complete alias set'
    assert 'Inherits=' not in (theme / 'index.theme').read_text(), theme_name
    for name, path in paths.items():
        assert path.is_symlink() == (name not in drawn), (theme_name, name)
        if path.is_symlink():
            target = os.readlink(path)
            assert '/' not in target and target in drawn, (theme_name, name, target)
            assert name in build['ALIASES'][target].split(), (theme_name, name, target)
            assert path.is_file(), f'{theme_name}/{name}: broken alias'
    for name in sorted(drawn):
        found = images(paths[name])
        rounded_images[theme_name, name] = found
        expected_frames = 12 if name in {'wait', 'progress'} else 1
        assert len(found) == len(rounded_sizes) * expected_frames, (theme_name, name)
        assert {entry[0] for entry in found} == set(rounded_sizes), (theme_name, name)
        for nominal in rounded_sizes:
            frames = [entry for entry in found if entry[0] == nominal]
            assert len(frames) == expected_frames, (theme_name, name, nominal)
            assert len({entry[3:5] for entry in frames}) == 1, 'animation hotspot moves'
            assert len({entry[-1] for entry in frames}) == expected_frames, 'duplicate animation frames'
            for _, w, h, xhot, yhot, delay, pixels in frames:
                context = (theme_name, name, nominal)
                assert (w, h) == (nominal, nominal), context
                assert 0 <= xhot < w and 0 <= yhot < h, context
                assert delay == (80 if expected_frames > 1 else build['STATIC_DELAY']), context
                if name in centered:
                    # Thin strokes are centred half a unit right of and below the middle.
                    slack = 1 + w / 48
                    assert abs(xhot - w / 2) <= slack and abs(yhot - h / 2) <= slack, context
                elif name in {'default', 'progress'}:
                    assert 0 < xhot < w / 4 and 0 < yhot < h / 4, context
                elif name == 'pointer':
                    assert w / 4 < xhot < w / 2 and 0 < yhot < h / 4, context
                if name not in HOLLOW:
                    neighborhood = [pixels[y * w + x]
                                    for y in range(max(0, yhot - 1), min(h, yhot + 2))
                                    for x in range(max(0, xhot - 1), min(w, xhot + 2))]
                    assert any(pixel >> 24 for pixel in neighborhood), \
                        f'{context}: hotspot misses the cursor'
                assert any(pixel >> 24 == 255 for pixel in pixels), f'{context}: no opaque body'
                assert any(0 < pixel >> 24 < 255 for pixel in pixels), f'{context}: no antialiasing or shadow'
                for pixel in pixels:
                    alpha = pixel >> 24
                    assert all((pixel >> shift) & 255 <= alpha for shift in (0, 8, 16)), context
        dimensions = {}
        for nominal, w, h, *_ in found:
            dimensions.setdefault((w, h), set()).add(nominal)
        assert all(len(values) == 1 for values in dimensions.values()), (theme_name, name)

for name in sorted(drawn):
    white = rounded_images['Emaki-White', name]
    dark = rounded_images['Emaki-Dark', name]
    for light_frame, dark_frame in zip(white, dark):
        assert light_frame[:-1] == dark_frame[:-1], f'{name}: variant geometry differs'
        light_pixels, dark_pixels = light_frame[-1], dark_frame[-1]
        assert [p >> 24 for p in light_pixels] == [p >> 24 for p in dark_pixels], name
        assert light_pixels != dark_pixels, f'{name}@{light_frame[0]}: identical palettes'
        # Narrow shapes can contain more outline than fill. Inspect matching interior pixels
        # at the largest size rather than comparing total brightness across the whole image.
        if light_frame[0] == max(rounded_sizes):
            assert any(light >> 24 >= 240 and dark >> 24 >= 240
                       and all((light >> shift) & 255 > 200 for shift in (0, 8, 16))
                       and all((dark >> shift) & 255 < 80 for shift in (0, 8, 16))
                       for light, dark in zip(light_pixels, dark_pixels)), \
                f'{name}: no matching white and dark body pixels'

# Pixel tolerance permits renderer drift, but never structural corruption or invalid alpha.
reference = build['on_disk'](ROOT / 'cursors/Emaki-White')
reference_files, reference_links = reference
sample = reference_files['cursors/default']
pixel_start = struct.unpack_from('<3I', sample, 16)[2] + 36

def altered_cursor(data):
    files = dict(reference_files)
    files['cursors/default'] = bytes(data)
    return files, reference_links

assert build['matches']('Emaki-White', reference, reference)
for position in (0, 12, 16, 24, pixel_start - 8):
    damaged = bytearray(sample)
    damaged[position] ^= 1
    assert not build['matches']('Emaki-White', reference, altered_cursor(damaged)), position
assert not build['matches']('Emaki-White', reference, altered_cursor(sample[:12]))
# A transparent background pixel cannot carry colour, even a one-level difference.
damaged = bytearray(sample)
assert damaged[pixel_start:pixel_start + 4] == bytes(4)
damaged[pixel_start] = 1
assert not build['matches']('Emaki-White', reference, altered_cursor(damaged))
# A one-level colour drift inside an opaque pixel remains acceptable.
opaque = next(i for i in range(pixel_start, pixel_start + 24 * 24 * 4, 4)
              if sample[i + 3] == 255 and sample[i] > 0)
drifted = bytearray(sample)
drifted[opaque] -= 1
assert build['matches']('Emaki-White', reference, altered_cursor(drifted))
print('PASS: tolerant comparison rejects damaged structure and invalid alpha, accepts pixel drift')

# Exercise the same installation target used by development and package staging, with an
# arbitrary prefix and no writes to the running system. Aliases must remain relative links.
with tempfile.TemporaryDirectory(prefix='emaki-cursors-') as temporary:
    prefix = '/opt/cursor-test'
    icons = Path(temporary) / prefix.lstrip('/') / 'share/icons'
    retired = icons / 'Emaki/cursors'
    retired.mkdir(parents=True)
    (retired / 'obsolete').write_bytes(b'old packaged cursor')
    unrelated = icons / 'Emaki-Custom'
    unrelated.mkdir()
    (unrelated / 'keep').write_text('personal theme')
    subprocess.run(['make', '--no-print-directory', '-j1', 'install-cursors',
                    f'DESTDIR={temporary}', f'PREFIX={prefix}'], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL)
    for theme_name in ('Emaki-White', 'Emaki-Dark'):
        source = ROOT / 'cursors' / theme_name
        installed = Path(temporary) / prefix.lstrip('/') / 'share/icons' / theme_name
        assert build['on_disk'](installed) == build['on_disk'](source), \
            f'{theme_name}: staged files or aliases differ'
    assert not (icons / 'Emaki').exists(), 'retired theme survived installation'
    assert (unrelated / 'keep').read_text() == 'personal theme'
    # The whole uninstall target also compares its rendered system config.
    system_config = Path(temporary) / 'etc/niri/config.kdl'
    system_config.parent.mkdir(parents=True)
    system_config.write_bytes(subprocess.check_output([
        sys.executable, str(ROOT / 'scripts/render-paths'), '--prefix', prefix,
        '--text', 'niri/system.kdl'], cwd=ROOT))
    subprocess.run(['make', '--no-print-directory', '-j1', 'uninstall',
                    f'DESTDIR={temporary}', f'PREFIX={prefix}'], cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL)
    assert all(not (icons / name).exists() for name in build['THEMES'])
    assert (unrelated / 'keep').read_text() == 'personal theme'
print(f'PASS: both rounded themes × {len(rounded_sizes)} sizes, complete names, hotspots, '
      'animation, premultiplied pixels, palettes, staged installation and uninstallation')
