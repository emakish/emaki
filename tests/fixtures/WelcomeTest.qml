pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell
import "../../shell" as Shell

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
        function shot(name) {
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
                equal(buttons[i % buttons.length].border.width, 2);
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
