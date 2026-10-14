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
    SystemMockup {
        id: backend
    }
    Window {
        id: window
        width: 1440
        height: 1000
        visible: true
        color: "#9b8270"
        Item {
            id: frame
            anchors.fill: parent
            focus: true
            Keys.onPressed: event => {
                if (event.key === Qt.Key_F1) {
                    scene.openLauncher(true);
                    event.accepted = true;
                } else if (event.key === Qt.Key_F2 || event.key === Qt.Key_F3) {
                    scene.openKeyboard(event.key === Qt.Key_F2 ? "sound" : "clock");
                    event.accepted = true;
                }
            }
            Rectangle {
                anchors.fill: parent
                color: window.color
            }
            WelcomeButton {
                id: accent
                text: "Next"
                primary: true
                visible: false
                x: 600
                y: 420
                z: 2
            }
            ShellScene {
                id: scene
                anchors.fill: parent
                anchors.bottomMargin: 50
                niri: service
                headless: true
                testWidth: window.width
                testHeight: window.height - 50
                borderMode: "soft"
                reservedSpace: 52
                skipIntro: true
            }
            Rectangle {
                anchors.bottom: parent.bottom
                width: parent.width
                height: 50
                color: "#f2e6d8"
                Text {
                    id: caption
                    anchors.centerIn: parent
                    color: "#241018"
                    font.pixelSize: 20
                }
            }
        }
        TestCase {
            name: "KeyboardWalkShots"
            when: window.visible
            onCompletedChanged: if (completed)
                console.log("KEYBOARD_WALK_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
            function shot(name, text, key) {
                const actual = window.activeFocusItem.key ?? window.activeFocusItem.objectName;
                if (actual !== key)
                    console.log("WALK_FOCUS_MISMATCH", name, actual, key);
                compare(actual, key, name + " caption focus");
                caption.text = text;
                wait(180);
                compare(window.activeFocusItem.key ?? window.activeFocusItem.objectName, key, name + " captured focus");
                let done = false;
                verify(frame.grabToImage(result => {
                    verify(result.saveToFile(Quickshell.env("KEYBOARD_SHOTS") + "/" + name + ".png"));
                    done = true;
                }));
                tryVerify(() => done);
            }
            function test_walk() {
                scene.services.backend = backend;
                service.connection = "connected";
                service.model = {
                    outputs: {
                        Fixture: {
                            logical: {
                                width: 1440,
                                height: 950
                            }
                        }
                    },
                    focused_output: "Fixture",
                    workspaces: {},
                    windows: {},
                    keyboard_layouts: {
                        names: ["English (US)"],
                        current_idx: 0
                    },
                    overview_open: false
                };
                window.requestActivate();
                frame.forceActiveFocus();
                keyClick(Qt.Key_F1);
                wait(250);
                verify(scene.launcherOpen);
                scene.input.modeIndex = 0;
                for (const child of scene.input.data)
                    if (child.record !== undefined && child.entries !== undefined)
                        child.entries = [
                            {
                                kind: "app",
                                ref: "fixture-app",
                                time: 1
                            }
                        ];
                wait(80);
                keyClick(Qt.Key_Down);
                verify(window.activeFocusItem.objectName === "launcher-results");
                shot("01-launcher-down", "Down — recent application", "launcher-results");
                keyClick(Qt.Key_Up);
                verify(window.activeFocusItem.objectName === "launcher-search");
                shot("02-launcher-up", "Up — Search Everything", "launcher-search");
                keyClick(Qt.Key_Right);
                verify(scene.input.headerControls()[2].activeFocus);
                shot("03-launcher-right", "Right — All", scene.input.headerControls()[2].objectName);
                keyClick(Qt.Key_Right);
                keyClick(Qt.Key_Return);
                compare(scene.input.modeIndex, 1);
                shot("04-launcher-apps", "Right, Enter — Apps", scene.input.headerControls()[3].objectName);
                keyClick(Qt.Key_Down);
                verify(scene.input.categoryControls()[0].activeFocus);
                shot("05-launcher-content", "Down — first application category", "");
                keyClick(Qt.Key_Escape);
                keyClick(Qt.Key_Escape);
                frame.forceActiveFocus();
                keyClick(Qt.Key_F2);
                wait(250);
                verify(scene.systemOpen);
                shot("06-sound-open", "F2 (sound entry) — output volume", "volume");
                keyClick(Qt.Key_Tab);
                shot("07-sound-tab", "Tab — output mute", "volume-icon");
                keyClick(Qt.Key_Up);
                shot("08-sound-up", "Up — tray tab", "cell-tray");
                keyClick(Qt.Key_Escape);
                frame.forceActiveFocus();
                keyClick(Qt.Key_F3);
                wait(250);
                verify(scene.drawerOpen);
                wait(350);
                for (let i = 0; i < 80 && window.activeFocusItem.key !== "month-next"; ++i)
                    keyClick(Qt.Key_Tab);
                shot("09-clock-open", "F3, Tab — next month", "month-next");
                keyClick(Qt.Key_Down);
                const firstDay = window.activeFocusItem.key;
                if (!/^day-\d+-\d+-6$/.test(firstDay))
                    console.log("WALK_CLOCK_MISMATCH", firstDay, "expected first day in this column");
                verify(/^day-\d+-\d+-6$/.test(firstDay), "Down reaches first day in this column");
                shot("10-clock-down", "Down — first day in this column", firstDay);
                keyClick(Qt.Key_Down);
                const nextWeek = firstDay.replace(/-6$/, "-13");
                shot("11-clock-down", "Down — same column, next week", nextWeek);
                keyClick(Qt.Key_Escape);
                verify(!scene.drawerOpen);
                accent.visible = true;
                accent.objectName = "accent-next";
                accent.forceActiveFocus(Qt.TabFocusReason);
                window.color = "#f2e6d8";
                shot("12-accent-light", "Focused primary button — light backdrop", "accent-next");
                window.color = "#392f29";
                shot("13-accent-dark", "Focused primary button — dark backdrop", "accent-next");
            }
        }
    }
}
