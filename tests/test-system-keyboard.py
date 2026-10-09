#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise production system controls with real offscreen key events."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / '.cache/evidence/n6b'
EVIDENCE.mkdir(parents=True, exist_ok=True)
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
        if mutation:
            assert 'Configuration Loaded' in result.stdout, result.stdout
            assert 'KEYBOARD_FAIL ' + expected in result.stdout, result.stdout
            assert 'SYSTEM_KEYBOARD_PASS' not in result.stdout, result.stdout
            assert 'TypeError:' not in result.stdout and 'ReferenceError:' not in result.stdout, result.stdout
            print('PASS: rejected ' + case_name)
            return
        assert result.returncode == 0 and 'SYSTEM_KEYBOARD_PASS' in result.stdout, result.stdout
        for name in ('volume', 'cell-light', 'night'):
            with Image.open(base / (name + '.png')) as ring:
                # Two adjacent palette edges remain distinct on both orange and pale fills.
                center = ring.width // 2
                assert ring.getpixel((center, 0)) == (242, 230, 216, 255), name
                assert ring.getpixel((center, 1)) == (20, 16, 13, 255), name
            shutil.copyfile(base / (name + '.png'), EVIDENCE / ('system-focus-' + name + '.png'))
        assert 'Cannot set activeFocusOnTab' not in result.stdout, result.stdout
        assert 'KEYBOARD_FAIL' not in result.stdout and 'TypeError:' not in result.stdout, result.stdout

run_case('system-keyboard')
print('PASS: system sliders, Wi-Fi, VPN, Bluetooth, layouts, power, tray menus and focus scrolling')
run_case('hidden system ring', ('SystemPanel.qml', 'shown: panel.opened && belongs', 'shown: false',
                                'system slider focus ring visible'))
run_case('orange-only system ring', ('FocusRing.qml', 'border.color: ShellPalette.text',
                                     'border.color: ShellPalette.accent',
                                     'system slider focus ring light outer edge'))

run_case('early system focus', ('SystemPanel.qml',
                               'keyboardFocusPending = true;\n        applyKeyboardFocus();',
                               'Keyboard.focusFirst(Keyboard.targets(body.pages).length ? body.pages : panel);',
                               'animated opening starts at first page control'))
