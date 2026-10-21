// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell
import "../../shell" as Shell
import "../../shell/WelcomeContent.js" as Content

ShellRoot {
    Shell.WelcomeController {
        id: controller
        loadWallpaper: false
    }
    TestCase {
        id: test
        name: "Welcome"
        when: true
        property int listOpened: 0
        property bool listWorks: true
        onCompletedChanged: if (completed)
            console.log("WELCOME_TEST_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function equal(actual, expected, message) {
            if (actual !== expected)
                console.error("Welcome comparison: " + actual + " != " + expected + " " + (message || "") + " " + new Error().stack);
            compare(actual, expected, message || "");
        }
        function check(condition, message) {
            if (!condition)
                console.error("Welcome check: " + (message || "") + " " + new Error().stack);
            verify(condition, message || "");
        }
        function view() {
            return controller.window.view;
        }
        function descendants(item) {
            let found = [item];
            for (const child of item.children ?? [])
                found = found.concat(descendants(child));
            return found;
        }
        function control(name) {
            return descendants(view()).find(c => c.objectName === "welcome-" + name);
        }
        function shown(item) {
            for (let parent = item; parent; parent = parent.parent) {
                if (!parent.visible)
                    return false;
            }
            return true;
        }
        // Every word the page promises: its content, the window's own labels and buttons.
        function expectedCopy() {
            const page = Content.pages()[view().page];
            let copy = ["GETTING STARTED", page.title, page.text, view().last ? "Start using Emaki" : "Next"];
            if (page.note)
                copy.push(page.note);
            if (view().page > 0)
                copy.push("Back");
            if (view().last)
                copy.push("All shortcuts");
            for (const keyboard of page.keyboards ?? [])
                copy = copy.concat([keyboard.key, keyboard.keyboard]);
            for (const place of page.places ?? [])
                copy = copy.concat([place.name, place.what, place.where]);
            for (const row of (page.keys ?? []).concat(page.shortcuts ?? []))
                copy = copy.concat(row.keys).concat([row.label]);
            return copy;
        }
        function textItems(copy) {
            const matches = descendants(view()).filter(item => item.text === copy && item.truncated !== undefined && shown(item));
            check(matches.length > 0, "Expected welcome copy: " + copy);
            return matches;
        }
        function visibleBounds(item) {
            const point = item.mapToItem(view(), 0, 0);
            check(item.visible, "Welcome copy is visible: " + item.text);
            for (let parent = item; parent; parent = parent.parent)
                check(parent.opacity >= 0.99, "Welcome copy is opaque: " + item.text);
            // Light theme secondary ink is #241018 at 68 % (settings_dim, 173/255 once stored; FX11: at least 5.5:1); dark is opaque.
            check(item.color.a >= 0.67, "Welcome copy has ink: " + item.text + " (alpha " + item.color.a + ")");
            check(!item.truncated && item.width > 0 && item.height > 0, "Welcome copy fits: " + item.text);
            check(point.x >= 0 && point.y >= 0 && point.x + item.width <= view().width + 1 && point.y + item.height <= view().height + 1, "Welcome copy stays inside the window: " + item.text);
            for (let parent = item.parent; parent && parent !== view(); parent = parent.parent) {
                if (!parent.clip)
                    continue;
                const clipped = item.mapToItem(parent, 0, 0);
                check(clipped.x >= -1 && clipped.y >= -1 && clipped.x + item.width <= parent.width + 1 && clipped.y + item.height <= parent.height + 1, "Welcome copy stays inside its viewport: " + item.text);
            }
            // Text inside a card of the picture stays inside that card.
            for (let parent = item.parent; parent && parent !== view(); parent = parent.parent) {
                if (parent.objectName !== "welcome-stage" && parent.objectName !== "welcome-card")
                    continue;
                const inside = item.mapToItem(parent, 0, 0);
                check(inside.x >= 0 && inside.y >= 0 && inside.x + item.width <= parent.width + 1 && inside.y + item.height <= parent.height + 1, "Welcome copy stays inside the picture: " + item.text + " in " + parent.objectName);
            }
            return {
                text: item.text,
                x: point.x,
                y: point.y,
                width: item.width,
                height: item.height
            };
        }
        function reveal(item) {
            const body = control("body");
            if (descendants(body).includes(item)) {
                body.contentY = 0;
                const position = item.mapToItem(body.contentItem, 0, 0);
                body.contentY = Math.max(0, Math.min(position.y, body.contentHeight - body.height));
            }
        }
        function checkPage() {
            for (const name of ["close", "next"].concat(view().page > 0 ? ["back"] : []).concat(view().last ? ["shortcuts"] : [])) {
                const button = control(name);
                reveal(button);
                const point = button.mapToItem(view(), 0, 0);
                check(button.visible && button.width > 0 && button.height > 0 && point.x >= 0 && point.y >= 0 && point.x + button.width <= view().width && point.y + button.height <= view().height, "Welcome control stays reachable: " + name);
            }
            for (let index = 0; index < Content.pages().length; ++index) {
                const dot = control("dot-" + index);
                const point = dot.mapToItem(view(), 0, 0);
                check(dot.visible && point.x >= 0 && point.x + dot.width <= view().width && point.y + dot.height <= view().height, "Welcome page dot stays reachable: " + index);
            }
            for (const copy of expectedCopy()) {
                for (const item of textItems(copy)) {
                    reveal(item);
                    visibleBounds(item);
                }
            }
            control("body").contentY = 0;
            noDemoLogo();
        }
        // The old orange "E" (the flat mark and the Hot slanted E) never appears in Welcome.
        function noDemoLogo() {
            for (const item of descendants(view())) {
                check(item.kind !== "logo", "Welcome draws no letter mark: " + item);
                const source = String(item.source ?? "");
                let decoded = source;
                try {
                    decoded = decodeURIComponent(source);
                } catch (_) {}
                check(!source.includes("mark.svg") && !source.includes("mark-large.svg") && !decoded.includes("M3 22L7.161 22L12.896 2L8.735 2Z"), "Welcome loads no letter mark: " + source.slice(0, 80));
            }
        }
        function shot(name) {
            const regions = [];
            const body = control("body");
            for (const copy of expectedCopy()) {
                for (const item of textItems(copy)) {
                    const position = item.mapToItem(body, 0, 0);
                    if (descendants(body).includes(item) && (position.y < -1 || position.y + item.height > body.height + 1))
                        continue;
                    regions.push(visibleBounds(item));
                }
            }
            console.log("WELCOME_CONTENT " + JSON.stringify({
                name: name,
                regions: regions
            }));
            let saved = false;
            check(view().grabToImage(result => {
                saved = result.saveToFile(Quickshell.env("WELCOME_SHOTS") + "/" + name + "@" + Quickshell.env("QT_SCALE_FACTOR") + "x.png");
            }));
            tryVerify(() => saved, 5000);
        }
        function settle() {
            // The page arrival fades in over 180 ms.
            wait(260);
        }
        function test_welcome() {
            tryVerify(() => DesktopEntries.applications.values.some(app => app.id === "emaki-welcome"));
            const entry = DesktopEntries.applications.values.find(app => app.id === "emaki-welcome");
            check(entry !== undefined, "Welcome launcher entry is discoverable");
            equal(entry.name, "Welcome to Emaki");
            equal(entry.icon, "emaki-welcome");
            tryCompare(controller.store, "restored", true);
            equal(controller.store.seen, false);
            wait(60);
            equal(controller.opened, false);
            controller.openShortcutList = () => {
                ++test.listOpened;
                return test.listWorks;
            };
            controller.ready = true;
            tryVerify(() => controller.window !== null);
            tryCompare(controller.store, "seen", true);
            view().Window.window.width = 800;
            view().Window.window.height = 660;
            controller.window.present();
            wait(100);
            const firstWindow = controller.window;
            equal(controller.present(), "presented");
            equal(controller.window, firstWindow);
            equal(view().page, 0);
            equal(control("next").activeFocus, true);
            equal(control("back").visible, false, "No Back on the first page");
            check(control("wordmark") !== undefined, "The first page shows the pixel wordmark");

            // Tab visits every button in both directions, with a visible focus indication:
            // the hover look on the pale buttons, the strong rim on the accent one.
            const buttons = view().focusButtons();
            equal(buttons.map(button => button.objectName).join(","), "welcome-close,welcome-next");
            control("close").forceActiveFocus();
            for (let i = 1; i <= buttons.length; ++i) {
                keyClick(Qt.Key_Tab);
                const button = buttons[i % buttons.length];
                equal(button.activeFocus, true);
                equal(button.focusRing.visible, true);
                equal(button.focusRing.strong, button.primary);
                equal(button.focusRing.border.width, button.primary ? 2 : 1);
            }
            keyClick(Qt.Key_Backtab);
            equal(control("next").activeFocus, true);
            control("close").forceActiveFocus();
            keyClick(Qt.Key_Down);
            check(!control("close").activeFocus, "Down leaves the close button");
            control("next").forceActiveFocus();

            // Enter goes on through every page; each fits the default window.
            for (let page = 0; page < Content.pages().length; ++page) {
                equal(view().page, page);
                settle();
                check(control("body").contentHeight <= control("body").height + 1, "Default page must fit: " + control("body").contentHeight + " / " + control("body").height);
                for (let index = 0; index < Content.pages().length; ++index)
                    equal(control("dot-" + index).Accessible.selected, index === page);
                const texts = descendants(view()).filter(c => c.text !== undefined && c.truncated !== undefined && shown(c));
                check(texts.length > 5);
                for (const text of texts)
                    equal(text.truncated, false, text.text);
                checkPage();
                shot("page-" + (page + 1));
                equal(control("title").text, Content.pages()[page].title);
                if (page < Content.pages().length - 1)
                    keyClick(Qt.Key_Return);
            }
            equal(control("next").text, "Start using Emaki");

            // Arrows turn the pages; they stop at either end.
            keyClick(Qt.Key_Right);
            equal(view().page, 4);
            keyClick(Qt.Key_Left);
            equal(view().page, 3);
            keyClick(Qt.Key_Left);
            keyClick(Qt.Key_Left);
            keyClick(Qt.Key_Left);
            equal(view().page, 0);
            keyClick(Qt.Key_Left);
            equal(view().page, 0);
            keyClick(Qt.Key_Right);
            equal(view().page, 1);
            // Back loses its place on the first page: focus moves on to Next.
            control("back").forceActiveFocus();
            keyClick(Qt.Key_Return);
            equal(view().page, 0);
            equal(control("next").activeFocus, true);
            // Leaving the last page must not activate its hidden shortcut button.
            view().page = 4;
            settle();
            control("shortcuts").forceActiveFocus();
            keyClick(Qt.Key_Left);
            equal(view().page, 3);
            equal(control("next").activeFocus, true, "Next receives focus when All shortcuts disappears");
            keyClick(Qt.Key_Return);
            equal(view().page, 4);
            equal(test.listOpened, 0, "Enter does not activate hidden All shortcuts");
            // Up and Down walk the buttons by where they are (the shell's spatial keys).
            view().page = 4;
            settle();
            control("shortcuts").forceActiveFocus();
            keyClick(Qt.Key_Down);
            check(control("back").activeFocus || control("next").activeFocus, "Down from All shortcuts reaches the footer");
            control("next").forceActiveFocus();
            keyClick(Qt.Key_Up);
            check(!control("next").activeFocus && (control("close").activeFocus || control("shortcuts").activeFocus), "Up from Next leaves the footer");

            // The mouse: dots, Next and Back.
            mouseClick(control("dot-2"));
            equal(view().page, 2);
            mouseClick(control("next"));
            equal(view().page, 3);
            mouseClick(control("back"));
            equal(view().page, 2);

            // Match WelcomeWindow's available size on a 1366 by 768 logical display.
            view().Window.window.width = Math.min(800, 1366 - 48);
            view().Window.window.height = Math.min(660, 768 - 120);
            for (let page = 0; page < Content.pages().length; ++page) {
                view().page = page;
                settle();
                checkPage();
                shot("1366x768-" + (page + 1));
            }
            view().page = 0;

            // A small logical display keeps the footer reachable and the body scrollable.
            view().Window.window.width = 560;
            view().Window.window.height = 420;
            wait(80);
            const body = control("body");
            check(body.contentHeight > body.height);
            mouseWheel(body, body.width / 2, body.height / 2, 0, -120);
            tryVerify(() => body.contentY > 0);
            body.cancelFlick();
            body.contentY = 0;
            for (let page = 0; page < Content.pages().length; ++page) {
                view().page = page;
                settle();
                checkPage();
                shot("small-" + (page + 1) + "-top");
                control("next").forceActiveFocus();
                for (let i = 0; i < 30; ++i)
                    keyClick(Qt.Key_PageDown);
                check(body.contentY > 0);
                equal(Math.round(body.contentY), Math.round(body.contentHeight - body.height));
                shot("small-" + (page + 1) + "-bottom");
                const nextPosition = control("next").mapToItem(view(), 0, 0);
                check(nextPosition.y + control("next").height <= controller.window.height);
                for (let i = 0; i < 30; ++i)
                    keyClick(Qt.Key_PageUp);
                equal(body.contentY, 0);
            }
            view().Window.window.width = 800;
            view().Window.window.height = 660;

            // All shortcuts opens the same searchable list as Super + /; a failure is shown.
            view().page = 4;
            settle();
            mouseClick(control("shortcuts"));
            equal(test.listOpened, 1);
            equal(controller.shortcutError, "");
            test.listWorks = false;
            mouseClick(control("shortcuts"));
            equal(test.listOpened, 2);
            equal(controller.shortcutError, "Could not open the shortcut list. Try Super + /.");
            settle();
            const failure = textItems("Could not open the shortcut list. Try Super + /.")[0];
            reveal(failure);
            visibleBounds(failure);
            test.listWorks = true;
            controller.window.present();
            keyClick(Qt.Key_Return);
            tryCompare(controller, "opened", false);
            wait(60);
            equal(controller.opened, false);

            // Reopened, the tour starts again at the first page.
            equal(controller.present(), "presented");
            wait(80);
            equal(view().page, 0);
            equal(controller.shortcutError, "");
            keyClick(Qt.Key_Escape);
            tryCompare(controller, "opened", false);
            controller.present();
            wait(80);
            mouseClick(control("close"));
            tryCompare(controller, "opened", false);

            controller.present();
            wait(80);
            view().Window.window.close();
            tryCompare(controller, "opened", false);
            controller.present();
            tryVerify(() => controller.window.backingWindowVisible);
            equal(controller.opened, true);
        }
    }
}
