#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Login cursor wrappers stay private and bounded without starting a compositor."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import shutil
from runtime_fixture import runtime_path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('greeter_cursor', ROOT / 'shell/helpers/greeter-cursor.py')
helper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(helper)


class CursorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='greeter-cursor-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir(mode=0o700)
        self.base = self.root / 'base.kdl'
        self.original = (ROOT / 'greetd/niri.kdl').read_bytes()
        self.base.write_bytes(self.original)
        self.env = patch.dict(os.environ, XDG_RUNTIME_DIR=str(self.runtime), NIRI_SOCKET='fixture',
                              EMAKI_GREETER_COMPOSITOR='niri-emaki')
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_light_dark_preserve_base_and_bound_command(self):
        with patch.object(helper.subprocess, 'run') as run:
            for theme in ('Emaki-White', 'Emaki-Dark', 'Emaki-White'):
                helper.apply(theme, str(self.base))
                wrapper = self.runtime / 'emaki-greeter-cursor.kdl'
                self.assertEqual(wrapper.read_text(), '// Managed login cursor.\ninclude '
                                 + json.dumps(str(self.base)) + '\ncursor { xcursor-theme '
                                 + json.dumps(theme) + '; }\n')
                self.assertEqual(stat.S_IMODE(wrapper.stat().st_mode), 0o600)
                self.assertEqual(self.base.read_bytes(), self.original)
                run.assert_called_with(['niri-emaki', 'msg', 'action', 'load-config-file', '--path',
                                        str(wrapper)], check=True, timeout=3, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.assertEqual(len(list(self.runtime.iterdir())), 1)

    def test_invalid_input_has_no_side_effects(self):
        with patch.object(helper.subprocess, 'run') as run:
            for theme in ('Emaki', 'automatic', 'dark', 'Emaki-Dark"; spawn "bad'):
                with self.assertRaises(ValueError):
                    helper.apply(theme, str(self.base))
            with self.assertRaises(ValueError):
                helper.apply('Emaki-White', 'relative.kdl')
            with patch.dict(os.environ, EMAKI_GREETER_COMPOSITOR='other'):
                with self.assertRaises(ValueError):
                    helper.apply('Emaki-White', str(self.base))
            self.assertEqual(list(self.runtime.iterdir()), [])
            run.assert_not_called()

    def test_unsafe_runtime_and_redirected_wrapper_are_rejected(self):
        with patch.object(helper.subprocess, 'run') as run:
            self.runtime.chmod(0o755)
            with self.assertRaises(ValueError):
                helper.apply('Emaki-White', str(self.base))
            self.runtime.chmod(0o700)
            link = self.root / 'redirect'
            link.symlink_to(self.runtime)
            with patch.dict(os.environ, XDG_RUNTIME_DIR=str(link)):
                with self.assertRaises(OSError):
                    helper.apply('Emaki-White', str(self.base))
            wrapper = self.runtime / 'emaki-greeter-cursor.kdl'
            wrapper.symlink_to(self.base)
            with self.assertRaises(ValueError):
                helper.apply('Emaki-White', str(self.base))
            self.assertEqual(self.base.read_bytes(), self.original)
            run.assert_not_called()

    def test_timeout_is_bounded_and_temporary_file_removed(self):
        with patch.object(helper.subprocess, 'run', side_effect=subprocess.TimeoutExpired('niri', 3)):
            with self.assertRaises(subprocess.TimeoutExpired):
                helper.apply('Emaki-Dark', str(self.base))
        self.assertEqual([p.name for p in self.runtime.iterdir()], ['emaki-greeter-cursor.kdl'])
        self.assertEqual(self.base.read_bytes(), self.original)

    def test_ui_resolves_overrides_and_avoids_offscreen_processes(self):
        source = (ROOT / 'shell/greeter.qml').read_text()
        self.assertIn('"appearance.cursor", "automatic"', source)
        self.assertIn('cursorPreference === "dark"', source)
        self.assertIn('cursorPreference !== "white" && Appearance.dark', source)
        self.assertIn('onCursorThemeChanged: cursorPending = true', source)
        self.assertIn('!cursorApply.running && Boolean(Quickshell.env("NIRI_SOCKET"))', source)

    def test_offscreen_live_theme_overrides_and_busy_coalescing(self):
        source = (ROOT / 'shell/greeter.qml').read_text()
        start = source.index('    readonly property string cursorPreference:')
        end = source.index('    function pickUser(', start)
        block = source[start:end].replace('onRead: _line => {}',
                                          'onRead: line => root.applied = root.applied.concat([line])')
        qml = self.root / 'qml'
        (qml / 'helpers').mkdir(parents=True)
        shutil.copy(ROOT / 'shell/AppearanceLogic.js', qml)
        (qml / 'helpers/greeter-cursor.py').write_text(
            "import sys, time\ntime.sleep(0.25)\nprint(sys.argv[1], flush=True)\n")
        (qml / 'qmldir').write_text('singleton Appearance 1.0 Appearance.qml\n'
                                   'singleton SettingsBridge 1.0 SettingsBridge.qml\n'
                                   'singleton Platform 1.0 Platform.qml\n')
        (qml / 'Appearance.qml').write_text('pragma Singleton\nimport QtQuick\nQtObject { property bool dark: false }\n')
        (qml / 'SettingsBridge.qml').write_text('pragma Singleton\nimport QtQuick\nQtObject { property var values: [] }\n')
        (qml / 'Platform.qml').write_text('pragma Singleton\nimport QtQuick\nQtObject { readonly property string python: "python3"; readonly property string dataDir: "/tmp/fixture-data" }\n')
        fixture = """import QtQuick
import Quickshell
import Quickshell.Io
import QtTest
import "AppearanceLogic.js" as AppearanceLogic
Scope {
    id: root
    property bool selectionLoaded: false
    property bool launching: false
    property var applied: []
BLOCK
    TestCase {
        name: "GreeterCursorLive"
        when: true
        function test_live() {
            wait(200); compare(root.applied.length, 0);
            root.selectionLoaded = true;
            tryCompare(root, "applied", ["Emaki-White"], 3000);
            Appearance.dark = true;
            tryCompare(root, "applied", ["Emaki-White", "Emaki-Dark"], 3000);
            SettingsBridge.values = [{key: "appearance.cursor", value: "white"}];
            tryCompare(root, "applied", ["Emaki-White", "Emaki-Dark", "Emaki-White"], 3000);
            Appearance.dark = false;
            wait(450); compare(root.applied.length, 3);
            SettingsBridge.values = [{key: "appearance.cursor", value: "dark"}];
            tryCompare(root, "applied", ["Emaki-White", "Emaki-Dark", "Emaki-White", "Emaki-Dark"], 3000);
            SettingsBridge.values = [];
            tryCompare(cursorApply, "running", true, 2000);
            Appearance.dark = true;
            tryCompare(root, "applied", ["Emaki-White", "Emaki-Dark", "Emaki-White", "Emaki-Dark", "Emaki-White", "Emaki-Dark"], 4000);
            root.launching = true;
            Appearance.dark = false;
            wait(500); compare(root.applied.length, 6);
            root.launching = false;
            tryCompare(root, "applied", ["Emaki-White", "Emaki-Dark", "Emaki-White", "Emaki-Dark", "Emaki-White", "Emaki-Dark", "Emaki-White"], 3000);
        }
        onCompletedChanged: if (completed) console.log("GREETER_CURSOR_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
    }
}
""".replace('BLOCK', block)
        (qml / 'test.qml').write_text(fixture)
        for name in ('home', 'config', 'data', 'state', 'cache'):
            (self.root / name).mkdir(mode=0o700)
        env = dict(os.environ, HOME=str(self.root / 'home'), XDG_CONFIG_HOME=str(self.root / 'config'),
                   XDG_DATA_HOME=str(self.root / 'data'), XDG_STATE_HOME=str(self.root / 'state'),
                   XDG_CACHE_HOME=str(self.root / 'cache'), XDG_RUNTIME_DIR=str(runtime_path(self.root)),
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QML_DISABLE_DISK_CACHE='1',
                   NIRI_SOCKET='fixture-only-no-compositor', DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(self.root / 'no-bus'))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY'):
            env.pop(key, None)
        result = subprocess.run(['qs', '-p', str(qml / 'test.qml'), '--no-color'], env=env,
                                capture_output=True, text=True, timeout=25)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn('GREETER_CURSOR_RESULT 2 0', output)
        # The real gate must suppress the process even if all appearance signals fire.
        first = fixture.index('            wait(200);')
        last = fixture.index('\n        }\n        onCompletedChanged', first)
        fixture = fixture[:first] + """            root.selectionLoaded = true;
            Appearance.dark = true;
            wait(500);
            compare(root.applied.length, 0);
            compare(cursorApply.running, false);""" + fixture[last:]
        (qml / 'test.qml').write_text(fixture)
        env.pop('NIRI_SOCKET')
        result = subprocess.run(['qs', '-p', str(qml / 'test.qml'), '--no-color'], env=env,
                                capture_output=True, text=True, timeout=10)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn('GREETER_CURSOR_RESULT 2 0', output)



if __name__ == '__main__':
    unittest.main()
