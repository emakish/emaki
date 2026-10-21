// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell
import Emaki.I18n
import "../../shell" as Shell

ShellRoot {
    Shell.WelcomeController {
        id: controller
        loadWallpaper: false
        releasePath: Quickshell.env("WHATSNEW_RELEASE")
    }
    TestCase {
        id: test
        name: "WhatsNew"
        property int languageOpens: 0
        Connections { target: controller; function onLanguageRequested() { ++test.languageOpens; } }
        when: true
        onCompletedChanged: if (completed)
            console.log("WHATSNEW_TEST_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function equal(actual, expected, message) {
            if (actual !== expected)
                console.error("WhatsNew comparison: " + actual + " != " + expected + " " + (message || "") + " " + new Error().stack);
            compare(actual, expected, message || "");
        }
        function check(condition, message) {
            if (!condition)
                console.error("WhatsNew check: " + message + " " + new Error().stack);
            verify(condition, message || "");
        }
        function descendants(item) {
            let items = [item];
            for (const child of item.children || [])
                items = items.concat(descendants(child));
            return items;
        }
        function shown(item) {
            for (let parent = item; parent; parent = parent.parent)
                if (!parent.visible || parent.opacity < 0.99)
                    return false;
            return true;
        }
        function control(name) {
            return descendants(controller.window.view).find(item => item.objectName === "welcome-" + name);
        }
        function capture(name) {
            const view = controller.window.view;
            const texts = descendants(view).filter(item => typeof item.text === "string" && item.text && item.truncated !== undefined && shown(item));
            check(texts.length >= 4, "Page contains visible copy");
            for (const copy of [view.current.title, view.current.text])
                check(texts.some(item => item.text === copy), "Page content rendered: " + copy);
            const regions = [];
            for (const item of texts) {
                const point = item.mapToItem(view, 0, 0);
                check(!item.truncated, "Untruncated copy: " + item.text);
                check(item.width > 0 && item.height > 0, "Visible copy: " + item.text);
                check(point.x >= -1 && point.y >= -1 && point.x + item.width <= view.width + 1 && point.y + item.height <= view.height + 1, "Copy inside window: " + item.text);
                for (let parent = item.parent; parent && parent !== view; parent = parent.parent) {
                    if (!parent.clip)
                        continue;
                    const inside = item.mapToItem(parent, 0, 0);
                    check(inside.x >= -1 && inside.y >= -1 && inside.x + item.width <= parent.width + 1 && inside.y + item.height <= parent.height + 1, "Copy inside viewport: " + item.text);
                }
                regions.push({text: item.text, x: point.x, y: point.y, width: item.width, height: item.height, truncated: item.truncated});
            }
            console.log("WHATSNEW_CONTENT " + JSON.stringify({name: name, regions: regions}));
            let saved = false;
            check(view.grabToImage(result => saved = result.saveToFile(Quickshell.env("WHATSNEW_SHOTS") + "/" + name + ".png")));
            tryVerify(() => saved, 5000);
        }
        function test_session() {
            try { session(); } catch (error) { console.error("SESSION " + error + " " + error.stack); throw error; }
        }
        function session() {
            Shell.SettingsBridge.values = [{key: "appearance.theme", value: Quickshell.env("WHATSNEW_THEME") || "light"}];
            tryCompare(controller.store, "restored", true);
            tryCompare(controller.whatsNewStore, "restored", true);
            equal(controller.opened, false, "Startup gate waits");
            controller.ready = true;
            const mode = Quickshell.env("WHATSNEW_CASE");
            if (["second", "patch", "suppressed"].includes(mode)) {
                wait(250);
                equal(controller.opened, false, "Seen or contentless version stays closed");
                return;
            }
            tryCompare(controller, "opened", true);
            tryVerify(() => controller.window !== null && controller.window.view !== null);
            equal(controller.showingWhatsNew, mode !== "fresh");
            if (mode === "fresh") {
                tryCompare(controller.store, "seen", true);
                wait(100);
                controller.dismiss();
                wait(100);
                equal(controller.opened, false);
                return;
            }
            const view = controller.window.view;
            tryVerify(() => view.Window.window !== null);
            view.Window.window.width = 800;
            view.Window.window.height = 660;
            controller.window.present();
            wait(100);
            equal(view.count, 4);
            if (mode === "render") {
                const language = Quickshell.env("EMAKI_LANGUAGE");
                equal(qsTranslate("Shell", "Settings"), ({en: "Settings", es: "Configuración", pt_BR: "Configurações"})[language]);
                for (let page = 0; page < 4; ++page) {
                    equal(view.page, page);
                    wait(300);
                    capture(language + "-" + Quickshell.env("WHATSNEW_THEME") + "-" + (page + 1));
                    if (page < 3)
                        mouseClick(control("next"));
                }
                mouseClick(control("next"));
            } else {
                keyClick(Qt.Key_Escape);
            }
            tryCompare(controller, "opened", false);
            wait(150);
            equal(controller.opened, false, "Dismissed update stays closed this login");
            equal(controller.presentWhatsNew(), "presented", "Update can be opened explicitly");
            tryCompare(controller, "opened", true);
            tryVerify(() => controller.window.view !== null);
            equal(controller.window.view.page, 0);
            controller.dismiss();
            equal(controller.present(), "presented");
            tryCompare(controller, "opened", true);
            equal(controller.showingWhatsNew, false);
            tryVerify(() => controller.window.view !== null);
            tryVerify(() => controller.window.view.Window.window !== null);
            controller.window.view.Window.window.width = 800;
            controller.window.view.Window.window.height = 660;
            controller.window.view.page = 0;
            wait(300);
            mouseClick(control("whatsnew"));
            tryCompare(controller, "showingWhatsNew", true);
            tryVerify(() => controller.window.view !== null);
            tryVerify(() => controller.window.view.Window.window !== null);
            controller.window.view.Window.window.width = 800;
            controller.window.view.Window.window.height = 660;
            controller.window.view.page = 1;
            wait(300);
            mouseClick(control("action"));
            tryCompare(test, "languageOpens", 1);
            tryCompare(controller, "opened", false);
        }
    }
}
