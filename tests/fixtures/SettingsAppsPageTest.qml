// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    id: root
    property var calls: []
    QtObject {
        id: catalog
        property bool busy: false
        readonly property bool writing: busy
        function row(key) {
            return {
                value: key === "defaults.mail" ? "mail.desktop" : null
            };
        }
        function set(key, value) {
            root.calls = root.calls.concat([
                {
                    key: key,
                    value: value
                }
            ]);
        }
        function reset(key) {
            root.calls = root.calls.concat([
                {
                    key: key,
                    value: null
                }
            ]);
        }
    }
    SettingsAppsPage {
        id: page
        width: 700
        catalog: catalog
    }
    TestCase {
        name: "SettingsAppsPage"
        when: true
        function test_choices_and_failures() {
            tryCompare(page, "applications", [
                {
                    id: "editor.desktop",
                    name: "Editor",
                    roles: ["editor"]
                }
            ]);
            compare(page.options("editor", null).length, 2);
            compare(page.options("editor", null)[0].label, "System default: Editor");
            compare(page.options("mail", null)[0].label, "System default: unavailable");
            compare(page.options("mail", "mail.desktop").length, 2);
            const mail = findChild(page, "setting-defaults.mail");
            verify(mail !== null);
            mail.requestValue("other.desktop");
            compare(root.calls[0].key, "defaults.mail");
            compare(root.calls[0].value, "other.desktop");
            mail.requestReset();
            compare(root.calls[1].value, null);
            catalog.busy = true;
            mail.requestValue("ignored.desktop");
            compare(root.calls.length, 2);
            catalog.busy = false;
            wait(0);
            tryCompare(page, "error", "");
            tryVerify(() => !findChild(page, "setting-defaults.mail").busy);
            page.run(["set", "fixture.desktop", "false"]);
            tryCompare(page, "error", "Fixture refused the change.");
            compare(page.startup[0].enabled, true);
        }
        onCompletedChanged: if (completed)
            console.log("APPS_PAGE_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
    }
}
