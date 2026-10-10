// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    SystemService {
        id: service
        live: false
        helpersEnabled: false
    }
    SettingsMaintenancePage {
        id: page
        width: 700
        page: "lock"
        service: service
    }
    TestCase {
        name: "SettingsMaintenancePage"
        when: true
        function visibleMessage(text) {
            for (const child of page.children) {
                if (child.text === text && child.visible)
                    return true;
            }
            return false;
        }
        function test_channel_password_and_navigation() {
            const mode = Quickshell.env("MAINTENANCE_FIXTURE_MODE");
            if (mode === "missing-channel") {
                page.page = "updates";
                tryCompare(page, "message", "The update channel helper could not be started.");
                compare(page.pending, false);
                compare(page.channel.channel, undefined);
                verify(visibleMessage(page.message));
                return;
            }
            if (mode === "missing-terminal") {
                const button = findChild(page, "settings-password");
                verify(button.controlItem !== null);
                button.controlItem.clicked();
                tryCompare(page, "message", "The password window could not be opened.");
                verify(visibleMessage(page.message));
                return;
            }
            compare(page.channel.channel, undefined);
            const password = findChild(page, "settings-password");
            verify(password !== null);
            verify(password.visible);
            verify(password.controlItem !== null);
            password.controlItem.clicked();
            tryCompare(page, "message", "Password changed.");
            password.controlItem.clicked();
            tryCompare(page, "message", "Password not changed.");
            password.controlItem.clicked();
            tryCompare(page, "message", "The password change could not be confirmed. Check the password window.");
            page.page = "updates";
            tryVerify(() => page.channel.channel === "stable" && !page.pending);
            const channel = findChild(page, "settings-update-channel");
            verify(channel !== null);
            verify(channel.visible);
            compare(channel.value, "stable");
            channel.requestValue("testing");
            tryVerify(() => page.channel.channel === "testing" && !page.pending);
            compare(channel.value, "testing");
            channel.requestReset();
            tryVerify(() => page.channel.channel === "stable" && !page.pending);
            compare(channel.value, "stable");
            channel.requestValue("testing");
            tryCompare(page, "message", "Fixture refused the channel change.");
            tryCompare(page, "pending", false);
            compare(channel.value, "stable");
            verify(visibleMessage("Fixture refused the channel change."));
        }
        onCompletedChanged: if (completed)
            console.log("MAINTENANCE_PAGE_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
    }
}
