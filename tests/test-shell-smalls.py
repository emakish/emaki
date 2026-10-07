#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Focused desktop regressions; no session or personal settings are used."""
import configparser
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from runtime_fixture import short_runtime

ROOT = Path(__file__).resolve().parent.parent


def contrast(a, b):
    def luminance(value):
        channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
        return sum(c * weight for c, weight in zip(linear, (.2126, .7152, .0722)))
    low, high = sorted((luminance(a), luminance(b)))
    return (high + .05) / (low + .05)


class DesktopSmalls(unittest.TestCase):
    def test_power_selection(self):
        parser = configparser.ConfigParser()
        parser.read_string('[main]\n' + (ROOT / 'fuzzel/fuzzel.ini').read_text())
        c = parser['colors']
        for foreground in ('selection-text', 'selection-match'):
            self.assertGreaterEqual(contrast(c[foreground], c['selection']), 4.5)
        self.assertGreaterEqual(contrast(c['selection'], c['background']), 4.5)

    def test_power_tooltip(self):
        source = (ROOT / 'shell/SystemCompactRow.qml').read_text()
        shortcuts = re.search(r'property var shortcuts: \(\{(.*?)\}\)', source, re.S).group(1)
        self.assertNotIn('power:', shortcuts, 'the power panel has no matching shortcut')

    def test_plain_service_messages(self):
        source = (ROOT / 'shell/SystemBody.qml').read_text()
        for sentence in ('Networking isn’t running (NetworkManager).',
                         'Night Light isn’t installed (wlsunset).',
                         'This control is off in this session.'):
            self.assertTrue('"' + sentence + '"' in source, sentence)

    def test_clock_only_publishes_changed_minutes(self):
        # Run the production date bindings with a controllable time source. A
        # long sleep and backwards clock correction must update at the next tick.
        source = (ROOT / 'shell/StaticBar.qml').read_text()
        declarations = '\n'.join(line for line in source.splitlines()
                                 if re.search(r'property (?:double minuteTime|date dateTime):', line))
        qml = '''import QtQuick
import Quickshell
Scope {
    id: root
    property int changes: 0
    QtObject { id: time; property date date: new Date(2026, 9, 6, 12, 0, 0) }
    DECLARATIONS
    onDateTimeChanged: changes++
    function check(ok, why) { if (!ok) throw new Error(why); }
    Component.onCompleted: {
        changes = 0;
        for (let s = 1; s < 60; s++) time.date = new Date(2026, 9, 6, 12, 0, s);
        check(changes === 0, "same-minute updates: " + changes);
        time.date = new Date(2026, 9, 6, 12, 1, 0);
        check(changes === 1 && dateTime.getMinutes() === 1, "minute boundary");
        time.date = new Date(2026, 9, 7, 8, 23, 42);
        check(changes === 2 && dateTime.getDate() === 7 && dateTime.getHours() === 8, "resume");
        time.date = new Date(2026, 9, 6, 11, 0, 1);
        check(changes === 3 && dateTime.getHours() === 11, "clock correction");
        console.log("CLOCK_MINUTES_PASS");
        Qt.quit();
    }
    Timer { interval: 500; running: true; onTriggered: Qt.quit() }
}
'''.replace('DECLARATIONS', declarations)
        (ROOT / '.cache').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='clock-', dir=ROOT / '.cache') as directory, short_runtime() as runtime:
            work = Path(directory)
            for name in ('runtime', 'cache', 'config', 'data', 'state'):
                (work / name).mkdir(mode=0o700)
            path = work / 'test.qml'
            path.write_text(qml)
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                       QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=runtime,
                       XDG_CACHE_HOME=str(work / 'cache'), XDG_CONFIG_HOME=str(work / 'config'),
                       XDG_DATA_HOME=str(work / 'data'), XDG_STATE_HOME=str(work / 'state'))
            for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS'):
                env.pop(name, None)
            result = subprocess.run(['qs', '-p', str(path), '--no-color'], env=env,
                                    capture_output=True, text=True, timeout=10)
            self.assertIn('CLOCK_MINUTES_PASS', result.stdout + result.stderr)

    def test_clock_refreshes_on_zone_replacement(self):
        source = (ROOT / 'shell/StaticBar.qml').read_text()
        clock = source[source.index('    readonly property double minuteTime:'):
                       source.index('    property bool clockPresent:')]
        watcher = re.search(r'    FileView \{.*?\n    \}', source, re.S).group(0)
        (ROOT / '.cache').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='clock-zone-', dir=ROOT / '.cache') as directory, short_runtime() as runtime:
            work = Path(directory)
            for name in ('runtime', 'cache', 'config', 'data', 'state', 'etc'):
                (work / name).mkdir(mode=0o700)
            zone = work / 'etc/localtime'
            zone.symlink_to('/usr/share/zoneinfo/Etc/UTC')
            qml = '''import QtQuick
import Quickshell
import Quickshell.Io
Scope {
    id: bar
    QtObject { id: time; property date date: new Date(2026, 9, 6, 12, 0, 0) }
    CLOCK
    WATCHER
    property int refreshes: 0
    onDateTimeChanged: { refreshes++; console.log("ZONE_REFRESH", refreshes); }
    Component.onCompleted: console.log("ZONE_READY", clockTime);
    Timer { interval: 5000; running: true; onTriggered: Qt.quit() }
}
'''.replace('CLOCK', clock).replace('WATCHER', watcher.replace('"/etc/."', json.dumps(str(work / 'etc') + '/.')))
            path = work / 'test.qml'
            path.write_text(qml)
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                       QML_DISABLE_DISK_CACHE='1',
                       XDG_RUNTIME_DIR=runtime, XDG_CACHE_HOME=str(work / 'cache'),
                       XDG_CONFIG_HOME=str(work / 'config'), XDG_DATA_HOME=str(work / 'data'),
                       XDG_STATE_HOME=str(work / 'state'))
            for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS'):
                env.pop(name, None)
            process = subprocess.Popen(['qs', '-p', str(path), '--no-color'], env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            try:
                lines = []
                ready = False
                for line in process.stdout:
                    lines.append(line)
                    if 'ZONE_READY' in line:
                        ready = True
                        replacement = zone.with_name('replacement')
                        replacement.symlink_to('/usr/share/zoneinfo/Etc/GMT-2')
                        replacement.replace(zone)
                    if ready and 'ZONE_REFRESH' in line:
                        break
                # The localtime replacement must republish immediately while the
                # clock source remains frozen in the same minute. The actual
                # system timezone is deliberately unchanged in this test.
                self.assertTrue(any('ZONE_REFRESH 2' in line for line in lines), ''.join(lines))
                self.assertFalse(any('TypeError' in line for line in lines), ''.join(lines))
            finally:
                process.terminate()
                process.communicate(timeout=10)

    def test_zone_watcher_starts_without_warnings(self):
        # The production watcher path, unchanged: Quickshell must not warn about it.
        source = (ROOT / 'shell/StaticBar.qml').read_text()
        watcher = re.search(r'    FileView \{.*?\n    \}', source, re.S).group(0)
        self.assertIn('path: "/etc/."', watcher)
        (ROOT / '.cache').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='zw-', dir=ROOT / '.cache') as directory, short_runtime() as runtime:
            work = Path(directory)
            (work / 'rt').mkdir(mode=0o700)
            qml = ('import QtQuick\nimport Quickshell\nimport Quickshell.Io\nScope {\n    id: bar\n'
                   '    function refreshTimeZone() {}\n' + watcher +
                   '\n    Timer { interval: 1000; running: true; onTriggered: Qt.quit() }\n}\n')
            (work / 'test.qml').write_text(qml)
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', XDG_RUNTIME_DIR=runtime)
            for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS'):
                env.pop(name, None)
            result = subprocess.run(['qs', '-p', str(work / 'test.qml'), '--no-color'], env=env,
                                    capture_output=True, text=True, timeout=20)
            output = result.stdout + result.stderr
            self.assertNotIn('WARN', output, output)
            self.assertNotIn('ERROR', output, output)


if __name__ == '__main__':
    unittest.main()
