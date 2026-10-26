#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real production crop/blur pixels and GL work with synthetic capture content.

The synthetic source supplies damage decisions; native protocol/mapping tests own
those decisions. This is surfaceless llvmpipe evidence, not compositor acceptance.
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = Path(os.environ.get('EMAKI_DAMAGE_OUTPUT', ROOT / '.cache/damage/pixels'))
spec = importlib.util.spec_from_file_location('render_shell', ROOT / 'tests/render-shell.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)

MOCK = '''// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
Item {
    id: capture
    property var captureSource
    property bool live: true
    property bool paintCursor: false
    readonly property var driver: parent.testDriver
    readonly property bool hasContent: driver.content && captureSource !== null
    readonly property size sourceSize: Qt.size(driver.bufferWidth * driver.bufferDpr, driver.bufferHeight * driver.bufferDpr)
    signal frameCaptured()
    function damageIntersects(rect) { return driver.intersects; }
    // A completed buffer is stretched into the view, including before its replacement.
    Item {
        id: buffer
        width: capture.driver.bufferWidth
        height: capture.driver.bufferHeight
        transform: Scale { xScale: capture.width / buffer.width; yScale: capture.height / buffer.height }
    Rectangle { anchors.fill: parent; color: "#26437b" }
    Repeater {
        model: 24
        Rectangle {
            required property int index
            x: index * 23
            width: 13
            height: buffer.height
            color: index % 2 ? "#e9b32e" : "#497cbd"
        }
    }
    Rectangle {
        x: 105 + capture.driver.insideVersion * 43
        y: 76
        width: 49
        height: 62
        color: capture.driver.insideVersion ? "#f44852" : "#2eee91"
    }
    Rectangle {
        x: 20 + capture.driver.outsideVersion % 300
        y: 250
        width: 60
        height: 45
        color: "#fafafa"
    }
    }
    FrameAnimation {
        running: capture.live
        onTriggered: {
            ++capture.driver.outsideVersion;
            ++capture.driver.sourceFrames;
            if (capture.hasContent)
                capture.frameCaptured();
        }
    }
}
'''


def offscreen_work(stats):
    """Exclude final-target redraws; count FBO work per completed capture frame."""
    frames = stats['fixture_end']['source_frames'] - stats['fixture_start']['source_frames']
    target = f"{round(stats['width'] * stats['scale'])}x{round(stats['height'] * stats['scale'])}"
    layers = [work for work in stats['framebuffer_work'].values()
              if work['draw_viewport_sizes'] != [target]]
    return dict(draws_per_capture=sum(work['draws'] for work in layers) / frames,
                clears_per_capture=sum(work['clears'] for work in layers) / frames,
                viewport_footprint=sorted(work['draw_viewport_sizes'] for work in layers))


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results = []
    digests = {}
    with tempfile.TemporaryDirectory(prefix='fixture-', dir=OUTPUT) as temporary:
        shell = Path(temporary) / 'shell'
        shutil.copytree(ROOT / 'shell', shell)
        source = shell / 'DockBackdrop.qml'
        original = source.read_text()
        for token in ('ScreencopyView {', 'property ShellScreen screen: null'):
            assert original.count(token) == 1, token
        assert 'damageAwareCapture' in original, 'production damage gate missing'
        source.write_text(original.replace('ScreencopyView {', 'SyntheticCapture {')
                          .replace('property ShellScreen screen: null',
                                   'property var screen: null\n    property var testDriver'))
        (shell / 'SyntheticCapture.qml').write_text(MOCK)
        shutil.copyfile(ROOT / 'tests/fixtures/DamageCapturePixels.qml', shell / 'DamageCapturePixels.qml')
        legacy = Path(temporary) / 'legacy'
        shutil.copytree(shell, legacy)
        legacy_mock = MOCK.replace('    signal frameCaptured()\n', '').replace(
            '    function damageIntersects(rect) { return driver.intersects; }\n', '').replace(
            '            if (capture.hasContent)\n                capture.frameCaptured();\n', '')
        assert 'frameCaptured' not in legacy_mock and 'damageIntersects' not in legacy_mock
        (legacy / 'SyntheticCapture.qml').write_text(legacy_mock)
        for scale in (1, 1.25, 2):
            for scenario in ('initial', 'inside', 'repeat', 'reopen', 'geometry', 'dpr', 'buffer', 'fallback', 'unknown'):
                case = f'{scenario}-{scale}'.replace('.', '_')
                images, measurements = [], []
                for gated in (False, True):
                    stem = OUTPUT / (case + ('-gated' if gated else '-live'))
                    fixture = (legacy if scenario == 'fallback' else shell) / 'DamageCapturePixels.qml'
                    log = renderer.render(fixture,
                                          png=stem.with_suffix('.png'), stats=stem.with_suffix('.json'),
                                          properties=dict(gated=gated, scenario=scenario, textureDpr=scale),
                                          width=320, height=300, scale=scale, warmup=150, milliseconds=350,
                                          ready_property='ready', shader_dir=ROOT / '.cache/shell-shaders')
                    stem.with_suffix('.log').write_text(log)
                    for error in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign',
                                  'Cannot assign', 'Failed to deserialize', 'Failed to find shader', 'Detected function', 'QML Connections:'):
                        assert error not in log, (case, gated, log)
                    stats = json.loads(stem.with_suffix('.json').read_text())
                    begin, end = stats['fixture_start'], stats['fixture_end']
                    assert 'llvmpipe' in stats['renderer'] and end['graphics_api'] == 3, stats
                    assert end['ready'] and end['settled'] and end['content_frames'] >= 2, end
                    expected_buffer = [560 * scale, 400 * scale] if scenario == 'buffer' else [480 * scale, 320 * scale]
                    assert end['buffer_size'] == expected_buffer, end
                    assert end['texture_dpr'] == scale * (1.5 if scenario == 'dpr' else 1), end
                    assert end['source_width'] == (520 if scenario == 'geometry' else 480), end
                    assert end['source_height'] == (360 if scenario == 'geometry' else 320), end
                    assert end['damage_tracking'] == (gated and scenario != 'fallback'), end
                    assert end['source_frames'] - begin['source_frames'] >= 10, stats
                    assert stats['frames'] >= 10, stats
                    images.append(np.asarray(Image.open(stem.with_suffix('.png')).convert('RGBA')))
                    measurements.append(stats)
                assert np.array_equal(*images), (case, 'gated crop differs from live crop')
                for row in range(3):
                    pixels = images[0][round((25 + row * 90) * scale):round((95 + row * 90) * scale),
                                       round(25 * scale):round(295 * scale), :3]
                    assert np.unique(pixels.reshape(-1, 3), axis=0).shape[0] > (3 if row == 0 else 16), (case, row, 'flat output')
                draws = [s['gl_draw_calls'] / s['frames'] for s in measurements]
                fbos = [s['drawn_framebuffers_summed_per_frame'] / s['frames'] for s in measurements]
                work = [offscreen_work(stats) for stats in measurements]
                if scenario in ('fallback', 'unknown'):
                    # Qt can redraw the final target a different number of times.
                    # Each capture must nevertheless refresh the same offscreen FBOs.
                    assert work[0] == work[1], (case, work)
                    assert work[0]['draws_per_capture'] > 0 and work[0]['clears_per_capture'] > 0, (case, work)
                else:
                    assert draws[1] < draws[0] * .75, (case, draws)
                    assert fbos[1] < fbos[0] * .75, (case, fbos)
                digests[scale, scenario] = images[0].tobytes()
                results.append(dict(case=case, changed_pixels=0, draws_per_frame=draws,
                                    drawn_fbos_per_frame=fbos, offscreen_work=work,
                                    source_frames=[s['fixture_end']['source_frames'] - s['fixture_start']['source_frames']
                                                   for s in measurements]))
                print(f'PASS {case}: identical pixels; draws/frame {draws}; FBOs/frame {fbos}', flush=True)
        for scale in (1, 1.25, 2):
            assert digests[scale, 'initial'] != digests[scale, 'inside'], 'inside update retained stale texture'
            assert digests[scale, 'inside'] != digests[scale, 'repeat'], 'repeated damage retained stale texture'
            assert digests[scale, 'inside'] == digests[scale, 'reopen'], 'reopen retained stale texture'
            assert digests[scale, 'initial'] != digests[scale, 'geometry'], 'geometry retained stale texture'
            assert digests[scale, 'inside'] != digests[scale, 'buffer'], 'new buffer retained stale texture'
            assert digests[scale, 'inside'] == digests[scale, 'fallback'], 'fallback differs from live pixels'
            assert digests[scale, 'inside'] == digests[scale, 'unknown'], 'unknown damage differs from live pixels'
    (OUTPUT / 'results.json').write_text(json.dumps(results, indent=2) + '\n')
    print('PASS real crop plus six blur layers; synthetic frame decisions, not native capture cadence')


if __name__ == '__main__':
    main()
