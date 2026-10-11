#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render the capture chooser without a compositor, capture helper, or user profile."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args()
    qs = shutil.which('qs')
    assert qs, 'qs is required'
    assert (ROOT / 'shell/Platform.qml').exists(), 'Run make render before the UI fixture'
    with tempfile.TemporaryDirectory(prefix='capture-ui-') as temporary:
        profile = Path(temporary)
        for name in ('runtime', 'config', 'cache', 'state', 'data', 'tmp', 'qml', 'images'):
            (profile / name).mkdir(mode=0o700)
        # Copy text only; the software renderer never loads compiled shaders.
        for source in (ROOT / 'shell').iterdir():
            if source.is_file() and source.suffix in ('.qml', '.js'):
                shutil.copyfile(source, profile / 'qml' / source.name)
            elif source.is_dir():
                (profile / 'qml' / source.name).symlink_to(source)
        shutil.copyfile(ROOT / 'shell/qmldir', profile / 'qml/qmldir')
        shutil.copyfile(ROOT / 'tests/fixtures/capture-panel.qml', profile / 'qml/fixture.qml')
        destination = args.evidence.resolve() if args.evidence else profile / 'images'
        destination.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, HOME=str(profile), QT_QPA_PLATFORM='offscreen',
                   QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                   XDG_RUNTIME_DIR=str(profile / 'runtime'),
                   XDG_CONFIG_HOME=str(profile / 'config'), XDG_CACHE_HOME=str(profile / 'cache'),
                   XDG_STATE_HOME=str(profile / 'state'), XDG_DATA_HOME=str(profile / 'data'),
                   XDG_DATA_DIRS=str(profile / 'data'), TMPDIR=str(profile / 'tmp'),
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session.sock'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system.sock'),
                   EMAKI_CAPTURE_TEST_OUTPUT=str(destination))
        env.pop('WAYLAND_DISPLAY', None)
        env.pop('DISPLAY', None)
        for width, height in ((800, 600), (1280, 720)):
            env.update(EMAKI_CAPTURE_TEST_WIDTH=str(width), EMAKI_CAPTURE_TEST_HEIGHT=str(height))
            result = subprocess.run([qs, '-p', str(profile / 'qml/fixture.qml'), '--no-duplicate'],
                                    env=env, text=True, capture_output=True, timeout=15)
            log = result.stdout + result.stderr
            assert result.returncode == 0 and 'CAPTURE_UI_PASSED' in log, log
            assert 'CAPTURE_UI_FAILURE' not in log and 'ReferenceError' not in log and 'TypeError' not in log, log
            for target in ('area', 'window', 'screen'):
                image = destination / f'{width}x{height}-{target}.png'
                assert image.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'), image
            print(f'Capture UI {width}x{height}: area keyboard geometry, target/mode/output/window choices, label bounds, three renders OK')


if __name__ == '__main__':
    main()
