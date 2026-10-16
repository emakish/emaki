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
            if (Quickshell.env("APPS_FIXTURE_MISSING")) {
                tryCompare(page, "error", "Apps could not be read. Try again.");
                return;
            }
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
        function test_program_removal_shows_the_preview_first() {
            if (Quickshell.env("APPS_FIXTURE_MISSING"))
                return;
            tryVerify(() => page.programs.length === 2 && !page.programsBusy);
            const card = findChild(page, "settings-installed-programs");
            verify(card !== null);
            const kate = findChild(card, "program-kate");
            verify(kate !== null);
            findChild(kate, "program-remove").clicked();
            tryVerify(() => page.preview.code === "required" && !page.programsBusy);
            const refused = kate.parent;
            verify(findChild(refused, "program-preview").visible);
            verify(!findChild(refused, "program-confirm").visible);
            findChild(refused, "program-cancel").clicked();
            compare(page.preview.code, undefined);
            const gwenview = findChild(card, "program-gwenview");
            findChild(gwenview, "program-remove").clicked();
            tryVerify(() => page.preview.code === "ready" && !page.programsBusy);
            const block = gwenview.parent;
            verify(findChild(block, "program-preview").visible);
            compare(findChild(block, "program-preview-packages").text, "gwenview\nkimageannotator");
            const confirm = findChild(block, "program-confirm");
            verify(confirm.visible);
            confirm.clicked();
            tryVerify(() => page.programsMessage === "Fixture removed");
            tryVerify(() => page.programs.length === 1 && page.programs[0].package === "kate" && !page.programsBusy);
            compare(page.preview.code, undefined);
        }
        onCompletedChanged: if (completed)
            console.log("APPS_PAGE_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
    }
}
