#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""A frozen crop must still synchronize the real capture methods and request frames."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import reaper
reaper.guard()

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / '.cache/damage/cadence'


def method(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end] + '\n'


def main():
    source_dir = Path(os.environ['QUICKSHELL_SOURCE_DIR']).resolve()
    source = (source_dir / 'src/wayland/screencopy/view.cpp').read_text()
    module = OUTPUT / 'imports/DamageCadence'
    module.mkdir(parents=True, exist_ok=True)
    (module / 'cadence-methods.inc').write_text('\n'.join(method(source, signature) for signature in (
        'void ScreencopyView::onFrameCaptured()',
        'bool ScreencopyView::damageIntersects(',
        'QSGNode* ScreencopyView::updatePaintNode(')))
    fixture = ROOT / 'tests/fixtures/damage-cadence.cpp'
    mocdir = Path(subprocess.check_output(['pkg-config', '--variable=libexecdir', 'Qt6Core'], text=True).strip())
    subprocess.run([str(mocdir / 'moc'), str(fixture), '-o', str(module / 'damage-cadence.moc')], check=True, timeout=30)
    flags = shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', 'Qt6Quick'], text=True))
    subprocess.run([os.environ.get('CXX', 'c++'), '-std=c++17', '-fPIC', '-shared', str(fixture),
                    '-I' + str(module), '-o', str(module / 'libdamagecadence.so'), *flags], check=True, timeout=90)
    (module / 'qmldir').write_text('module DamageCadence\nplugin damagecadence\n')
    spec = importlib.util.spec_from_file_location('render_shell', ROOT / 'tests/render-shell.py')
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    imports = str(module.parent.resolve()) + os.pathsep + str(renderer.build_plugin())
    scene = OUTPUT / 'scene'
    scene.mkdir(exist_ok=True)
    shutil.copy(ROOT / 'tests/fixtures/DamageCaptureCadence.qml', scene)
    rows = []
    for gated in (False, True):
        name = 'gated' if gated else 'live'
        statsfile = OUTPUT / (name + '.json')
        log = renderer.render(scene / 'DamageCaptureCadence.qml', stats=statsfile,
                              properties={'gated': gated}, width=400, height=100,
                              ready_property='ready', warmup=150, milliseconds=350,
                              extra_env={'QML_IMPORT_PATH': imports})
        (OUTPUT / (name + '.log')).write_text(log)
        stats = json.loads(statsfile.read_text())
        assert stats['readiness_reached'] and 'llvmpipe' in stats['renderer'], stats
        values = stats['fixture_end']
        assert values['capture_frames'] >= 30, values
        assert stats['frames'] >= 10, stats
        rows.append({'gated': gated, 'capture_frames': values['capture_frames'],
                     'draws_per_frame': stats['gl_draw_calls'] / stats['frames']})
    assert rows[1]['draws_per_frame'] < rows[0]['draws_per_frame'], rows
    (OUTPUT / 'results.json').write_text(json.dumps(rows, indent=2) + '\n')
    print('PASS real Qt frozen-crop capture cadence:', json.dumps(rows))


if __name__ == '__main__':
    main()
