// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

SettingsIpcHarness {
    id: harness
    TestCase {
        name: "SettingsDesktop"
        when: true
        onCompletedChanged: if (completed)
            console.log("SETTINGS_DESKTOP_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function equal(actual, expected) {
            if (actual !== expected)
                console.error("Desktop compare " + actual + " != " + expected + " " + new Error().stack);
            compare(actual, expected);
        }
        function check(value) {
            if (!value)
                console.error("Desktop verification " + new Error().stack);
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
            equal(JSON.parse(harness.controller.open(page)).status, "opened");
            tryVerify(() => harness.controller.opened && harness.scene.launcherOpen);
            const view = harness.controller.view;
            harness.window.width = 1536;
            harness.window.height = 960;
            harness.window.requestActivate();
            wait(350);
            return view;
        }
        function click(item) {
            check(item !== undefined);
            const view = harness.controller.view;
            const position = item.mapToItem(view.scroller.contentItem, 0, 0);
            view.scroller.contentY = Math.max(0, Math.min(position.y - 80, view.scroller.contentHeight - view.scroller.height));
            wait(20);
            mouseClick(item);
        }
        function segment(row, value) {
            return nodes(row.controlItem).find(item => item.modelData && item.modelData.value === value && item.clicked !== undefined);
        }
        function init() {
            harness.catalog.wallpapers = [
                {
                    name: "Morning",
                    path: Quickshell.env("SETTINGS_CHOSEN_IMAGE")
                },
                {
                    name: "Evening",
                    path: Quickshell.env("SETTINGS_SECOND_IMAGE")
                }
            ];
            harness.catalog.inheritedWallpaper = Quickshell.env("SETTINGS_SECOND_IMAGE");
            harness.catalog.busy = false;
            harness.catalog.rejectNext = false;
            harness.catalog.values = harness.catalog.values.map(row => ({
                        key: row.key,
                        value: row.default,
                        default: row.default
                    }));
            harness.catalog.calls = [];
            harness.catalog.lastStatus = "";
        }
        function cleanup() {
            harness.controller.dismiss();
            wait(30);
        }
        function test_panel_options() {
            show("panel");
            for (const key of ["bar.autohide", "bar.overview_workspaces", "dock.on", "dock.auto_hide"]) {
                const row = named("setting-" + key);
                const before = row.value;
                click(row.controlItem);
                equal(harness.catalog.calls[harness.catalog.calls.length - 1].key, key);
                equal(row.value, !before);
                click(row.resetItem);
                equal(harness.catalog.calls[harness.catalog.calls.length - 1].operation, "reset");
                equal(harness.catalog.calls[harness.catalog.calls.length - 1].key, key);
                equal(row.value, before);
            }
        }
        function test_window_options_reset_busy_rejection() {
            show("windows");
            const width = named("setting-windows.default_column_width");
            check(width !== undefined);
            for (const value of ["full", "half", "third", "twothirds"]) {
                click(segment(width, value));
                const call = harness.catalog.calls[harness.catalog.calls.length - 1];
                equal(call.operation, "set");
                equal(call.key, "windows.default_column_width");
                equal(call.value, value);
                equal(width.value, value);
            }
            click(width.resetItem);
            equal(width.value, width.defaultValue);
            equal(harness.catalog.calls[harness.catalog.calls.length - 1].key, "windows.default_column_width");
            const focus = named("setting-windows.focus_follows_mouse");
            click(segment(focus, true));
            equal(harness.catalog.calls[harness.catalog.calls.length - 1].key, "windows.focus_follows_mouse");
            equal(harness.catalog.calls[harness.catalog.calls.length - 1].value, "true");
            equal(focus.value, true);
            click(focus.resetItem);
            equal(focus.value, false);
            const count = harness.catalog.calls.length;
            harness.catalog.busy = true;
            click(segment(focus, true));
            equal(harness.catalog.calls.length, count);
            harness.catalog.busy = false;
            harness.catalog.rejectNext = true;
            click(segment(focus, true));
            equal(focus.value, false);
            equal(harness.catalog.lastStatus, "rejected");
            const gaps = named("setting-appearance.gaps");
            click(gaps.controlItem);
            equal(harness.catalog.calls[harness.catalog.calls.length - 1].key, "appearance.gaps");
            click(gaps.resetItem);
            equal(gaps.value, gaps.defaultValue);
        }
        function wallpaperPage() {
            return nodes(harness.controller.view).find(item => typeof item.choosePicture === "function");
        }
        function test_wallpaper_choice_reset_busy() {
            show("wallpaper");
            const row = named("setting-appearance.wallpaper");
            const choice = named("wallpaper-choice-0");
            equal(wallpaperPage().currentPath, harness.catalog.inheritedWallpaper);
            equal(wallpaperPage().currentName, "Evening");
            equal(named("wallpaper-choice-1").Accessible.checked, true);
            click(choice);
            equal(harness.catalog.calls.length, 1);
            equal(harness.catalog.calls[0].key, "appearance.wallpaper");
            equal(harness.catalog.calls[0].value, harness.catalog.wallpapers[0].path);
            equal(row.value, harness.catalog.wallpapers[0].path);
            click(row.resetItem);
            equal(harness.catalog.calls[1].operation, "reset");
            equal(harness.catalog.calls[1].key, "appearance.wallpaper");
            equal(row.value, row.defaultValue);
            equal(wallpaperPage().currentPath, harness.catalog.inheritedWallpaper);
            equal(named("wallpaper-choice-1").Accessible.checked, true);
            harness.catalog.busy = true;
            click(choice);
            click(named("wallpaper-choose"));
            equal(harness.catalog.calls.length, 2);
            equal(wallpaperPage().chooser.busy, false);
        }
        function test_portal_results() {
            show("wallpaper");
            const page = wallpaperPage();
            check(page !== undefined);
            page.chooser.helper = Quickshell.env("SETTINGS_CHOOSER_READY");
            click(named("wallpaper-choose"));
            tryCompare(page.chooser, "busy", true);
            check(!named("wallpaper-choose").enabled);
            tryCompare(page.chooser, "busy", false);
            equal(harness.catalog.calls.length, 1);
            equal(harness.catalog.calls[0].key, "appearance.wallpaper");
            equal(harness.catalog.calls[0].value, Quickshell.env("SETTINGS_CHOSEN_IMAGE"));
            page.chooser.helper = Quickshell.env("SETTINGS_CHOOSER_CANCELLED");
            click(named("wallpaper-choose"));
            tryCompare(page.chooser, "busy", true);
            tryCompare(page.chooser, "busy", false);
            equal(harness.catalog.calls.length, 1);
            equal(named("setting-appearance.wallpaper").value, Quickshell.env("SETTINGS_CHOSEN_IMAGE"));
            page.chooser.helper = Quickshell.env("SETTINGS_CHOOSER_FAILED");
            click(named("wallpaper-choose"));
            tryCompare(page.chooser, "busy", true);
            tryCompare(page.chooser, "busy", false);
            equal(harness.catalog.calls.length, 1);
            const error = named("wallpaper-error");
            check(error.visible);
            check(error.text.length > 0);
            equal(error.textFormat, Text.PlainText);
        }
        function test_search_routes() {
            const view = show("panel");
            for (const key of ["appearance.wallpaper", "windows.default_column_width", "windows.focus_follows_mouse"]) {
                const queries = {
                    "appearance.wallpaper": "wallpaper",
                    "windows.default_column_width": "new windows",
                    "windows.focus_follows_mouse": "focus follows mouse"
                };
                view.search.text = queries[key];
                wait(40);
                const result = nodes(view).find(item => item.modelData && item.modelData.key === key && item.clicked !== undefined);
                check(result !== undefined);
                mouseClick(result);
                equal(view.page, key === "appearance.wallpaper" ? "wallpaper" : "windows");
                equal(view.search.text, "");
                check(named("setting-" + key) !== undefined);
                wait(40);
            }
        }
        function shot(view, filename) {
            let saved = false;
            check(view.grabToImage(result => {
                saved = result.saveToFile(Quickshell.env("SETTINGS_SHOT_DIR") + "/" + filename);
            }));
            tryVerify(() => saved);
        }
        function test_screenshots() {
            for (const page of ["wallpaper", "panel", "windows"]) {
                const view = show(page);
                shot(view, "desktop-" + page + ".png");
                view.Window.window.width = 760;
                view.Window.window.height = 600;
                wait(350);
                shot(view, "desktop-" + page + "-narrow.png");
            }
        }
    }
}
