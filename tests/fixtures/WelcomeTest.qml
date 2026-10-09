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
        function descendants(item) {
            let found = [item];
            for (const child of item.children ?? [])
                found = found.concat(descendants(child));
            return found;
        }
        function control(name) {
            return descendants(controller.window.view).find(c => c.objectName === "welcome-" + name);
        }
        function expectedCopy() {
            const page = Content.pages[controller.window.view.page];
            let copy = ["Welcome to Emaki", page.title, page.description, page.diagram, "All shortcuts", controller.window.view.page === 2 ? "Start using Emaki" : "Next"];
            for (const action of page.actions)
                copy = copy.concat([action.title, action.pointer, action.keys, action.keyboard]);
            return copy;
        }
        function textItem(copy) {
            const matches = descendants(controller.window.view).filter(item => item.text === copy && item.truncated !== undefined);
            equal(matches.length, 1, "Expected welcome copy: " + copy);
            return matches[0];
        }
        function visibleBounds(item) {
            const view = controller.window.view;
            const point = item.mapToItem(view, 0, 0);
            check(item.visible, "Welcome copy is visible: " + item.text);
            for (let parent = item; parent; parent = parent.parent)
                check(parent.opacity >= 0.99, "Welcome copy is opaque: " + item.text);
            check(item.color.a > 0.99, "Welcome copy has ink: " + item.text);
            check(!item.truncated && item.width > 0 && item.height > 0, "Welcome copy fits: " + item.text);
            check(point.x >= 0 && point.y >= 0 && point.x + item.width <= view.width + 1 && point.y + item.height <= view.height + 1, "Welcome copy stays inside the window: " + item.text);
            for (let parent = item.parent; parent && parent !== view; parent = parent.parent) {
                if (!parent.clip)
                    continue;
                const clipped = item.mapToItem(parent, 0, 0);
                check(clipped.x >= -1 && clipped.y >= -1 && clipped.x + item.width <= parent.width + 1 && clipped.y + item.height <= parent.height + 1, "Welcome copy stays inside its viewport: " + item.text);
            }
            return {
                text: item.text,
                x: point.x,
                y: point.y,
                width: item.width,
                height: item.height
            };
        }
        function checkPage() {
            const body = control("body");
            const view = controller.window.view;
            for (const name of ["close", "shortcuts", "back", "next", "tab-0", "tab-1", "tab-2"]) {
                const button = control(name);
                const point = button.mapToItem(view, 0, 0);
                check(button.visible && button.width > 0 && button.height > 0 && point.x >= 0 && point.y >= 0 && point.x + button.width <= view.width && point.y + button.height <= view.height, "Welcome control stays reachable: " + name);
            }
            for (const copy of expectedCopy()) {
                const item = textItem(copy);
                if (descendants(body).includes(item)) {
                    body.contentY = 0;
                    const position = item.mapToItem(body, 0, 0);
                    body.contentY = Math.max(0, Math.min(position.y, body.contentHeight - body.height));
                }
                visibleBounds(item);
            }
            body.contentY = 0;
        }
        function shot(name) {
            const regions = [];
            const body = control("body");
            for (const copy of expectedCopy()) {
                const item = textItem(copy);
                const position = item.mapToItem(body, 0, 0);
                if (descendants(body).includes(item) && (position.y < -1 || position.y + item.height > body.height + 1))
                    continue;
                regions.push(visibleBounds(item));
            }
            console.log("WELCOME_CONTENT " + JSON.stringify({
                name: name,
                regions: regions
            }));
            let saved = false;
            check(controller.window.view.grabToImage(result => {
                saved = result.saveToFile(Quickshell.env("WELCOME_SHOTS") + "/" + name + "@" + Quickshell.env("QT_SCALE_FACTOR") + "x.png");
            }));
            tryVerify(() => saved, 5000);
        }
        function test_welcome() {
            tryVerify(() => DesktopEntries.applications.values.some(app => app.id === "emaki-welcome"));
            const entry = DesktopEntries.applications.values.find(app => app.id === "emaki-welcome");
            check(entry !== undefined, "Welcome launcher entry is discoverable");
            equal(entry.name, "Welcome to Emaki");
            tryCompare(controller.store, "restored", true);
            equal(controller.store.seen, false);
            wait(60);
            equal(controller.opened, false);
            controller.ready = true;
            tryVerify(() => controller.window !== null);
            tryCompare(controller.store, "seen", true);
            controller.window.view.Window.window.width = 800;
            controller.window.view.Window.window.height = 660;
            controller.window.present();
            wait(100);
            const firstWindow = controller.window;
            equal(controller.present(), "presented");
            equal(controller.window, firstWindow);
            equal(control("next").activeFocus, true);

            // Visit every control in both directions, with a visible focus indication.
            const buttons = controller.window.view.focusButtons();
            control("close").forceActiveFocus();
            for (let i = 1; i <= buttons.length; ++i) {
                keyClick(Qt.Key_Tab);
                equal(buttons[i % buttons.length].activeFocus, true);
                equal(buttons[i % buttons.length].focusRing.visible, true);
                equal(buttons[i % buttons.length].focusRing.border.width, 2);
            }
            keyClick(Qt.Key_Backtab);
            equal(control("next").activeFocus, true);

            for (let page = 0; page < 3; ++page) {
                equal(controller.window.view.page, page);
                wait(80);
                check(control("body").contentHeight <= control("body").height, "Default page must fit: " + control("body").contentHeight + " / " + control("body").height);
                const texts = descendants(controller.window.view).filter(c => c.text !== undefined && c.truncated !== undefined);
                check(texts.length > 10);
                for (const text of texts)
                    equal(text.truncated, false, text.text);
                checkPage();
                shot("page-" + (page + 1));
                if (page < 2)
                    keyClick(Qt.Key_Return);
            }
            keyClick(Qt.Key_Left);
            equal(controller.window.view.page, 1);
            keyClick(Qt.Key_Right);
            equal(controller.window.view.page, 2);
            mouseClick(control("tab-0"));
            equal(controller.window.view.page, 0);
            mouseClick(control("next"));
            equal(controller.window.view.page, 1);
            mouseClick(control("back"));
            equal(controller.window.view.page, 0);

            // Match WelcomeWindow's available size on a 1366 by 768 logical display.
            controller.window.view.Window.window.width = Math.min(800, 1366 - 48);
            controller.window.view.Window.window.height = Math.min(660, 768 - 120);
            for (let page = 0; page < 3; ++page) {
                controller.window.view.page = page;
                wait(80);
                checkPage();
                shot("1366x768-" + (page + 1));
            }
            controller.window.view.page = 0;

            // A small logical display keeps the footer reachable and the body scrollable.
            controller.window.view.Window.window.width = 560;
            controller.window.view.Window.window.height = 420;
            wait(80);
            const body = control("body");
            check(body.contentHeight > body.height);
            mouseWheel(body, body.width / 2, body.height / 2, 0, -120);
            tryVerify(() => body.contentY > 0);
            body.cancelFlick();
            body.contentY = 0;
            for (let page = 0; page < 3; ++page) {
                controller.window.view.page = page;
                wait(80);
                checkPage();
                shot("small-" + (page + 1) + "-top");
                control("next").forceActiveFocus();
                for (let i = 0; i < 30; ++i)
                    keyClick(Qt.Key_Down);
                check(body.contentY > 0);
                equal(Math.round(body.contentY), Math.round(body.contentHeight - body.height));
                shot("small-" + (page + 1) + "-bottom");
                const nextPosition = control("next").mapToItem(controller.window.view, 0, 0);
                check(nextPosition.y + control("next").height <= controller.window.height);
                for (let i = 0; i < 30; ++i)
                    keyClick(Qt.Key_Up);
                equal(body.contentY, 0);
            }
            controller.window.view.Window.window.width = 800;
            controller.window.view.Window.window.height = 660;
            controller.window.present();
            keyClick(Qt.Key_Return);
            tryCompare(controller, "opened", false);
            wait(60);
            equal(controller.opened, false);

            equal(controller.present(), "presented");
            wait(80);
            equal(controller.window.view.page, 0);
            keyClick(Qt.Key_Escape);
            tryCompare(controller, "opened", false);
            controller.present();
            wait(80);
            mouseClick(control("close"));
            tryCompare(controller, "opened", false);

            controller.present();
            wait(80);
            controller.window.view.Window.window.close();
            tryCompare(controller, "opened", false);
            controller.present();
            tryVerify(() => controller.window.backingWindowVisible);
            wait(80);
            mouseClick(control("shortcuts"));
            wait(150);
            equal(controller.shortcutError, "");
            equal(controller.opened, true);
        }
    }
}
