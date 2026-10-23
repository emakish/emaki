// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell
import "Commands.js" as Commands

// The command menu in a real LauncherBody: the tab, search, keyboard and mouse, the confirm
// step, and each kind of call. The system and the Settings catalogue are recorders here; the
// programs a row starts are fakes on PATH that write what they were given
// (tests/test-launcher-commands.py reads that file after this run).
ShellRoot {
    NiriService {
        id: service
        binary: ""
    }
    QtObject {
        id: system
        property var acts: []
        function act(action, value) {
            acts = acts.concat([action + ":" + value]);
        }
        property QtObject night: QtObject {
            property bool on: false
            function setOn(value) {
                on = value;
                return true;
            }
        }
    }
    QtObject {
        id: catalogue
        property var sets: []
        function set(key, value) {
            sets = sets.concat([key + "=" + value]);
        }
    }
    Window {
        id: window
        width: 740
        height: 720
        visible: true
        property int dismissals: 0
        property var pages: []
        property var sessions: []
        LauncherBody {
            id: launcher
            width: 700
            height: 650
            expansion: 1
            opened: true
            niri: service
            commandSystem: system
            commandSettings: catalogue
            onDismissed: window.dismissals++
            onSettingsPageRequested: page => window.pages = window.pages.concat([page])
            onSessionRequested: action => window.sessions = window.sessions.concat([action])
        }
        TestCase {
            name: "LauncherCommands"
            when: window.visible
            onCompletedChanged: if (completed)
                console.log("LAUNCHER_COMMANDS_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
            function init() {
                launcher.setMode("Commands");
                launcher.setQuery("");
                launcher.takeFocus(true);
            }
            function ids() {
                return launcher.results.map(row => row.id);
            }
            function test_1_tab_opens_the_full_list_with_the_mouse() {
                launcher.setMode("Apps");
                const tab = findChild(launcher, "launcher-tab-commands");
                verify(tab !== null);
                compare(tab.Accessible.name, "Commands");
                mouseClick(tab);
                compare(launcher.modes[launcher.modeIndex], "Commands");
                // Night Light is off: only "Turn Night Light on" is offered.
                compare(launcher.results.length, Commands.actions.length - 1);
                verify(ids().includes("night-on"));
                verify(!ids().includes("night-off"));
                verify(findChild(launcher, "launcher-tab-emoji") !== null, "Emoji tab is present");
            }
            function test_2_search_titles_and_keywords() {
                launcher.setQuery("wifi");
                compare(ids()[0], "settings-wifi");
                launcher.setQuery("shut");
                compare(ids()[0], "poweroff");
                launcher.setQuery("reboot");
                compare(ids()[0], "reboot");
                launcher.setQuery("screenshot");
                compare(ids()[0], "screenshot");
                launcher.setQuery("dark");
                compare(ids()[0], "theme-dark");
                launcher.setQuery("qqqzzz");
                compare(launcher.results.length, 0);
                compare(launcher.note, "Nothing matches “qqqzzz”");
            }
            function test_3_keyboard_type_arrows_enter() {
                keyClick(Qt.Key_W);
                keyClick(Qt.Key_I);
                keyClick(Qt.Key_F);
                keyClick(Qt.Key_I);
                compare(launcher.queryText, "wifi");
                compare(launcher.results[launcher.selected].id, "settings-wifi");
                keyClick(Qt.Key_Return);
                compare(window.pages[window.pages.length - 1], "wifi");
                launcher.setQuery("settings");
                const first = launcher.results[0].id;
                keyClick(Qt.Key_Down);
                keyClick(Qt.Key_Down);
                verify(launcher.results[launcher.selected].id !== first);
                const chosen = launcher.results[launcher.selected].entry.call.settings;
                keyClick(Qt.Key_Return);
                compare(window.pages[window.pages.length - 1], chosen);
                // Esc clears the query first, as in every mode.
                launcher.takeFocus(true);
                keyClick(Qt.Key_Escape);
                compare(launcher.queryText, "");
            }
            function test_4_session_asks_in_the_power_panel() {
                // Enter, a second Enter and a double click only ask the power panel's question
                // (ShellScene.confirmSession); the launcher never runs the session action itself.
                const before = window.dismissals;
                launcher.setQuery("restart");
                compare(ids()[0], "reboot");
                keyPress(Qt.Key_Return);
                keyPress(Qt.Key_Return);
                keyRelease(Qt.Key_Return);
                keyClick(Qt.Key_Return);
                compare(window.sessions, ["reboot", "reboot", "reboot"]);
                compare(system.acts, []);
                compare(window.dismissals, before);
                launcher.setQuery("log out");
                compare(ids()[0], "logout");
                mouseDoubleClickSequence(findChild(launcher, "launcher-result-0"));
                compare(window.sessions[window.sessions.length - 1], "logout");
                launcher.setQuery("shut down");
                mouseClick(findChild(launcher, "launcher-result-0"));
                compare(window.sessions[window.sessions.length - 1], "poweroff");
                compare(system.acts, [], "no session action from the launcher");
                compare(launcher.results[0].label, "Shut down", "no question in the row");
            }
            function test_5_mouse_click_runs_a_row() {
                launcher.setQuery("night");
                const row = findChild(launcher, "launcher-result-0");
                verify(row !== null);
                compare(launcher.results[0].id, "night-on");
                mouseClick(row);
                compare(system.night.on, true);
                launcher.setQuery("night");
                compare(launcher.results[0].id, "night-off");
                launcher.setQuery("dark appearance");
                mouseClick(findChild(launcher, "launcher-result-0"));
                compare(catalogue.sets, ["appearance.theme=dark"]);
                launcher.setQuery("lock");
                compare(launcher.results[0].id, "lock");
                mouseClick(findChild(launcher, "launcher-result-0"));
                launcher.setQuery("record the screen");
                launcher.activate();
                launcher.setQuery("take a screenshot");
                launcher.activate();
            }
            function test_6_modes_from_the_menu() {
                launcher.setQuery("clipboard");
                launcher.activate();
                compare(launcher.modes[launcher.modeIndex], "Clipboard");
                compare(launcher.queryText, "", "the clipboard is not searched for “clipboard”");
                launcher.setMode("Commands");
                launcher.setQuery("emoji");
                compare(ids()[0], "emoji");
                launcher.activate();
                compare(launcher.modes[launcher.modeIndex], "Emoji");
                compare(launcher.queryText, "", "the picker opens on its recent list, not a search for “emoji”");
                tryVerify(() => launcher.emoji.state === "ready", 10000);
                tryVerify(() => launcher.results.length === launcher.emoji.limit, 5000, "recent and catalogue rows, unfiltered");
                verify(launcher.setMode("Commands"));
            }
            function test_7_emoji_search_copy_recent() {
                const before = window.dismissals;
                verify(launcher.setMode("Emoji"));
                tryVerify(() => launcher.emoji.state === "ready", 10000);
                tryVerify(() => launcher.results.length > 0);
                compare(launcher.results[0].kind, "emoji");
                launcher.setQuery("fire");
                compare(launcher.results[0].emoji, "🔥");
                keyClick(Qt.Key_Return);
                compare(Quickshell.clipboardText, "🔥");
                compare(window.dismissals, before + 1);
                launcher.setQuery("red heart");
                compare(launcher.results[0].emoji, "❤️");
                mouseClick(findChild(launcher, "launcher-result-0"));
                compare(Quickshell.clipboardText, "❤️");
                compare(window.dismissals, before + 2);
                // Empty query: the recent ones first, newest first, then the catalogue.
                launcher.setQuery("");
                compare(launcher.results[0].emoji, "❤️");
                compare(launcher.results[1].emoji, "🔥");
                verify(launcher.results.length > 2);
                launcher.setQuery("qqqzzzxx");
                compare(launcher.results.length, 0);
                compare(launcher.note, "Nothing matches “qqqzzzxx”");
                // The command menu and header both reach Emoji.
                verify(launcher.setMode("Commands"));
            }
        }
    }
}
