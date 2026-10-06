#!/usr/bin/env python3
"""Render production QML in isolated offscreen Qt; verify painted pixel bounds."""
import json
import statistics
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image
import importlib.util
import sys
import reaper
reaper.guard()  # nothing this test starts outlives it
sys.dont_write_bytecode = True

spec = importlib.util.spec_from_file_location("wallpaper_tests", Path(__file__).with_name("test-wallpaper.py"))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)
DEST = Path(os.environ.get('EMAKI_SHELL_SHOT_DIR', CACHE / 'shots')).resolve()
if not DEST.is_relative_to(ROOT) or DEST == ROOT:
    raise SystemExit('EMAKI_SHELL_SHOT_DIR must be a directory inside the repository')
DEST.mkdir(parents=True, exist_ok=True)
KINDS = ('logo', 'corner', 'tray', 'file', 'wifi', 'wired', 'bt', 'sound', 'light', 'battery',
         'power-saver', 'balanced', 'performance', 'lock', 'sleep', 'restart', 'shutdown', 'logout', 'hibernate')
SIZES = (16, 18, 22, 36)
report = []

for scale, fallback in ((1, ''), (2, ''), (1, 'config_invalid'), (1, 'image_missing')):
    profile = Path(tempfile.mkdtemp(prefix='ss-', dir=CACHE))
    for name in ('runtime', 'cache', 'config', 'state', 'data', 'tmp'):
        (profile / name).mkdir(mode=0o700)
    shutil.copytree(ROOT / 'shell', profile / 'qml')
    shutil.copyfile(ROOT / 'tests/fixtures/ShellShots.qml', profile / 'qml/shell.qml')
    shutil.copyfile(ROOT / 'tests/fixtures/SystemFixture.qml', profile / 'qml/SystemFixture.qml')
    with (profile / 'qml/qmldir').open('a') as module: module.write('SystemFixture 1.0 SystemFixture.qml\n')
    wallpaper_path = fixtures.fixture(profile)
    if fallback == 'config_invalid':
        (profile / 'config/wpaperd/config.toml').write_text('broken [toml')
    elif fallback == 'image_missing':
        wallpaper_path.unlink()
    apps = profile / 'data/applications'
    apps.mkdir()
    for name in ('Browser', 'Editor', 'Files'):
        (apps / f'fixture-{name.lower()}.desktop').write_text(
            f'[Desktop Entry]\nType=Application\nName={name}\nExec=/usr/bin/true\nTerminal=false\n')
    # Recent (empty All): synthetic log, newest first, as launcher-tools.py would write it.
    (profile / 'state/emaki').mkdir(mode=0o700)
    (profile / 'state/emaki/recent.json').write_text(json.dumps([
        dict(kind='page', ref='dock', t=1790200000300), dict(kind='app', ref='fixture-editor', t=1790200000200),
        dict(kind='file', ref='/fixture/notes/Synthetic draft.md', t=1790200000100), dict(kind='app', ref='fixture-browser', t=1790200000000)]))
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', QT_SCALE_FACTOR=str(scale),
               EMAKI_SHELL_TRAY='0', EMAKI_TEST_SYSTEM='0', EMAKI_BIN='', EMAKI_SETTINGS_PROFILE='', EMAKI_TEST_MPRIS='0', EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_SHOT_DIR=str(DEST),
               EMAKI_FIXTURE_WALLPAPER='' if fallback else wallpaper_path.as_uri(),
               EMAKI_FIXTURE_FALLBACK=fallback,
               EMAKI_SHELL_GLASS_RENDERER='canvas', PYTHONDONTWRITEBYTECODE='1',
               XDG_RUNTIME_DIR=str(profile / 'runtime'), XDG_CACHE_HOME=str(profile / 'cache'),
               XDG_CONFIG_HOME=str(profile / 'config'), XDG_STATE_HOME=str(profile / 'state'),
               XDG_DATA_HOME=str(profile / 'data'), XDG_DATA_DIRS=str(profile / 'data'),
               TMPDIR=str(profile / 'tmp'), NIRI_SOCKET='',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session.sock'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system.sock'))
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_SCREEN_SCALE_FACTORS', 'QT_PLUGIN_PATH',
                 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH', 'QT_LOGGING_RULES'):
        env.pop(name, None)
    with (profile / 'qs.log').open('w') as log:
        result = subprocess.run(['qs', '-p', str(profile / 'qml'), '--no-color'], env=env,
                                text=True, stdout=log, stderr=subprocess.STDOUT, timeout=60)
    result.stdout = (profile / 'qs.log').read_text()
    assert result.returncode == 0 and 'SHOTS_COMPLETE' in result.stdout, result.stdout
    assert not any(word in result.stdout for word in ('WARN', 'ERROR', 'ReferenceError', 'TypeError')), result.stdout
    if fallback:
        assert 'FLAT_FALLBACK_OK' in result.stdout
        print(f'Flat QML fallback {fallback}: no warnings, no glass')
        continue
    assert 'MATERIAL_IDLE_OK' in result.stdout, result.stdout
    assert 'PRIVATE' not in result.stdout, result.stdout
    for state in ('bar', 'bar-hover', 'bar-logo-hover', 'launcher', 'launcher-selected', 'launcher-search', 'drawer', 'drawer-dnd', 'drawer-hover', 'notification-peek', 'notification-flood', 'launcher-clipboard', 'launcher-web', 'launcher-frequent', 'drawer-media', 'dock', 'dock-list', 'dock-menu', 'osd-sound', 'osd-light', 'privacy', 'privacy-open', 'wifi-portal', 'wifi-hidden', 'launcher-recent', 'bar-system-hover', 'system-sound', 'system-wifi', 'system-bt', 'system-power', 'system-power-confirm'):
        with Image.open(DEST / f'{state}@{scale}x.png') as frame:
            assert frame.size == (1536 * scale, 960 * scale), frame.size
    # The selected / current drops (today, the chosen player, the open page's tab) are orange,
    # the hover drop stays plain (2026-09-27) — here in the flat stand-in; the GPU glass
    # is looked at in `make glass-shots`. Rects come from the fixture (screen px), 3 px inset.
    def orange_share(frame, r):
        box = tuple(round(v * scale) for v in (r['x'] + 3, r['y'] + 3, r['x'] + r['width'] - 3, r['y'] + r['height'] - 3))
        pixels = list(frame.crop(box).getdata())
        return sum(1 for p in pixels if p[0] > 180 and p[0] - p[2] > 90 and p[0] - p[1] > 50) / len(pixels)
    marks = [json.loads(line[line.index('SELECTED_DROPS {') + 15:]) for line in result.stdout.splitlines() if 'SELECTED_DROPS {' in line]
    assert sorted(m['shot'] for m in marks) == ['drawer-hover', 'system-sound'], marks
    shares = []
    for mark in marks:
        with Image.open(DEST / f"{mark['shot']}@{scale}x.png").convert('RGB') as frame:
            for kind, limit in (('orange', .5), ('plain', .05)):
                for r in mark[kind]:
                    share = orange_share(frame, r)
                    assert share > limit if kind == 'orange' else share < limit, (scale, mark['shot'], kind, r, share)
                    shares.append(f"{mark['shot']} {kind} {share:.2f}")
    print(f'offscreen scale {scale}: selected drops orange, hover plain: ' + ', '.join(shares))
    with Image.open(DEST / f'bar@{scale}x.png').convert('RGB') as frame:
        # Actual strip in the bar, flat stand-in of the glass: only the five existing
        # workspaces, cells of 28 on a pitch of 30 from x 62 (island 54 + 8). The active
        # workspace (2) carries its mark in full ink: a solid 6x2 of the exact colour in the
        # lower band, within 1 px of the cell centre, with at least 3 clear logical px above.
        # tests/test-strip.py checks every mark at 1x, 1.25x and 2x on a plain background.
        x, width, color = 92, 28, (36, 16, 24)
        center = x + width // 2
        actual = {(px, py) for px in range(x * scale, (x + width) * scale)
                  for py in range(32 * scale, 38 * scale)
                  if frame.getpixel((px, py)) == color}
        xs = {px for px, _ in actual}
        ys = {py for _, py in actual}
        assert actual and len(actual) == len(xs) * len(ys), (scale, 'mark not a solid rectangle')
        assert (len(xs), len(ys)) == (6 * scale, 2 * scale), (scale, (len(xs), len(ys)))
        assert abs((min(xs) + max(xs) + 1) / 2 - center * scale) <= scale, (scale, min(xs), max(xs))
        assert all(frame.getpixel((px, py)) != color
                   for px in xs
                   for py in range(min(ys) - 3 * scale, min(ys))), (scale, 'no clear gap')
    # Grab the actual date label of the clock island on transparency: the glass's dim ink,
    # dark at .55 (the glass is always the light one, 2026-09-27). The old opaque
    # muted RGB (163,148,131) fails this color assertion.
    with Image.open(DEST / f'date-text@{scale}x.png').convert('RGBA') as text_image:
        ink = [p for p in text_image.getdata() if p[3] > 100]
        assert len(ink) > 20, (scale, 'date glyphs missing')
        assert statistics.median(p[0] for p in ink) < 80 and statistics.median(p[1] for p in ink) < 60, (scale, 'light date on the light glass')
        assert max(p[3] for p in ink) < 200, (scale, 'date not a step dimmer than the time')
    # Pointer is actual QtTest input, not an artificial material flag.
    with Image.open(DEST / f'bar@{scale}x.png') as normal, Image.open(DEST / f'bar-hover@{scale}x.png') as hover:
        crop = (628*scale, 8*scale, 909*scale, 44*scale)
        assert normal.crop(crop).tobytes() != hover.crop(crop).tobytes(), 'hover has no visible effect'
    # The system island (right edge, 36 px): the pointer on a cell raises its drop.
    with Image.open(DEST / f'bar@{scale}x.png') as normal, Image.open(DEST / f'bar-system-hover@{scale}x.png') as hover:
        crop = (1200*scale, 8*scale, 1526*scale, 44*scale)
        assert normal.crop(crop).tobytes() != hover.crop(crop).tobytes(), 'system hover has no visible effect'
    for kind in KINDS:
        for size in SIZES:
            filename = f'icon-{kind}-{size}@{scale}x.png'
            with Image.open(DEST / filename) as image:
                assert image.size == (size * scale, size * scale), (filename, image.size)
                bbox = image.convert('RGBA').getchannel('A').getbbox()
                assert bbox is not None, filename
                dx = (bbox[0] + bbox[2] - image.width) / 2
                dy = (bbox[1] + bbox[3] - image.height) / 2
                report.append(dict(file=filename, bbox=bbox, dx=dx, dy=dy,
                                   distance=math.hypot(dx, dy)))
    print(f'offscreen scale {scale}: bar, launcher, workspace lines/gaps/colors, {len(KINDS) * len(SIZES)} icon renders; log: {profile.relative_to(ROOT)}/qs.log')

(DEST / 'pixels.json').write_text(json.dumps(report, indent=2) + '\n')
failures = [row for row in report if row['distance'] > .5]
assert not failures, failures
print(f'PASS: all {len(report)} painted icon bounds within 0.5 physical px; PNGs: {DEST.relative_to(ROOT)}')
