// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    id: test
    property bool failed: false
    UpdateController { id: controller }
    TestCase { id: input; when: false; name: "UpdateControllerProcess" }
    function check(ok: bool, message: string): void {
        if (!ok) { failed = true; console.error("ASSERTION_FAILED " + message); }
    }
    function settle(): void {
        let remaining = 5000;
        while (controller.busy && remaining > 0) { input.wait(20); remaining -= 20; }
        check(!controller.busy, "operation exits before timeout");
    }
    Timer {
        interval: 25
        running: true
        onTriggered: {
            test.settle();
            test.check(controller.phase === "idle" && controller.received, "initial result received");
            test.check(controller.data.cached === true && controller.repositoryCount === 1, "startup uses cached check and counts only repository rows");
            controller.check();
            test.check(controller.phase === "checking" && controller.busy, "manual refresh enters checking");
            test.settle();
            test.check(controller.data.cached === false && controller.data.downloadSize === 123, "refresh replaces cached list");
            controller.apply();
            test.check(controller.phase === "applying" && controller.busy, "apply starts process");
            controller.check(); controller.apply(); controller.reboot();
            test.check(controller.phase === "applying", "busy rejects overlapping operations");
            test.settle();
            const scenario = Quickshell.env("TEST_SCENARIO");
            if (scenario === "cancelled") {
                test.check(controller.phase === "idle" && !controller.busy && controller.repositoryCount > 0, "cancelled permission returns to actionable list");
                test.check(controller.message === "The update was not started." && controller.output === "", "cancelled permission has one plain explanation");
                if (!test.failed) console.log("CONTROLLER_OK");
                Qt.quit();
                return;
            }
            test.check(controller.phase === "finished", "apply exits to result screen");
            test.check(controller.output.includes("Preparing the update.") && controller.output.includes("Download detail."), "stdout progress and stderr details are retained");
            if (scenario === "success") {
                test.check(controller.succeeded && controller.restart && controller.signOut, "success exposes restart and sign-out advice");
                test.check(controller.message === "Update complete.", "success result message");
                controller.reboot();
                test.check(controller.phase === "restarting", "restart starts process");
                test.settle();
                test.check(controller.phase === "idle" && !controller.succeeded && controller.restart, "failed restart remains available");
                test.check(controller.message === "Restart was refused.", "restart failure explanation");
            } else {
                test.check(!controller.succeeded, "failed operation is never successful");
                if (scenario === "failure") {
                    test.check(controller.message === "Update refused." && !controller.restart && !controller.signOut, "refusal carries message and clears result flags");
                } else if (scenario === "malformed") {
                    test.check(controller.message.includes("unreadable response"), "malformed protocol is explained");
                } else if (scenario === "empty") {
                    test.check(controller.message.includes("could not finish"), "missing final event is explained");
                }
            }
            if (!test.failed) console.log("CONTROLLER_OK");
            Qt.quit();
        }
    }
}
