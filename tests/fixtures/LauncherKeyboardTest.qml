// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    NiriService {
        id: service
        binary: ""
    }
    Window {
        id: window
        width: 740
        height: 720
        visible: true
        property int dismissals: 0
        property int settingsOpened: 0
        Item {
            anchors.fill: parent
            property int leaked: 0
            Keys.onPressed: leaked++
            LauncherBody {
                id: launcher
                width: 700
                height: 650
                expansion: 1
                opened: true
                niri: service
                onDismissed: window.dismissals++
                onSettingsRequested: window.settingsOpened++
            }
        }
        TestCase {
            name: "LauncherKeyboard"
            when: window.visible
            onCompletedChanged: if (completed)
                console.log("LAUNCHER_KEYBOARD_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
            function traverse() {
                const query = launcher.keyboardControls()[0];
                keyClick(Qt.Key_Tab, query.activeFocus ? Qt.ControlModifier : Qt.NoModifier);
            }
            function test_controls() {
                window.requestActivate();
                launcher.takeFocus();
                wait(80);
                const query = launcher.keyboardControls()[0];
                verify(query.activeFocus);
                verify(!launcher.keyboardMode);
                const initialMode = launcher.modeIndex;
                keyClick(Qt.Key_Tab);
                compare(launcher.modeIndex, (initialMode + 1) % launcher.modes.length);
                verify(launcher.keyboardMode);
                verify(query.activeFocus);
                keyClick(Qt.Key_Backtab);
                compare(launcher.modeIndex, initialMode);
                verify(query.activeFocus);
                keyClick(Qt.Key_A);
                verify(launcher.keyboardMode);
                compare(query.text, "a");
                mouseClick(query, query.width / 2, query.height / 2);
                verify(!launcher.keyboardMode);
                keyClick(Qt.Key_Backspace);
                traverse();
                verify(launcher.keyboardControls()[1].activeFocus);
                traverse();
                const all = launcher.keyboardControls()[2];
                verify(all.activeFocus);
                keyClick(Qt.Key_Space);
                compare(launcher.modeIndex, 0);
                keyClick(Qt.Key_Right);
                keyClick(Qt.Key_Return);
                compare(launcher.modeIndex, 1);
                const modes = launcher.modes.length;
                for (let i = 1; i < modes; ++i)
                    traverse();
                const gear = launcher.keyboardControls()[2 + modes];
                verify(gear.activeFocus);
                keyClick(Qt.Key_Return);
                compare(window.settingsOpened, 1);
                traverse();
                const category = launcher.keyboardControls()[3 + modes];
                verify(category.activeFocus);
                keyClick(Qt.Key_Right);
                keyClick(Qt.Key_Enter);
                compare(launcher.category, "Development");
                // Wrap the complete current focus loop back to the query.
                for (let i = 0; i < launcher.keyboardControls().length && !query.activeFocus; ++i)
                    traverse();
                verify(query.activeFocus);
                // Reach Clipboard solely through the mode controls.
                for (let i = 0; i < 6; ++i)
                    traverse();
                keyClick(Qt.Key_Space);
                compare(launcher.modeIndex, 4);
                keyClick(Qt.Key_Backtab);
                traverse();
                verify(launcher.keyboardControls()[6].activeFocus);
                wait(100);
                launcher.clipboard.entries = [
                    {
                        id: "fixture",
                        preview: "Clipboard fixture",
                        text: "Clipboard fixture"
                    }
                ];
                wait(30);
                // Clipboard footer actions are in the same cyclic order.
                let controls = launcher.keyboardControls();
                compare(controls.length, 4 + launcher.modes.length);
                const clear = controls[controls.length - 1];
                compare(clear.text, "Clear all");
                for (let i = 0; i < controls.length; ++i) {
                    traverse();
                    verify(controls.some(item => item.activeFocus));
                }
                for (let i = 0; i < controls.length && !clear.activeFocus; ++i)
                    traverse();
                verify(clear.activeFocus);
                keyClick(Qt.Key_Space);
                compare(launcher.clipboard.actionState, "pending");
                keyClick(Qt.Key_Escape);
                compare(window.dismissals, 1);
                compare(launcher.parent.leaked, 0);
            }
        }
    }
}
