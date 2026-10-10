// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    id: root
    QtObject {
        id: fixtureCatalog
        property bool busy: false
        readonly property bool writing: busy
        property bool rejectWrites: false
        property var values: ({
                "notifications.dnd": false,
                "notifications.until": "0",
                "notifications.schedule": '{"enabled":false,"start":"22:00","end":"07:00"}',
                "notifications.rules": "{}"
            })
        property var writes: []
        function row(key) {
            return {
                value: values[key],
                default: key === "notifications.schedule" ? '{"enabled":false,"start":"22:00","end":"07:00"}' : false
            };
        }
        function set(key, value) {
            if (rejectWrites)
                return false;
            writes = writes.concat([
                {
                    key: key,
                    value: value
                }
            ]);
            const next = Object.assign({}, values);
            next[key] = key === "notifications.dnd" ? value === "true" : value;
            values = next;
        }
        function reset(key) {
            set(key, key === "notifications.until" ? "0" : "false");
        }
    }
    QtObject {
        id: fixtureStore
        property bool effectiveDnd: false
        property var applications: [
            {
                id: "desktop:chat",
                name: "Chat"
            },
            {
                id: "name:Calendar",
                name: "Calendar"
            }
        ]
    }
    FloatingWindow {
        id: window
        visible: true
        implicitWidth: 760
        implicitHeight: 900
        Rectangle {
            anchors.fill: parent
            color: "#eee4db"
            NotificationsSettingsPage {
                id: page
                x: 20
                y: 20
                width: 720
                catalog: fixtureCatalog
                store: fixtureStore
            }
        }
    }
    TestCase {
        name: "NotificationsSettingsPage"
        when: window.backingWindowVisible
        onCompletedChanged: if (completed)
            console.log("NOTIFICATIONS_SETTINGS_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function find(item, name) {
            if (item.objectName === name)
                return item;
            for (const child of item.children ?? []) {
                const result = find(child, name);
                if (result)
                    return result;
            }
            return null;
        }
        function equal(actual, expected) {
            if (actual !== expected)
                console.error("COMPARE", actual, expected);
            compare(actual, expected);
        }
        function check(value) {
            if (!value)
                console.error("CHECK FAILED");
            verify(value);
        }
        function test_controls() {
            try {
                const dnd = find(page, "setting-notifications.dnd");
                mouseClick(dnd.controlItem);
                equal(fixtureCatalog.values["notifications.dnd"], true);
                mouseClick(dnd.resetItem);
                equal(fixtureCatalog.values["notifications.dnd"], false);
                fixtureCatalog.busy = true;
                dnd.requestValue(true);
                equal(fixtureCatalog.values["notifications.dnd"], false);
                fixtureCatalog.busy = false;
                const until = find(page, "setting-notifications.until");
                until.requestValue("30");
                check(Number(fixtureCatalog.values["notifications.until"]) > Date.now() + 1790000);
                until.requestReset();
                equal(fixtureCatalog.values["notifications.until"], "0");
                const start = find(page, "settings-notifications-start");
                const end = find(page, "settings-notifications-end");
                const schedule = find(page, "setting-notifications.schedule");
                check(!start.enabled && !end.enabled);
                check(!start.controlItem.enabled && !end.controlItem.enabled);
                schedule.requestValue(true);
                check(start.controlItem.enabled && end.controlItem.enabled);
                start.controlItem.forceActiveFocus();
                start.controlItem.selectAll();
                for (const character of "25:61")
                    keyClick(character);
                keyClick(Qt.Key_Return);
                equal(page.schedule.start, "22:00");
                check(page.validationError.length > 0);
                start.controlItem.selectAll();
                for (const character of "23:15")
                    keyClick(character);
                keyClick(Qt.Key_Return);
                equal(page.schedule.start, "23:15");
                equal(page.schedule.end, "07:00");
                equal(page.validationError, "");
                schedule.requestValue(false);
                check(!start.controlItem.enabled && !start.resetItem.enabled);
                mouseClick(start.resetItem);
                equal(page.schedule.start, "23:15");
                equal(page.schedule.end, "07:00");
                schedule.requestValue(true);
                equal(start.controlItem.text, "23:15");
                start.requestReset();
                equal(page.schedule.start, "22:00");
                equal(start.controlItem.text, "22:00");
                fixtureCatalog.rejectWrites = true;
                start.controlItem.selectAll();
                for (const character of "21:30")
                    keyClick(character);
                keyClick(Qt.Key_Return);
                equal(page.schedule.start, "22:00");
                equal(start.controlItem.text, "22:00");
                fixtureCatalog.rejectWrites = false;
                const app = find(page, "settings-notifications-app-desktop:chat");
                app.requestValue("silent");
                equal(page.rules["desktop:chat"], "silent");
                const calendar = find(page, "settings-notifications-app-name:Calendar");
                calendar.requestValue("off");
                equal(page.rules["name:Calendar"], "off");
                equal(page.rules["desktop:chat"], "silent");
                app.requestReset();
                equal(page.rules["desktop:chat"], undefined);
                equal(page.rules["name:Calendar"], "off");
                if (Quickshell.env("NOTIFICATIONS_SCREENSHOT")) {
                    dnd.requestValue(true);
                    find(page, "setting-notifications.schedule").requestValue(true);
                    fixtureStore.effectiveDnd = true;
                    app.requestValue("silent");
                    wait(100);
                    const shot = grabImage(window.contentItem);
                    shot.save(Quickshell.env("NOTIFICATIONS_SCREENSHOT"));
                }
                fixtureStore.applications = [];
                wait(1);
                check(find(page, "settings-notifications-app-desktop:chat") === null);
                page.store = null;
                wait(1);
            } catch (error) {
                console.error(error, error.stack);
                throw error;
            }
        }
    }
}
