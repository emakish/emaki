#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare cached production glass with its uncached form using surfaceless RHI.

This proves headless rendered pixels, not VM capture or opening-time equivalence.
"""
import importlib.util
import itertools
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path(os.environ.get('EMAKI_GLASS_DECODE_OUTPUT', ROOT / '.cache/evidence/glass-decode-frames'))
spec = importlib.util.spec_from_file_location('render_shell', ROOT / 'tests/render-shell.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


def pattern(path, alternate):
    """Fine detail plus broad colour bands exercise blur and refracted edges."""
    y, x = np.indices((960, 1440))
    fine = ((x // 3 + y // 5) % 2) * 83
    pixels = np.stack(((x // 71 * 37 + fine + alternate * 113) % 256,
                       (y // 59 * 43 + fine + alternate * 61) % 256,
                       ((x + y) // 97 * 53 + fine + alternate * 157) % 256), axis=2)
    Image.fromarray(pixels.astype(np.uint8)).save(path)


def check_replacements():
    """Reject a stale texture even when both cache variants render it equally."""
    captured = {}
    results = json.loads((OUTPUT / 'results.json').read_text())
    for row in results:
        geometry, scenario, material = row['case'].rsplit('-', 2)
        size, dpr = geometry.split('-')
        width, height = (int(value) for value in size.split('x'))
        scale = float(dpr.replace('_', '.'))
        with Image.open(OUTPUT / (row['case'] + '-cached.png')) as picture:
            assert picture.size == (round(width * scale), round(height * scale)), (row['case'], picture.size)
            digest = hashlib.sha256(picture.convert('RGBA').tobytes()).hexdigest()
        captured.setdefault((geometry, material), {})[scenario] = digest
    assert len(captured) == 12, captured
    for key, frames in captured.items():
        assert frames['initial'] != frames['replacement'], (key, 'replacement retained old pixels')
        assert frames['replacement'] == frames['reopen'], (key, 'reopen changed settled pixels')
    print('PASS 12 texture replacement/reopen checks: new pixels replace old; reopen preserves them')


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory(prefix='glass-decode-') as temporary:
        scratch = Path(temporary)
        sources = {}
        for variant in ('cached', 'uncached'):
            shell = scratch / variant
            shutil.copytree(ROOT / 'shell' if variant == 'cached' else sources['cached'].parent, shell)
            shutil.copyfile(ROOT / 'tests/fixtures/GlassDecodeFrames.qml', shell / 'GlassDecodeFrames.qml')
            for name in ('WelcomeGlass.qml', 'SettingsGlass.qml'):
                path = shell / name
                original = path.read_text()
                assert original.count('cacheWallpaper: true') == 1, (name, 'production cache opt-in missing')
                if variant == 'uncached':
                    path.write_text(original.replace('cacheWallpaper: true', 'cacheWallpaper: false'))
            sources[variant] = shell / 'GlassDecodeFrames.qml'
        textures = [scratch / 'first.png', scratch / 'second.png']
        for index, path in enumerate(textures):
            pattern(path, index)
        for width, height in ((640, 420), (803, 537)):
            for scale in (1, 1.25, 2):
                for scenario, material in itertools.product(('initial', 'replacement', 'reopen'), ('welcome', 'settings')):
                    case = f'{width}x{height}-{scale}-{scenario}-{material}'.replace('.', '_')
                    images = []
                    for variant, source in sources.items():
                        stem = OUTPUT / f'{case}-{variant}'
                        log = renderer.render(
                            source, png=stem.with_suffix('.png'), stats=stem.with_suffix('.json'),
                            properties=dict(width=width, height=height, textureDpr=scale,
                                            firstTexture=textures[0].as_uri(), secondTexture=textures[1].as_uri(),
                                            scenario=scenario, materialKind=material),
                            width=width, height=height, scale=scale, warmup=120, milliseconds=40,
                            ready_property='ready', shader_dir=ROOT / '.cache/shell-shaders')
                        stem.with_suffix('.log').write_text(log)
                        for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign',
                                      'Cannot assign', 'Failed to deserialize', 'Failed to find shader'):
                            assert error not in log, (case, variant, log)
                        stats = json.loads(stem.with_suffix('.json').read_text())
                        state = stats['fixture_end']
                        assert stats['readiness_reached'] and 'llvmpipe' in stats['renderer'], stats
                        assert state['graphics_api'] == 3, state
                        assert state['material_ready'] and state['shown'], state
                        assert state['material_kind'] == material, state
                        assert len(state['shader_states']) == 7 and 1 in state['shader_states'], state
                        assert all(status in (0, 1) for status in state['shader_states']), state
                        assert state['phase'] == (0 if scenario == 'initial' else 2), state
                        assert state['texture'] == textures[0 if scenario == 'initial' else 1].as_uri(), state
                        images.append(np.asarray(Image.open(stem.with_suffix('.png')).convert('RGBA')))
                    assert images[0].shape == images[1].shape
                    delta = np.abs(images[0].astype(np.int16) - images[1].astype(np.int16))
                    changed = int(np.count_nonzero(np.any(delta, axis=2)))
                    assert changed == 0, (case, changed, int(delta.max()))
                    # Interior pixels must contain rendered detail, not a flat fallback.
                    inset = round(70 * scale)
                    assert np.unique(images[0][inset:-inset, inset:-inset, :3].reshape(-1, 3), axis=0).shape[0] > 32
                    results.append(dict(case=case, changed_pixels=changed, maximum_delta=int(delta.max())))
                    print(f'PASS {case}: production shader material, exact pixel equality', flush=True)
    (OUTPUT / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    check_replacements()
    print(f'PASS {len(results)} headless render comparisons; not VM capture or opening-time proof')


if __name__ == '__main__':
    main()
