#!/usr/bin/env python3
"""Compare fake-mic render work in two shell trees, using isolated llvmpipe.

No audio stream, compositor, live session, host GPU, or running shell is used.
Rates are software-renderer throughput, not laptop GPU or power measurements.
The original tree must contain shell/ and its original shader sources.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('render_shell', ROOT / 'tests/render-shell.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


def prepare(source, destination):
    shutil.copytree(source / 'shell', destination, dirs_exist_ok=True)
    for name in ('SoundRender.qml', 'MicMeterRender.qml', 'SystemFixture.qml'):
        shutil.copyfile(ROOT / 'tests/fixtures' / name, destination / name)
    with (destination / 'qmldir').open('a') as module:
        module.write('\nSoundRender 1.0 SoundRender.qml\nMicMeterRender 1.0 MicMeterRender.qml\nSystemFixture 1.0 SystemFixture.qml\n')
    for name in ('dock', 'down', 'gauss'):
        source = destination / 'shaders' / (name + '.frag')
        subprocess.run(['/usr/lib/qt6/bin/qsb', '--glsl', '100 es,120,150', '--hlsl', '50', '--msl', '12',
                        '-o', str(source.with_suffix('.frag.qsb')), str(source)], check=True, capture_output=True)


def summarize(stats):
    seconds = stats['measured_elapsed_ms'] / 1000
    start, end = stats['fixture_start'], stats['fixture_end']
    return dict(seconds=seconds, frames=stats['frames'], frames_per_second=stats['frames'] / seconds,
                gl_clear_calls=stats['gl_clear_calls'], clear_calls_per_second=stats['gl_clear_calls'] / seconds,
                gl_draw_calls=stats['gl_draw_calls'], draw_calls_per_second=stats['gl_draw_calls'] / seconds,
                fake_samples=end.get('sample_count', 0) - start.get('sample_count', 0),
                meter_updates=end.get('meter_updates', 0) - start.get('meter_updates', 0),
                separate_meter=end.get('separate_meter'), panel_cache_enabled=end.get('panel_cache_enabled'),
                framebuffer_work=stats['framebuffer_work'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True, help='Original repository tree (with shell/)')
    parser.add_argument('--after', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, default=ROOT / '.cache/sound-offscreen')
    parser.add_argument('--seconds', type=float, default=5)
    parser.add_argument('--scale', type=float, default=1.25)
    parser.add_argument('--screen-scale', type=float, default=2)
    parser.add_argument('--sample-ms', type=int, default=21,
                        help='Requested fake callback interval; default 21 ms approximates a 1024/48000 quantum')
    parser.add_argument('--meter-only', action='store_true',
                        help='Render the production meter alone to measure sample delivery without full-panel software-rendering cost')
    parser.add_argument('--samples', nargs='+', choices=('idle', 'constant', 'clamped', 'microjitter', 'varying'),
                        default=('idle', 'constant', 'clamped', 'microjitter', 'varying'))
    args = parser.parse_args()
    if args.seconds <= 0 or args.sample_ms <= 0:
        parser.error('--seconds and --sample-ms must be positive')
    if args.meter_only and any(not (tree / 'shell/MicMeter.qml').is_file() for tree in (args.before, args.after)):
        parser.error('--meter-only requires MicMeter.qml in both supplied shell trees')
    args.output.mkdir(parents=True, exist_ok=True)
    result = {'method': 'offscreen real Quickshell, Qt Quick RHI, surfaceless EGL llvmpipe; wall clock rates; no vsync',
              'note': 'gl_clear_calls count actual GL clears (render pass proxy); framebuffer sizes are actual draw viewports; software throughput is not live FPS or GPU power',
              'scale': args.scale, 'screen_scale': args.screen_scale, 'sample_timer_ms': args.sample_ms,
              'fixture': 'meter-only' if args.meter_only else 'sound-panel', 'runs': {}}
    for label, source in (('before', args.before), ('after', args.after)):
        qml = args.output / label
        prepare(source.resolve(), qml)
        result['runs'][label] = {}
        for samples in args.samples:
            name = label + '-' + samples
            stats_file = args.output / (name + '.json')
            properties = dict(samples=samples, level=1.1 if samples == 'clamped' else .4,
                              startMilliseconds=1, sampleMilliseconds=args.sample_ms)
            log = renderer.render(qml / ('MicMeterRender.qml' if args.meter_only else 'SoundRender.qml'), stats=stats_file,
                                  properties=properties, width=400 if args.meter_only else 1536,
                                  height=24 if args.meter_only else 960,
                                  scale=args.scale, screen_scale=args.screen_scale,
                                  warmup=1000, milliseconds=round(args.seconds * 1000),
                                  ready_property='ready', shader_dir=qml / 'shaders',
                                  extra_env={'EMAKI_FIXTURE_WALLPAPER': (ROOT / 'art/wallpaper/ring.png').as_uri()})
            (args.output / (name + '.log')).write_text(log)
            summary = summarize(json.loads(stats_file.read_text()))
            result['runs'][label][samples] = summary
            print(f'{label:6} {samples:8} {summary["frames_per_second"]:7.2f} frames/s '
                  f'{summary["clear_calls_per_second"]:7.2f} clears/s '
                  f'{summary["draw_calls_per_second"]:8.2f} draws/s '
                  f'{summary["fake_samples"]:4} fake samples / {summary["meter_updates"]:4} meter updates', flush=True)
    (args.output / 'report.json').write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
