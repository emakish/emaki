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
    // A short catalog: the first 24 of the fixture's apps, four rows that all fit once the
    // launcher is as tall as it asks to be.
    AppCatalog {
        id: shortCatalog
        readonly property var entries: DesktopEntries.applications.values.filter(entry => shown(entry)).sort((a, b) => a.name.localeCompare(b.name)).slice(0, 24)
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
        Component {
            id: shortLauncher
            LauncherBody {
                width: 700
                height: 350
                niri: service
                sharedCatalog: shortCatalog
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
                check(launcher.selectedGeometry() !== null, "Search focus shows the Enter target");
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
                // Down enters the nearest layer at its first row.
                keyClick(Qt.Key_Down);
                equal(launcher.selected, 0);
                check(!launcher.inputFocused, "Down from search enters the results");
                for (let i = 1; i < 40; ++i)
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
                check(launcher.headerControls().includes(window.activeFocusItem), "Long grid Up returns through categories to the nearest header control");
            }
            function test_removed_result_recovery() {
                window.requestActivate();
                launcher.setQuery("");
                launcher.setMode("Clipboard");
                tryVerify(() => launcher.clipboard.state !== "pending" && launcher.clipboard.actionState !== "pending");
                const clips = [0, 1, 2].map(index => ({
                            id: "recovery-" + index,
                            preview: "Recovery row " + index,
                            text: "Row " + index
                        }));
                launcher.clipboard.entries = clips;
                tryVerify(() => launcher.results.length === 3);
                launcher.selectAt(1);
                launcher.focusResults();
                keyClick(Qt.Key_Delete);
                check(launcher.clipboard.actionState === "pending", "Delete requests removal of the selected clip");
                tryVerify(() => launcher.clipboard.actionState !== "pending");
                // Deliver the refreshed history after the deletion completes.
                launcher.clipboard.entries = [clips[0], clips[2]];
                tryVerify(() => launcher.selected === -1);
                keyClick(Qt.Key_Down);
                equal(launcher.selected, 0);
                tryVerify(selectionVisible, 2000, "Down recovers after deleting the selected clip");
                keyClick(Qt.Key_Down);
                equal(launcher.selected, 1);

                service.model = {
                    windows: {
                        1: {
                            id: 1
                        },
                        2: {
                            id: 2
                        }
                    },
                    workspaces: {},
                    keyboard_layouts: null,
                    overview_open: false
                };
                service.connection = "connected";
                launcher.setMode("Windows");
                tryVerify(() => launcher.results.length === 2);
                launcher.selectAt(1);
                launcher.focusResults();
                service.model = {
                    windows: {
                        1: {
                            id: 1
                        }
                    },
                    workspaces: {},
                    keyboard_layouts: null,
                    overview_open: false
                };
                tryVerify(() => launcher.selected === -1);
                keyClick(Qt.Key_Down);
                equal(launcher.selected, 0);
                tryVerify(selectionVisible, 2000, "Down recovers after the selected window closes");
                keyClick(Qt.Key_Up);
                check(launcher.inputFocused, "Up from the recovered first window reaches search");
                service.connection = "connecting";
                service.model = null;
            }
            function test_wheel_scrolled_result_recovery() {
                window.requestActivate();
                launcher.setQuery("");
                for (const mode of ["Clipboard", "Apps"]) {
                    launcher.setMode(mode);
                    if (mode === "Clipboard") {
                        tryVerify(() => launcher.clipboard.state !== "pending" && launcher.clipboard.actionState !== "pending");
                        launcher.clipboard.entries = Array.from({
                            length: 40
                        }, (_, index) => ({
                                    id: "wheel-" + index,
                                    preview: "Wheel row " + index,
                                    text: "Row " + index
                                }));
                    }
                    tryVerify(() => launcher.results.length >= 40);
                    launcher.selectAt(0);
                    launcher.focusResults();
                    tryVerify(selectionVisible);
                    const view = findChild(launcher, mode === "Apps" ? "launcher-grid" : "launcher-list");
                    mouseWheel(view, view.width / 2, view.height / 2, 0, -1200);
                    tryVerify(() => launcher.geometryOf(launcher.selected) === null, 2000, "Wheel hides the selected " + mode + " result");
                    tryVerify(() => !view.moving, 2000);
                    check(window.activeFocusItem.objectName === "launcher-results", "Wheel retains result keyboard focus");
                    keyClick(Qt.Key_Down);
                    equal(launcher.selected, 0);
                    tryVerify(selectionVisible, 2000, "Arrow reveals the wheel-scrolled " + mode + " selection");
                    keyClick(Qt.Key_Down);
                    equal(launcher.selected, mode === "Apps" ? 7 : 1);
                    tryVerify(selectionVisible);
                }
            }
            function test_first_open_selection_uses_accent() {
                const fresh = createTemporaryObject(freshLauncher, stage, {
                    expansion: 0,
                    opened: false
                });
                verify(fresh !== null);
                wait(50);
                fresh.opened = true;
                fresh.expansion = 1;
                const tab = findChild(fresh, "launcher-tab-apps");
                tryVerify(() => tab !== null && !fresh.animating, 3000);
                equal(fresh.modes[fresh.modeIndex], "Apps");
                verify(tab.Accessible.selected);
                equal(fresh.glassStatus().mode.alpha, 0);
                equal(fresh.glassStatus().category.alpha, 0);
                console.log("LAUNCHER_FIRST_OPEN_MODE " + JSON.stringify(fresh.glassStatus().mode));
            }
            // A launcher off the screen holds no app tiles (each with its icons); showing it
            // builds them, hiding it releases them (1.0 lightness).
            function test_closed_launcher_builds_no_tiles() {
                const fresh = createTemporaryObject(freshLauncher, stage, {
                    expansion: 0,
                    opened: false,
                    visible: false
                });
                verify(fresh !== null, "fresh launcher");
                tryVerify(() => fresh.results.length >= 70, 3000, "apps loaded while closed");
                const grid = findChild(fresh, "launcher-grid");
                verify(grid !== null, "app grid");
                wait(100);
                equal(grid.count, 0);
                fresh.visible = true;
                fresh.opened = true;
                fresh.expansion = 1;
                tryVerify(() => grid.count > 0 && grid.count === fresh.results.length - fresh.frequentCount, 3000, "tiles built on open");
                // Scrolled down, closed the way ShellScene's closeTimer closes it (hidden,
                // then reset) and reopened: the grid is where the person left it.
                wait(300); // the opening reveal of the selection settles first
                grid.contentY = grid.originY + grid.contentHeight - grid.height;
                wait(50);
                const scrolled = grid.contentY;
                verify(scrolled > 0, "grid scrolled");
                fresh.opened = false;
                fresh.expansion = 0;
                fresh.visible = false;
                fresh.reset();
                tryVerify(() => grid.count === 0, 2000, "tiles released when hidden");
                fresh.visible = true;
                fresh.opened = true;
                fresh.expansion = 1;
                tryVerify(() => grid.count > 0, 2000, "tiles rebuilt on reopen");
                wait(50);
                equal(grid.contentY, scrolled, "scroll position kept across close and reopen");
                // A grid at the top stays at the top.
                grid.contentY = grid.originY;
                wait(50);
                const top = grid.contentY;
                fresh.visible = false;
                fresh.reset();
                fresh.visible = true;
                tryVerify(() => grid.count > 0, 2000, "tiles rebuilt again");
                wait(50);
                equal(grid.contentY, top, "top kept");
            }
            // Only the grid that was hidden gets its scroll back. A reset search, another
            // mode or a search typed at once opens at the top, as changed results always did.
            function openGrid() {
                const fresh = createTemporaryObject(freshLauncher, stage, {
                    expansion: 1,
                    opened: true
                });
                verify(fresh !== null, "fresh launcher");
                tryVerify(() => fresh.results.length >= 70, 3000, "apps loaded");
                const grid = findChild(fresh, "launcher-grid");
                verify(grid !== null, "app grid");
                tryVerify(() => grid.count > 0, 3000, "tiles built");
                wait(300); // the opening reveal of the selection settles first
                return [fresh, grid];
            }
            // ShellScene's close (hidden, then reset) and a reopen.
            function closeAndReopen(fresh, grid) {
                fresh.opened = false;
                fresh.expansion = 0;
                fresh.visible = false;
                fresh.reset();
                tryVerify(() => grid.count === 0, 2000, "tiles released when hidden");
                fresh.visible = true;
                fresh.opened = true;
                fresh.expansion = 1;
                tryVerify(() => grid.count > 0, 2000, "tiles rebuilt on reopen");
                wait(50);
            }
            function test_reopened_grid_after_a_reset_search_is_at_the_top() {
                const [fresh, grid] = openGrid();
                fresh.setQuery("Fixture");
                tryVerify(() => grid.count > 0, 2000, "search results");
                wait(50);
                grid.contentY = Math.min(300, grid.originY + grid.contentHeight - grid.height);
                wait(50);
                verify(grid.contentY > grid.originY, "search grid scrolled");
                closeAndReopen(fresh, grid);
                compare(fresh.queryText, "");
                equal(grid.contentY, grid.originY, "a reset search reopens All at the top");
            }
            function test_reopened_grid_after_another_mode_stays_at_the_top() {
                const [fresh, grid] = openGrid();
                grid.contentY = 300;
                wait(50);
                verify(grid.contentY > grid.originY, "All scrolled");
                verify(fresh.setMode("Windows"), "Windows mode");
                wait(50);
                closeAndReopen(fresh, grid);
                equal(grid.contentY, grid.originY, "Apps after another mode opens at the top");
                closeAndReopen(fresh, grid);
                equal(grid.contentY, grid.originY, "a grid left at the top stays at the top");
            }
            function test_search_typed_on_reopen_cancels_the_restore() {
                const [fresh, grid] = openGrid();
                grid.contentY = 300;
                wait(50);
                verify(grid.contentY > grid.originY, "All scrolled");
                fresh.visible = false;
                tryVerify(() => grid.count === 0, 2000, "tiles released");
                fresh.visible = true;
                fresh.setQuery("Fixture");
                tryVerify(() => grid.count > 0, 2000, "search results");
                wait(50);
                equal(grid.contentY, grid.originY, "a search typed at once opens at its top");
            }
            // ShellScene clamps the closed launcher to the output's height. A grid that a taller
            // display shows whole opens at its top and keeps no older offset: back on the
            // shorter display it still opens at the top.
            function test_grid_shown_whole_on_a_taller_display_forgets_its_offset() {
                const fresh = createTemporaryObject(shortLauncher, stage, {
                    expansion: 1,
                    opened: true
                });
                verify(fresh !== null, "short launcher");
                const grid = findChild(fresh, "launcher-grid");
                verify(grid !== null, "app grid");
                tryVerify(() => grid.count === 24, 3000, "short catalog tiles built");
                wait(300); // the opening reveal of the selection settles first
                grid.contentY = 100;
                wait(50);
                equal(grid.contentY, 100, "short grid scrolled");
                const reopen = height => {
                    fresh.opened = false;
                    fresh.expansion = 0;
                    fresh.visible = false;
                    fresh.reset();
                    tryVerify(() => grid.count === 0, 2000, "tiles released when hidden");
                    fresh.height = height;
                    fresh.visible = true;
                    fresh.opened = true;
                    fresh.expansion = 1;
                    tryVerify(() => grid.count > 0, 2000, "tiles rebuilt on reopen");
                    wait(50);
                };
                reopen(fresh.desiredHeight);
                verify(grid.contentHeight <= grid.height, "the taller display shows every row");
                equal(grid.contentY, grid.originY, "a grid shown whole opens at its top");
                reopen(350);
                verify(grid.contentHeight > grid.height, "the shorter display scrolls again");
                equal(grid.contentY, grid.originY, "back on the shorter display it stays at the top");
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
                equal(launcher.selected, 0);
                keyClick(Qt.Key_Down);
                equal(launcher.selected, 1);
                check(window.activeFocusItem.objectName === "launcher-results", "Down enters the recent programs");
                lookShot("1-down-recent", null);
                keyClick(Qt.Key_Up);
                equal(launcher.selected, 0);
                lookShot("2-up-recent", null);
                keyClick(Qt.Key_Up);
                check(search.activeFocus, "Up reaches search");
                lookShot("3-up-search", null);
                keyClick(Qt.Key_Right);
                check(launcher.headerControls()[2].activeFocus, "Right reaches All");
                lookShot("4-right-all", launcher.headerControls()[2], launcher.tabFocusPad);
                keyClick(Qt.Key_Right);
                check(launcher.headerControls()[3].activeFocus, "Right reaches Apps");
                lookShot("5-right-apps", launcher.headerControls()[3], launcher.tabFocusPad);
                keyClick(Qt.Key_Escape);
            }
            function nearestAtX(controls, x) {
                return controls.slice().sort((a, b) => {
                    const ap = a.mapToItem(launcher, a.width / 2, a.height / 2);
                    const bp = b.mapToItem(launcher, b.width / 2, b.height / 2);
                    return Math.abs(ap.x - x) - Math.abs(bp.x - x);
                })[0];
            }
            function test_spatial_layers_and_smooth_travel() {
                window.requestActivate();
                launcher.setMode("Apps");
                launcher.setQuery("");
                launcher.category = "All";
                tryVerify(() => launcher.results.length >= 70 && launcher.categoryControls().length === 8);
                launcher.selectAt(5);
                launcher.focusResults();
                tryVerify(() => !launcher.animating, 3000);
                const start = launcher.geometryOf(5).rect;
                const target = nearestAtX(launcher.categoryControls(), start.x + start.width / 2);
                check(target.modelData !== "All", "The spatial target differs from the selected category");
                const before = launcher.glassStatus().select;
                keyClick(Qt.Key_Up);
                check(target.activeFocus, "Up reaches the nearest category, not the selected one");
                const after = launcher.glassStatus().select;
                check(Math.abs(after.y - before.y) < 1, "Up retargets the existing bubble without an instant jump");
                tryVerify(() => !launcher.animating, 3000);
                const targetRect = target.mapToItem(launcher, 0, 0, target.width, target.height);
                const rest = launcher.glassStatus().select;
                check(Math.abs(rest.x - targetRect.x + launcher.tabFocusPad) <= 1 && Math.abs(rest.y - targetRect.y + launcher.tabFocusPad) <= 1, "The shared glass settles on the category");
                equal(launcher.glassStatus().drops.length, 1);
                const header = nearestAtX(launcher.headerControls(), targetRect.x + targetRect.width / 2);
                keyClick(Qt.Key_Up);
                check(header.activeFocus, "Up from categories reaches the nearest header control");
                keyClick(Qt.Key_Down);
                const hp = header.mapToItem(launcher, header.width / 2, header.height / 2);
                check(nearestAtX(launcher.categoryControls(), hp.x).activeFocus, "Down from modes reaches the nearest category");
                keyClick(Qt.Key_Down);
                check(window.activeFocusItem.objectName === "launcher-results", "Down enters the grid");
                const row = launcher.selected;
                check(row >= 0 && row < 7, "Down enters the first visual row");
                keyClick(Qt.Key_Right);
                equal(launcher.selected, Math.min(6, row + 1));
                keyClick(Qt.Key_Down);
                equal(launcher.selected, Math.min(6, row + 1) + 7);
                launcher.takeFocus(true);
                keyClick(Qt.Key_Right);
                tryVerify(() => !launcher.animating, 3000);
                const left = launcher.glassStatus().select.x;
                keyClick(Qt.Key_Right);
                check(Math.abs(launcher.glassStatus().select.x - left) < 1, "Right does not instantly move the glass");
                wait(40);
                const moving = launcher.glassStatus().select.x;
                check(moving > left, "Right advances the same spring");
                tryVerify(() => !launcher.animating, 3000);
                check(launcher.glassStatus().select.x > moving, "Right travels smoothly before settling");
                equal(launcher.glassStatus().drops.length, 1);
                const originalWidth = launcher.width;
                launcher.width = 480;
                launcher.setMode("All");
                launcher.takeFocus(true);
                keyClick(Qt.Key_Right);
                tryVerify(() => !launcher.animating, 3000);
                const strip = findChild(launcher, "launcher-mode-strip");
                const tab = findChild(launcher, "launcher-tab-all");
                check(tab.activeFocus, "The first tab carries keyboard focus");
                // Scroll the viewport while retaining focus, as a touch gesture can do.
                strip.contentX = 12;
                tryVerify(() => !launcher.animating, 3000);
                const clipped = launcher.glassStatus().drops.find(drop => drop.kind === "select");
                const stripRect = strip.mapToItem(launcher, 0, 0, strip.width, strip.height);
                check(!clipped || (clipped.x >= stripRect.x && clipped.x + clipped.width <= stripRect.x + stripRect.width && clipped.alpha <= 1), "Focused glass clips to the mode viewport");
                strip.contentX = 30;
                tryVerify(() => !launcher.animating, 3000);
                const faded = launcher.glassStatus().drops.find(drop => drop.kind === "select");
                check(strip.fadeLeft && (!faded || faded.alpha < 1), "Focused glass fades when label ink enters the fade");
                const ink = findChild(launcher, "launcher-mode-ink");
                check(ink.parent.clip && ink.parent.layer.effect !== null && ink.parent.layer.enabled === strip.layer.enabled, "Focused mode ink uses the same clipped fade layer");
                launcher.width = originalWidth;
                launcher.takeFocus();
            }
            function test_mode_icons_keep_keyboard_and_accessibility() {
                SettingsBridge.values = [
                    {
                        key: "appearance.theme",
                        value: "light"
                    },
                    {
                        key: "launcher.mode_icons",
                        value: true
                    }
                ];
                launcher.setMode("Apps");
                launcher.takeFocus(true);
                tryVerify(() => launcher.modeIcons);
                keyClick(Qt.Key_Right);
                for (let i = 0; i < launcher.modes.length; ++i) {
                    const tab = findChild(launcher, "launcher-tab-" + launcher.modes[i].toLowerCase());
                    check(tab.activeFocus, "Icon tab is keyboard reachable");
                    equal(tab.Accessible.name, launcher.modeLabels[i]);
                    equal(tab.width, 38);
                    keyClick(Qt.Key_Return);
                    equal(launcher.modeIndex, i);
                    if (i + 1 < launcher.modes.length)
                        keyClick(Qt.Key_Right);
                }
                const tooltip = findChild(launcher, "launcher-mode-tooltip");
                const query = findChild(launcher, "launcher-search");
                verify(!!tooltip, "Icon modes have a hover label");
                for (let i = 0; i < launcher.modes.length; ++i) {
                    const tab = findChild(launcher, "launcher-tab-" + launcher.modes[i].toLowerCase());
                    mouseMove(tab, tab.width / 2, tab.height / 2);
                    tryVerify(() => tooltip.visible, 1000, "Mode name appears on hover");
                    equal(tooltip.label, launcher.modeLabels[i]);
                    mouseClick(tab, tab.width / 2, tab.height / 2);
                    equal(launcher.modeIndex, i, "Hover label preserves tab clicks");
                    tryVerify(() => !tooltip.visible, 1000, "Click dismisses the label");
                }
                const allTab = findChild(launcher, "launcher-tab-all");
                mouseMove(query, 2, 2);
                mouseMove(allTab, allTab.width / 2, allTab.height / 2);
                tryVerify(() => tooltip.visible, 1000);
                mouseMove(query, 2, 2);
                tryVerify(() => !tooltip.visible, 1000, "Leaving hides the label");
                SettingsBridge.values = [
                    {
                        key: "appearance.theme",
                        value: "light"
                    }
                ];
                tryVerify(() => !launcher.modeIcons);
                mouseMove(allTab, allTab.width / 2, allTab.height / 2);
                wait(500);
                verify(!tooltip.visible, "Text modes do not show redundant tooltips");
            }
            function test_emoji_grid_and_search() {
                launcher.setMode("Emoji");
                launcher.setQuery("");
                window.requestActivate();
                tryVerify(() => launcher.emoji.state === "ready", 5000);
                const grid = findChild(launcher, "launcher-grid");
                tryVerify(() => grid.count > 14 && launcher.gridMode && launcher.categoryControls().length === 0);
                keyClick(Qt.Key_Down);
                check(window.activeFocusItem.objectName === "launcher-results", "Down enters emoji tiles");
                const start = launcher.selected;
                keyClick(Qt.Key_Right);
                equal(launcher.selected, Math.min(6, start + 1));
                keyClick(Qt.Key_Down);
                equal(launcher.selected, Math.min(6, start + 1) + 7);
                keyClick(Qt.Key_Left);
                equal(launcher.selected, Math.max(7, Math.min(6, start + 1) + 6));
                launcher.setQuery("heart");
                launcher.takeFocus(true);
                tryVerify(() => launcher.results.length > 0 && grid.count === launcher.results.length);
                check(launcher.results.every(row => row.kind === "emoji"), "Search results remain emoji tiles");
                keyClick(Qt.Key_Down);
                check(launcher.geometryOf(launcher.selected).shape === "tile", "Search supports grid keyboard navigation");
                launcher.setQuery("");
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
                check(query.activeFocus, "Up from recent app reaches search");
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
