// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    QtObject {
        id: fakeBackend
        property bool connected: true
        property var keys: [
            {
                id: "7",
                label: "Fixture app",
                kind: "time",
                remainingSeconds: 60
            }
        ]
        property string revoked: ""
        property bool fail: false
        function revoke(id) {
            revoked = id;
            if (!fail)
                keys = [];
        }
    }
    QtObject {
        id: support
        property bool installed: true
        property var backend: fakeBackend
    }
    FloatingWindow {
        id: window
        visible: true
        implicitWidth: 740
        implicitHeight: 1000
        Rectangle {
            id: canvas
            width: parent.width
            height: page.height + 40
            color: "#eee4db"
            SettingsPrivacyPage {
                id: page
                width: parent.width - 40
                x: 20
                y: 20
                sudoSupport: support
            }
        }
    }
    SignalSpy {
        id: lockSpy
        target: page
        signalName: "lockRequested"
    }
    TestCase {
        name: "SettingsPrivacyPage"
        when: window.backingWindowVisible
        function test_privacy() {
            const mode = Quickshell.env("PRIVACY_FIXTURE_MODE");
            tryCompare(page, "pending", false);
            if (mode === "revoke-failed") {
                compare(page.available, true);
                findChild(page, "privacy-portal-revoke").clicked();
                tryCompare(page, "pending", false);
                compare(page.portals.rows.length, 1);
                verify(page.message.includes("could not be confirmed"));
                return;
            }
            if (mode !== "ready") {
                compare(page.available, false);
                verify(page.message.indexOf("could not be confirmed") >= 0);
                compare(Object.keys(page.snapshot).length, 0);
                findChild(page, "privacy-refresh").clicked();
                tryCompare(page, "pending", false);
                compare(page.available, false);
                return;
            }
            compare(page.available, true);
            if (Quickshell.env("PRIVACY_SETTINGS_SHOT")) {
                wait(100);
                let saved = false;
                canvas.grabToImage(result => saved = result.saveToFile(Quickshell.env("PRIVACY_SETTINGS_SHOT")));
                tryVerify(() => saved, 3000);
            }
            // A stale or forged row must not inherit a matching grant's authority.
            const grant = page.portals.rows[0];
            const originalSnapshot = page.snapshot;
            const originalMessage = page.message;
            for (const permission of [false, undefined, "true", 1]) {
                const readOnlyRow = Object.assign({}, grant, {
                    can_revoke: permission
                });
                page.revokePortal(readOnlyRow);
                compare(page.pending, false);
                compare(page.snapshot, originalSnapshot);
                compare(page.message, originalMessage);
            }
            const unknownRow = Object.assign({}, grant, {
                app: "org.example.Unknown"
            });
            page.revokePortal(unknownRow);
            compare(page.pending, false);
            compare(page.snapshot, originalSnapshot);
            verify(page.snapshotText().includes("42"));
            verify(page.menuText().includes("bootability has not been tested"));
            verify(page.lockText().includes("300"));
            verify(page.encryptionText(page.snapshot.encryption.state).includes("Encrypted"));
            findChild(page, "privacy-lock").clicked();
            compare(lockSpy.count, 1);
            compare(fakeBackend.revoked, "");
            const key = findChild(page, "sudo-key-revoke-7");
            verify(key !== null);
            key.clicked();
            tryCompare(fakeBackend, "revoked", "7");
            tryCompare(page, "pendingKey", "");
            verify(page.sudoMessage.includes("revoked"));
            fakeBackend.keys = [
                {
                    id: "8",
                    label: "Other app",
                    kind: "time",
                    remainingSeconds: 60
                }
            ];
            fakeBackend.fail = true;
            tryVerify(() => findChild(page, "sudo-key-revoke-8") !== null);
            findChild(page, "sudo-key-revoke-8").clicked();
            tryCompare(page, "pendingKey", "", 6500);
            verify(page.sudoMessage.includes("could not be confirmed"));
            findChild(page, "privacy-portal-revoke").clicked();
            tryCompare(page, "pending", false);
            compare(page.portals.rows.length, 0);
            verify(page.message.includes("revoked"));
            let next = JSON.parse(JSON.stringify(page.snapshot));
            next.lock.delay = 0;
            next.recovery.snapshot.state = "none";
            next.recovery.menu.state = "missing";
            next.portals.state = "partial";
            page.snapshot = next;
            verify(page.lockText().includes("disabled"));
            verify(page.snapshotText().includes("No pre-update"));
            verify(page.menuText().includes("missing"));
            next = JSON.parse(JSON.stringify(page.snapshot));
            next.lock.state = "unavailable";
            next.recovery.snapshot.state = "unavailable";
            page.snapshot = next;
            compare(page.lockText(), "Unavailable");
            compare(page.snapshotText(), "Unavailable");
            support.installed = false;
            fakeBackend.connected = false;
            tryVerify(() => findChild(page, "sudo-key-revoke-8") === null);
            page.refresh();
            tryCompare(page, "pending", false);
            verify(page.available);
        }
        onCompletedChanged: if (completed)
            console.log("PRIVACY_PAGE_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
    }
}
