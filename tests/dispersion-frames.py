#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render deterministic production appearance frames with surfaceless Qt Quick.

A source tree argument permits identical fixtures against a read-only baseline.
Each image is an actual RHI draw of ShellScene, not a reconstruction of uniforms.
The scene clock is stepped to the requested time and frozen before its PNG.
Battery and panel scenarios send native pointer events through production handlers.
The explicit window-leave variant also exercises modal pointer-focus transfer.
"""
import argparse
import importlib.util
import json
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import shlex
import subprocess

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('render_shell', ROOT / 'tests/render-shell.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, default=ROOT / '.cache/dispersion-frames')
    parser.add_argument('--wallpaper', type=Path, default=ROOT / 'art/wallpaper/ring.png')
    parser.add_argument('--compare', type=Path, help='Compare matching frames with this output directory')
    parser.add_argument('--scenarios', nargs='+', default=['battery', 'panel', 'dock', 'dock-bubble'],
                        choices=['battery', 'panel', 'panelclick', 'panelleave', 'dock', 'dock-bubble'])
    parser.add_argument('--frames', nargs='+', type=float,
                        default=[round(i * 1000 / 60, 3) for i in range(25)],
                        help='Animation timestamps in ms; default: first 25 frames at 60 Hz')
    parser.add_argument('--regression', action='store_true', help='Render the hover, click/leave and settled regression matrix')
    parser.add_argument('--scale', type=float, default=1, help='Render target and screen DPR')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    shutil.copytree(args.source / 'shell', out / 'qml', dirs_exist_ok=True)
    for name in ('DispersionFrames.qml', 'SystemFixture.qml'):
        shutil.copyfile(ROOT / 'tests/fixtures' / name, out / 'qml' / name)
    with (out / 'qml/qmldir').open('a') as f:
        f.write('\nSystemFixture 1.0 SystemFixture.qml\n')
    shader_dir = out / 'shaders'
    shader_dir.mkdir(exist_ok=True)
    qsb = os.environ.get('QSB') or shutil.which('qsb')
    if not qsb:
        bindir = subprocess.check_output(['pkg-config', '--variable=libexecdir', 'Qt6Core'], text=True).strip()
        qsb = str(Path(bindir) / 'qsb')
        if not Path(qsb).exists():
            prefix = subprocess.check_output(['pkg-config', '--variable=prefix', 'Qt6Core'], text=True).strip()
            qsb = str(Path(prefix) / 'lib/qt6/bin/qsb')
    for shader in (args.source / 'shell/shaders').glob('*.frag'):
        subprocess.run([qsb, '--glsl', '100 es,120,150', '-o', str(shader_dir / (shader.name + '.qsb')), str(shader)], check=True)
    applications = out / 'data/applications'
    applications.mkdir(parents=True, exist_ok=True)
    for name in ('Browser', 'Editor', 'Files'):
        (applications / f'fixture-{name.lower()}.desktop').write_text(
            f'[Desktop Entry]\nType=Application\nName={name}\nExec=true\nTerminal=false\n')
    # Every helper, socket and configuration write remains inside the worktree.
    runtime = ROOT / '.cache/dispersion-frames/run'
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    renderer.runtime_path = lambda profile: runtime
    imports = renderer.build_plugin()
    module = imports / 'DispersionInput'
    module.mkdir(exist_ok=True)
    source = ROOT / 'tests/dispersion-pointer.cpp'
    with (module / 'build.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        version = subprocess.check_output(['pkg-config', '--modversion', 'Qt6Core'])
        fingerprint = hashlib.sha256(source.read_bytes() + version).hexdigest()
        stamp = module / 'source.sha256'
        if not stamp.exists() or stamp.read_text() != fingerprint:
            mocdir = Path(subprocess.check_output(['pkg-config', '--variable=libexecdir', 'Qt6Core'], text=True).strip())
            subprocess.run([str(mocdir / 'moc'), str(source), '-o', str(module / 'dispersion-pointer.moc')], check=True)
            flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'Qt6Quick'], text=True))
            library = module / 'libdispersioninput-next.so'
            subprocess.run(['c++', '-std=c++17', '-shared', '-fPIC', str(source), '-I' + str(module),
                            '-o', str(library), *flags], check=True)
            library.replace(module / 'libdispersioninput.so')
            stamp.write_text(fingerprint)
        (module / 'qmldir').write_text('module DispersionInput\nplugin dispersioninput\n')
    report = {'source': str(args.source.resolve()), 'method': 'native Qt Quick RHI; surfaceless llvmpipe; deterministic production frame functions', 'frames': []}
    matrix = [(scenario, args.frames) for scenario in args.scenarios]
    if args.regression:
        animation = [round(i * 1000 / 60, 3) for i in range(13)] + [400]
        matrix = [('battery', animation), ('panelleave', animation),
                  ('panelclick', [0, 16.667, 33.333, 50, 66.667, 400]),
                  ('dock', [0, 400]), ('dock-bubble', [0, 400])]
    for scenario, frames in matrix:
        for frame in frames:
            target = out / (f'{scenario}-{frame:07.2f}ms'.replace('.', '_'))
            properties = dict(shot=scenario, frameMs=frame,
                              wallpaper=args.wallpaper.resolve().as_uri(),
                              shared=True, cropDock=True, retainedDockRegion=True, cachedBarBand=True)
            log = renderer.render(out / 'qml/DispersionFrames.qml', png=target.with_suffix('.png'),
                                  stats=target.with_suffix('.json'), properties=properties,
                                  width=1536, height=960, scale=args.scale, screen_scale=args.scale,
                                  warmup=100, milliseconds=20, ready_property='ready', shader_dir=shader_dir,
                                  extra_env=dict(EMAKI_TEST_SYSTEM='0', EMAKI_BIN='', EMAKI_SETTINGS_PROFILE='',
                                                 EMAKI_TEST_MPRIS='0', EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_TRAY='0',
                                                 EMAKI_SHELL_GLASS_RENDERER='canvas', XDG_DATA_DIRS=str(out / 'data')))
            target.with_suffix('.log').write_text(log)
            errors = [line for line in log.splitlines() if any(term in line for term in
                      ('ReferenceError', 'TypeError', 'Cannot assign', 'Unable to assign', 'Binding loop'))]
            if errors:
                raise RuntimeError('\n'.join(errors))
            stats = json.loads(target.with_suffix('.json').read_text())
            assert stats['readiness_reached'] and 'llvmpipe' in stats['renderer'], stats
            row = {'scenario': scenario, 'frame_ms': frame, 'png': str(target.with_suffix('.png')), 'fixture': stats['fixture_end']}
            if args.compare:
                import numpy as np
                from PIL import Image
                before = args.compare / target.with_suffix('.png').name
                a = np.asarray(Image.open(before).convert('RGBA'), dtype=np.int16)
                b = np.asarray(Image.open(target.with_suffix('.png')).convert('RGBA'), dtype=np.int16)
                delta = np.abs(a - b)
                row['comparison'] = {'changed_pixels': int(np.any(delta != 0, axis=2).sum()),
                                     'maximum_channel_delta': int(delta.max())}
                # Other animated surfaces can rasterize at different times; also
                # report the surface being exercised, in fixed fixture coordinates.
                roi = {'battery': (1420, 0, 1536, 50), 'panel': (1040, 0, 1536, 960),
                       'panelclick': (1420, 0, 1536, 50), 'panelleave': (1420, 0, 1536, 50),
                       'dock': (600, 800, 950, 960), 'dock-bubble': (600, 800, 950, 960)}[scenario]
                x0, y0, x1, y1 = (round(v * args.scale) for v in roi)
                region = delta[y0:y1, x0:x1]
                row['comparison'].update(region=roi,
                    changed_region_pixels=int(np.any(region != 0, axis=2).sum()),
                    maximum_region_channel_delta=int(region.max()))
                Image.fromarray(np.minimum(delta[:, :, :3] * 8, 255).astype(np.uint8)).save(target.with_suffix('.difference.png'))
            report['frames'].append(row)
            (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
            print(f'Rendered {scenario} at {frame:g} ms', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
