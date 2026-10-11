// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell

// Real close-all, scene reset and Settings Loader; only account and native windows are fixtures.
ShellRoot {
    id: test
    NiriService {
        id: niri
        binary: ""
    }
    ShellOutputs {
        id: outputs
        niri: niri
        headless: true
        screens: [
            {
                name: "fixture",
                width: 1536,
                height: 960
            }
        ]
    }
    PolkitPromptService {
        id: authentication
        outputs: outputs
    }
    QtObject {
        id: backend
        property var flow: null
        property bool responseRequired: true
        property bool passwordRequest: true
        signal rejected
        function cancel() {
            flow = null;
        }
    }
    readonly property var controller: outputs.shared.settingsController
    readonly property var scene: outputs.activeScene
    readonly property var page: find(controller.view, "users-page")
    property int operationIndex: 0
    property int phase: 0
    property int ticks: 0
    readonly property var operations: [["set-name", "bo", "Bo"], ["set-administrator", "bo", "yes"], ["set-password", "bo", "--terminal"], ["own-password"], ["remove", "bo", "--home", "keep"]]
    function find(item, name) {
        if (!item)
            return null;
        if (item.objectName === name)
            return item;
        // The page has no public fixture-only identifier.
        if (name === "users-page" && String(item).includes("SettingsUsersPage_"))
            return item;
        for (const child of item.children || []) {
            const found = find(child, name);
            if (found)
                return found;
        }
        return null;
    }
    function check(ok, text) {
        if (!ok) {
            console.error("FAILED", text, operationIndex, phase);
            Qt.exit(1);
        }
    }
    Timer {
        interval: 120
        running: true
        repeat: true
        onTriggered: {
            test.ticks++;
            test.check(test.ticks < 190, "lifetime timeout");
            if (!test.scene)
                return;
            if (test.phase === 0) {
                authentication.controller.backend = backend;
                authentication.controller.locked = false;
                test.controller.open("users");
                test.phase++;
            } else if (test.phase === 1) {
                if (!test.page || !test.page.received || test.page.pending)
                    return;
                const args = test.operations[test.operationIndex];
                if (args[0] === "own-password")
                    test.find(test.page, "settings-users-own-password").clicked();
                else
                    test.page.run(args);
                test.phase++;
            } else if (test.phase === 2) {
                test.check(SettingsUsersService.pending || SettingsUsersService.passwordPending, "operation waits for authentication/terminal");
                backend.flow = {};
                test.phase++;
            } else if (test.phase === 3) {
                if (test.scene.launcherPresent || test.scene.input.settingsActive)
                    return;
                test.check(test.controller.view === null && test.page === null, "Settings Loader unloaded");
                test.check(SettingsUsersService.pending || SettingsUsersService.passwordPending, "operation survived unloaded page");
                backend.flow = null;
                // Reopen while the helper is still alive, then close a second time.
                test.controller.open("users");
                test.phase++;
            } else if (test.phase === 4) {
                if (!test.page)
                    return;
                test.check(SettingsUsersService.pending || SettingsUsersService.passwordPending, "reopening keeps operation in flight");
                backend.flow = {};
                test.phase++;
            } else if (test.phase === 5) {
                if (test.scene.launcherPresent || SettingsUsersService.pending || SettingsUsersService.passwordPending)
                    return;
                test.check(!SettingsUsersService.problem, "receipt reaches session service after closing");
                backend.flow = null;
                test.controller.open("users");
                test.phase++;
            } else if (test.phase === 6) {
                if (!test.page)
                    return;
                const op = test.operations[test.operationIndex][0];
                const bo = test.page.accounts.find(user => user.login === "bo");
                if (op === "set-name")
                    test.check(bo.name === "Bo", "renamed account after reopening");
                if (op === "set-administrator")
                    test.check(bo.administrator, "role after reopening");
                if (op.includes("password"))
                    test.check(test.page.message === "Password changed.", "terminal receipt preserved after reopening");
                if (op === "remove")
                    test.check(!bo, "removed account after reopening");
                test.operationIndex++;
                if (test.operationIndex === test.operations.length) {
                    console.log("USERS_PAGE_PASS");
                    Qt.quit();
                }
                test.phase = 1;
            }
        }
    }
}
