// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    NiriService {
        id: niri
        binary: ""
    }
    Window {
        id: window
        width: 760
        height: 700
        visible: true
        LauncherBody {
            id: body
            width: 700
            opened: true
            expansion: 1
            niri: niri
        }
        TestCase {
            name: "LauncherSettingsGlass"
            when: window.visible
            function test_settings_drop() {
                const glass = findChild(body, "launcher-glass");
                const gear = findChild(body, "launcher-settings-ink");
                verify(glass !== null && gear !== null);
                verify(glass.uRefraction > 0 && glass.uDispersion > 0);
                body.settingsActive = true;
                wait(500);
                compare(glass.uRefraction, 0);
                compare(glass.uDispersion, 0);
                compare(glass.uThickness, 1.5);
                compare(glass.uBulge.x, 5);
                verify(gear.visible && gear.opacity === 1);
                compare(gear.x + gear.width / 2, body.modeRect.x + 14);
                compare(gear.y + gear.height / 2, body.modeRect.y + 14);
                body.settingsActive = false;
                verify(!gear.visible);
                verify(glass.uRefraction > 0 && glass.uDispersion > 0);
            }
            onCompletedChanged: if (completed)
                console.log("LAUNCHER_SETTINGS_GLASS_RESULT", qtest_results.passCount, qtest_results.failCount)
        }
    }
}
