// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    // The focus-look contrasts checked by the test are the light ones: choose light, since
    // nothing saved is dark.
    Component.onCompleted: SettingsBridge.values = [
        {
            key: "appearance.theme",
            value: "light"
        }
    ]
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
        property int backs: 0
        property int settingsOpened: 0
        // A wallpaper-like backdrop under the translucent plate, as on the screen.
        Item {
            id: stage
            anchors.fill: parent
            Rectangle {
                anchors.fill: parent
                color: "#9b8270"
            }
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
        // A launcher made the way the shell makes it at start: closed, opened later.
        Component {
            id: freshLauncher
            LauncherBody {
                width: 700
                height: 650
                niri: service
            }
        }
        Connections {
            target: launcher
            ignoreUnknownSignals: true
            function onBackRequested() {
                window.backs++;
            }
        }
        TestCase {
            name: "LauncherKeyboard"
            when: window.visible
            onCompletedChanged: if (completed)
                console.log("LAUNCHER_KEYBOARD_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
            function check(ok, message) {
                if (!ok)
                    console.log("LAUNCHER_FAIL " + message);
                verify(ok, message);
            }
            function equal(actual, expected) {
                if (actual !== expected)
                    console.log("LAUNCHER_COMPARE " + actual + " expected " + expected);
                compare(actual, expected);
            }
            function selectionVisible() {
                const geometry = launcher.geometryOf(launcher.selected);
                return geometry !== null && geometry.rect.x >= geometry.clip.x - 1 && geometry.rect.y >= geometry.clip.y - 1 && geometry.rect.x + geometry.rect.width <= geometry.clip.x + geometry.clip.width + 1 && geometry.rect.y + geometry.rect.height <= geometry.clip.y + geometry.clip.height + 1;
            }
            function test_tab_strip_navigation() {
                const previousWidth = launcher.width;
                launcher.width = 480;
                launcher.setQuery("");
                launcher.setMode("All");
                window.requestActivate();
                const strip = findChild(launcher, "launcher-mode-strip");
                check(!!strip, "The tabs have a bounded strip");
                const query = findChild(launcher, "launcher-search");
                tryVerify(() => query.width >= 160);
                function fullyVisible(item) {
                    const p = item.mapToItem(strip, 0, 0);
                    const left = strip.fadeLeft ? strip.fadeWidth : 0;
                    const right = strip.width - (strip.fadeRight ? strip.fadeWidth : 0);
                    return p.x - launcher.tabFocusPad >= left - .75 && p.x + item.width + launcher.tabFocusPad <= right + .75;
                }
                for (const name of launcher.modes) {
                    launcher.setMode(name);
                    const tab = findChild(launcher, "launcher-tab-" + name.toLowerCase());
                    check(!!tab, name + " has a tab");
                    tryVerify(() => fullyVisible(tab), 2000, name + " scrolls into view");
                }
                launcher.setMode("All");
                launcher.takeFocus(true);
                keyClick(Qt.Key_Right);
                for (let index = 0; index < launcher.modes.length; ++index) {
                    const tab = findChild(launcher, "launcher-tab-" + launcher.modes[index].toLowerCase());
                    tryVerify(() => tab.activeFocus && fullyVisible(tab));
                    keyClick(Qt.Key_Return);
                    equal(launcher.modeIndex, index);
                    if (index < launcher.modes.length - 1)
                        keyClick(Qt.Key_Right);
                }
                // Scroll using the same wheel input a mouse sends, then click an exposed tab.
                launcher.setMode("All");
                tryVerify(() => strip.contentX < 1);
                mouseWheel(strip, strip.width / 2, strip.height / 2, 0, -120);
                tryVerify(() => strip.contentX > 1, 2000, "Mouse wheel scrolls tabs");
                const exposed = launcher.modes.map(name => findChild(launcher, "launcher-tab-" + name.toLowerCase())).find(tab => fullyVisible(tab));
                check(!!exposed, "A scrolled tab remains clickable");
                mouseClick(exposed, exposed.width / 2, exposed.height / 2);
                equal(launcher.modeIndex, exposed.index);
                tryVerify(() => fullyVisible(exposed), 2000, "Clicked tab stays outside the fade");
                // Clicking the same selected tab must reveal it again after manual scrolling.
                launcher.setMode("All");
                tryVerify(() => strip.contentX < 1);
                mouseWheel(strip, strip.width / 2, strip.height / 2, 0, -20);
                tryVerify(() => strip.contentX > 1);
                const first = findChild(launcher, "launcher-tab-all");
                mouseClick(first, first.width - 5, first.height / 2);
                equal(launcher.modeIndex, 0);
                tryVerify(() => fullyVisible(first), 2000, "Reselected tab clears the fade");
                mouseDrag(strip, strip.width - 10, strip.height / 2, -80, 0);
                tryVerify(() => strip.contentX > 1, 2000, "Dragging still scrolls tabs");
                tryVerify(() => !strip.moving, 2000, "Tab drag settles");
                launcher.width = previousWidth;
                launcher.setMode("Apps");
                launcher.takeFocus(true);
            }
            function test_accessible_selection() {
                launcher.setMode("Apps");
                launcher.setQuery("");
                launcher.takeFocus(true);
                tryVerify(() => launcher.results.length >= 70);
                const apps = findChild(launcher, "launcher-tab-apps");
                equal(apps.Accessible.name, "Apps");
                equal(apps.Accessible.role, Accessible.PageTab);
                check(apps.Accessible.selected, "current mode is announced as selected");
                const categories = launcher.categoryControls();
                check(categories.length > 0, "categories exist");
                for (const chip of categories) {
                    equal(chip.Accessible.name, chip.modelData);
                    equal(chip.Accessible.role, Accessible.PageTab);
                    equal(chip.Accessible.selected, chip.current);
                }
                launcher.selectAt(0);
                launcher.focusResults();
                const results = findChild(launcher, "launcher-results");
                equal(results.Accessible.role, Accessible.ListItem);
                equal(results.Accessible.name, launcher.results[0].label);
                check(results.Accessible.selected, "logical focused result is selected");
                launcher.selectAt(1);
                equal(results.Accessible.name, launcher.results[1].label);
                const tile = findChild(launcher, "launcher-result-1");
                check(!!tile, "visible tile exists");
                equal(tile.Accessible.role, Accessible.ListItem);
                equal(tile.Accessible.name, launcher.results[1].label);
                check(tile.Accessible.selected, "visible tile exposes selection");
                launcher.selectAt(0);
                check(!tile.Accessible.selected, "previous tile loses its selected state");
                launcher.setMode("Clipboard");
                tryVerify(() => launcher.clipboard.state !== "pending" && launcher.clipboard.actionState !== "pending");
                launcher.clipboard.entries = [
                    {
                        id: "accessible",
                        preview: "Accessible clipboard row",
                        text: "Clipboard text"
                    }
                ];
                tryVerify(() => launcher.results.length === 1 && !!findChild(launcher, "launcher-result-0"));
                const row = findChild(launcher, "launcher-result-0");
                equal(row.Accessible.role, Accessible.ListItem);
                equal(row.Accessible.name, "Accessible clipboard row");
                check(row.Accessible.selected, "clipboard row exposes selection");
                check(!apps.Accessible.selected, "previous mode loses its selected state");
            }
            function test_search_selection() {
                window.requestActivate();
                launcher.setMode("Apps");
                launcher.setQuery("");
                launcher.takeFocus(true);
                tryVerify(() => launcher.results.length >= 70);
                tryVerify(() => launcher.geometryOf(launcher.selected) !== null);
                check(launcher.selectedGeometry() !== null, "Empty search shows the Enter target");
                keyClick(Qt.Key_F);
                keyClick(Qt.Key_I);
                keyClick(Qt.Key_X);
                tryVerify(() => launcher.results.length > 0);
                check(launcher.inputFocused, "Typing keeps search focus");
                tryVerify(() => launcher.geometryOf(launcher.selected) !== null);
                check(launcher.selectedGeometry() !== null, "Typed search shows the Enter target");
                keyClick(Qt.Key_Tab);
                check(launcher.selectedGeometry() === null, "Header focus hides result selection");
                launcher.setQuery("");
            }
            function test_clear_results_search() {
                window.requestActivate();
                launcher.setMode("Apps");
                launcher.setQuery("fix");
                launcher.takeFocus(true);
                tryVerify(() => launcher.results.length > 0);
                keyClick(Qt.Key_Down);
                check(!launcher.inputFocused, "Down enters search results");
                keyClick(Qt.Key_Escape);
                equal(launcher.queryLength, 0);
                check(launcher.inputFocused, "Clearing results search returns focus to the editor");
            }
            function test_long_results() {
                window.requestActivate();
                launcher.setQuery("");
                launcher.setMode("Clipboard");
                tryVerify(() => launcher.clipboard.state !== "pending" && launcher.clipboard.actionState !== "pending");
                launcher.clipboard.entries = Array.from({
                    length: 40
                }, (_, index) => ({
                            id: "long-" + index,
                            preview: "Clipboard row " + index,
                            text: "Row " + index
                        }));
                tryVerify(() => launcher.results.length === 40, 2000, "Forty clipboard rows");
                check(launcher.inputFocused, "Search keeps focus over the clipboard list");
                // The first row is shown as Enter's target; Down moves on to the second.
                keyClick(Qt.Key_Down);
                equal(launcher.selected, 1);
                check(!launcher.inputFocused, "Down from search enters the results");
                for (let i = 2; i < 40; ++i)
                    keyClick(Qt.Key_Down);
                equal(launcher.selected, 39);
                tryVerify(selectionVisible, 1000, "Last clipboard row is fully visible");
                for (let i = 0; i < 39; ++i)
                    keyClick(Qt.Key_Up);
                equal(launcher.selected, 0);
                check(!launcher.inputFocused, "Up walks the rows before the search");
                keyClick(Qt.Key_Up);
                check(launcher.inputFocused, "Long list Up returns to search");
                launcher.setMode("Apps");
                launcher.category = "All";
                tryVerify(() => launcher.results.length >= 70, 2000, "Long grid has deterministic entries");
                keyClick(Qt.Key_Down);
                for (let i = 0; i < 20; ++i)
                    keyClick(Qt.Key_Down);
                for (let i = 0; i < 7; ++i)
                    keyClick(Qt.Key_Right);
                equal(launcher.selected, launcher.results.length - 1);
                tryVerify(selectionVisible, 1000, "Last app tile is fully visible");
                for (let i = 0; i < 20; ++i)
                    keyClick(Qt.Key_Up);
                check(launcher.inputFocused, "Long grid Up returns through categories to search");
            }
            // The first open after the shell starts: the mode drop stands on the Apps tab (pad 4)
            // and its label is drawn on the glass over it, never an 8x8 drop at (-4, -4) with the
            // tab's own label faded out.
            function test_mode_drop_first_open() {
                const fresh = createTemporaryObject(freshLauncher, stage, {
                    expansion: 0,
                    opened: false
                });
                verify(fresh !== null, "fresh launcher");
                wait(50);
                fresh.opened = true;
                fresh.expansion = 1;
                equal(fresh.modes[fresh.modeIndex], "Apps");
                const tab = findChild(fresh, "launcher-tab-apps");
                verify(tab !== null, "Apps tab");
                tryVerify(() => fresh.modeAlpha > .99 && !fresh.animating, 3000, "mode drop up and at rest");
                const p = tab.mapToItem(fresh, 0, 0);
                check(tab.width > 0 && tab.height > 0, "Apps tab has its geometry");
                const r = fresh.modeRect;
                const drop = fresh.glassStatus().mode;
                console.log("LAUNCHER_FIRST_OPEN_MODE " + JSON.stringify(drop));
                check(Math.abs(r.x - p.x) < .5 && Math.abs(r.y - p.y) < .5 && Math.abs(r.width - tab.width) < .5 && Math.abs(r.height - tab.height) < .5, "mode rect " + r + " is the Apps tab " + p.x + "," + p.y + " " + tab.width + "x" + tab.height);
                check(Math.abs(drop.x - (p.x - 4)) <= 1 && Math.abs(drop.y - (p.y - 4)) <= 1 && Math.abs(drop.width - (tab.width + 8)) <= 1 && Math.abs(drop.height - (tab.height + 8)) <= 1, "mode drop " + JSON.stringify(drop) + " stands on the Apps tab");
            }
            // One frame of the launcher with the focused control's rectangle, for the pixel
            // check in test-launcher-keyboard.py (hover look, no dark outline).
            function lookShot(name, item, pad) {
                // The drops and the mode bubble flow on springs; shoot them at rest.
                tryVerify(() => !launcher.animating, 3000, name + " at rest");
                const grow = pad ?? 0;
                const r = item ? item.mapToItem(stage, -grow, -grow, item.width + 2 * grow, item.height + 2 * grow) : launcher.selectedGeometry().rect;
                let done = false;
                verify(stage.grabToImage(result => {
                    verify(result.saveToFile(Quickshell.env("LAUNCHER_LOOK_DIR") + "/" + name + ".png"));
                    done = true;
                }));
                tryVerify(() => done, 2000, name + " captured");
                console.log("LAUNCHER_LOOK " + name + " " + r.x + " " + r.y + " " + r.width + " " + r.height + " " + (item ? "control" : "result"));
            }
            // The keyboard path: a recent program, Up to Search everything, Right along the tabs.
            function test_focus_look() {
                window.requestActivate();
                launcher.setQuery("");
                launcher.setMode("All");
                for (const child of launcher.data)
                    if (child.record !== undefined && child.entries !== undefined)
                        child.entries = [
                            {
                                kind: "app",
                                ref: "fixture-app",
                                time: 2
                            },
                            {
                                kind: "app",
                                ref: "fixture-00",
                                time: 1
                            }
                        ];
                tryVerify(() => launcher.results.length === 2, 2000, "two recent programs");
                const search = launcher.headerControls()[1];
                check(search.activeFocus, "the launcher opens in the search");
                keyClick(Qt.Key_Down);
                equal(launcher.selected, 1);
                check(window.activeFocusItem.objectName === "launcher-results", "Down enters the recent programs");
                lookShot("1-down-recent", null);
                keyClick(Qt.Key_Up);
                equal(launcher.selected, 0);
                lookShot("2-up-recent", null);
                keyClick(Qt.Key_Up);
                check(search.activeFocus, "Up from the first recent program reaches Search everything");
                lookShot("3-up-search", search);
                keyClick(Qt.Key_Right);
                check(launcher.headerControls()[2].activeFocus, "Right reaches All");
                lookShot("4-right-all", launcher.headerControls()[2], launcher.tabFocusPad);
                keyClick(Qt.Key_Right);
                check(launcher.headerControls()[3].activeFocus, "Right reaches Apps");
                lookShot("5-right-apps", launcher.headerControls()[3], launcher.tabFocusPad);
                keyClick(Qt.Key_Escape);
            }
            function test_controls() {
                window.requestActivate();
                launcher.takeFocus();
                launcher.modeIndex = 0;
                wait(100);
                for (const child of launcher.data)
                    if (child.record !== undefined && child.entries !== undefined)
                        child.entries = [
                            {
                                kind: "app",
                                ref: "fixture-app",
                                time: 1
                            }
                        ];
                const query = window.activeFocusItem;
                check(query.activeFocus, "initial search focus");
                launcher.clipboard.entries = [
                    {
                        id: "fixture",
                        preview: "Clipboard fixture",
                        text: "Clipboard fixture"
                    }
                ];
                // The fixture's seeded recent application is the initial content.
                tryVerify(() => launcher.results.length > 0);
                keyClick(Qt.Key_Down);
                check(window.activeFocusItem.objectName === "launcher-results", "Down enters recent results");
                keyClick(Qt.Key_Up);
                check(query.activeFocus, "Up from recent app reaches Search Everything");
                keyClick(Qt.Key_Right);
                check(launcher.headerControls()[2].activeFocus, "Right reaches All tab");
                keyClick(Qt.Key_Right);
                check(launcher.headerControls()[3].activeFocus, "Right reaches Apps tab");
                keyClick(Qt.Key_Return);
                equal(launcher.modeIndex, 1);
                keyClick(Qt.Key_Right);
                keyClick(Qt.Key_Return);
                equal(launcher.modeIndex, 2);
                keyClick(Qt.Key_A);
                check(query.activeFocus, "Typing in tabs focuses search");
                equal(query.text, "a");
                keyClick(Qt.Key_Escape);
                equal(query.text, "");
                equal(window.dismissals, 0);
                keyClick(Qt.Key_Tab);
                check(launcher.headerControls()[2].activeFocus, "Tab enters controls");
                keyClick(Qt.Key_Backtab);
                check(query.activeFocus, "Backtab returns search");
                const headers = launcher.headerControls();
                for (let i = 0; i < headers.length && !headers[headers.length - 1].activeFocus; ++i)
                    keyClick(Qt.Key_Tab);
                keyClick(Qt.Key_Return);
                equal(window.settingsOpened, 1);
                launcher.setMode("Clipboard");
                tryVerify(() => launcher.clipboard.state !== "pending" && launcher.clipboard.actionState !== "pending", 2000, "Clipboard loaded");
                launcher.clipboard.entries = [
                    {
                        id: "fixture",
                        preview: "Clipboard fixture",
                        text: "Clipboard fixture"
                    }
                ];
                // The footer is created with the list; wait for it, not for a fixed time.
                tryVerify(() => launcher.keyboardControls().slice(-1)[0]?.text === "Clear all", 2000, "Clipboard footer shown");
                const controls = launcher.keyboardControls();
                const clear = controls[controls.length - 1];
                equal(clear.text, "Clear all");
                for (let i = 0; i < controls.length && !clear.activeFocus; ++i)
                    keyClick(Qt.Key_Tab);
                check(clear.activeFocus, "Clipboard footer is reachable");
                keyClick(Qt.Key_Space);
                equal(launcher.clipboard.actionState, "pending");
                keyClick(Qt.Key_Escape);
                check(query.activeFocus, "Escape returns one level to search");
                keyClick(Qt.Key_Escape);
                equal(window.backs, 1);
                equal(window.dismissals, 0);
            }
        }
    }
}
