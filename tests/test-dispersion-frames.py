#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check native pointer-path pixels from dispersion-frames.py --regression.

The metric is horizontal RGB variation in the empty rows immediately above and
below glyphs. Refracted copies produce vertical streaks in those rows. The small
absolute allowance covers the changing meniscus itself: comparing only a ratio
with settled (nearly flat) glass would reject clean, content-free glass too.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
ANIMATION = [round(i * 1000 / 60, 3) for i in range(13)]
CARRY = ANIMATION[:5]


def name(scenario, frame):
    return f'{scenario}-{frame:07.2f}ms'.replace('.', '_')


def image(directory, scenario, frame):
    return np.asarray(Image.open(directory / (name(scenario, frame) + '.png')).convert('RGB'), dtype=float)


def fixture(directory, scenario, frame):
    return json.loads((directory / (name(scenario, frame) + '.json')).read_text())['fixture_end']


def energy(pixels, rect):
    x0, y0, x1, y1 = rect
    return float(np.abs(np.diff(pixels[y0:y1, x0:x1], axis=1)).sum(axis=2).mean())


def check(directory, compare=None):
    failures = []
    metrics = []
    battery_bands = [(1478, 14, 1505, 21), (1478, 32, 1505, 37)]
    for scenario in ('battery', 'panelleave'):
        settled = image(directory, scenario, 400)
        settled_energy = [energy(settled, band) for band in battery_bands]
        for frame in ANIMATION:
            pixels = image(directory, scenario, frame)
            state = fixture(directory, scenario, frame)
            if not state['pointer_hovered']:
                failures.append(f'{scenario} {frame:g}: native hover did not reach the battery handler')
            if scenario == 'panelleave' and not (state['pointer_clicked'] and state['pointer_left']):
                failures.append(f'{scenario} {frame:g}: click/open or window leave handler was not exercised')
            if scenario == 'panelleave' and state['battery']['hover_key']:
                failures.append(f'{scenario} {frame:g}: pointer hover key survived the window leave')
            values = [energy(pixels, band) for band in battery_bands]
            # The forming lens has a moving edge absent from the settled reference.
            # Allow three RGB variation units beyond that reference (1/255 per channel).
            for side, value, rest in zip(('above', 'below'), values, settled_energy, strict=True):
                if value > 2 * rest + 3:
                    failures.append(f'{scenario} {frame:g}: {side} glyph streak energy {value:.3f} > {2 * rest + 3:.3f}')
            metrics.append(dict(scenario=scenario, frame_ms=frame, battery_energy=values))
            if scenario == 'panelleave':
                rect = state['panel_row_rect']
                if len(rect) != 4:
                    failures.append(f'{scenario} {frame:g}: Balanced row missing from the rendered page')
                    continue
                x, y, _, height = map(round, rect)
                # Before the morph reveals the row, these screen pixels are the
                # wallpaper. Check its glyph bands once its resting drop exists.
                if not any(abs(drop[1] - y) < 1 and abs(drop[3] - height) < 1
                           for drop in state['panel_drops']):
                    continue
                # "Balanced" is x+50..110, with glyph ink at y+16..27.
                bands = [(x + 50, y + 9, x + 110, y + 14), (x + 50, y + 30, x + 110, y + 35)]
                row_values = [energy(pixels, band) for band in bands]
                metrics[-1]['row_energy'] = row_values
                for side, value, band in zip(('above', 'below'), row_values, bands, strict=True):
                    # The wide row's meniscus stays outside this glyph span;
                    # only a half-level allowance for raster rounding is needed.
                    limit = 2 * energy(settled, band) + .5
                    if value > limit:
                        failures.append(f'Balanced {frame:g}: {side} glyph streak energy {value:.3f} > {limit:.3f}')
    for frame in CARRY:
        state = fixture(directory, 'panelclick', frame)
        if not state['pointer_clicked'] or not state['pointer_hovered']:
            failures.append(f'panelclick {frame:g}: native hover/click handlers not reached')
        a = image(directory, 'panelclick', frame)[0:50, 1420:1536]
        b = image(directory, 'panelleave', frame)[0:50, 1420:1536]
        delta = np.abs(a - b)
        maximum = float(delta.max())
        metrics.append(dict(scenario='carry-focus-transfer', frame_ms=frame, maximum_channel_delta=maximum))
        if maximum > 3:
            failures.append(f'click/leave {frame:g}: battery ROI differs by {maximum:g} channel levels')
    if compare:
        regions = {'battery': (1420, 0, 1536, 50), 'panelclick': (1040, 0, 1536, 400),
                   'panelleave': (1040, 0, 1536, 400), 'dock': (600, 800, 950, 960),
                   'dock-bubble': (600, 800, 950, 960)}
        for scenario, (x0, y0, x1, y1) in regions.items():
            for frame in (0, 400):
                a = image(directory, scenario, frame)[y0:y1, x0:x1]
                b = image(compare, scenario, frame)[y0:y1, x0:x1]
                delta = np.abs(a - b)
                maximum = float(delta.max())
                changed = int(np.any(delta != 0, axis=2).sum())
                metrics.append(dict(scenario=scenario, frame_ms=frame, comparison_maximum=maximum,
                                    comparison_changed_pixels=changed))
                # Both native click paths can lose hover as the panel maps.
                # Their first frame is the fixed defect, not an idle reference.
                if scenario in ('panelclick', 'panelleave') and frame == 0:
                    continue
                if maximum > 3:
                    failures.append(f'unchanged {scenario} {frame:g}: ROI changed by {maximum:g} channel levels')
    (directory / 'pixel-metrics.json').write_text(json.dumps(dict(metrics=metrics, failures=failures), indent=2) + '\n')
    if failures:
        raise AssertionError('\n'.join(failures))
    print(f'PASS: {len(ANIMATION)} native hover and click/leave frames, {len(CARRY)} carry comparisons; glyph rows clean')
    if compare:
        print('PASS: idle and settled surface regions unchanged within three channel levels')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frames-dir', type=Path, default=ROOT / '.cache/dispersion-frames')
    parser.add_argument('--compare', type=Path, help='Baseline render directory for idle and settled identity')
    args = parser.parse_args()
    check(args.frames_dir, args.compare)
