#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""CPU geometry sweep for the strip's mip selection, snapped edges and wrap.

The Rust strip tests evaluate the area shader against actual staged mip texels.
This independent simulation checks camera/layout coverage across 315,000 combinations.
No renderer, display server or media assets are needed.
"""
from itertools import product
from math import ceil, floor, isclose
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = (ROOT / 'packaging/niri-emaki/0005-emaki-wallpaper.patch').read_text()
block = PATCH.split('+++ b/src/render_helpers/emaki_wallpaper/strip.rs\n', 1)[1]
source = '\n'.join(line[1:] for line in block.splitlines() if line.startswith('+'))
# Keep this independent simulation attached to the actual renderer's critical formulae.
for expression in (
    'let original_footprint = 1. / (original_scale * view.zoom);',
    'let scale = original_scale * original_width / circumference;',
    'let origin = (view.origin * view.art_scale() / scale).rem_euclid(circumference);',
    'let edge = |v: f64| (v * scale * view.zoom).round() / view.zoom;',
    '(world - tile.start as f64 + 1. + left / scale - x) as f32',
    't.strip_size == level',
    '.max(height as f64 / original.1 as f64) <= 2.',
):
    assert expression in source, expression


def level_size(width, height, footprint):
    original = width, height
    while footprint * max(width / original[0], height / original[1]) > 2:
        if width == height == 1:
            break
        width, height = (width + 1) // 2, (height + 1) // 2
    return width, height


cases = 0
for width, height, output_height, zoom, output_scale, limit, camera in product(
    (1023, 1024, 4094, 4095, 13500, 15000, 27000),
    (2, 1200, 2160, 4320, 8192),
    (360, 720, 1080, 1440, 2160),
    (.125, .5, 1.),
    (1., 1.25, 2.),
    (256, 512, 1024, 4096, 16384),
    range(-20, 20),
):
    cases += 1
    output_width = output_height * 16 / 9
    original_scale = output_height * output_scale / height
    footprint = 1 / (original_scale * zoom)
    mip_width, mip_height = level_size(width, height, footprint)
    scale = original_scale * width / mip_width
    scale_y = output_height * output_scale / mip_height
    assert max(1 / (scale * zoom), 1 / (scale_y * zoom)) <= 2 + 1e-9 or (mip_width, mip_height) == (1, 1)
    art_scale = max(output_height * output_scale / 1200, 1)
    origin = (camera * 4093.125 * art_scale / scale) % mip_width
    visible_width = output_width * output_scale / scale
    stride = min(max(limit, 3), 4096) - 2
    x, previous_right = 0., 0.
    segments = 0
    while x < visible_width - .00001:
        world = (origin + x) % mip_width
        start = min(int(world // stride) * stride, mip_width - 1)
        tile_width = min(stride, mip_width - start)
        count = min(visible_width - x, start + tile_width - world)
        assert count > 0, (cases, x, world, start)
        left = floor(x * scale * zoom + .5) / zoom
        right = floor((x + count) * scale * zoom + .5) / zoom
        assert isclose(left, previous_right, abs_tol=1e-7), cases
        assert right >= left
        # Source edges compensate output rounding, preserving one camera mapping
        # through every tile and through the panorama wrap.
        source_left = world - start + 1 + left / scale - x
        reconstructed = (source_left - 1 + start) % mip_width
        expected = (origin + left / scale) % mip_width
        error = min(abs(reconstructed - expected), mip_width - abs(reconstructed - expected))
        assert error < 1e-7, (cases, error)
        # At <=2 texels/pixel a first/last nonempty fragment touches at most
        # the one guard texel. Test actual fragment centres, not clipped edges.
        if right > left and (mip_width, mip_height) != (1, 1):
            for centre in (left + .5 / zoom, right - .5 / zoom):
                sample = world - start + 1 + centre / scale - x
                radius = .5 / (scale * zoom)
                assert sample - radius >= -1e-7, (cases, sample, radius)
                assert sample + radius <= tile_width + 2 + 1e-7, (cases, sample, radius)
        previous_right = right
        x += count
        segments += 1
        assert segments <= ceil(visible_width / stride) + ceil(visible_width / mip_width) + 3
    expected_right = floor(output_width * output_scale * zoom + .5) / zoom
    assert isclose(previous_right, expected_right, abs_tol=1e-7), cases

assert cases == 315000, cases
print(f'theme strip filter: {cases:,} CPU mip/geometry/wrap cases passed')
