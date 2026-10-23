#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render every translated launcher tab in both themes with normal and stressed widths."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('renderer', ROOT / 'tests/render-shell.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
LANGUAGES = ('en', 'es', 'pt_BR', 'de', 'fr', 'ru')
MODES = ('All', 'Apps', 'Windows', 'Files', 'Clipboard', 'Commands', 'Emoji')


def contained(inner, outer, tolerance=.75):
    return (inner['x'] >= outer['x'] - tolerance and inner['y'] >= outer['y'] - tolerance
            and inner['x'] + inner['width'] <= outer['x'] + outer['width'] + tolerance
            and inner['y'] + inner['height'] <= outer['y'] + outer['height'] + tolerance)


def overlap(a, b):
    return (min(a['x'] + a['width'], b['x'] + b['width']) - max(a['x'], b['x']) > .75
            and min(a['y'] + a['height'], b['y'] + b['height']) - max(a['y'], b['y']) > .75)


def signal_geometry(state, offset=None, fade=None):
    """Measure ink crossing the viewport and ink within each fade's outer half."""
    offset = state['contentX'] if offset is None else offset
    fade = state['fadeWidth'] if fade is None else fade
    left = state['strip']['x']
    right = left + state['strip']['width']
    ink = [(label['ink']['x'] + state['contentX'] - offset,
            label['ink']['x'] + label['ink']['width'] + state['contentX'] - offset)
           for tab in state['tabs'] for label in tab['labels']]
    return {
        'left': {'hidden': min(a for a, b in ink) < left - .01,
                 'signal': max(max(0, min(b, left + fade / 2) - max(a, left)) for a, b in ink)},
        'right': {'hidden': max(b for a, b in ink) > right + .01,
                  'signal': max(max(0, min(b, right) - max(a, right - fade / 2)) for a, b in ink)}
    }


def signal_feasibility(state):
    """Use a valid captured placement first; sample alternatives only to diagnose failures."""
    edges = signal_geometry(state)
    left = state['strip']['x'] + (state['fadeWidth'] if edges['left']['hidden'] else 0)
    right = state['strip']['x'] + state['strip']['width'] - (state['fadeWidth'] if edges['right']['hidden'] else 0)
    bubble = state['bubble']
    if (16 <= state['fadeWidth'] <= 48 and bubble['x'] >= left - .75
            and bubble['x'] + bubble['width'] <= right + .75
            and all(not edge['hidden'] or edge['signal'] > .5 for edge in edges.values())):
        return {'sampleStep': 0, 'feasible': [{'contentX': state['contentX'], 'fadeWidth': state['fadeWidth']}]}
    maximum = max(0, state['contentWidth'] - state['strip']['width'])
    valid = []
    for fade in range(16, 49):
        for step in range(round(maximum * 4) + 1):
            offset = min(maximum, step / 4)
            edges = signal_geometry(state, offset, fade)
            left = state['strip']['x'] + (fade if edges['left']['hidden'] else 0)
            right = state['strip']['x'] + state['strip']['width'] - (fade if edges['right']['hidden'] else 0)
            bubble = state['bubble']
            x = bubble['x'] + state['contentX'] - offset
            if (x >= left - .75 and x + bubble['width'] <= right + .75
                    and all(not edge['hidden'] or edge['signal'] > .5 for edge in edges.values())):
                valid.append({'contentX': offset, 'fadeWidth': fade})
                break
    return {'sampleStep': .25, 'feasible': valid}


def validate(state, mode, labels, theme):
    assert state["dark"] == (theme == "dark"), ("wrong theme", state["dark"], theme)
    assert state['labels'] == labels, ('wrong translated labels', state['labels'], labels)
    assert state['mode'] == mode, state
    assert state['search']['width'] >= 160, ('search below usable minimum', state['search'])
    assert len(state['tabs']) == 7, ('missing tab', state['tabs'])
    assert state['strip'] is not None, 'missing tab strip'
    assert contained(state['strip'], state['body']), ('strip outside body', state)
    assert not overlap(state['search'], state['strip']), ('search overlaps tabs', state)
    maximum = max(0, state['contentWidth'] - state['strip']['width'])
    assert -.01 <= state['contentX'] <= maximum + .01, ('scroll outside bounds', state)
    signals = signal_geometry(state)
    for side, field in (('left', 'fadeLeft'), ('right', 'fadeRight')):
        assert state[field] == signals[side]['hidden'], ('fade does not match hidden ink', side, signals, state)
    assert state['underEffect'] and state['overEffect'], ('missing production mask effect', state)
    overflow = state['fadeLeft'] or state['fadeRight']
    assert state['underLayer'] == state['overLayer'] == overflow, ('wrong production fade layer state', state)
    assert state.get('maskLayer', state['underLayer']) == state['underLayer'], ('wrong mask layer state', state)
    assert 16 <= state['fadeWidth'] <= 48, ('fade width outside spacing range', state['fadeWidth'], state['strip']['width'])
    assert state['fadeWidth'] < state['strip']['width'] / 2, ('invalid fade width', state)
    clear = dict(state['strip'])
    if state['fadeLeft']:
        clear['x'] += state['fadeWidth']
        clear['width'] -= state['fadeWidth']
    if state['fadeRight']:
        clear['width'] -= state['fadeWidth']
    chosen = next(tab for tab in state['tabs'] if tab['name'] == mode)
    if state['scenario'] != 'reveal':
        validate_wheel(state)
        return
    assert contained(state['bubble'], clear), ('selected pill enters fade', state['bubble'], clear)
    assert contained(chosen['bounds'], clear), ('selected tab enters fade', chosen, clear)
    assert contained(chosen['bounds'], state['strip']), ('selected tab clipped', chosen, state['strip'])
    for label in chosen['labels']:
        assert not label['truncated'] and label['contentWidth'] <= label['bounds']['width'] + .75, label
        assert contained(label['bounds'], state['strip']), ('selected label clipped', label)
        for tab in state['tabs']:
            if tab['name'] != mode:
                for other in tab['labels']:
                    assert not overlap(label['bounds'], other['bounds']), ('labels overlap', label, other)
    for field in ('x', 'y', 'width', 'height'):
        assert abs(state['modeRect'][field] - chosen['bounds'][field]) < .75, ('mode highlight misaligned', state)
        expected = chosen['bounds'][field] + (-4 if field in ('x', 'y') else 8)
        assert abs(state['bubble'][field] - expected) <= 1, ('pill misaligned', state['bubble'], chosen)
    assert state['bubble']['alpha'] > .95, ('pill not shown', state['bubble'])
    selected_text = state['labels'][MODES.index(mode)]
    on_glass = [item for item in state['texts'] if item['objectName'] == 'launcher-mode-ink'
                and item['text'] == selected_text
                and contained(item['bounds'], chosen['bounds'])]
    assert len(on_glass) == 1, ('selected on-glass text not rendered', mode, state['texts'])
    assert contained(on_glass[0]['bounds'], state['strip']), ('on-glass label clipped', on_glass)
    assert contained(on_glass[0]['bounds'], on_glass[0]['clip']), ('on-glass label clip', on_glass)
    for item in state['texts']:
        if item['painted']['width'] > .75 and item['painted']['height'] > .75:
            assert contained(item['painted'], state['body']), ('rendered text outside launcher', item)
    for side, field in (('left', 'fadeLeft'), ('right', 'fadeRight')):
        assert not state[field] or signals[side]['signal'] > .5, ('fade has no label ink in outer half', side, signals, signal_feasibility(state))


def validate_wheel(state):
    assert abs(state['contentX'] - state['wheelBefore']) > 1, ('wheel did not scroll', state)
    assert abs(state['contentX'] - state['wheelBefore'] + state['wheelDelta']) < 1, ('wrong wheel distance', state)
    side = state['scenario'].removeprefix('wheel-')
    assert state['fadeLeft' if side == 'left' else 'fadeRight'], ('wheel edge is not faded', state)
    bubble, strip = state['bubble'], state['strip']
    selected = next(tab for tab in state['tabs'] if tab['name'] == state['mode'])
    ink = selected['labels'][0]['ink']
    if side == 'left':
        ink_coverage = (ink['x'] - strip['x']) / state['fadeWidth']
    else:
        ink_coverage = (strip['x'] + strip['width'] - ink['x'] - ink['width']) / state['fadeWidth']
    assert 0 < ink_coverage < 1, ('selected label is not inside faded zone', side, ink_coverage, ink)
    coverage = 1.0
    if state['fadeLeft']:
        coverage = min(coverage, max(0, (bubble['x'] - strip['x']) / state['fadeWidth']))
    if state['fadeRight']:
        coverage = min(coverage, max(0, (strip['x'] + strip['width'] - bubble['x'] - bubble['width']) / state['fadeWidth']))
    assert .1 < coverage < .9, ('selected pill is not partially in fade', coverage, state)
    drop = next((drop for drop in state['drops'] if drop['kind'] == 'mode'), None)
    # Omitting an intersecting drop is also permitted; an opaque clipped drop is not.
    if drop is not None:
        assert 0 < drop['alpha'] <= bubble['alpha'] * coverage + .025, ('mode drop ignores fade coverage', drop, coverage)


def check_pixels(path, width):
    with Image.open(path) as source:
        image = source.convert('RGB')
    # Glass deliberately casts a soft shadow outside the panel. Detect sharp glyph edges,
    # rather than rejecting that smooth shadow as escaped text.
    horizontal = ImageChops.difference(image, ImageChops.offset(image, 1, 0))
    vertical = ImageChops.difference(image, ImageChops.offset(image, 0, 1))
    difference = ImageChops.lighter(horizontal, vertical)
    # The fixture adds a 20 px background margin around the launcher.
    for rect in ((1, 1, width + 39, 19), (1, 742, width + 39, 759),
                 (1, 20, 19, 740), (width + 22, 20, width + 39, 740)):
        assert max(high for low, high in difference.crop(rect).getextrema()) <= 25, ('sharp pixels outside launcher', path, rect)


def check_fade_pixels(prefix, state):
    for layer, y in (('under', 780), ('over', 830)):
        with Image.open(f'{prefix}.png') as source:
            image = source.convert('RGB')
            row = [image.getpixel((20 + x, y + round(state['strip']['height'] / 2)))[0]
                   for x in range(round(state['strip']['width']))]
        extent = round(state['fadeWidth'])
        assert min(row[extent:len(row) - extent]) >= 250, ('nonopaque fade interior', layer)
        for edge, active in ((row[:extent], state['fadeLeft']),
                             (list(reversed(row[-extent:])), state['fadeRight'])):
            if not active:
                assert min(edge) >= 250, ('unneeded edge fade', layer, edge)
            else:
                assert edge[0] < 25 and edge[-1] > 230, ('missing smooth fade', layer, edge)
                assert len(set(edge)) >= 10, ('hard clip instead of fade', layer, edge)
                assert all(a <= b + 2 for a, b in zip(edge, edge[1:])), ('nonmonotonic fade', layer, edge)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--language', action='append', choices=LANGUAGES)
    parser.add_argument('--mode', action='append', choices=MODES)
    parser.add_argument('--theme', action='append', choices=('light', 'dark'))
    parser.add_argument('--width', action='append', type=int)
    parser.add_argument('--glass', action='store_true', help='render the real wallpaper glass shader')
    parser.add_argument('--source', type=Path, help='use a saved LauncherBody.qml for a regression witness')
    parser.add_argument('--output', type=Path, default=ROOT / '.cache/evidence/launcher-tabs')
    parser.add_argument('--skip-compile', action='store_true')
    args = parser.parse_args()
    if not args.skip_compile:
        subprocess.run([sys.executable, 'i18n/catalogues.py', 'compile'], cwd=ROOT, check=True)
    if args.glass:
        subprocess.run(['make', 'shell-shaders'], cwd=ROOT, check=True)
    args.output.mkdir(parents=True, exist_ok=True)
    failures = []
    geometry_limits = []
    count = 0
    with tempfile.TemporaryDirectory(prefix='launcher-tabs-') as directory:
        base = Path(directory)
        qml = base / 'shell'
        shutil.copytree(ROOT / 'shell', qml)
        if args.source:
            shutil.copyfile(args.source, qml / 'LauncherBody.qml')
        subprocess.run([sys.executable, str(ROOT / 'scripts/render-paths'), '--source', str(base)], check=True)
        for path in qml.rglob('*.qml'):
            content = path.read_text()
            content = re.sub(r'(?m)^(\s*)command: (?!\[).+$', r'\1command: ["/nonexistent-emaki-fixture-command"]', content)
            content = re.sub(r'command: \[', 'command: ["/nonexistent-emaki-fixture-command", ', content)
            content = re.sub(r'(\.command = )\[', r'\1["/nonexistent-emaki-fixture-command", ', content)
            content = re.sub(r'(?m)^(\s*\w+\.command = )(?!\[).+$', r'\1["/nonexistent-emaki-fixture-command"];', content)
            path.write_text(content)
        shutil.copyfile(ROOT / 'tests/fixtures/LauncherTabsRender.qml', qml / 'LauncherTabsRender.qml')
        for language in args.language or LANGUAGES:
            catalogue = ET.parse(ROOT / f'i18n/shell/{language}.ts')
            translations = {message.findtext('source'): ''.join(message.find('translation').itertext())
                            for context in catalogue.findall('context')
                            if context.findtext('name') == 'LauncherBody'
                            for message in context.findall('message')}
            labels = [translations[name] for name in MODES]
            env = {'EMAKI_LANGUAGE': language, 'EMAKI_LOCALE_DIR': str(ROOT / 'i18n/locale'),
                   'QML_IMPORT_PATH': str(renderer.build_plugin()) + os.pathsep + str(ROOT / '.cache/i18n-native/qml'),
                   'EMAKI_BIN': '/nonexistent-emaki-fixture-command', 'EMAKI_SETTINGS_PROFILE': '',
                   'EMAKI_FIXTURE_WALLPAPER': str(ROOT / 'art/wallpaper/ring.png'),
                   'EMAKI_SHELL_NOTIFICATIONS': '0', 'EMAKI_SHELL_TRAY': '0',
                   'EMAKI_SHELL_CLIPBOARD_RECORDER': '0', 'EMAKI_LIVE_SESSION': '0'}
            for theme in args.theme or ('light', 'dark'):
                for width in args.width or (720, 620, 480):
                    cases = [(mode, 'reveal') for mode in args.mode or MODES]
                    if language == 'ru' and width == 720:
                        cases += [('Apps', 'wheel-left'), ('Commands', 'wheel-right')]
                    for mode, scenario in cases:
                        name = f'{language}-{theme}-{width}-{mode.lower()}'
                        if scenario != 'reveal':
                            name += '-' + scenario
                        stats = args.output / f'{name}.json'
                        log = renderer.render(qml / 'LauncherTabsRender.qml', png=args.output / f'{name}.png',
                                              stats=stats, properties={'mode': mode, 'bodyWidth': width, 'glass': args.glass, 'dark': theme == 'dark', 'scenario': scenario},
                                              shader_dir=ROOT / '.cache/shell-shaders' if args.glass else None,
                                              width=width + 40, height=880, warmup=200, milliseconds=1, extra_env=env, ready_property='ready')
                        (args.output / f'{name}.log').write_text(log)
                        rendered = json.loads(stats.read_text())
                        state = rendered['fixture_end']
                        state['signalGeometry'] = signal_geometry(state)
                        if scenario == 'reveal':
                            state['signalFeasibility'] = signal_feasibility(state)
                            if not state['signalFeasibility']['feasible']:
                                geometry_limits.append(name)
                        stats.write_text(json.dumps(rendered, indent=2))
                        try:
                            for error in ('ReferenceError:', 'TypeError:', 'Binding loop'):
                                assert error not in log, log
                            validate(state, mode, labels, theme)
                            assert state['glassReady'] == args.glass, ('glass readiness', state['glassReady'])
                            check_pixels(args.output / f'{name}.png', width)
                            check_fade_pixels(args.output / name, state)
                        except AssertionError as error:
                            failures.append((name, str(error)))
                            print(f'FAIL {name}: {error}', flush=True)
                        else:
                            print(f'PASS {name}', flush=True)
                        count += 1
    (args.output / 'summary.json').write_text(json.dumps({'captures': count, 'failures': failures,
                                                         'noSampledFeasiblePlacement': geometry_limits}, indent=2))
    print(f'{count - len(failures)}/{count} launcher captures passed; evidence: {args.output}')
    return bool(failures)


if __name__ == '__main__':
    sys.exit(main())
