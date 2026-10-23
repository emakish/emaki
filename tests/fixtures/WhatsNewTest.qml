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
        function inside(item) {
            const view = controller.window.view;
            const point = item.mapToItem(view, 0, 0);
            if (point.x < -1 || point.y < -1 || point.x + item.width > view.width + 1 || point.y + item.height > view.height + 1)
                return false;
            for (let parent = item.parent; parent && parent !== view; parent = parent.parent) {
                if (!parent.clip)
                    continue;
                const point = item.mapToItem(parent, 0, 0);
                if (point.x < -1 || point.y < -1 || point.x + item.width > parent.width + 1 || point.y + item.height > parent.height + 1)
                    return false;
            }
            return true;
        }
        function reveal(item) {
            const body = control("body");
            if (descendants(body).includes(item)) {
                body.contentY = 0;
                const point = item.mapToItem(body.contentItem, 0, 0);
                body.contentY = Math.max(0, Math.min(point.y, body.contentHeight - body.height));
            }
        }
        function capture(name) {
            const view = controller.window.view;
            const texts = descendants(view).filter(item => typeof item.text === "string" && item.text && item.truncated !== undefined && shown(item) && inside(item));
            check(texts.length > 0, "Frame contains visible copy");
            const regions = [];
            for (const item of texts) {
                const point = item.mapToItem(view, 0, 0);
                check(!item.truncated, "Untruncated copy: " + item.text);
                check(item.width > 0 && item.height > 0, "Visible copy: " + item.text);
                regions.push({text: item.text, x: point.x, y: point.y, width: item.width, height: item.height, truncated: item.truncated});
            }
            console.log("WHATSNEW_CONTENT " + JSON.stringify({name: name, regions: regions}));
            let saved = false;
            check(view.grabToImage(result => saved = result.saveToFile(Quickshell.env("WHATSNEW_SHOTS") + "/" + name + ".png")));
            tryVerify(() => saved, 5000);
        }
        function checkPage(name) {
            const view = controller.window.view;
            const body = control("body");
            const texts = descendants(view).filter(item => typeof item.text === "string" && item.text && item.truncated !== undefined && shown(item));
            for (const copy of [view.current.title, view.current.text])
                check(texts.some(item => item.text === copy), "Page content rendered: " + copy);
            const positions = [0];
            for (const item of texts) {
                reveal(item);
                check(!item.truncated && item.width > 0 && item.height > 0 && inside(item), "Copy fully reachable: " + item.text);
                if (!positions.includes(body.contentY))
                    positions.push(body.contentY);
            }
            for (const name of ["close", "next"].concat(view.page > 0 ? ["back"] : []).concat(view.current.action ? ["action"] : [])) {
                const button = control(name);
                check(button !== undefined, "Control exists: " + name);
                reveal(button);
                check(shown(button) && button.enabled && button.width > 0 && button.height > 0 && inside(button), "Control reachable: " + name);
            }
            for (let index = 0; index < view.count; ++index) {
                const dot = control("dot-" + index);
                check(dot !== undefined && shown(dot) && inside(dot), "Page dot reachable: " + index);
            }
            const bottom = Math.max(0, body.contentHeight - body.height);
            if (!positions.includes(bottom))
                positions.push(bottom);
            for (let index = 0; index < positions.length; ++index) {
                body.contentY = positions[index];
                wait(60);
                capture(name + (index === 0 ? "-top" : positions[index] === bottom ? "-bottom" : "-scroll-" + index));
            }
            if (bottom > 0) {
                body.contentY = 0;
                mouseWheel(body, body.width / 2, body.height / 2, 0, -120);
                tryVerify(() => body.contentY > 0);
                body.cancelFlick();
                control("next").forceActiveFocus();
                for (let index = 0; index < 30; ++index)
                    keyClick(Qt.Key_PageDown);
                equal(Math.round(body.contentY), Math.round(bottom), "Keyboard reaches lower copy");
            }
            body.contentY = 0;
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
            view.Window.window.width = Number(Quickshell.env("WHATSNEW_WIDTH") || "800");
            view.Window.window.height = Number(Quickshell.env("WHATSNEW_HEIGHT") || "660");
            controller.window.present();
            wait(100);
            equal(view.count, 6);
            if (mode === "render") {
                const language = Quickshell.env("EMAKI_LANGUAGE");
                equal(qsTranslate("Shell", "Settings"), ({en: "Settings", es: "Configuración", pt_BR: "Configurações", de: "Einstellungen", fr: "Paramètres", ru: "Настройки"})[language]);
                for (let page = 0; page < 6; ++page) {
                    equal(view.page, page);
                    wait(300);
                    checkPage(language + "-" + Quickshell.env("WHATSNEW_THEME") + "-" + Quickshell.env("WHATSNEW_SIZE") + "-" + (page + 1));
                    if (page < 5)
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
            controller.window.view.page = 0;
            wait(300);
            mouseClick(control("action"));
            tryCompare(test, "languageOpens", 1);
            tryCompare(controller, "opened", false);
        }
    }
}
