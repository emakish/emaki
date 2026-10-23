#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise live manager colours, an unset theme as light, and the complete light colour tree."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
UI = ROOT / 'update-manager/ui'
QML = '''import QtQuick
import QtTest
import Quickshell
import "file:ROOT/shell" as Shell
ShellRoot {
    id: root
    property bool failed: false
    TestCase { id: input; when: false }
    Item {
        id: scene
        ManagerLabel { id: label; text: "Program" }
        ManagerButton { id: primary; text: "Install"; primary: true; badge: 2 }
        ManagerButton { id: secondary; text: "Details" }
        ManagerButton { id: quiet; text: "More"; quiet: true }
        ProgramIcon { id: icon }
        ProgramCard { id: card; program: ({name: "Program", summary: "Description"}); featured: true }
        Stars { id: stars; rating: ({average: 4.5, count: 12}) }
    }
    function check(ok, why) { if (!ok) { failed = true; console.error("ASSERTION_FAILED " + why); } }
    function colours(item) {
        const out = [];
        if (item.color !== undefined) out.push(String(item.color));
        if (item.border !== undefined) out.push(String(item.border.color));
        if (item.gradient && item.gradient.stops)
            for (const stop of item.gradient.stops) out.push(String(stop.color));
        for (const child of item.children || []) out.push(...colours(child));
        return out;
    }
    Timer {
        interval: 100; running: true
        onTriggered: {
            // Nothing saved: the light tree with the rose accent. Compare themes with the same accent.
            const unset = JSON.stringify(colours(scene));
            Shell.SettingsBridge.values = [{key: "appearance.theme", value: "light"}, {key: "appearance.accent", value: "rose"}];
            input.wait(10);
            root.check(unset === JSON.stringify(colours(scene)), "an unset theme renders the light colours");
            Shell.SettingsBridge.values = [{key: "appearance.theme", value: "dark"}, {key: "appearance.accent", value: "rose"}];
            input.wait(10);
            root.check(unset !== JSON.stringify(colours(scene)), "an unset theme does not render the dark colours");
            // The frozen tree keeps its original explicit light and orange choices.
            Shell.SettingsBridge.values = [{key: "appearance.theme", value: "light"}, {key: "appearance.accent", value: "orange"}];
            input.wait(10);
            console.log("LIGHT_COLOURS " + JSON.stringify(colours(scene)));
            for (const dark of [false, true]) {
                for (const accent of Shell.ShellPalette.accents) {
                    Shell.SettingsBridge.values = [
                        {key: "appearance.theme", value: dark ? "dark" : "light"},
                        {key: "appearance.accent", value: accent.id}
                    ];
                    input.wait(10);
                    root.check(String(primary.background.color) === accent.base, "primary follows " + accent.id);
                    root.check(String(label.color) === String(Shell.ShellPalette.managerInk), "body follows theme");
                    root.check(String(input.findChild(icon, "package-body").color) === accent.strong, "fallback follows accent");
                    root.check(String(quiet.contentItem.children[0].color) === (dark ? accent.on_dark : accent.on_light), "quiet action readable");
                    root.check(String(card.background.gradient.stops[0].color) === String(Qt.alpha(Shell.ShellPalette.accent, .26)), "featured gradient follows accent");
                }
            }
            if (!root.failed) console.log("PALETTE_OK");
            Qt.quit();
        }
    }
}
'''


def run(mutant=False):
    with tempfile.TemporaryDirectory(prefix='manager-palette-') as directory:
        root = Path(directory)
        for path in UI.glob('*.qml'):
            source = path.read_text()
            if mutant and path.name == 'ManagerButton.qml':
                source = source.replace('Shell.ShellPalette.accent)', 'Shell.ShellPalette.accents[0].base)')
            (root / path.name).write_text(source.replace('@EMAKI_DATADIR@', str(ROOT)))
        (root / 'ManagerLogic.js').write_text((UI / 'ManagerLogic.js').read_text().replace('@EMAKI_DATADIR@', str(ROOT)))
        (root / 'test.qml').write_text(QML.replace('ROOT', str(ROOT)))
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1')
        for name in ('runtime', 'cache', 'config', 'data', 'state'):
            (root / name).mkdir(mode=0o700)
            env['XDG_' + name.upper() + ('_DIR' if name == 'runtime' else '_HOME')] = str(root / name)
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'NIRI_SOCKET'):
            env.pop(name, None)
        result = subprocess.run(['qs', '-p', str(root / 'test.qml'), '--no-color'], env=env, text=True, capture_output=True, timeout=30)
        log = result.stdout + result.stderr
        if mutant:
            assert 'ASSERTION_FAILED primary follows' in log, log
            return
        assert result.returncode == 0, log
        assert not any(word in log for word in ('ASSERTION_FAILED', 'ReferenceError', 'TypeError', 'Unable to assign', 'Binding loop')), log
        assert 'PALETTE_OK' in log, log
        return json.loads(next(line.split('LIGHT_COLOURS ', 1)[1] for line in log.splitlines() if 'LIGHT_COLOURS ' in line))


if __name__ == '__main__':
    original = json.loads((UI / 'tests/fixtures/default-colours.json').read_text())['colours']
    current = run()
    assert original == current, (original, current)
    run(mutant=True)
    print('PASS manager: unset theme is light; light colour tree unchanged; live light/dark and eight accents; frozen accent rejected')
