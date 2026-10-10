// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

SettingsIpcHarness {
    id: harness
    Window {
        id: quickWindow
        width: 480
        height: 800
        visible: false
        property string requested: ""
        SystemBody {
            id: quick
            anchors.fill: parent
            service: harness.service
            niri: harness.niri
            opened: quickWindow.visible
            onSettingsRequested: page => quickWindow.requested = page
        }
    }
    TestCase {
        id: test
        name: "SettingsLauncher"
        when: true
        onCompletedChanged: if (completed)
            console.log("SETTINGS_LAUNCHER_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function equal(actual, expected) {
            if (actual !== expected)
                console.error("Settings compare " + actual + " != " + expected + " " + new Error().stack);
            compare(actual, expected);
        }
        function check(value) {
            if (!value)
                console.error("Settings verification " + new Error().stack);
            verify(value);
        }
        function nodes(item) {
            let found = [item];
            for (const child of item.children || [])
                found = found.concat(nodes(child));
            return found;
        }
        function named(name) {
            return nodes(harness.controller.view).find(item => item.objectName === name);
        }
        function show(page) {
            const answer = JSON.parse(harness.controller.open(page || ""));
            equal(answer.status, "opened");
            tryVerify(() => harness.controller.opened && harness.scene.launcherOpen);
            harness.window.requestActivate();
            wait(350);
            return harness.controller.view;
        }
        function init() {
            harness.catalog.busy = false;
            harness.catalog.calls = [];
            harness.catalog.history = [];
            harness.catalog.lastStatus = "";
            harness.controller.enabled = true;
            harness.window.width = 1536;
            harness.window.height = 960;
        }
        function cleanup() {
            harness.controller.dismiss();
            quickWindow.visible = false;
            tryCompare(harness.scene, "launcherPresent", false);
        }
        function test_integrated_pages_and_search() {
            const pages = [["panel", "SettingsCorePage", "Panel auto-hide", "panel-panel-auto-hide", "bar.autohide"], ["windows", "SettingsCorePage", "Window gaps", "windows-window-gaps", "appearance.gaps"], ["wifi", "SettingsWifiPage", "Wi-Fi", "wifi-radio", "settings-wifi-power"], ["bluetooth", "SettingsBluetoothPage", "Bluetooth", "bluetooth-radio", "settings-bluetooth-power"], ["network", "SettingsNetworkPage", "Wired", "network-wired", "settings-network-wired"], ["sound", "SoundSettingsPage", "Volume", "sound-volume", "settings-sound-volume"], ["displays", "DisplaysSettingsPage", "Resolution", "displays-resolution", "settings-display-resolution"], ["battery", "SettingsPowerPage", "Power mode", "battery-power-mode", "settings-power-profile"], ["keyboard", "KeyboardSettingsPage", "Keyboard shortcuts", "keyboard-keyboard-shortcuts", "settings-keyboard-shortcuts"], ["mouse", "MouseSettingsPage", "Mouse pointer speed", "mouse-pointer-speed", "mouse.speed"], ["notifications", "NotificationsSettingsPage", "Do Not Disturb", "notifications-do-not-disturb", "notifications.dnd"], ["wallpaper", "SettingsWallpaperPage", "Your own picture", "wallpaper-your-own-picture", "appearance.wallpaper"], ["apps", "SettingsAppsPage", "Startup apps", "apps-startup-apps", "settings-startup-apps"], ["region", "SettingsRegionPage", "Language", "region-language", "settings-language"], ["about", "SettingsAboutPage", "Emaki version", "about-emaki-version", "settings-about-version"], ["updates", "SettingsMaintenancePage", "Update channel", "updates-update-channel", "settings-update-channel"], ["lock", "SettingsMaintenancePage", "Password", "lock-password", "settings-password"]];
            equal(harness.controller.opened, false);
            equal(harness.scene.input.settingsActive, false);
            compare(Array.from(harness.controller.availablePages).sort(), pages.map(entry => entry[0]).sort());
            for (const entry of pages) {
                const view = show(entry[0]);
                equal(view.page, entry[0]);
                const loader = nodes(view).find(item => item.sourceComponent !== undefined && item.item && String(item.item).includes(entry[1] + "_"));
                check(loader !== undefined);
                if (["panel", "windows"].includes(entry[0]))
                    equal(loader.item.section, entry[0]);
                if (["lock", "updates"].includes(entry[0]))
                    equal(loader.item.page, entry[0]);
                view.search.text = entry[2];
                const result = view.results.find(row => row.id === entry[3]);
                check(result !== undefined);
                equal(result.page, entry[0]);
                equal(result.enabled, true);
                equal(result.target, entry[4]);
                view.search.text = "";
                check(view.navigate(result.page, result.target));
                wait(30);
                check(named(result.target) !== undefined || named("setting-" + result.target) !== undefined);
            }
        }
        function test_keyboard_traversal() {
            const view = show("panel");
            const toggle = named("setting-bar.autohide").controlItem;
            view.search.forceActiveFocus();
            for (let i = 0; i < 80 && !toggle.activeFocus; ++i)
                keyClick(Qt.Key_Tab);
            check(toggle.activeFocus);
            for (let i = 0; i < 80 && !view.search.activeFocus; ++i)
                keyClick(Qt.Key_Tab, Qt.ShiftModifier);
            check(view.search.activeFocus);
            equal(harness.controller.opened, true);
        }
        function test_keyboard_dynamic_reset_keys() {
            const original = harness.catalog.values;
            try {
                harness.catalog.values = original.concat([
                    {
                        key: "keyboard.repeat_delay",
                        value: 700,
                        default: 500
                    },
                    {
                        key: "shortcuts.editable",
                        value: "Mod+E",
                        default: "Mod+D",
                        editable: true
                    },
                    {
                        key: "shortcuts.read_only",
                        value: "Mod+R",
                        default: "Mod+Q",
                        editable: false
                    },
                    {
                        key: "shortcuts.no_default",
                        value: "Mod+N",
                        default: null,
                        editable: true
                    },
                    {
                        key: "unrelated.setting",
                        value: true,
                        default: false
                    }
                ]);
                const view = show("keyboard");
                check(view.pageKeys.includes("keyboard.repeat_delay"));
                check(view.pageKeys.includes("shortcuts.editable"));
                check(!view.pageKeys.includes("shortcuts.read_only"));
                check(!view.pageKeys.includes("shortcuts.no_default"));
                check(!view.pageKeys.includes("unrelated.setting"));
                harness.catalog.values = harness.catalog.values.concat([
                    {
                        key: "shortcuts.added",
                        value: "Mod+A",
                        default: "Mod+B",
                        editable: true
                    }
                ]);
                tryVerify(() => view.pageKeys.includes("shortcuts.added"));
                check(view.pageModified);
                mouseClick(named("settings-reset-page"));
                compare(harness.catalog.calls.map(call => call.key).sort(), ["keyboard.repeat_delay", "shortcuts.added", "shortcuts.editable"]);
                equal(harness.catalog.row("shortcuts.read_only").value, "Mod+R");
                equal(harness.catalog.row("shortcuts.no_default").value, "Mod+N");
                equal(harness.catalog.row("unrelated.setting").value, true);
            } finally {
                harness.catalog.values = original;
            }
        }
        function test_mouse_absent_trackpad_reset() {
            const original = harness.catalog.values;
            try {
                harness.catalog.values = original.concat([
                    {
                        key: "mouse.speed",
                        value: 0,
                        default: 0,
                        editable: true
                    },
                    {
                        key: "touchpad.speed",
                        value: null,
                        default: 0,
                        editable: false
                    },
                    {
                        key: "touchpad.tap",
                        value: null,
                        default: true,
                        editable: false
                    }
                ]);
                const view = show("mouse");
                check(!view.pageModified);
                check(!named("settings-reset-page").visible);
                harness.catalog.values = harness.catalog.values.map(row => row.key === "mouse.speed" ? Object.assign({}, row, {
                        value: 0.5
                    }) : row);
                tryVerify(() => view.pageModified);
                mouseClick(named("settings-reset-page"));
                compare(harness.catalog.calls.map(call => call.key), ["mouse.speed"]);
                check(!view.pageModified);
            } finally {
                harness.catalog.values = original;
            }
        }
        function test_slider_close_flush() {
            for (const mode of ["escape", "close", "busy-close", "tab"]) {
                harness.catalog.busy = false;
                show("windows");
                const row = named("setting-appearance.gaps");
                const before = Number(row.value);
                harness.catalog.calls = [];
                row.controlItem.forceActiveFocus();
                keyClick(Qt.Key_Right);
                keyClick(Qt.Key_Right);
                equal(harness.catalog.calls.length, 0);
                if (mode === "busy-close")
                    harness.catalog.busy = true;
                if (mode === "escape")
                    keyClick(Qt.Key_Escape);
                else if (mode === "tab")
                    mouseClick(nodes(harness.scene.input).find(item => item.modelData === "Apps" && item.activate !== undefined));
                else
                    harness.controller.dismiss();
                equal(harness.controller.opened, false);
                equal(harness.catalog.calls.length, 1);
                equal(harness.catalog.calls[0].operation, "set");
                equal(harness.catalog.calls[0].key, "appearance.gaps");
                equal(Number(harness.catalog.calls[0].value), before + 2);
                equal(Number(harness.catalog.row("appearance.gaps").value), before + 2);
            }
        }
        function test_lifecycle() {
            equal(harness.controller.opened, false);
            const gear = nodes(harness.scene.input).find(item => item.objectName === "launcher-settings");
            const tab = nodes(harness.scene.input).find(item => item.objectName === "launcher-tab-apps");
            harness.scene.openLauncher();
            tryVerify(() => harness.scene.expansion > .55 && harness.scene.expansion < 1);
            const gearX = gear.x;
            const tabsX = tab.parent.x;
            tryCompare(harness.scene, "expansion", 1);
            equal(gear.x, gearX);
            equal(tab.parent.x, tabsX);
            harness.scene.closeLauncher();
            tryVerify(() => harness.scene.expansion > 0 && harness.scene.expansion < 1);
            equal(gear.x, gearX);
            equal(tab.parent.x, tabsX);
            tryCompare(harness.scene, "launcherPresent", false);
            let view = show("");
            equal(view.page, "panel");
            view = show("sound");
            view.search.text = "volume";
            mouseClick(gear);
            equal(view.page, "sound");
            equal(view.search.text, "volume");
            const first = view.Window.window;
            show("windows");
            equal(harness.controller.view.Window.window, first);
            equal(view.page, "windows");
            equal(JSON.parse(harness.controller.open("appearance")).status, "unavailable");
            equal(view.page, "windows");
            harness.controller.enabled = false;
            equal(JSON.parse(harness.controller.open("panel")).status, "unavailable");
            harness.controller.enabled = true;
            tryVerify(() => harness.scene.expansion === 1 && Math.abs(harness.scene.input.shownWidth - harness.scene.panelWidth) < 1);
            const expandedWidth = harness.scene.input.shownWidth;
            harness.scene.closeLauncher();
            check(harness.scene.input.shownWidth >= expandedWidth - 1);
            equal(harness.controller.opened, false);
            tryVerify(() => harness.scene.expansion > 0 && harness.scene.expansion < 1);
            equal(harness.controller.view, view);
            check(harness.scene.input.settingsActive);
            check(!nodes(harness.scene.input).find(item => item.objectName === "launcher-content").visible);
            check(harness.scene.input.contentAlpha < 1);
            shot(harness.scene, "settings-close-mid.png");
            tryCompare(harness.scene, "launcherPresent", false);
            equal(harness.controller.view, null);
            view = show("");
            view.search.forceActiveFocus();
            view.search.text = "gaps";
            keyClick(Qt.Key_Escape);
            equal(view.search.text, "");
            equal(harness.controller.opened, true);
            keyClick(Qt.Key_Escape);
            tryCompare(harness.controller, "opened", false);
            check(harness.scene.launcherOpen);
            wait(350);
            equal(harness.scene.panelWidth, Metrics.launcherWidth);
            show("");
            mouseClick(nodes(harness.scene.input).find(item => item.objectName === "launcher-close"));
            tryCompare(harness.controller, "opened", false);
            check(!harness.scene.launcherOpen);
            show("panel");
        }
        function inside(rect, plate) {
            return rect.x >= plate.x - 0.01 && rect.y >= plate.y - 0.01 && rect.x + rect.width <= plate.x + plate.width + 0.01 && rect.y + rect.height <= plate.y + plate.height + 0.01;
        }
        function checkGlassGeometry() {
            const body = harness.scene.input;
            const status = body.glassStatus();
            check(status.plate.width > 0 && status.plate.height > 0);
            for (const drop of status.drops) {
                if (drop.kind === "logo")
                    continue;
                check(inside(drop, status.plate));
                if (drop.kind === "mode" || drop.kind === "category")
                    check(drop.alpha <= body.contentAlpha + 0.001);
            }
            check(!status.focus.eligible || inside(status.focus, status.plate));
            check(!status.focus.shown || status.focus.eligible);
            return status;
        }
        function test_glass_motion_geometry() {
            const body = harness.scene.input;
            harness.scene.openLauncher(true);
            tryCompare(harness.scene, "expansion", 1);
            wait(400);
            let status = checkGlassGeometry();
            check(status.drops.some(drop => drop.kind === "mode" && drop.alpha > 0.9));
            check(status.drops.some(drop => drop.kind === "category" && drop.alpha > 0.9));
            harness.scene.closeLauncher();
            tryVerify(() => harness.scene.expansion < 0.98 && harness.scene.expansion > 0.6);
            check(body.contentAlpha < 1);
            checkGlassGeometry();
            tryCompare(harness.scene, "launcherPresent", false);

            harness.controller.open("panel");
            harness.window.requestActivate();
            tryVerify(() => harness.scene.expansion > 0.6 && harness.scene.expansion < 0.85);
            body.takeFocus(true);
            nodes(body).find(item => item.objectName === "launcher-settings").forceActiveFocus();
            status = checkGlassGeometry();
            check(status.focus.width > 0 && status.focus.height > 0);
            check(!inside(status.focus, status.plate));
            check(!status.focus.eligible);
            tryCompare(harness.scene, "expansion", 1);
            wait(400);
            status = checkGlassGeometry();
            check(status.focus.eligible);
            harness.scene.closeLauncher();
            tryCompare(harness.scene, "launcherPresent", false);

            harness.scene.openLauncher(true);
            tryCompare(harness.scene, "expansion", 1);
            wait(400);
            harness.controller.open("panel");
            tryVerify(() => body.shownWidth > Metrics.launcherWidth + 5 && body.shownWidth < body.width - 5);
            const view = harness.controller.view;
            equal(view.width, body.width);
            equal(view.height, body.height - Metrics.launcherHeader);
            checkGlassGeometry();
            tryVerify(() => Math.abs(body.shownWidth - body.width) < 1);
            wait(400);
            status = checkGlassGeometry();
            check(status.drops.some(drop => drop.kind === "mode" && drop.alpha > 0.9));
            const targetWidth = view.width;
            const targetHeight = view.height;
            harness.scene.closeLauncher();
            tryVerify(() => harness.scene.expansion < 0.98 && harness.scene.expansion > 0.6);
            check(body.contentAlpha < 1);
            equal(view.width, targetWidth);
            equal(view.height, targetHeight);
            checkGlassGeometry();
            tryCompare(harness.scene, "launcherPresent", false);

            show("panel");
            wait(400);
            harness.controller.view.search.forceActiveFocus();
            keyClick(Qt.Key_Escape);
            tryVerify(() => body.shownWidth > Metrics.launcherWidth + 5 && body.shownWidth < targetWidth - 5);
            status = checkGlassGeometry();
            check(status.drops.some(drop => drop.kind === "mode" && drop.alpha > 0));
            const header = nodes(body).filter(item => item.objectName === "launcher-settings" || item.objectName.startsWith("launcher-tab-"));
            check(header.length > 1);
            for (const item of header) {
                check(item.visible);
                const right = item.mapToItem(body, item.width, 0).x;
                check(status.content_width >= right);
                check(status.texture_width >= right);
            }
            tryVerify(() => Math.abs(body.shownWidth - Metrics.launcherWidth) < 1);
        }
        function test_reopen_during_close() {
            const body = harness.scene.input;
            body.setQuery("old query");
            body.modeIndex = 3;
            body.category = "Development";
            show("sound");
            harness.controller.view.search.text = "volume";
            harness.scene.closeLauncher();
            tryVerify(() => harness.scene.expansion > 0 && harness.scene.expansion < 1);
            check(body.settingsActive);
            harness.scene.openLauncher(true);
            tryCompare(harness.scene, "launcherOpen", true);
            check(!body.settingsActive);
            equal(harness.controller.view, null);
            equal(body.queryLength, 0);
            equal(body.modeIndex, 1);
            equal(body.category, "All");
            tryCompare(harness.scene, "expansion", 1);

            show("sound");
            harness.controller.view.search.text = "volume";
            harness.scene.closeLauncher();
            tryVerify(() => harness.scene.expansion > 0 && harness.scene.expansion < 1);
            equal(JSON.parse(harness.controller.open("windows")).status, "opened");
            tryCompare(harness.controller, "opened", true);
            equal(harness.controller.view.page, "windows");
            equal(harness.controller.view.search.text, "");
            wait(450);
            check(harness.scene.launcherOpen && body.settingsActive);
            equal(harness.controller.view.page, "windows");
        }
        function test_sections_keyboard_and_disabled() {
            const view = show("panel");
            equal(view.sidebar.count, 23);
            const windows = named("section-windows");
            windows.forceActiveFocus();
            keyClick(Qt.Key_Return);
            equal(view.page, "windows");
            view.search.text = "gaps";
            view.search.forceActiveFocus();
            keyClick(Qt.Key_Enter);
            equal(view.search.text, "");
            equal(view.page, "windows");
            view.navigate("panel", "");
            const appearance = named("section-appearance");
            check(appearance !== undefined);
            equal(appearance.enabled, false);
            equal(appearance.hoverEnabled, false);
            equal(appearance.focusPolicy, Qt.NoFocus);
            mouseClick(appearance);
            equal(view.page, "panel");
            appearance.forceActiveFocus();
            keyClick(Qt.Key_Space);
            equal(view.page, "panel");
            check(!view.navigate("appearance", ""));
            view.search.forceActiveFocus();
            keyClick(Qt.Key_Down);
            check(named("section-wifi").activeFocus);
            keyClick(Qt.Key_Down);
            check(named("section-bluetooth").activeFocus);
            keyClick(Qt.Key_Down);
            const focused = view.Window.window.activeFocusItem;
            check(focused.enabled);
            equal(focused.objectName, "section-network");
            for (let i = 0; i < view.sidebar.count; ++i) {
                const entry = view.sidebar.itemAt(i);
                equal(entry.button.enabled, harness.controller.availablePages.includes(entry.modelData.id));
            }
            const nav = named("settings-sidebar-scroll");
            function selectedVisible() {
                const button = named("section-" + view.page);
                const top = button.mapToItem(nav, 0, 0).y;
                return top >= -1 && top + button.height <= nav.height + 1;
            }
            view.search.forceActiveFocus();
            view.navigate("about", "");
            tryVerify(selectedVisible);
            check(nav.contentY > 0);
            check(view.search.activeFocus);
            view.search.text = "password";
            const lockResult = view.results.find(result => result.page === "lock");
            check(lockResult !== undefined);
            view.navigate(lockResult.page, lockResult.target);
            equal(view.page, "lock");
            tryVerify(selectedVisible);
            harness.window.height = 640;
            tryVerify(selectedVisible);
            view.page = "wifi";
            tryVerify(selectedVisible);
        }
        function test_search_navigation_and_clear() {
            const view = show("panel");
            view.search.text = "restore protection";
            wait(30);
            equal(view.results.length, 0);
            view.search.text = "animations";
            wait(30);
            equal(view.results.length, 0);
            view.search.text = "hide dock";
            wait(30);
            check(view.results.length > 0);
            const result = nodes(view).find(item => item.modelData && item.modelData.key === "dock.auto_hide" && item.clicked !== undefined);
            check(result !== undefined);
            mouseClick(result);
            equal(view.page, "panel");
            equal(view.search.text, "");
            wait(30);
            check(named("setting-dock.auto_hide").controlItem.activeFocus);
            view.search.text = "not a setting 9281";
            equal(view.results.length, 0);
            const clear = nodes(view.search).find(item => item.text === "×" && item.clicked !== undefined);
            mouseClick(clear);
            equal(view.search.text, "");
            check(view.search.activeFocus);
        }
        function test_toggle_reset_and_busy() {
            const view = show("panel");
            const row = named("setting-bar.autohide");
            const resetPage = named("settings-reset-page");
            check(!row.resetItem.visible);
            check(!resetPage.visible);
            mouseClick(row.controlItem);
            equal(harness.catalog.calls.length, 1);
            equal(harness.catalog.calls[0].operation, "set");
            equal(harness.catalog.calls[0].key, "bar.autohide");
            equal(harness.catalog.calls[0].value, "true");
            tryCompare(row, "value", true);
            check(row.resetItem.visible);
            check(resetPage.visible);
            mouseClick(row.resetItem);
            equal(harness.catalog.calls[1].operation, "reset");
            tryCompare(row, "value", false);
            check(!row.resetItem.visible);
            check(!resetPage.visible);
            harness.catalog.busy = true;
            mouseClick(row.controlItem);
            equal(harness.catalog.calls.length, 2);
            equal(row.controlItem.enabled, false);
            harness.catalog.busy = false;
            mouseClick(row.controlItem);
            tryVerify(() => resetPage.visible);
            mouseClick(resetPage);
            tryCompare(row, "value", false);
            check(!resetPage.visible);
            equal(harness.catalog.calls[3].operation, "reset");
        }
        function test_history_guards() {
            const view = show("panel");
            harness.catalog.history = [
                {
                    id: "change",
                    kind: "set",
                    changes: [
                        {
                            key: "bar.autohide"
                        }
                    ]
                },
                {
                    id: "import",
                    kind: "import",
                    changes: [
                        {
                            key: "dock.on"
                        }
                    ]
                },
                {
                    id: "prepare",
                    kind: "prepare",
                    changes: []
                }
            ];
            mouseClick(named("settings-history"));
            check(view.historyShown);
            const buttons = nodes(view).filter(item => item.text === "Undo" && item.clicked !== undefined);
            equal(buttons.length, 3);
            const undo = buttons.find(item => item.entry.id === "change");
            check(undo.enabled);
            check(!buttons.find(item => item.entry.id === "import").enabled);
            check(!buttons.find(item => item.entry.id === "prepare").enabled);
            harness.catalog.busy = true;
            check(!undo.enabled);
            harness.catalog.busy = false;
            // Scroll the oldest record into view before its real mouse event.
            wait(50);
            view.scroller.contentY = Math.max(0, view.scroller.contentHeight - view.scroller.height);
            wait(30);
            mouseClick(undo);
            equal(harness.catalog.calls.length, 1);
            equal(harness.catalog.calls[0].operation, "undo");
            equal(harness.catalog.calls[0].id, "change");
            harness.catalog.history = harness.catalog.history.concat([
                {
                    id: "undo",
                    kind: "undo",
                    undo_of: "change",
                    changes: []
                }
            ]);
            tryVerify(() => nodes(view).some(item => item.text === "Undone" && item.enabled === false));
            equal(view.historyValue(true), "On");
            equal(view.historyValue(false), "Off");
        }
        function shot(view, filename) {
            let saved = false;
            check(view.grabToImage(result => {
                saved = result.saveToFile(Quickshell.env("SETTINGS_SHOT_DIR") + "/" + filename);
            }));
            tryVerify(() => saved);
        }
        function test_quick_links() {
            quickWindow.visible = true;
            quickWindow.requestActivate();
            const mapping = {
                wifi: "wifi",
                bt: "bluetooth",
                sound: "sound",
                light: "displays",
                power: "battery",
                kb: "keyboard"
            };
            for (const page of Object.keys(mapping)) {
                quick.page = page;
                quickWindow.requested = "";
                wait(40);
                const link = nodes(quick).find(item => item.objectName === "quick-settings-link");
                check(link !== undefined && link.visible);
                const scroller = nodes(quick).find(item => item.contentHeight !== undefined && item.contentY !== undefined);
                scroller.contentY = Math.max(0, scroller.contentHeight - scroller.height);
                wait(30);
                mouseClick(link);
                equal(quickWindow.requested, mapping[page]);
            }
            quick.page = "tray";
            check(!nodes(quick).find(item => item.objectName === "quick-settings-link").visible);
        }
        function test_screenshots() {
            harness.scene.openLauncher();
            wait(600);
            shot(harness.scene, "launcher-output.png");
            harness.controller.open("panel");
            wait(100);
            check(harness.scene.input.shownWidth > Metrics.launcherWidth);
            check(harness.scene.input.shownWidth < harness.scene.panelWidth);
            shot(harness.scene, "settings-grow-mid.png");
            const view = show("panel");
            equal(view.Window.window, harness.window);
            check(harness.scene.panelWidth > Metrics.launcherWidth);
            check(harness.scene.panelWidth <= 1240);
            check(harness.scene.panelHeight <= 810);
            shot(harness.scene.panel, "settings-launcher.png");
            shot(harness.scene, "settings-output.png");
            view.search.text = "dock";
            wait(50);
            shot(view, "settings-search.png");
            view.search.text = "";
            view.Window.window.width = 760;
            view.Window.window.height = 600;
            wait(350);
            check(harness.scene.panelWidth < 760);
            check(harness.scene.panelHeight < 600);
            check(harness.scene.panel.x + harness.scene.panel.width <= harness.scene.bar.systemIsland.x - Metrics.side);
            shot(harness.scene.panel, "settings-narrow.png");
            for (const size of [[1280, 800], [1024, 640]]) {
                harness.window.width = size[0];
                harness.window.height = size[1];
                wait(600);
                check(harness.scene.panel.x + harness.scene.panel.width <= harness.scene.bar.systemIsland.x - Metrics.side);
                check(harness.scene.panel.y + harness.scene.panel.height < size[1]);
                shot(harness.scene, "settings-" + size[0] + "x" + size[1] + ".png");
            }
            harness.window.width = 1536;
            harness.window.height = 960;
            wait(600);
            harness.scene.input.leaveSettings();
            wait(100);
            check(harness.scene.input.shownWidth > Metrics.launcherWidth);
            const activeTab = nodes(harness.scene.input).find(item => item.objectName === "launcher-tab-apps");
            equal(harness.scene.input.modeRect.x, activeTab.parent.x + activeTab.x);
            shot(harness.scene, "settings-shrink-mid.png");
            wait(600);
            equal(harness.scene.panelWidth, Metrics.launcherWidth);
            const gear = nodes(harness.scene.input).find(item => item.objectName === "launcher-settings");
            check(gear.visible);
            shot(harness.scene.panel, "launcher-normal.png");
            mouseClick(gear);
            tryCompare(harness.controller, "opened", true);
            const tab = nodes(harness.scene.input).find(item => item.modelData === "Apps" && item.activate !== undefined);
            check(tab !== undefined);
            mouseClick(tab);
            tryCompare(harness.controller, "opened", false);
            check(harness.scene.launcherOpen);
        }
    }
}
