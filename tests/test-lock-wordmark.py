#!/usr/bin/env python3
"""Canonical pixels, approved HTML motion, and actual Qt Canvas physical pixels.

Every temporary profile/image stays in the worktree. No Wayland, bus, socket IPC,
GPU, password, screen capture, browser, or additional JavaScript runtime is used.
"""
from runtime_fixture import runtime_path
import json
import os
from pathlib import Path
import re
import resource
import shutil
import subprocess
import tempfile

from PIL import Image
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)


def reference():
    # The approved lock mockup's pixels and motion code, extracted into a fixture.
    mockup = json.loads((ROOT / 'tests/fixtures/lock/mockup-reference.json').read_text())
    pixels = mockup['pixels']
    generated = (ROOT / 'shell/LockWordmarkData.js').read_text()
    data = json.loads(re.search(r'var PIXELS = (\[.*\]);', generated, re.S).group(1))
    assert [p[:4] for p in data] == pixels, 'Canonical geometry differs from the approved lock mockup'
    assert len(set(tuple(p[:2]) for p in data)) == len(data), 'Duplicate pixel in geometry'
    assert set(p[2] for p in data) == set(range(6)), 'Letter/dot groups missing'
    assert set(p[4] for p in data) == set(range(4)), 'Hot palette is incomplete'
    print(f'PASS: {len(data)} unique canonical pixels, all six letter groups and four Hot colours')
    header = mockup['header'].replace('__PIXELS__', json.dumps(pixels)).replace('__W__', '200')
    wave, particles, intro, idle_paint = (mockup[k] for k in ('wave', 'particles', 'intro', 'idle'))
    helpers = '''
var ctx = null, logo = null, dw = 0, dpr = 1, sw = 0, sh = 0;
var clock = 0, tPhase = 0, phase = "drain", particles = null;
var S = {intro: .5, stagger: .15, drain: "down", drainDur: 1};
function clamp01(value) { return Math.max(0, Math.min(1, value)); }
function idleAt(mode, pixel, time) { return IDLE[mode](pixel, time); }
function sparkAt(time) { return sparks(time); }
function colorAt(x, y) { return hotIndex(x, y); }
function introAt(time, setup) {
    const output = [];
    ctx = {fillStyle: "", fillRect: function(x, y, w, h) { output.push([x, y, w, h, this.fillStyle]); }};
    logo = {x: setup.x, y: setup.y, s: 1}; dw = setup.dw;
    drawIntro(time); return output;
}
function beginDrain(setup, width, height, start) {
    logo = {x: setup.x, y: setup.y, s: 1}; dpr = setup.dpr;
    sw = width; sh = height; clock = start; tPhase = start;
    spawnParticles();
}
function drainFrame(dt, elapsed, now) { clock = now; stepParticles(dt, edge()); }
function particleState() { return particles; }
function paintIdle(context, mode, time, setup, ox, oy) {
    ctx = context; logo = {x: setup.x - ox, y: setup.y - oy, s: 1};
    S.idle = mode; drawIdle(time);
}
'''
    return '.pragma library\n' + header + helpers + wave + particles + intro + idle_paint, data


def no_core():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def main():
    subprocess.run(['python3', str(ROOT / 'scripts/build-lock-wordmark'), '--check'], check=True)
    golden, pixels = reference()
    with tempfile.TemporaryDirectory(prefix='lw-', dir=CACHE) as work:
        profile = Path(work)
        for name in ('r', 'cache', 'config', 'state', 'data', 'tmp', 'qml'):
            (profile / name).mkdir(mode=0o700)
        qml = profile / 'qml'
        for name in ('LockWordmark.qml', 'LockWordmark.js', 'LockWordmarkData.js'):
            shutil.copyfile(ROOT / 'shell' / name, qml / name)
        shutil.copyfile(ROOT / 'tests/fixtures/lock/LockWordmarkTest.qml', qml / 'LockWordmarkTest.qml')
        (qml / 'qmldir').write_text('LockWordmark 1.0 LockWordmark.qml\n')
        (qml / 'WordmarkReference.js').write_text(golden)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(runtime_path(profile)),
                   XDG_CACHE_HOME=str(profile / 'cache'), XDG_CONFIG_HOME=str(profile / 'config'),
                   XDG_STATE_HOME=str(profile / 'state'), XDG_DATA_HOME=str(profile / 'data'),
                   TMPDIR=str(profile / 'tmp'), PYTHONDONTWRITEBYTECODE='1', NIRI_SOCKET='',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'))
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_LOGGING_RULES', 'QT_SCREEN_SCALE_FACTORS'):
            env.pop(name, None)
        for scale in (1, 1.25, 2):
            env['QT_SCALE_FACTOR'] = str(scale)
            try:
                result = subprocess.run(['qs', '-p', str(qml / 'LockWordmarkTest.qml'), '--no-color'],
                                        env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        timeout=30, check=False, preexec_fn=no_core)
            except subprocess.TimeoutExpired as error:
                raise AssertionError(error.stdout.decode() if isinstance(error.stdout, bytes) else error.stdout) from error
            assert result.returncode == 0 and 'LOCK_WORDMARK_COMPLETE' in result.stdout, result.stdout
            output = result.stdout
            assert 'Failed to start IPC server' not in output, output
            assert 'ERROR' not in output and 'WARN' not in output, output
            if scale == 1:
                print('\n'.join(line[line.index('LOCK_WORDMARK_PASS '):] for line in output.splitlines()
                                if 'LOCK_WORDMARK_PASS ' in line))
                print('\n'.join(line[line.index('LOCK_WORDMARK_BENCH '):] for line in output.splitlines()
                                if 'LOCK_WORDMARK_BENCH ' in line))
            image = Image.open(qml / 'wordmark.png').convert('RGBA')
            assert image.size == (round(640 * scale), round(360 * scale)), image.size
            colors = [(176, 30, 120, 255), (236, 42, 85, 255), (255, 106, 42, 255), (255, 182, 46, 255)]
            origin = (image.width - round(40 * scale) - 200, image.height - round(40 * scale) - 48)
            expected = {(origin[0] + p[0], origin[1] + p[1]): colors[p[4]] for p in pixels}
            actual = {(x, y): image.getpixel((x, y)) for y in range(image.height)
                      for x in range(image.width) if image.getpixel((x, y))[3]}
            assert actual == expected, (scale, len(actual), len(expected), image.getbbox(), output)
            print(f'PASS: real Qt Canvas at DPR {scale}: exact 48 physical px, margin, every pixel/colour, no blended edges')
    print('PASS: lock wordmark data, approved motion, Canvas lifecycle and scales')


if __name__ == '__main__':
    main()
