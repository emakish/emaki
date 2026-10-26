#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Isolated six-window wallpaper decode/readiness probe; never opens a display.

--source-root allows an archived baseline. Pss is of this offscreen fixture only,
not the live shell. Raw Qt image logs and samples accompany the JSON report.
The fixture enables production glass Loaders under software rendering to test
image decoding and lifetime; this does not validate GPU rendering.
"""
from runtime_fixture import runtime_path
import argparse
import errno
import json
import os
from pathlib import Path
import re
import shutil
import socket
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
    # This fixture exercises no IPC. Some sandboxes deny Unix socket binds;
    # only a demonstrated permission denial permits the matching IPC diagnostic.
    ipc_probe = runtime_path(profile) / 'ipc-probe.sock'
    ipc = dict(available=True, reason=None)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.bind(str(ipc_probe))
    except OSError as error:
        if error.errno not in (errno.EPERM, errno.EACCES):
            raise
        ipc = dict(available=False, reason=f'{errno.errorcode[error.errno]}: {error.strerror}')
    finally:
        ipc_probe.unlink(missing_ok=True)
    shutil.copytree(source_root / 'shell', profile / 'qml')
    shutil.copyfile(ROOT / 'tests/fixtures/BackdropMemory.qml', profile / 'qml/shell.qml')
    for name, color in (('first', '#c85189'), ('second', '#478ec5')):
        # Full physical-size RGBA PNG: matching production sharp cache dimensions.
        Image.new('RGBA', (3072, 1920), color).save(profile / (name + '.png'))
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', QT_SCALE_FACTOR='1', MALLOC_CONF='thp:never',
               QT_LOGGING_RULES='qt.quick.image=true;qt.quick.pixmapcache=true',
               XDG_RUNTIME_DIR=str(runtime_path(profile)), XDG_CACHE_HOME=str(profile/'cache'),
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
                if elapsed > 20:
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
    assert not any(word in log for word in ('ReferenceError', 'TypeError', 'Backdrop was not ready',
                                          'Revoked backdrop remained ready', 'Hidden glass material was recreated',
                                          'Expected one glass material Loader')), log
    for stage in range(6):
        assert f'BACKDROP_SAMPLE {stage} {"true" if stage < 5 else "false"}' in log, log
    ipc_warning = re.compile(r'ERROR quickshell\.ipc: Failed to start IPC server on path "'
                             + re.escape(env['XDG_RUNTIME_DIR'])
                             + r'/quickshell/by-id/[^/\"]+/ipc\.sock"')
    def expected_ipc(line):
        return not ipc['available'] and ipc_warning.fullmatch(line.strip()) is not None
    assert all(expected_ipc(line) for line in log.splitlines() if 'Failed to start IPC server' in line), log
    expected_offscreen = ('Failed to create wl_display (Operation not permitted)',
                          'Failed to create wl_display (No such file or directory)',
                          'Failed to query render formats: No GL context.',
                          'Render format initialization failed. All buffers will fall back to SHM.')
    errors = [line for line in log.splitlines() if ('ERROR' in line or 'WARN' in line)
              and not any(note in line for note in expected_offscreen) and not expected_ipc(line)]
    assert not errors, errors
    reads = [line for line in log.splitlines() if 'QImageReader' in line]
    report = dict(method='Qt software/offscreen, 6 isolated windows, five DockBackdrops plus WelcomeGlass and two SettingsGlass; material Loaders enabled for decode/lifetime checks, no GPU rendering proof; MALLOC_CONF=thp:never',
                  source_root=str(source_root), profile=str(profile), profile_removed_on_exit=True,
                  ipc=ipc, samples=samples, image_reader_log=reads)
    (output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2))
    # Both URLs require one full-image read shared by all seven whole-image
    # consumers, plus one independent bar-band read. Preserve the report on failure.
    assert len(reads) == 4, (4, reads)
    assert sum('requestRegion QRect(0,0 3072x480)' in line for line in reads) == 2, reads
    assert sum('requestRegion QRect(0,0 0x0)' in line for line in reads) == 2, reads
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, default=ROOT/'.cache/backdrop-test')
    args = parser.parse_args()
    run(args.source_root.resolve(), args.output.resolve())
