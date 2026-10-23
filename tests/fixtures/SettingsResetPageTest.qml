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
    DockStore {
        id: dock
    }
    // The notification store's surface used by the reset: offer, dismiss and the local action.
    QtObject {
        id: notices
        property var offered: []
        property var dismissed: []
        signal localActionRequested(string action)
        function offer(app, summary, body, action, label) {
            offered = offered.concat([
                {
                    app: app,
                    summary: summary,
                    body: body,
                    action: action,
                    label: label
                }
            ]);
            return -1 - offered.length;
        }
        function dismiss(ids) {
            dismissed = dismissed.concat(ids);
        }
    }
    SettingsReset {
        id: resetter
        notificationStore: notices
    }
    SettingsMaintenancePage {
        id: page
        width: 700
        page: "updates"
        service: service
        resetter: resetter
    }
    // The same page opened again later (Settings closed and reopened).
    Loader {
        id: reopened
        active: false
        sourceComponent: SettingsMaintenancePage {
            width: 700
            page: "updates"
            service: service
            resetter: resetter
        }
    }
    TestCase {
        name: "SettingsResetPage"
        when: true
        function texts(name) {
            const found = [];
            const stack = [page];
            while (stack.length) {
                const item = stack.pop();
                if (item.objectName === name && item.visible)
                    found.push(item.text);
                for (const child of item.children || [])
                    stack.push(child);
            }
            return found;
        }
        function button(name, from) {
            const stack = [from || page];
            while (stack.length) {
                const item = stack.pop();
                if (item.objectName === name)
                    return item;
                for (const child of item.children || [])
                    stack.push(child);
            }
            return null;
        }
        function test_review_reset_and_undo() {
            compare(JSON.stringify(dock.pinned), JSON.stringify(["kitty", "org.example.Custom"]));
            compare(service.night.warmth, 80);
            const card = findChild(page, "settings-reset");
            verify(card !== null && card.visible);
            const review = findChild(page, "settings-reset-review");
            verify(!review.visible);
            const start = button("settings-reset-start");
            verify(start !== null && start.enabled);
            compare(start.focusPolicy, Qt.StrongFocus);
            start.clicked();
            verify(review.visible);
            // The confirmation names what resets and says plainly what is never touched.
            const items = texts("settings-reset-item");
            compare(items.length, 8);
            verify(items.some(text => text.includes("Apps pinned to the dock")));
            const kept = texts("settings-reset-kept");
            compare(kept.length, 5);
            verify(kept.some(text => text.includes("Your files, notes, pictures and wallpapers")));
            verify(kept.some(text => text.includes("Settings inside your apps")));
            verify(kept.some(text => text.includes("Your own niri configuration")));
            button("settings-reset-cancel").clicked();
            verify(!review.visible);
            compare(resetter.state, "idle");
            start.clicked();
            verify(review.visible);
            button("settings-reset-confirm").clicked();
            verify(!review.visible);
            tryCompare(resetter, "state", "done", 15000);
            compare(resetter.message, "Emaki settings are back to their defaults.");
            // The running stores read their files again: defaults now.
            compare(JSON.stringify(dock.pinned), JSON.stringify(dock.defaultPins));
            compare(dock.pinsChosen, false);
            compare(service.night.warmth, 50);
            compare(notices.offered.length, 1);
            // The notice's action names its own backup.
            verify(resetter.backupId !== "");
            compare(notices.offered[0].action, "settings-reset-undo:" + resetter.backupId);
            compare(notices.offered[0].label, "Undo");
            compare(notices.offered[0].body, "Your files and app settings were not changed.");
            // The page offers Undo too while it stays open.
            const undo = button("settings-reset-undo");
            verify(undo !== null && undo.text === "Undo");
            // Undo from the notification.
            notices.localActionRequested(notices.offered[0].action);
            tryCompare(resetter, "state", "undone", 15000);
            compare(JSON.stringify(dock.pinned), JSON.stringify(["kitty", "org.example.Custom"]));
            compare(dock.pinsChosen, true);
            compare(service.night.warmth, 80);
            verify(button("settings-reset-undo") === null);

            // Two resets of different settings: the first backs up the person's pins, the
            // second the defaults. Starting the second retires the first notice.
            button("settings-reset-start").clicked();
            button("settings-reset-confirm").clicked();
            tryCompare(resetter, "state", "done", 15000);
            const first = notices.offered[1];
            const firstId = resetter.backupId;
            compare(first.action, "settings-reset-undo:" + firstId);
            compare(JSON.stringify(dock.pinned), JSON.stringify(dock.defaultPins));
            // The page offers Undo while a reset is undoable; the second reset comes from the
            // controller (the reset command started elsewhere).
            verify(button("settings-reset-start") === null);
            verify(resetter.start());
            tryCompare(resetter, "state", "done", 15000);
            const second = notices.offered[2];
            const secondId = resetter.backupId;
            verify(secondId !== firstId);
            compare(second.action, "settings-reset-undo:" + secondId);
            verify(notices.dismissed.includes(-1 - 2));

            // Undo of the second notice finds the settings busy: Undo stays, in a new notice
            // for the same backup and on the page, also on a page opened again.
            notices.localActionRequested(second.action);
            compare(resetter.undoingId, secondId);
            tryCompare(resetter, "state", "done", 15000);
            verify(resetter.undoable);
            compare(resetter.backupId, secondId);
            compare(resetter.message, "Your previous Emaki settings could not be restored. Try Undo again.");
            compare(notices.offered.length, 4);
            compare(notices.offered[3].action, "settings-reset-undo:" + secondId);
            compare(notices.offered[3].summary, "Your previous Emaki settings could not be restored. Try Undo again.");
            reopened.active = true;
            tryVerify(() => reopened.item !== null);
            const again = button("settings-reset-undo", reopened.item);
            verify(again !== null && again.visible && again.text === "Undo");
            compare(findChild(reopened.item, "settings-reset-emaki").explanation, "Your previous Emaki settings could not be restored. Try Undo again.");
            verify(button("settings-reset-undo") !== null);
            // The retry from the new notice restores the second backup: still the defaults.
            notices.localActionRequested(notices.offered[3].action);
            compare(resetter.undoingId, secondId);
            tryCompare(resetter, "state", "undone", 15000);
            compare(JSON.stringify(dock.pinned), JSON.stringify(dock.defaultPins));
            verify(button("settings-reset-undo", reopened.item) === null);
            // The first notice restores its own backup: the person's pins again.
            notices.localActionRequested(first.action);
            compare(resetter.undoingId, firstId);
            tryCompare(resetter, "state", "undone", 15000);
            compare(JSON.stringify(dock.pinned), JSON.stringify(["kitty", "org.example.Custom"]));
            compare(service.night.warmth, 80);
            reopened.active = false;

            // A refused reset changes nothing and says so.
            const mode = Quickshell.env("RESET_FIXTURE_REFUSE");
            if (mode === "3") {
                button("settings-reset-start").clicked();
                button("settings-reset-confirm").clicked();
                tryCompare(resetter, "state", "failed", 15000);
                compare(resetter.message, "Emaki settings could not be reset. Nothing was changed.");
            }
        }
        onCompletedChanged: if (completed)
            console.log("SETTINGS_RESET_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
    }
}
