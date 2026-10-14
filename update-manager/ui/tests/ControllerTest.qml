// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    id: test
    property bool failed: false
    property int hides: 0
    UpdateController { id: controller; onHideReady: test.hides += 1 }
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
            const scenario = Quickshell.env("TEST_SCENARIO");
            if (scenario.startsWith("index-")) {
                input.wait(100);
                test.check(controller.restorePoints.length === 0 && !!controller.restoreError, "restore index failure is distinguished from no snapshots");
                if (scenario === "index-error") test.check(controller.restoreError === "Restore index is unavailable.", "backend restore explanation retained");
                if (!test.failed) console.log("CONTROLLER_OK");
                Qt.quit(); return;
            }
            if (scenario === "flatpak" || scenario === "aur") {
                test.check(controller.repositoryCount === 0, "non-repository rows excluded from repository count");
                controller.apply();
                test.check(controller.busy === (scenario === "flatpak"), "only managed rows can start update");
                test.settle();
                if (!test.failed) console.log("CONTROLLER_OK");
                Qt.quit(); return;
            }
            if (scenario === "reminder") {
                test.check(controller.restart && controller.reminder, "startup restores reminder");
                controller.dismissReminder(); test.settle();
                test.check(test.hides === 0, "Later keeps the unified window open");
                test.check(controller.restart && !controller.reminder, "dismissal keeps restart available");
                controller.check(); test.settle();
                test.check(controller.restart && !controller.reminder, "new check preserves dismissed reminder");
                if (!test.failed) console.log("CONTROLLER_OK");
                Qt.quit(); return;
            }
            if (scenario.startsWith("restore-")) {
                controller.loadRestorePoints(); test.settle();
                test.check(controller.restorePoints.length === 1 && controller.restoreOpen, "restore list received");
                controller.selectRestore(controller.restorePoints[0]);
                controller.cancelRestore();
                test.check(!controller.selectedRestore && !controller.restoreOpen, "cancel drops selection");
                controller.selectRestore(controller.restorePoints[0]);
                controller.restorePoints = [{id: 99, date: "2026-10-11T12:00:00Z"}];
                test.check(controller.selectedRestore.id === 42, "index refresh preserves confirmed restore identity");
                controller.restore();
                controller.apply(); controller.reboot(); controller.loadRestorePoints();
                test.check(controller.phase === "restoring", "restore blocks overlapping actions");
                test.settle();
                const ok = scenario === "restore-success";
                test.check(controller.succeeded === ok && controller.restorePending === (ok || scenario === "restore-partial") && controller.restart === (ok || scenario === "restore-partial"), "restore result retains refusal or pending restart");
                test.check(controller.message === (ok ? "Restore prepared." : "Restore refused."), "restore result explanation");
                if (controller.restorePending) { controller.apply(); test.check(!controller.busy, "prepared restore prevents updates"); }
                if (!test.failed) console.log("CONTROLLER_OK");
                Qt.quit(); return;
            }
            test.check(controller.data.cached === true && controller.repositoryCount === 1, "startup uses cached check and counts only repository rows");
            controller.externalBusy = true;
            controller.apply();
            test.check(!controller.busy, "program transactions block updates");
            controller.externalBusy = false;
            controller.check();
            test.check(controller.phase === "checking" && controller.busy, "manual refresh enters checking");
            test.settle();
            test.check(controller.data.cached === false && controller.data.downloadSize === 123, "refresh replaces cached list");
            controller.apply();
            test.check(controller.phase === "applying" && controller.busy, "apply starts process");
            controller.check(); controller.apply(); controller.reboot();
            test.check(controller.phase === "applying", "busy rejects overlapping operations");
            test.settle();
            if (scenario === "cancelled") {
                test.check(controller.phase === "idle" && !controller.busy && controller.repositoryCount > 0, "cancelled permission returns to actionable list");
                test.check(controller.message === "The update was not started." && controller.output === "", "cancelled permission has one plain explanation");
                if (!test.failed) console.log("CONTROLLER_OK");
                Qt.quit();
                return;
            }
            test.check(controller.phase === "finished", "apply exits to result screen");
            if (scenario === "long-log") {
                const progress = controller.progress || {};
                test.check(controller.output.length <= 32768 && !controller.output.includes("pre snapshots"), "the log window dropped the step markers");
                test.check(progress.step === 3 && progress.restorePoint === true, "steps and the restore point outlive the log window");
                test.check(progress.done === 1 && progress.total === 2, "piped pacman lines count against the summary");
                controller.check(); test.settle();
                test.check(controller.output === "" && (controller.progress || {}).step === 0 && !(controller.progress || {}).restorePoint, "a new operation clears the log and its steps together");
                if (!test.failed) console.log("CONTROLLER_OK");
                Qt.quit(); return;
            }
            test.check(controller.output.includes("Preparing the update.") && controller.output.includes("Download detail."), "stdout progress and stderr details are retained");
            test.check((controller.progress || {}).step === 0 && !(controller.progress || {}).restorePoint, "a log without step markers stays at getting ready");
            if (scenario === "delayed-index") {
                input.wait(400);
                test.check(controller.restorePoints.length === 1, "delayed system index appears without navigation");
                test.check(controller.restoreRefreshes === 0, "index polling ends after bounded retries");
                if (!test.failed) console.log("CONTROLLER_OK");
                Qt.quit(); return;
            }
            if (scenario === "success") {
                test.check(controller.succeeded && controller.restart && controller.signOut, "success exposes restart and sign-out advice");
                test.check(controller.message === "Update complete.", "success result message");
                const previousOutput = controller.output;
                let remaining = 5000;
                while (controller.countRefreshing && remaining > 0) { input.wait(20); remaining -= 20; }
                test.check(controller.completedUpdates.length === 1 && controller.completedUpdates[0].name === "linux" && !!controller.completedAt, "completed rows and timestamp survive count refresh");
                test.check(controller.managedCount === 0 && controller.data.updates.length === 1, "completed count comes from backend recheck, retaining AUR");
                test.check(controller.phase === "finished" && controller.message === "Update complete." && controller.output === previousOutput && controller.restart && controller.signOut, "count refresh preserves completed result and advice");
                controller.hide(); test.settle();
                test.check(controller.phase === "finished" && controller.message === "Update complete." && controller.output === previousOutput, "Later preserves completed result until explicit check");
                test.check(!controller.reminder && controller.restart, "Later dismisses only the reminder");
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
