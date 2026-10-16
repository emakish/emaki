#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real shared greeter surfaces: black preparation, clipped pour and late decode."""
from runtime_fixture import runtime_path
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache/evidence'
CACHE.mkdir(parents=True, exist_ok=True)


def main():
    with tempfile.TemporaryDirectory(prefix='gv-', dir=CACHE) as work:
        base = Path(work)
        for name in ('r', 'cache', 'config', 'state', 'data', 'tmp'):
            (base / name).mkdir(mode=0o700)
        shutil.copytree(ROOT / 'shell', base / 'qml')
        shutil.copy(ROOT / 'tests/fixtures/greeter/GreeterVisualTest.qml', base / 'qml/check.qml')
        Image.new('RGB', (640, 480), '#d31f3d').save(base / 'wallpaper.png')
        # Only metadata/pixel preparation is delayed. The real Surface, Visual,
        # Canvas, Session, Input and Wordmark run with their production bindings.
        (base / 'qml/helpers/wallpaper.py').write_text('''import json, os, pathlib, sys, time
assert sys.flags.isolated
root = pathlib.Path(os.environ['EMAKI_VISUAL_OUTPUT'])
assert os.environ['EMAKI_GREETER_WALLPAPER_ROOT'] == str(root)
time.sleep(3.0 if os.environ['EMAKI_VISUAL_LATE'] == '1' else .4)
print(json.dumps(dict(state='ready', texture=(root / 'wallpaper.png').as_uri())))
''')
        (base / 'qml/helpers/lock-environment.py').write_text('''import json, sys, time
assert sys.flags.isolated
print(json.dumps(dict(layout='EN', outputs_active=True, caps=False)), flush=True)
time.sleep(15)
''')
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(runtime_path(base)),
                   XDG_CACHE_HOME=str(base / 'cache'), XDG_CONFIG_HOME=str(base / 'config'),
                   XDG_STATE_HOME=str(base / 'state'), XDG_DATA_HOME=str(base / 'data'),
                   TMPDIR=str(base / 'tmp'), HOME=str(base), NIRI_SOCKET='', GREETD_SOCK='',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'none'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'none-system'),
                   EMAKI_VISUAL_OUTPUT=str(base), QS_DISABLE_CRASH_HANDLER='1')
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_LOGGING_RULES', 'EMAKI_PYTHON'):
            env.pop(name, None)
        # The login screen loads no settings, so it shows the default theme:
        # light. The dark run sets the theme row in the fixture only, to keep
        # the dark plate of the shared lock surfaces covered.
        def luminance(rgb):
            linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in (channel / 255 for channel in rgb)]
            return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))
        dark_plate = (28, 19, 16)  # ShellPalette.flatPlate, dark, made opaque
        for late, rejection, real_wallpaper, theme in ((False, False, False, ''), (True, False, False, ''), (False, True, False, ''),
                                                       (False, True, False, 'dark'), (False, True, True, '')):
            dark = theme == 'dark'
            if real_wallpaper:
                shutil.copy2(ROOT / 'art/wallpaper/fallback.png', base / 'wallpaper.png')
            result = subprocess.run(['qs', '-p', str(base / 'qml/check.qml'), '--no-color'],
                                    env=dict(env, EMAKI_VISUAL_LATE=str(int(late)), EMAKI_VISUAL_REJECTION=str(int(rejection)),
                                             EMAKI_VISUAL_THEME=theme), text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=20)
            assert result.returncode == 0 and 'GREETER_VISUAL_PASS' in result.stdout, result.stdout
            assert not any(text in result.stdout for text in ('ReferenceError', 'TypeError', 'Binding loop')), result.stdout
            for name in ('before-selection', 'decoding'):
                with Image.open(base / (name + '.png')) as image:
                    assert image.convert('RGB').getbbox() is None, (name, image.getpixel((20, 20)))
            with Image.open(base / 'pour.png') as image:
                top, bottom = image.getpixel((20, 20))[:3], image.getpixel((20, 460))[:3]
                # The pour has reached the top: the plate, not the black sheet.
                poured = (max(top) > 15 and top[0] < 60) if dark else top[0] > 200
                assert (real_wallpaper or poured) and bottom == (0, 0, 0), (theme, late, top, bottom)
            with Image.open(base / 'locked.png') as image:
                pixel = image.getpixel((20, 20))[:3]
                # A timed-out decode remains the flat palette after the helper
                # eventually succeeds; a timely image tints the actual plate.
                if dark:
                    flat = all(abs(a - b) <= 2 for a, b in zip(pixel, dark_plate))
                    tinted = pixel[0] - dark_plate[0] > 8 and pixel[0] < 60
                    assert real_wallpaper or (flat if late else tinted), (theme, late, pixel)
                else:
                    assert real_wallpaper or (pixel[1] > 230 if late else pixel[1] < 200), (theme, late, pixel)
            if rejection:
                # Dark: ShellPalette.dangerOnDark on the dark plate; light:
                # dangerOnLight on the light plate.
                plate, ink = (dark_plate, (255, 179, 201)) if dark else ((255, 248, 243), (160, 27, 69))
                with Image.open(base / 'rejected.png') as image:
                    # The real error label below the field: Emaki error ink, six
                    # seconds after refusal, still visible while a retry is typed.
                    region = image.convert('RGB').crop((100, 282, 540, 310))
                    pixels = [region.getpixel((x, y)) for y in range(region.height) for x in range(region.width)]
                    if dark:
                        assert sum(r > 200 and r > g * 1.2 and r > b * 1.1 for r, g, b in pixels) > 30
                    else:
                        assert sum(r > g * 1.7 and r > b * 1.2 and r < 190 for r, g, b in pixels) > 30
                    # The fully opaque plate fixes contrast even on a real image.
                    assert image.convert('RGB').getpixel((320, 280)) == plate, (theme, image.convert('RGB').getpixel((320, 280)))
                    assert ink in pixels, theme
                    assert (max(luminance(plate), luminance(ink)) + .05) / (min(luminance(plate), luminance(ink)) + .05) > 7
                name = 'u3b-greeter-real-wallpaper.png' if real_wallpaper else 'u3b-greeter-rejected%s.png' % ('-dark' if theme == 'dark' else '')
                shutil.copy2(base / 'rejected.png', CACHE / name)
                print('PASS: greeter rejection remains visible after six seconds and while typing; theme =', theme or 'default (light)')
            with Image.open(base / 'finished.png') as image:
                assert image.getpixel((20, 20))[:3] == (0, 0, 0)
            print('PASS: greeter black preparation/pour/drain; late decode =', late, '; theme =', theme or 'default (light)')


if __name__ == '__main__':
    main()
