#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The window at a niri third of every screen size: layout checks at each width, and the
default size drawn exactly as before.

  layout.py                  layout checks, minimum size, pixel comparison with BASE
  layout.py --source REV     the layout checks against the UI of a git revision (shows a fix's "before")
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import interactions  # noqa: E402  (the fixture controllers and the offscreen runner)

UI = interactions.UI
ROOT = interactions.ROOT
# The window niri gives a column of one third in a live session: 206x342 on a 1280x800 output at
# scale 2, 313x482 on 1920x1080 at scale 2, 419x742 on 1280x800 at scale 1 and on 2560x1600 at
# scale 2, 633x1022 on 1920x1080 at scale 1, 846x1542 on 2560x1600 at scale 1; widths between, and
# 632x342, the whole 1280x800 output at scale 2; 206x399 and 206x400, both sides of the height where
# a list page's heading starts scrolling with its rows.
SIZES = [[206, 342], [206, 399], [206, 400], [240, 420], [313, 482], [400, 600], [419, 560], [419, 742], [480, 700],
         [560, 700], [632, 342], [633, 1022], [720, 700], [846, 1542], [960, 600], [1000, 700], [1180, 780], [1200, 800]]
# The narrowest and the shortest third above: the window's minimum must stay within it.
THIRD = (206, 342)
# The reviewed 0.9 look: Rose accent, real Emaki mark and stronger secondary text.
# The shared fixture includes a foreign package, so its AUR-off notice remains visible.
# Interaction tests separately cover zero/unknown counts. Keep this reference pinned:
# only an intentional, reviewed visual change may advance the pixel baseline.
BASE = 'b5bd4f9816f9a33aed71b091b9331f134fb24715'
DEFAULT = [[1180, 780]]


def walk(size, source=UI, shots=''):
    """One window size in its own process: an offscreen window does not reliably take a new size."""
    try:
        log = interactions.run(*size, entry='LayoutTest.qml', source=source, timeout=300,
                               extra={'TEST_SIZES': json.dumps([size]), 'TEST_SHOTS': shots})
    except AssertionError as error:
        log = str(error)
    found = re.search(r'LAYOUT_CHECKED (\d+) FAILURES (\d+)', log)
    failures = [line.split('ASSERTION_FAILED ', 1)[1] for line in log.splitlines() if 'ASSERTION_FAILED' in line]
    failures += [line.strip() for line in log.splitlines() if any(word in line for word in ('Binding loop', 'TypeError', 'ReferenceError', 'Unable to assign'))]
    if not found and not failures:
        failures = ['no result: ' + log[-2000:]]
    return size, int(found.group(1)) if found else 0, failures


def layout(source=UI, sizes=SIZES, shots=''):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda size: walk(size, source, shots), sizes))
    failed = [line for _, _, failures in results for line in failures]
    for size, checked, failures in results:
        print(f'{"FAIL" if failures else "PASS"} layout {size[0]}x{size[1]}: {checked} items, {len(failures)} failures')
    assert not failed, '\n'.join(failed[:80])


def minimum(source=UI):
    """The window's minimum size fits the narrowest third, and every page is usable at it."""
    text = (source / 'shell.qml').read_text()
    found = re.search(r'minimumSize: Qt\.size\((\d+), (\d+)\)', text)
    assert found, 'shell.qml sets the window minimum size'
    width, height = int(found.group(1)), int(found.group(2))
    assert width <= THIRD[0] and height <= THIRD[1], f'the window minimum {width}x{height} is over {THIRD[0]}x{THIRD[1]}: niri cannot narrow it to a third'
    layout(source, [[width, height]])
    print('PASS minimum size', f'{width}x{height}', 'fits a third and every page is usable at it')


def checkout(revision, target):
    """The UI directory of a git revision; None outside a git checkout."""
    try:
        listing = subprocess.run(['git', '-C', str(ROOT), 'ls-tree', '--name-only', revision, 'update-manager/ui/'],
                                 capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        return None
    target.mkdir(parents=True)
    for name in listing:
        if name.endswith(('.qml', '.js')):
            content = subprocess.run(['git', '-C', str(ROOT), 'show', f'{revision}:{name}'], capture_output=True, check=True).stdout
            (target / Path(name).name).write_bytes(content)
    return target


def identical():
    with tempfile.TemporaryDirectory(prefix='emaki-manager-pixels-') as directory:
        root = Path(directory)
        base = checkout(BASE, root / 'base')
        if base is None:
            print('SKIP pixel comparison: no git checkout with', BASE)
            return
        shots = {}
        for name, source in (('base', base), ('now', UI)):
            (root / f'{name}-shots').mkdir()
            interactions.run(entry='LayoutTest.qml', source=source, timeout=300,
                             extra={'TEST_SIZES': json.dumps(DEFAULT), 'TEST_SHOTS': str(root / f'{name}-shots'), 'TEST_SHOTS_ONLY': '1'})
            shots[name] = {path.name: path.read_bytes() for path in (root / f'{name}-shots').glob('*.png')}
        assert shots['base'] and set(shots['base']) == set(shots['now']), (sorted(shots['base']), sorted(shots['now']))
        differ = sorted(name for name in shots['base'] if shots['base'][name] != shots['now'][name])
        if differ:
            keep = ROOT / '.cache/evidence/N81W/pixel-diff'
            shutil.rmtree(keep, ignore_errors=True)
            shutil.copytree(root, keep, ignore=shutil.ignore_patterns('*.qml', '*.js', 'tests'))
            raise AssertionError(f'the default size draws differently from {BASE}: {differ} (shots kept in {keep})')
        print('PASS pixel-identical at 1180x780 with', BASE, f'({len(shots["now"])} pages)')


# Program icons as a picture that takes a while to load: a plain fill with many tiny shapes in one
# corner, so a capture taken before it has loaded shows the fallback icon instead.
SLOW_FILL = (0xd2, 0x80, 0x9a)


def slow_icon(path, shapes=60000):
    tiny = ''.join(f'<rect x="{index % 40 / 4}" y="{index // 40 % 40 / 4}" width="0.2" height="0.2" fill="#c0707a"/>' for index in range(shapes))
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512">'
                    f'<rect width="512" height="512" fill="#{bytes(SLOW_FILL).hex()}"/>{tiny}</svg>')


def images():
    """A page is captured only after its images have loaded: the slow icon is in the program page's capture."""
    from PIL import Image
    with tempfile.TemporaryDirectory(prefix='emaki-manager-images-') as directory:
        root = Path(directory)
        slow_icon(root / 'slow.svg')
        (root / 'shots').mkdir()
        interactions.run(entry='LayoutTest.qml', timeout=300,
                         extra={'TEST_SIZES': json.dumps(DEFAULT), 'TEST_SHOTS': str(root / 'shots'), 'TEST_SHOTS_ONLY': '1', 'TEST_ICON': str(root / 'slow.svg')})
        shot = Image.open(root / 'shots/program-1180x780.png').convert('RGB')
        filled = list(shot.getdata()).count(SLOW_FILL)
        assert filled > 3000, f'the program page was captured before its icon loaded ({filled} pixels of the icon)'
        print('PASS images loaded before capture', f'({filled} pixels of the slow icon on the program page)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', help='run the layout checks against the UI of this git revision')
    arguments = parser.parse_args()
    if arguments.source:
        with tempfile.TemporaryDirectory(prefix='emaki-manager-source-') as directory:
            source = checkout(arguments.source, Path(directory) / 'ui')
            minimum(source)
            layout(source)
        sys.exit(0)
    minimum()
    layout()
    images()
    identical()
