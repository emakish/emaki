#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise production system controls with real offscreen key events."""
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
from collections import namedtuple
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / '.cache/evidence/n6b'
EVIDENCE.mkdir(parents=True, exist_ok=True)
Rect = namedtuple('Rect', 'x y width height')
def run_case(case_name, mutation=None):
    with tempfile.TemporaryDirectory(prefix='emaki-system-keys-') as temporary:
        base = Path(temporary)
        shutil.copytree(ROOT / 'shell', base / 'qml')
        if mutation:
            filename, before, after, expected = mutation
            target = base / 'qml' / filename
            source = target.read_text()
            assert before in source, (case_name, before)
            target.write_text(source.replace(before, after, 1))
        shutil.copyfile(ROOT / 'tests/fixtures/SystemKeyboardTest.qml', base / 'qml/shell.qml')
        shutil.copyfile(ROOT / 'tests/fixtures/SystemFixture.qml', base / 'qml/SystemFixture.qml')
        # Keep production controls intact; replace only external service/menu dependencies.
        # Native menu handles require a D-Bus socket unavailable in the offscreen sandbox.
        (base / 'qml/NightLight.qml').write_text("""// Copyright (C) 2026 Artur Yakymenko
    // SPDX-License-Identifier: GPL-3.0-or-later
    import QtQuick
    QtObject {
        property bool enabled: false
        property bool on: false
        property int warmth: 50
        readonly property string state: on ? "on" : "off"
        function setOn(value) { on = value; return true; }
        function setWarmth(value) { warmth = value; return true; }
    }
    """)
        (base / 'qml/TrayService.qml').write_text("""// Copyright (C) 2026 Artur Yakymenko
    // SPDX-License-Identifier: GPL-3.0-or-later
    import QtQuick
    QtObject {
        id: tray
        property string state: "active"
        property int triggered: 0
        property bool checked: false
        readonly property var leaf: ({ text: "Nested action", enabled: true,
            isSeparator: false, hasChildren: false, triggered: () => ++tray.triggered })
        readonly property var submenu: ({ text: "More", enabled: true,
            isSeparator: false, hasChildren: true, entries: [leaf] })
        readonly property var check: ({ text: "Toggle", enabled: true,
            isSeparator: false, hasChildren: false, checkState: 0,
            triggered: () => { tray.checked = !tray.checked; ++tray.triggered; } })
        readonly property var disabled: ({ text: "Unavailable", enabled: false,
            isSeparator: false, hasChildren: false, triggered: () => tray.triggered += 100 })
        readonly property var separator: ({ text: "", enabled: false, isSeparator: true })
        readonly property var items: [{ title: "Fixture app", id: "fixture",
            hasMenu: true, menu: { entries: [disabled, separator, check, submenu] } }]
    }
    """)
        (base / 'qml/MockMenuOpener.qml').write_text("""// Copyright (C) 2026 Artur Yakymenko
    // SPDX-License-Identifier: GPL-3.0-or-later
    import QtQuick
    QtObject {
        property var menu: null
        readonly property var children: ({ values: menu?.entries ?? [] })
    }
    """)
        body_path = base / 'qml/SystemBody.qml'
        body_path.write_text(body_path.read_text().replace('QsMenuOpener {', 'MockMenuOpener {'))
        with (base / 'qml/qmldir').open('a') as definitions:
            definitions.write('\nSystemFixture 1.0 SystemFixture.qml\nMockMenuOpener 1.0 MockMenuOpener.qml\n')
        for name in ('home', 'config', 'state', 'cache', 'runtime', 'data'):
            (base / name).mkdir(mode=0o700)
        env = dict(os.environ, EMAKI_TEST_EVIDENCE=str(base), HOME=str(base / 'home'),
                   XDG_CONFIG_HOME=str(base / 'config'), XDG_STATE_HOME=str(base / 'state'),
                   XDG_CACHE_HOME=str(base / 'cache'), XDG_RUNTIME_DIR=str(base / 'runtime'),
                   XDG_DATA_HOME=str(base / 'data'), QT_QPA_PLATFORM='offscreen',
                   QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                   EMAKI_SHELL_TRAY='0', EMAKI_SHELL_NOTIFICATIONS='0',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'missing-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'missing-system-bus'))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_LOGGING_RULES'):
            env.pop(key, None)
        try:
            result = subprocess.run(['qs', '-p', str(base / 'qml'), '--no-color'], env=env,
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    timeout=30)
        except subprocess.TimeoutExpired as error:
            raise AssertionError(error.stdout.decode() if isinstance(error.stdout, bytes) else error.stdout) from error
        (EVIDENCE / ('system-' + case_name.replace(' ', '-') + '.log')).write_text(result.stdout)
        if mutation and expected:
            assert 'Configuration Loaded' in result.stdout, result.stdout
            assert 'KEYBOARD_FAIL ' + expected in result.stdout, result.stdout
            assert 'SYSTEM_KEYBOARD_PASS' not in result.stdout, result.stdout
            assert 'TypeError:' not in result.stdout and 'ReferenceError:' not in result.stdout, result.stdout
            print('PASS: rejected ' + case_name)
            return
        assert result.returncode == 0 and 'SYSTEM_KEYBOARD_PASS' in result.stdout, result.stdout
        if mutation:
            # A pixel-only mutant: the QML checks pass, the composited rim must not.
            try:
                pixels(base, result.stdout)
            except AssertionError as error:
                print('PASS: rejected ' + case_name + ':', error.args[0])
                return
            raise AssertionError((case_name, 'pixel mutant was not rejected'))
        for line in pixels(base, result.stdout):
            print(line)
        for name, strong in (('volume', False), ('cell-light', True), ('night', True)):
            with Image.open(base / (name + '.png')) as ring:
                # Pale: the hover look's light 1 px rim over its faint fill. Orange (the open
                # tab, a switch that is on): the strong 2 px rim. Never an opaque dark frame.
                center = ring.width // 2
                rim, inner = ring.getpixel((center, 0))[3], ring.getpixel((center, 1))[3]
                if strong:
                    assert 170 < rim < 210 and 170 < inner < 210, (name, rim, inner)
                else:
                    assert 30 < rim < 60 and inner < 30, (name, rim, inner)
            shutil.copyfile(base / (name + '.png'), EVIDENCE / ('system-focus-' + name + '.png'))
        assert 'Cannot set activeFocusOnTab' not in result.stdout, result.stdout
        assert 'KEYBOARD_FAIL' not in result.stdout and 'TypeError:' not in result.stdout, result.stdout


def luminance(pixel):
    channels = [v / 255 for v in pixel[:3]]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in channels]
    return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))


def ratio(a, b):
    a, b = luminance(a), luminance(b)
    return (max(a, b) + .05) / (min(a, b) + .05)


def pixels(base, output):
    """Focused against unfocused at the same scene pixels, along each control's straight top
    rim (both rows of a 2 px rim), away from rounded antialiasing and text."""
    rects = {name: Rect(*(round(float(v)) for v in values)) for name, *values in
             (match.split() for match in re.findall(r'CONTRAST_RECT (\w+ \S+ \S+ \S+ \S+)', output))}
    for index, name in enumerate(('primary', 'selected', 'pale')):
        rects[name] = Rect(20 + index * 110, 19, 100, 42)
    assert set(rects) == {'primary', 'selected', 'pale', 'keep', 'later'}, rects
    lines = []
    for name, rect in rects.items():
        before_path = base / ('contrast-' + name + '-false.png')
        after_path = base / ('contrast-' + name + '-true.png')
        with Image.open(before_path) as before, Image.open(after_path) as after:
            columns = range(rect.x + 15, rect.x + rect.width - 15)
            rows = [[ratio(before.getpixel((x, y)), after.getpixel((x, y))) for x in columns]
                    for y in (rect.y, rect.y + 1)]
        if name in ('primary', 'selected', 'keep'):
            # Orange controls: the strong rim, at least 3:1 on both of its rows.
            low = min(min(row) for row in rows)
            assert low >= 3, (name, 'focus contrast below 3:1 on orange', low)
            lines.append(f'PASS: {name} (orange) focus rim minimum contrast {low:.2f}:1')
        else:
            # Pale controls: the hover look. Visible, but a light rim, not a dark outline.
            high = max(rows[0])
            assert 1.05 <= high <= 2.2, (name, 'pale focus is not the light hover rim', high)
            lines.append(f'PASS: {name} (pale) hover-look focus rim contrast {high:.2f}:1')
        for path in (before_path, after_path):
            shutil.copyfile(path, EVIDENCE / path.name)
    return lines


run_case('system-keyboard')
print('PASS: system sliders, Wi-Fi, VPN, Bluetooth, layouts, power, tray menus and focus scrolling')
run_case('hidden system ring', ('SystemPanel.qml', 'shown: panel.opened && belongs', 'shown: false',
                                'system slider focus ring visible'))
RING = ('border.color: strong ? LiquidPalette.focusOnAccent : LiquidPalette.flatDropRim\n'
        '    border.width: strong ? 2 : 1')
run_case('opaque system ring', ('FocusRing.qml', RING,
                                  'border.color: ShellPalette.background\n    border.width: strong ? 2 : 1',
                                  'system slider focus ring existing hover rim'))
# The previous round's ring: the 2 px, 60 % ink rim on every control (a dark outline on pale).
run_case('dark rim on pale controls', ('FocusRing.qml', RING,
                                       'border.color: Qt.rgba(LiquidPalette.flatDropRim.r, LiquidPalette.flatDropRim.g, LiquidPalette.flatDropRim.b, .6)\n'
                                       '    border.width: 2',
                                       'system slider focus ring light hover rim'))
# Without the strong rim the focus disappears on orange: the pixel check must refuse it.
run_case('light rim on orange', ('LiquidPalette.qml',
                                 'focusOnAccent: Qt.rgba(36 / 255, 16 / 255, 24 / 255, .75)',
                                 'focusOnAccent: Qt.rgba(36 / 255, 16 / 255, 24 / 255, .16)', None))

run_case('early system focus', ('SystemPanel.qml',
                               'keyboardFocusPending = true;\n        applyKeyboardFocus();',
                               'Keyboard.focusFirst(Keyboard.targets(body.pages).length ? body.pages : panel);',
                               'animated opening starts at first page control'))
