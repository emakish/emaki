#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The time zone map on its own (MapTest.qml): light and dark palettes at 1× and 2×.

Offscreen and isolated like render.py. Checks land, water, chosen and hovered pixels,
pointer and keyboard picking and paint/hit-test times; saves one picture per run.
"""
import argparse
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image

UI = Path(__file__).resolve().parents[1]
ROOT = UI.parents[1]
RUNS = [('light', 1), ('dark', 1), ('light', 2), ('dark', 2)]
WIDTH = 900  # MapTest.qml's window


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.cache/evidence/installer-map')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='emaki-map-') as temporary, \
            tempfile.TemporaryDirectory(prefix='eim-', dir='/tmp') as runtime:
        root = Path(temporary)
        shutil.copytree(UI, root / 'ui', ignore=shutil.ignore_patterns('__pycache__', 'artifacts'))
        entry = (root / 'ui/tests/MapTest.qml').read_text().replace('".." as UI', '"." as UI').replace('"../Timezones.js"', '"Timezones.js"')
        (root / 'ui/map-test.qml').write_text(entry)
        for name in ('cache', 'config', 'state', 'data'):
            (root / name).mkdir(mode=0o700)
        for theme, scale in RUNS:
            picture = args.output.resolve() / f'map-{theme}-{scale}x.png'
            env = dict(os.environ, QT_SCALE_FACTOR=str(scale), QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                       QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1', XDG_RUNTIME_DIR=runtime,
                       XDG_CACHE_HOME=str(root / 'cache'), XDG_CONFIG_HOME=str(root / 'config'),
                       XDG_STATE_HOME=str(root / 'state'), XDG_DATA_HOME=str(root / 'data'),
                       EMAKI_MAP_THEME=theme, EMAKI_MAP_SHOT=str(picture))
            for key in ('WAYLAND_DISPLAY', 'DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS'):
                env.pop(key, None)
            result = subprocess.run(['qs', '-p', str(root / 'ui/map-test.qml'), '--no-color'], env=env,
                                    text=True, capture_output=True, timeout=120)
            log = result.stdout + result.stderr
            (args.output / f'map-{theme}-{scale}x.log').write_text(log.replace(str(ROOT), '<checkout>'))
            assert result.returncode == 0 and f'MAP_OK {theme} scale {scale}' in log, log
            for diagnostic in ('ASSERTION_FAILED', 'ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign'):
                assert diagnostic not in log, log
            assert picture.is_file(), picture
            # The saved picture, at the scale's full resolution, has the logged colours.
            samples = re.findall(r'SAMPLE (\w+) ([\d.]+) ([\d.]+) #([0-9a-f]{6})', log)
            assert len(samples) == 3, log
            with Image.open(picture) as image:
                assert image.width == round(WIDTH * scale), image.size
                for name, x, y, expected in samples:
                    found = image.convert('RGB').getpixel((round(float(x) * scale), round(float(y) * scale)))
                    wanted = bytes.fromhex(expected)
                    assert all(abs(a - b) <= 6 for a, b in zip(found, wanted)), (theme, scale, name, found, expected)
            times = '; '.join(line.split('PERF ', 1)[1] for line in log.splitlines() if 'PERF ' in line)
            print(f'PASS map {theme} {scale}x: land, water, chosen and hovered pixels, pointer, keyboard; {times}', flush=True)


if __name__ == '__main__':
    main()
