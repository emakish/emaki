#!/usr/bin/env python3
"""Real Qt glass before/after PNGs using isolated qs + surfaceless llvmpipe.

Requires an existing baseline checkout/archive (read-only). No compositor, live
session, network or installed config is used. Differences are evidence for review,
not automatic visual acceptance. Candidate texture scale is kept separate.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import numpy as np
from PIL import Image, ImageOps
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('render_shell', ROOT/'tests/render-shell.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
SHOTS = ('bar', 'dock-bubble', 'dock-menu', 'launcher', 'clock', 'system-sound', 'system-power')


def prepare(source, target):
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source/'shell', target/'qml', dirs_exist_ok=True)
    # The common fixture can select the old crop calculation or the new retained
    # bounds. Loading this inert policy also lets pre-optimization archives render.
    shutil.copyfile(ROOT/'shell/DockBackdropRegion.qml', target/'qml/DockBackdropRegion.qml')
    for fixture in ('ShellRender.qml', 'SystemFixture.qml'):
        shutil.copyfile(ROOT/'tests/fixtures'/fixture, target/'qml'/fixture)
    with (target/'qml/qmldir').open('a') as file:
        file.write('\nSystemFixture 1.0 SystemFixture.qml\n')
        if 'DockBackdropRegion 1.0' not in (target/'qml/qmldir').read_text():
            file.write('DockBackdropRegion 1.0 DockBackdropRegion.qml\n')
    (target/'shaders').mkdir(exist_ok=True)
    for shader in (source/'shell/shaders').glob('*.frag'):
        subprocess.run(['/usr/lib/qt6/bin/qsb', '--glsl', '100 es,120,150', '--hlsl', '50', '--msl', '12',
                        '-o', str(target/'shaders'/(shader.name+'.qsb')), str(shader)], check=True)


def differences(before, after, output):
    a = np.asarray(Image.open(before).convert('RGBA'), dtype=np.int16)
    b = np.asarray(Image.open(after).convert('RGBA'), dtype=np.int16)
    assert a.shape == b.shape
    delta = np.abs(a-b)
    changed = np.any(delta != 0, axis=2)
    Image.fromarray(np.minimum(delta[:, :, :3]*16, 255).astype(np.uint8)).save(output)
    return dict(pixels=int(changed.size), changed_pixels=int(changed.sum()),
                max_channel_delta=int(delta.max()), mean_absolute_channel_delta=float(delta.mean()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=ROOT/'.cache/shell-glass-comparison')
    parser.add_argument('--scale', type=float, default=1.25, help='redirected window buffer DPR')
    parser.add_argument('--screen-scale', type=float, default=2, help='independent synthetic QScreen DPR')
    parser.add_argument('--texture-scale', type=float, default=2)
    parser.add_argument('--wallpaper', type=Path, default=ROOT/'art/wallpaper/ring.png')
    parser.add_argument('--candidate-scale', type=float, help='optional separate texture/decode scale candidate')
    parser.add_argument('--shots', nargs='+', choices=(*SHOTS, 'dock-release'), default=SHOTS)
    parser.add_argument('--release-frame', type=int, default=0, help='deterministic return steps for dock-release')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for name, source in (('before', args.baseline.resolve()), ('after', ROOT)):
        prepare(source, out/name)
    wallpaper = Image.open(args.wallpaper).convert('RGBA')
    scales = {args.texture_scale}
    if args.candidate_scale:
        scales.add(args.candidate_scale)
    for scale in scales:
        ImageOps.fit(wallpaper, (round(1536*scale), round(960*scale)), Image.Resampling.LANCZOS).save(out/f'wall-{scale:g}.png')
    data = out/'data/applications'
    data.mkdir(parents=True, exist_ok=True)
    for name in ('Browser', 'Editor', 'Files'):
        (data/f'fixture-{name.lower()}.desktop').write_text(f'[Desktop Entry]\nType=Application\nName={name}\nExec=/usr/bin/true\nTerminal=false\n')
    variants = [('before', args.texture_scale), ('after', args.texture_scale)]
    if args.candidate_scale:
        variants.append(('candidate', args.candidate_scale))
    report = dict(method='real production ShellScene in isolated qs; Qt Quick RHI, surfaceless llvmpipe; synthetic services/apps, fixed date; no live session',
                  baseline=str(args.baseline.resolve()), window_scale=args.scale, screen_scale=args.screen_scale,
                  texture_scale=args.texture_scale, candidate_scale=args.candidate_scale, surfaces={})
    for shot in args.shots:
        for name, texture_scale in variants:
            source = out/('before' if name == 'before' else 'after')
            target = out/f'{name}-{shot}'
            original = args.baseline.resolve() if name == 'before' else ROOT
            surfaces = (original/'shell/Surfaces.qml').read_text()
            properties = dict(shot=shot, releaseFrame=args.release_frame, wallpaper=(out/f'wall-{texture_scale:g}.png').as_uri(),
                              background=(out/f'wall-{args.texture_scale:g}.png').as_uri(), textureScale=texture_scale,
                              shared='sharedWallpaper: true' in surfaces,
                              cropDock='region: dockBackdrop.region' in surfaces,
                              retainedDockRegion=(original/'shell/DockBackdropRegion.qml').exists(),
                              cachedBarBand='cacheWallpaper: true' in surfaces)
            log = renderer.render(source/'qml/ShellRender.qml', png=target.with_suffix('.png'),
                                  stats=target.with_suffix('.json'), properties=properties,
                                  width=1536, height=960, scale=args.scale, screen_scale=args.screen_scale,
                                  warmup=500, milliseconds=100, ready_property="ready", shader_dir=source/'shaders',
                                  extra_env=dict(EMAKI_TEST_SYSTEM='0', EMAKI_BIN='', EMAKI_SETTINGS_PROFILE='',
                                                 EMAKI_TEST_MPRIS='0', EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_TRAY='0',
                                                 EMAKI_SHELL_GLASS_RENDERER='canvas', MALLOC_CONF='thp:never',
                                                 XDG_DATA_DIRS=str(out/'data')))
            target.with_suffix('.log').write_text(log)
            assert not any(term in log for term in ('ReferenceError', 'TypeError', 'Cannot assign', 'Unable to assign', 'Binding loop')), log
            print(f'rendered {name} {shot}', flush=True)
        row = differences(out/f'before-{shot}.png', out/f'after-{shot}.png', out/f'diff-{shot}-16x.png')
        if args.candidate_scale:
            row['scale_candidate'] = differences(out/f'after-{shot}.png', out/f'candidate-{shot}.png', out/f'diff-scale-{shot}-16x.png')
        report['surfaces'][shot] = row
        (out/'report.json').write_text(json.dumps(report, indent=2)+'\n')
        print(shot, row, flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
