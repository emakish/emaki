#!/usr/bin/env python3
"""Isolated three-window wallpaper decode/readiness probe; never opens a display.

--source-root allows an archived baseline. Pss is of this offscreen fixture only,
not the live shell. Raw Qt image logs and samples accompany the JSON report.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from PIL import Image
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent


def run(source_root, output):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='backdrop-', dir=ROOT / '.cache') as temporary:
        return run_profile(source_root, output, Path(temporary))


def run_profile(source_root, output, profile):
    for name in ('runtime', 'cache', 'config', 'state', 'data'):
        (profile / name).mkdir(mode=0o700)
    shutil.copytree(source_root / 'shell', profile / 'qml')
    shutil.copyfile(ROOT / 'tests/fixtures/BackdropMemory.qml', profile / 'qml/shell.qml')
    for name, color in (('first', '#c85189'), ('second', '#478ec5')):
        # Full physical-size RGBA PNG: matching production sharp cache dimensions.
        Image.new('RGBA', (3072, 1920), color).save(profile / (name + '.png'))
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', QT_SCALE_FACTOR='1', MALLOC_CONF='thp:never',
               QT_LOGGING_RULES='qt.quick.image=true;qt.quick.pixmapcache=true',
               XDG_RUNTIME_DIR=str(profile/'runtime'), XDG_CACHE_HOME=str(profile/'cache'),
               XDG_CONFIG_HOME=str(profile/'config'), XDG_STATE_HOME=str(profile/'state'),
               XDG_DATA_HOME=str(profile/'data'), XDG_DATA_DIRS=str(profile/'data'),
               DBUS_SESSION_BUS_ADDRESS='unix:path='+str(profile/'no-session'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path='+str(profile/'no-system'), NIRI_SOCKET='',
               EMAKI_FIXTURE_WALLPAPER=(profile/'first.png').as_uri(),
               EMAKI_FIXTURE_WALLPAPER_NEXT=(profile/'second.png').as_uri(),
               EMAKI_SHELL_SHADER_DIR=str(ROOT/'.cache/shell-shaders'))
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_SCREEN_SCALE_FACTORS', 'QT_PLUGIN_PATH', 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH'):
        env.pop(name, None)
    samples = []
    with (output/'qs.log').open('w') as log:
        child = subprocess.Popen(['qs', '-p', str(profile/'qml'), '--no-color'], env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            start = time.monotonic()
            sampled = set()
            while child.poll() is None:
                elapsed = time.monotonic() - start
                if elapsed > 15:
                    raise AssertionError('offscreen backdrop deadline exceeded')
                # Sample before each transition, after allowing initialization to settle.
                stage = int(elapsed // 2)
                if elapsed % 2 > 1.5 and stage not in sampled:
                    text = Path(f'/proc/{child.pid}/smaps_rollup').read_text()
                    values = {line.split(':')[0]: int(line.split()[1]) * 1024 for line in text.splitlines() if ':' in line and len(line.split()) == 3 and line.split()[2] == 'kB'}
                    samples.append(dict(stage=stage, elapsed_seconds=elapsed, bytes=values))
                    sampled.add(stage)
                time.sleep(.05)
            assert child.returncode == 0, (output/'qs.log').read_text()
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
    log = (output/'qs.log').read_text()
    assert 'BACKDROP_COMPLETE' in log, log
    assert not any(word in log for word in ('ReferenceError', 'TypeError', 'Backdrop was not ready', 'Revoked backdrop remained ready')), log
    # qs's unused IPC socket is denied in restricted sandboxes; it doesn't affect
    # this fixture's image loading. No warning other than that is accepted.
    expected_offscreen = ('Failed to start IPC server on path',
                          'Failed to create wl_display (Operation not permitted)',
                          'Failed to create wl_display (No such file or directory)',
                          'Failed to query render formats: No GL context.',
                          'Render format initialization failed. All buffers will fall back to SHM.')
    errors = [line for line in log.splitlines() if ('ERROR' in line or 'WARN' in line)
              and not any(note in line for note in expected_offscreen)]
    assert not errors, errors
    reads = [line for line in log.splitlines() if 'QImageReader' in line]
    shared = 'property bool sharedWallpaper' in (source_root/'shell/DockBackdrop.qml').read_text()
    band_cache = 'property bool cacheWallpaper' in (source_root/'shell/DockBackdrop.qml').read_text()
    expected_reads = 4 if band_cache else 2 if shared else 10
    assert len(reads) == expected_reads, (expected_reads, reads)
    if shared and not band_cache:
        assert all('requestRegion QRect(0,0 0x0)' in line for line in reads), reads
    if band_cache:
        assert sum('requestRegion QRect(0,0 3072x480)' in line for line in reads) == 2, reads
        assert sum('requestRegion QRect(0,0 0x0)' in line for line in reads) == 2, reads
    report = dict(method='Qt software/offscreen, 3 isolated windows, five production DockBackdrops; MALLOC_CONF=thp:never',
                  source_root=str(source_root), profile=str(profile), profile_removed_on_exit=True,
                  samples=samples, image_reader_log=reads)
    (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, default=ROOT/'.cache/backdrop-test')
    args = parser.parse_args()
    run(args.source_root.resolve(), args.output.resolve())
