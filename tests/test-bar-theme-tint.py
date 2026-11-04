#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real bar shader pixels with synthetic capture starvation; no compositor proof."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / '.cache/evidence/bar-theme-tint'
spec = importlib.util.spec_from_file_location('theme_pictures', ROOT / 'tests/theme-pictures.py')
pictures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pictures)


MOCK = """// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
Item {
    id: capture
    property var captureSource
    property bool live: true
    property bool paintCursor: false
    readonly property var driver: parent.testDriver
    property string heldTexture: ""
    readonly property bool hasContent: captureSource !== null && frame.status === Image.Ready
    readonly property size sourceSize: Qt.size(width, height)
    signal frameCaptured()
    function damageIntersects(rect) { return true; }
    function publish(texture) {
        heldTexture = texture;
        driver.capturedTexture = texture;
    }
    onCaptureSourceChanged: {
        if (captureSource !== null) {
            ++driver.restarts;
            // The first unconditional copy always samples actual compositor pixels.
            publish(driver.compositorTexture);
        }
    }
    Image {
        id: frame
        anchors.fill: parent
        source: capture.heldTexture
        onStatusChanged: if (status === Image.Ready) {
            ++capture.driver.completedCaptures;
            capture.frameCaptured();
        }
    }
    Connections {
        target: capture.driver
        function onCompositorTextureChanged() {
            if (capture.captureSource && !capture.driver.scene.launcherPresent)
                capture.publish(capture.driver.compositorTexture);
        }
        function onTextureChanged() {
            if (!capture.captureSource) return;
            if (!capture.driver.texture)
                capture.publish(capture.driver.creamTexture);
            else if (!capture.driver.scene.launcherPresent)
                capture.publish(capture.driver.compositorTexture);
            // While the peer is present, retain the cream frame indefinitely.
        }
    }
}
"""

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--without-refresh', action='store_true', help='Disable BarBackdrop refresh handlers in the temporary copy')
    parser.add_argument('--mode', choices=('wallpaper', 'capture', 'both'), default='both')
    args = parser.parse_args()
    output = OUTPUT / ('without-refresh' if args.without_refresh else 'current')
    output.mkdir(parents=True, exist_ok=True)
    pictures.WORK = output / 'support'
    with tempfile.TemporaryDirectory(prefix='bar-tint-') as temporary:
        scratch = Path(temporary)
        shell = scratch / 'shell'
        pictures.stage(ROOT, shell)
        fixture = shell / 'ShellRender.qml'
        fixture.write_text(fixture.read_text().replace('id: root\n',
            'id: root\n    readonly property alias fixtureBarMap: barMap\n'
            '    readonly property alias fixtureLauncherMap: launcherMap\n', 1))
        text = fixture.read_text()
        old = '    Backdrop {\n        id: barMap\n        bandCrop: root.cachedBarBand'
        assert text.count(old) == 1
        text = text.replace(old, '    BarBackdrop {\n        id: barMap\n'
            '        mode: root.parent.backdropMode\n'
            '        surfacePresent: scene.launcherPresent\n'
            '        testDriver: root.parent\n'
            '        screen: ({})\n'
            '        wallpaperTexture: root.configured ? root.wallpaper : ""\n'
            '        screenWidth: root.width\n        screenHeight: root.height\n'
            '        dpr: root.textureScale\n        cacheWallpaper: true\n'
            '        damageAwareCapture: true')
        fixture.write_text(text)
        backdrop = shell / 'DockBackdrop.qml'
        text = backdrop.read_text()
        assert text.count('ScreencopyView {') == 1
        assert text.count('property ShellScreen screen: null') == 1
        backdrop.write_text(text.replace('ScreencopyView {', 'SyntheticCapture {')
            .replace('property ShellScreen screen: null', 'property var screen: null\n    property var testDriver: null'))
        (shell / 'SyntheticCapture.qml').write_text(MOCK)
        if args.without_refresh:
            bar = shell / 'BarBackdrop.qml'
            text = bar.read_text()
            assert 'refresh' in text
            # Shadow the inherited refresh method in this temporary negative control.
            text = text.replace('DockBackdrop {', 'DockBackdrop {\n    function refresh(): void {}', 1)
            bar.write_text(text)
        shutil.copyfile(ROOT / 'tests/fixtures/BarThemeTint.qml' , shell / 'BarThemeTint.qml')
        textures = []
        y, x = np.indices((960, 1536))
        for index in range(2):
            pixels = np.stack(((x // 31 * 27 + index * 113) % 256,
                               (y // 19 * 43 + index * 71) % 256,
                               ((x + y) // 29 * 53 + index * 149) % 256), axis=2)
            path = scratch / f'pattern-{index}.png'
            Image.fromarray(pixels.astype(np.uint8)).save(path)
            textures.append(path.as_uri())
        cream = scratch / 'cream.png'
        Image.new('RGB', (1536, 960), '#fff8f3').save(cream)
        modes = ('wallpaper', 'capture') if args.mode == 'both' else (args.mode,)
        for mode in modes:
            run_cases(shell, textures, cream.as_uri(), mode, output)


def run_cases(shell, textures, cream, mode, output):
    crops = {}
    states = {}
    histories = {}
    for case, scenario, surface in [('initial', 'initial', 'launcher'),
                                    ('cream', 'cream', 'launcher'),
                                    ('reference', 'plain', 'launcher'),
                                    ('launcher', 'surface', 'launcher'),
                                    ('settings', 'surface', 'settings')]:
        stem = output / (mode + '-' + case)
        log = pictures.RENDER.render(shell / 'BarThemeTint.qml',
            png=stem.with_suffix('.png'), stats=stem.with_suffix('.json'),
            properties=dict(firstTexture=textures[0], secondTexture=textures[1],
                            scenario=scenario, surfaceKind=surface, creamTexture=cream, backdropMode=mode),
            width=1536, height=84, warmup=500, milliseconds=100,
            ready_property='ready', shader_dir=ROOT / '.cache/shell-shaders',
            extra_env=dict(pictures.environment(), LP_NUM_THREADS='1', QML_DISABLE_DISTANCEFIELD='1'))
        stem.with_suffix('.log').write_text(log)
        for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Cannot assign', 'Failed to find shader'):
            assert error not in log, (case, error, log)
        stats = json.loads(stem.with_suffix('.json').read_text())['fixture_end']
        state = stats['state']
        states[case] = state
        histories[case] = stats['history']
        assert stats['history'][0]['style'] == 'emaki', stats
        assert state['separate_windows'] and state['bar_map_same_window'], stats
        assert state['glass_ready'] and state['backdrop_ready'], stats
        if scenario == 'surface':
            assert any(row['launcher_open'] and row['expansion'] == 1 for row in stats['history']), stats
            if surface == 'settings':
                assert any(row['settings'] for row in stats['history']), stats
        assert state['sharp_width'] > 0 and stats['graphics_api'] == 3, stats
        image = np.asarray(Image.open(stem.with_suffix('.png')).convert('RGB'))
        crops[case] = []
        for key in ('logo_rect', 'workspace_rect', 'clock_rect', 'system_rect'):
            x0, y0, width, height = [round(value) for value in state[key]]
            crops[case].append(image[y0 + 8:y0 + height - 8, x0 + 8:x0 + width - 8])
        print('RENDER', mode, case, json.dumps(state), flush=True)
        if mode == 'capture':
            if scenario == 'surface':
                before_close = next(row for row in stats['history'] if row['phase'] == 4)
                assert before_close['captured_texture'] == cream, stats
                assert before_close['compositor_texture'] == textures[1], stats
            assert state['capture_settled'] and state['content_frames'] >= 2, stats
            assert state['completed_captures'] > 0, stats
    for index in range(4):
        assert not np.array_equal(crops['initial'][index], crops['reference'][index]), 'replacement retained original pixels'
        for case in ('launcher', 'settings'):
            delta = np.abs(crops[case][index].astype(int) - crops['reference'][index].astype(int))
            assert not np.array_equal(crops[case][index], crops['cream'][index]), (mode, case, 'retained cream')
            assert delta.max() <= 1, (mode, case, index, int(delta.max()), int(np.count_nonzero(delta)))
    for case in ('reference', 'launcher', 'settings'):
        assert states[case]['style'] == '07-blue-light', states[case]
        if mode == 'capture':
            assert states[case]['captured_texture'] == textures[1], states[case]
            if case != 'reference':
                before_close = next(row for row in histories[case] if row['phase'] == 4)
                assert states[case]['capture_restarts'] > before_close['capture_restarts'], states[case]
    print('PASS', mode, 'bar pills match replacement after launcher and Settings close; synthetic capture, no compositor proof')

if __name__ == '__main__':
    main()
