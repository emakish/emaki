// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    Window {
        visible: true
        width: 160
        height: 160
        Rectangle {
            id: surface
            anchors.fill: parent
            color: LiquidPalette.flatPanel
            Text {
                anchors.centerIn: parent
                text: "Appearance"
                color: LiquidPalette.ink
            }
        }
        TestCase {
            name: "AppearancePalette"
            when: true
            onCompletedChanged: if (completed)
                console.log("APPEARANCE_PALETTE_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
            function init() {
                SettingsBridge.values = [];
            }
            function test_liveTheme() {
                // Unset theme: light, with the rose accent (2026-10-15); orange stays a choice,
                // and an unknown saved name falls back to the default too.
                compare(Appearance.theme, "light");
                compare(Appearance.accent, "rose");
                compare(ShellPalette.dark, false);
                compare(ShellPalette.accent, Qt.color("#e780aa"));
                SettingsBridge.values = [
                    {
                        key: "appearance.accent",
                        value: "orange"
                    }
                ];
                compare(ShellPalette.accent, Qt.color("#e2733f"));
                SettingsBridge.values = [
                    {
                        key: "appearance.accent",
                        value: "purple"
                    }
                ];
                compare(ShellPalette.accent, Qt.color("#e780aa"));
                SettingsBridge.values = [];
                compare(LiquidPalette.ink, Qt.color("#241018"));
                SettingsBridge.values = [
                    {
                        key: "appearance.theme",
                        value: "dark"
                    }
                ];
                compare(ShellPalette.dark, true);
                compare(LiquidPalette.ink, Qt.color("#f4e9f0"));
                SettingsBridge.values = [];
                compare(ShellPalette.dark, false);
                compare(LiquidPalette.ink, Qt.color("#241018"));
                compare(LiquidPalette.flatPlate, Qt.rgba(1, .973, .953, .70));
            }
            function test_unsetThemeRendersLight() {
                wait(20);
                const unset = grabImage(surface);
                SettingsBridge.values = [
                    {
                        key: "appearance.theme",
                        value: "light"
                    },
                    {
                        key: "appearance.accent",
                        value: "rose"
                    }
                ];
                wait(20);
                verify(unset.equals(grabImage(surface)), "An unset theme renders the light pixels");
                SettingsBridge.values = [
                    {
                        key: "appearance.theme",
                        value: "dark"
                    }
                ];
                wait(20);
                verify(!unset.equals(grabImage(surface)), "An unset theme does not render the dark pixels");
            }
            function test_liveAccents() {
                for (const entry of ShellPalette.accents) {
                    SettingsBridge.values = [
                        {
                            key: "appearance.theme",
                            value: "light"
                        },
                        {
                            key: "appearance.accent",
                            value: entry.id
                        }
                    ];
                    compare(ShellPalette.accent, Qt.color(entry.base));
                    compare(LiquidPalette.accentOnLight, Qt.color(entry.on_light));
                    compare(LiquidPalette.selectedDrop, Qt.rgba(ShellPalette.accent.r, ShellPalette.accent.g, ShellPalette.accent.b, .9));
                }
            }
            function test_scheduleBinding() {
                SettingsBridge.values = [
                    {
                        key: "appearance.theme",
                        value: "automatic"
                    },
                    {
                        key: "appearance.light_time",
                        value: "07:00"
                    },
                    {
                        key: "appearance.dark_time",
                        value: "19:00"
                    }
                ];
                compare(Appearance.clock.precision, SystemClock.Minutes);
                compare(Appearance.clock.enabled, true);
                Appearance.now = new Date(2026, 1, 1, 7, 0);
                compare(ShellPalette.dark, false);
                Appearance.now = new Date(2026, 1, 1, 19, 0);
                compare(ShellPalette.dark, true);
                Appearance.now = new Date(2026, 1, 1, 8, 0);
                compare(ShellPalette.dark, false);
                Appearance.clock.dateChanged();
                verify(Math.abs(Appearance.now.getTime() - Date.now()) < 1000, "A clock tick evaluates current wall time after a jump");
                SettingsBridge.values = [
                    {
                        key: "appearance.theme",
                        value: "light"
                    }
                ];
                compare(Appearance.clock.enabled, false);
            }
            function test_lightPixels() {
                // The released light pixels remain reproducible after changing themes.
                SettingsBridge.values = [
                    {
                        key: "appearance.theme",
                        value: "light"
                    },
                    {
                        key: "appearance.accent",
                        value: "orange"
                    }
                ];
                wait(20);
                const before = grabImage(surface);
                compare(before.width, 160);
                compare(before.height, 160);
                SettingsBridge.values = [
                    {
                        key: "appearance.theme",
                        value: "dark"
                    },
                    {
                        key: "appearance.accent",
                        value: "blue"
                    }
                ];
                wait(20);
                verify(!before.equals(grabImage(surface)), "Dark appearance changes rendered pixels");
                SettingsBridge.values = [
                    {
                        key: "appearance.theme",
                        value: "light"
                    },
                    {
                        key: "appearance.accent",
                        value: "orange"
                    }
                ];
                wait(20);
                const after = grabImage(surface);
                verify(before.equals(after), "Returning to light and orange reproduces identical pixels");
            }
        }
    }
}
