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
            harness.catalog.inheritedWallpaper = Quickshell.env("SETTINGS_CHOSEN_IMAGE");
            harness.catalog.busy = false;
            harness.catalog.rejectNext = false;
            harness.catalog.values = harness.catalog.values.map(row => ({
                        key: row.key,
                        value: row.key === "appearance.wallpaper" ? Quickshell.env("SETTINGS_SECOND_IMAGE") : row.default,
                        default: row.key === "appearance.wallpaper" ? Quickshell.env("SETTINGS_SECOND_IMAGE") : row.default
                    }));
            harness.catalog.calls = [];
            harness.catalog.lastChoice = "";
            harness.catalog.lastStatus = "";
        }
        function cleanup() {
            harness.controller.dismiss();
            wait(30);
        }
        function test_panel_options() {
            show("panel");
            // The panel auto-hide toggle became a four-way choice (bar.autohide_mode) in 0.8.1.
            const mode = named("setting-bar.autohide_mode");
            check(mode !== undefined);
            const choice = mode.controlItem;
            equal(mode.value, "fullscreen");
            click(choice);
            tryVerify(() => choice.popup.opened);
            keyClick(Qt.Key_Home);
            keyClick(Qt.Key_Return);
            tryVerify(() => !choice.popup.opened);
            equal(harness.catalog.calls[harness.catalog.calls.length - 1].operation, "set");
            equal(harness.catalog.calls[harness.catalog.calls.length - 1].key, "bar.autohide_mode");
            equal(harness.catalog.calls[harness.catalog.calls.length - 1].value, "always");
            equal(mode.value, "always");
            click(mode.resetItem);
            equal(harness.catalog.calls[harness.catalog.calls.length - 1].operation, "reset");
            equal(harness.catalog.calls[harness.catalog.calls.length - 1].key, "bar.autohide_mode");
            equal(mode.value, "fullscreen");
            for (const key of ["bar.overview_workspaces", "bar.clock_seconds_closed", "bar.clock_seconds_open", "dock.on", "dock.auto_hide"]) {
                const row = named("setting-" + key);
                check(row !== undefined);
                const before = row.value;
                click(row.controlItem);
                equal(harness.catalog.calls[harness.catalog.calls.length - 1].key, key);
                equal(row.value, !before);
                click(row.resetItem);
                equal(harness.catalog.calls[harness.catalog.calls.length - 1].operation, "reset");
                equal(harness.catalog.calls[harness.catalog.calls.length - 1].key, key);
                equal(row.value, before);
            }
            const labels = named("setting-launcher.mode_icons");
            equal(labels.value, false);
            const labelsChoice = labels.controlItem;
            equal(labelsChoice.displayText, "Text");
            click(labelsChoice);
            tryVerify(() => labelsChoice.popup.opened);
            keyClick(Qt.Key_End);
            keyClick(Qt.Key_Return);
            tryVerify(() => !labelsChoice.popup.opened);
            equal(harness.catalog.calls[harness.catalog.calls.length - 1].key, "launcher.mode_icons");
            equal(harness.catalog.calls[harness.catalog.calls.length - 1].value, "true");
            equal(labels.value, true);
            equal(labelsChoice.displayText, "Icons");
            click(labels.resetItem);
            equal(labels.value, false);
            equal(labelsChoice.displayText, "Text");
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
            equal(wallpaperPage().currentPath, Quickshell.env("SETTINGS_SECOND_IMAGE"));
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
            equal(wallpaperPage().currentPath, Quickshell.env("SETTINGS_SECOND_IMAGE"));
            equal(named("wallpaper-choice-1").Accessible.checked, true);
            harness.catalog.busy = true;
            click(choice);
            click(named("wallpaper-choose"));
            equal(harness.catalog.calls.length, 2);
            equal(wallpaperPage().chooser.busy, false);
            // A personal niri wallpaper has no managed preview. An unrelated
            // wpaperd image must not be presented as the current Emaki choice.
            harness.catalog.values = harness.catalog.values.map(item => Object.assign({}, item, item.key === "appearance.wallpaper" ? {
                    value: null
                } : {}));
            equal(wallpaperPage().currentPath, "");
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
            equal(harness.catalog.lastChoice, "wallpaper");
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
        function test_overview_chooser() {
            show("wallpaper");
            const catalog = harness.catalog;
            catalog.values = catalog.values.concat([
                {
                    key: "appearance.overview_still",
                    value: Quickshell.env("SETTINGS_SECOND_IMAGE"),
                    default: Quickshell.env("SETTINGS_SECOND_IMAGE")
                },
                {
                    key: "appearance.overview_video",
                    value: "/theme/overview.mp4",
                    default: "/theme/overview.mp4"
                }
            ]);
            catalog.styles = [
                {
                    id: "emaki",
                    name: "Emaki",
                    overview_video: "/theme/overview.mp4",
                    overview_still: Quickshell.env("SETTINGS_SECOND_IMAGE"),
                    available: true,
                    video_available: true
                }
            ];
            check(named("overview-theme").checked);
            check(named("overview-choice-0").checked);
            click(named("overview-choice-0"));
            equal(catalog.calls[catalog.calls.length - 1].value, "catalog:emaki");
            catalog.busy = true;
            check(!named("overview-choose").enabled);
            check(!named("overview-choice-0").enabled);
            catalog.busy = false;
            for (const kind of ["PICTURE", "VIDEO", "BROKEN", "PORTAL_FAILED", "PORTAL_UNAVAILABLE", "CANCELLED"]) {
                catalog.overviewChooser.helper = Quickshell.env("SETTINGS_OVERVIEW_" + kind);
                const before = catalog.calls.length;
                const beforeValues = JSON.stringify(catalog.values);
                click(named("overview-choose"));
                tryCompare(catalog.overviewChooser, "busy", true);
                check(!named("overview-choose").enabled);
                tryCompare(catalog.overviewChooser, "busy", false);
                if (kind === "CANCELLED") {
                    equal(catalog.calls.length, before);
                } else if (["BROKEN", "PORTAL_FAILED", "PORTAL_UNAVAILABLE"].includes(kind)) {
                    equal(catalog.calls.length, before);
                    equal(JSON.stringify(catalog.values), beforeValues);
                    check(named("overview-error").visible);
                } else {
                    const call = catalog.calls[catalog.calls.length - 1];
                    equal(call.key, "appearance.overview_background");
                    equal(catalog.lastChoice, "overview");
                    const media = JSON.parse(call.value);
                    equal(media.video, kind === "VIDEO" ? Quickshell.env("SETTINGS_CHOSEN_IMAGE") : "");
                    equal(media.still, Quickshell.env(kind === "VIDEO" ? "SETTINGS_SECOND_IMAGE" : "SETTINGS_CHOSEN_IMAGE"));
                }
            }
            click(named("overview-theme"));
            equal(catalog.calls[catalog.calls.length - 1].key, "appearance.overview_background");
            equal(catalog.calls[catalog.calls.length - 1].operation, "reset");
            // Page reset uses the pair transaction even for a different selected theme.
            catalog.styles = [
                {
                    id: "ribbon-day",
                    name: "Ribbon",
                    overview_video: "/theme/ribbon.mp4",
                    overview_still: "/theme/ribbon.png",
                    available: true,
                    video_available: true
                }
            ];
            catalog.values = catalog.values.filter(row => row.key !== "appearance.style").concat([
                {
                    key: "appearance.style",
                    value: "ribbon-day",
                    default: "emaki"
                }
            ]).map(row => Object.assign({}, row, {
                    value: row.key === "appearance.wallpaper" ? Quickshell.env("SETTINGS_CHOSEN_IMAGE") : row.key === "appearance.overview_video" ? "/own/video.mp4" : row.key === "appearance.overview_still" ? Quickshell.env("SETTINGS_CHOSEN_IMAGE") : row.value
                }));
            catalog.calls = [];
            check(named("settings-reset-page").visible);
            click(named("settings-reset-page"));
            equal(catalog.calls.length, 2);
            equal(catalog.calls[0].key, "appearance.wallpaper");
            equal(catalog.calls[1].key, "appearance.overview_background");
            check(catalog.calls.every(call => call.operation === "reset"));
            // The response's selected-theme pair is already reset, despite global defaults.
            catalog.values = catalog.values.map(row => Object.assign({}, row, {
                    value: row.key === "appearance.overview_video" ? "/theme/ribbon.mp4" : row.key === "appearance.overview_still" ? "/theme/ribbon.png" : row.value
                }));
            check(!named("settings-reset-page").visible);
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
                view.scroller.contentY = 0;
                wait(40);
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
