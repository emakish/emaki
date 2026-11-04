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
            height: 600
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
                const flatCanvas = findChild(body, "launcher-canvas-flat");
                const glassCanvas = findChild(body, "launcher-canvas");
                verify(flatCanvas !== null && glassCanvas !== null);
                compare(flatCanvas.shown, LiquidPalette.sharedCanvas);
                compare(glassCanvas.shown, false);
                for (const canvas of [flatCanvas, glassCanvas]) {
                    compare(canvas.y, 0);
                    compare(canvas.height, body.shownHeight);
                }
                verify(glass.uRefraction > 0 && glass.uDispersion > 0);
                body.settingsActive = true;
                wait(500);
                compare(glass.uRefraction, 0);
                compare(glass.uDispersion, 0);
                compare(glass.uThickness, 1.5);
                compare(glass.uBulge.x, 5);
                // Settings owns the body tint even with the shared canvas disabled.
                // Both launcher rendering paths stop at its top edge, without overlap.
                compare(flatCanvas.shown, true);
                compare(glassCanvas.shown, false);
                for (const canvas of [flatCanvas, glassCanvas]) {
                    compare(canvas.y, 0);
                    compare(canvas.height, Metrics.launcherHeader);
                    compare(canvas.width, body.shownWidth);
                    compare(canvas.bottomLeftRadius, 0);
                    compare(canvas.bottomRightRadius, 0);
                }
                body.expansion = .01;
                for (const canvas of [flatCanvas, glassCanvas])
                    compare(canvas.height, body.shownHeight);
                body.expansion = 1;
                verify(gear.visible && gear.opacity === 1);
                compare(gear.x + gear.width / 2, body.modeRect.x + 14);
                compare(gear.y + gear.height / 2, body.modeRect.y + 14);
                body.settingsActive = false;
                verify(!gear.visible);
                verify(glass.uRefraction > 0 && glass.uDispersion > 0);
                compare(flatCanvas.shown, LiquidPalette.sharedCanvas);
                for (const canvas of [flatCanvas, glassCanvas]) {
                    compare(canvas.height, body.shownHeight);
                    compare(canvas.bottomLeftRadius, body.shownRadius);
                    compare(canvas.bottomRightRadius, body.shownRadius);
                }
            }
            onCompletedChanged: if (completed)
                console.log("LAUNCHER_SETTINGS_GLASS_RESULT", qtest_results.passCount, qtest_results.failCount)
        }
    }
}
