#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real launcher keyboard traversal in an isolated offscreen shell."""
from runtime_fixture import runtime_path
import os
from pathlib import Path
import re
import subprocess
import shutil
import tempfile
from PIL import Image
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / '.cache/n81k-evidence/launcher-look'
# The focus ring the hover look replaced on pale controls: a 2 px rim of 60 % ink.
DARK_RIM = ('border.color: strong ? strongInk : LiquidPalette.flatDropRim\n'
            '    border.width: strong ? 2 : 1',
            'border.color: Qt.rgba(LiquidPalette.flatDropRim.r, LiquidPalette.flatDropRim.g, LiquidPalette.flatDropRim.b, .6)\n'
            '    border.width: 2')


def luminance(pixel):
    channels = [v / 255 for v in pixel[:3]]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
    return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))


def contrast(a, b):
    a, b = luminance(a), luminance(b)
    return (max(a, b) + .05) / (min(a, b) + .05)


def focus_look(output, shots):
    """Each focused pale control on the launcher path shows the hover look: a light rim and
    fill over the plate, never a dark outline. Returns the rim contrasts by step."""
    steps = re.findall(r'LAUNCHER_LOOK (\S+) (\S+) (\S+) (\S+) (\S+) (\w+)', output)
    assert [s[0] for s in steps] == ['1-down-recent', '2-up-recent', '3-up-search', '4-right-all',
                                     '5-right-apps'], output
    found = {}
    for name, x, y, width, height, kind in steps:
        x, y, width, height = (round(float(v)) for v in (x, y, width, height))
        with Image.open(shots / (name + '.png')) as image:
            image = image.convert('RGB')
            if kind == 'result':
                continue
            # The plate just above the control, then the control's straight top edge (the
            # rim row and the row under it), away from the rounded corners.
            plate = image.getpixel((x + width // 2, y - 4))
            rows = [[image.getpixel((px, py)) for px in range(x + 10, x + width - 10)]
                    for py in (y, y + 1)]
            rim = max(contrast(plate, p) for p in rows[0] + rows[1])
            shown = max(contrast(plate, p) for p in rows[0])
            found[name] = rim
            assert shown >= 1.1, (name, 'no visible focus rim', shown)
            assert rim <= 2.2, (name, 'dark outline instead of the hover look', rim)
    return found


def run(mutation=None):
    with tempfile.TemporaryDirectory(prefix='emaki-launcher-') as temporary:
        base = Path(temporary)
        shutil.copytree(ROOT / 'shell', base / 'shell')
        override = os.environ.get('EMAKI_TEST_LAUNCHER_SOURCE')
        if override:
            shutil.copyfile(override, base / 'shell/LauncherBody.qml')
        if mutation:
            path, before, after = mutation
            text = (base / path).read_text()
            assert before in text, (path, before)
            (base / path).write_text(text.replace(before, after, 1))
        fixture = (ROOT / 'tests/fixtures/LauncherKeyboardTest.qml').read_text()
        fixture = fixture.replace('import "../../shell" as Shell\n', '').replace('Shell.', '')
        (base / 'shell/launcher-test.qml').write_text(fixture)
        for name in ('home', 'config', 'data', 'cache', 'state', 'runtime', 'look'):
            (base / name).mkdir(mode=0o700)
        (base / 'data/applications').mkdir()
        (base / 'data/applications/fixture-app.desktop').write_text(
            '[Desktop Entry]\nType=Application\nName=Keyboard fixture\nExec=true\nIcon=utilities-terminal\n')
        for index in range(70):
            (base / f'data/applications/fixture-{index:02d}.desktop').write_text(
                f'[Desktop Entry]\nType=Application\nName=Fixture {index:02d}\nExec=true\n')
        (base / 'state/emaki').mkdir()
        (base / 'state/emaki/recent.json').write_text(
            '{"version":1,"entries":[{"kind":"app","ref":"fixture-app","time":1}]}')
        env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
                   XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
                   XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
                   XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(runtime_path(base)),
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                   LAUNCHER_LOOK_DIR=str(base / 'look'),
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET'):
            env.pop(key, None)
        result = subprocess.run(['qs', '-p', str(base / 'shell/launcher-test.qml'), '--no-color'],
                                env=env, capture_output=True, text=True, timeout=40)
        output = result.stdout + result.stderr
        # The completed signal precedes QtTest's cleanupTestCase counter increment.
        assert result.returncode == 0 and 'LAUNCHER_KEYBOARD_RESULT 14 0' in output, output
        if mutation:
            try:
                focus_look(output, base / 'look')
            except AssertionError as error:
                return error
            raise AssertionError(('dark focus rim was not rejected', output))
        rims = focus_look(output, base / 'look')
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        for shot in (base / 'look').glob('*.png'):
            shutil.copyfile(shot, EVIDENCE / shot.name)
        first = re.search(r"LAUNCHER_FIRST_OPEN_MODE (\{.*\})", output)
        assert first, output
        return rims, first.group(1)


rims, first_open = run()
print('PASS: launcher modes, categories, bounded traversal, clipboard footer, Escape and visible last rows')
print('PASS: first open puts the mode drop on the Apps tab:', first_open)
print('PASS: hover-look focus on the launcher path, rim contrast against the plate '
      + ', '.join(f'{name} {value:.2f}:1' for name, value in rims.items()))
print('PASS: rejected the dark 2 px focus rim:', run(('shell/FocusRing.qml',) + DARK_RIM).args[0])
