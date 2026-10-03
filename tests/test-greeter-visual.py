#!/usr/bin/env python3
"""Real shared greeter surfaces: black preparation, clipped pour and late decode."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)


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
                   QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(base / 'r'),
                   XDG_CACHE_HOME=str(base / 'cache'), XDG_CONFIG_HOME=str(base / 'config'),
                   XDG_STATE_HOME=str(base / 'state'), XDG_DATA_HOME=str(base / 'data'),
                   TMPDIR=str(base / 'tmp'), HOME=str(base), NIRI_SOCKET='', GREETD_SOCK='',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'none'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'none-system'),
                   EMAKI_VISUAL_OUTPUT=str(base), QS_DISABLE_CRASH_HANDLER='1')
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_LOGGING_RULES', 'EMAKI_PYTHON'):
            env.pop(name, None)
        for late in (False, True):
            result = subprocess.run(['qs', '-p', str(base / 'qml/check.qml'), '--no-color'],
                                    env=dict(env, EMAKI_VISUAL_LATE=str(int(late))), text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=12)
            assert result.returncode == 0 and 'GREETER_VISUAL_PASS' in result.stdout, result.stdout
            assert not any(text in result.stdout for text in ('ReferenceError', 'TypeError', 'Binding loop')), result.stdout
            for name in ('before-selection', 'decoding'):
                with Image.open(base / (name + '.png')) as image:
                    assert image.convert('RGB').getbbox() is None, (name, image.getpixel((20, 20)))
            with Image.open(base / 'pour.png') as image:
                top, bottom = image.getpixel((20, 20)), image.getpixel((20, 460))
                assert top[0] > 200 and bottom[:3] == (0, 0, 0), (late, top, bottom)
            with Image.open(base / 'locked.png') as image:
                pixel = image.getpixel((20, 20))
                # A timed-out decode remains the flat palette after the helper
                # eventually succeeds; a timely image tints the actual plate.
                assert (pixel[1] > 230 if late else pixel[1] < 200), (late, pixel)
            with Image.open(base / 'finished.png') as image:
                assert image.getpixel((20, 20))[:3] == (0, 0, 0)
            print('PASS: greeter black preparation/pour/drain; late decode =', late)


if __name__ == '__main__':
    main()
