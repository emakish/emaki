// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    NotificationStore {
        id: notes
    }
    SecureBootNotice {
        id: notice
        store: notes
        // Restoring history must finish before the session notice is posted.
        ready: notes.restored
    }
    // The retry modes run the notice's pacing fast: two quick tries, then 200 ms doubling up to
    // 400 ms. Set from script, so a notice without these settings keeps its own (and fails).
    Component.onCompleted: {
        if (!["flaky", "slow", "appears"].includes(Quickshell.env("SECUREBOOT_FIXTURE_MODE")))
            return;
        try {
            notice.attempts = 2;
            notice.retryInterval = 100;
            notice.slowInterval = 200;
            notice.slowestInterval = 400;
        } catch (_) {}
    }
    FloatingWindow {
        id: window
        visible: true
        implicitWidth: 740
        implicitHeight: 700
        Rectangle {
            id: canvas
            width: parent.width
            height: parent.height
            color: "#eee4db"
            SecureBootSettings {
                id: card
                width: parent.width - 40
                x: 20
                y: 20
            }
        }
    }
    // Counts the card's finished status reads (each one assigns a new status object).
    QtObject {
        id: statusReads
        property int count: 0
    }
    Connections {
        target: card
        function onStatusChanged(): void {
            statusReads.count += 1;
        }
    }
    SignalSpy {
        id: settingsSpy
        target: notice
        signalName: "settingsRequested"
    }
    TestCase {
        name: "SecureBootSettings"
        when: window.backingWindowVisible
        // After a privileged attempt the card reads the status again; the case ends only once
        // that read has finished, so the command log always holds it.
        function refreshed(before: int): void {
            tryVerify(() => statusReads.count > before, 10000);
            tryCompare(card, "busy", false);
        }
        function test_secure_boot() {
            const mode = Quickshell.env("SECUREBOOT_FIXTURE_MODE");
            const confirm = findChild(card, "settings-secure-boot-confirm");
            const passwordBox = findChild(card, "settings-secure-boot-password");
            const state = findChild(card, "settings-secure-boot-state");
            const signRow = findChild(card, "settings-secure-boot-sign");
            if (["flaky", "slow", "appears"].includes(mode)) {
                // A failed, malformed or not yet known answer is asked again until it is known:
                // past the quick tries, at the slower pace.
                tryVerify(() => notes.entries.length === 1, 10000);
                compare(notes.entries[0].summary, "Finish setting up Secure Boot.");
                verify(notice.asked >= 3, "asked " + notice.asked);
                if (mode === "slow")
                    verify(notice.asked > notice.attempts, "asked " + notice.asked);
                const asked = notice.asked;
                wait(600);
                compare(notice.asked, asked);
                compare(notes.entries.length, 1);
                return;
            }
            if (mode === "late") {
                // The session started before root wrote its copy: the first answer knows no key,
                // the notice asks again and offers its notice once the copy is there.
                // entries is replaced on every change, so the property is read anew each time.
                tryVerify(() => notes.entries.length === 1, 15000);
                compare(notes.entries[0].summary, "Finish setting up Secure Boot.");
                compare(notice.asked, 2);
                wait(300);
                compare(notes.entries.length, 1);
                compare(notice.asked, 2);
                return;
            }
            if (mode === "no-key" || mode === "unknown") {
                wait(600);
                compare(card.visible, false);
                compare(notes.entries.length, 0);
                return;
            }
            tryCompare(card, "visible", true);
            if (mode.startsWith("unsigned-")) {
                // Unsigned startup files: their notice comes first, whatever the firmware switch
                // says and even when the key is not confirmed either; it is the only one.
                tryVerify(() => notes.entries.length === 1);
                const unsignedEntry = notes.entries[0];
                compare(unsignedEntry.summary, "Startup files are not signed.");
                compare(unsignedEntry.body, "An update could not sign them, so Emaki will not start with Secure Boot on. Free some disk space, then open Settings and choose Sign again.");
                compare(unsignedEntry.actions[0].id, "settings:privacy");
                compare(unsignedEntry.actions[0].text, "Open Settings");
                verify(notes.activate(unsignedEntry.id, "settings:privacy"));
                compare(settingsSpy.count, 1);
                const left = notes.entries.length;
                tryCompare(signRow, "visible", true);
                compare(signRow.title, "Sign the startup files");
                compare(state.explanation, "Some startup files are not signed, so Emaki will not start with Secure Boot on. Free some disk space, then choose Sign again.");
                const button = findChild(card, "settings-secure-boot-sign-button");
                compare(button.text, "Sign again");
                const signReads = statusReads.count;
                button.clicked();
                if (mode === "unsigned-fail") {
                    tryCompare(card, "signMessage", "The startup files could not be signed. Free some disk space and try again.");
                    refreshed(signReads);
                    verify(signRow.visible);
                    return;
                }
                if (mode === "unsigned-cancel") {
                    tryCompare(card, "signMessage", "Authentication was cancelled.");
                    refreshed(signReads);
                    verify(signRow.visible);
                    return;
                }
                // Signed again: the page reads the status once more and the row goes away.
                tryCompare(signRow, "visible", false);
                compare(card.signMessage, "");
                verify(!state.explanation.includes("not signed"));
                // Still once per session: reading the status again offers nothing new.
                wait(200);
                compare(notes.entries.length, left);
                return;
            }
            compare(signRow.visible, false);
            if (mode === "enrolled-on" || mode === "signed-on") {
                wait(300);
                verify(state.explanation.startsWith("Secure Boot is on."));
                compare(confirm.visible, false);
                compare(signRow.visible, false);
                compare(notes.entries.length, 0);
                return;
            }
            if (mode === "missing-on") {
                // Settings still offers it; the session notice is only for Secure Boot off.
                verify(confirm.visible);
                wait(300);
                compare(notes.entries.length, 0);
                return;
            }
            // missing with Secure Boot off: the notice points to Settings.
            tryVerify(() => notes.entries.length === 1);
            const entry = notes.entries[0];
            compare(entry.summary, "Finish setting up Secure Boot.");
            compare(entry.actions[0].id, "settings:privacy");
            verify(notes.activate(entry.id, "settings:privacy"));
            compare(settingsSpy.count, 1);
            verify(state.explanation.includes("not confirmed yet"));
            verify(confirm.visible);
            compare(passwordBox.visible, false);
            const enrollReads = statusReads.count;
            findChild(card, "settings-secure-boot-enroll").clicked();
            if (mode === "cancel") {
                tryCompare(card, "message", "Authentication was cancelled.");
                refreshed(enrollReads);
                compare(card.password, "");
                verify(confirm.visible);
                return;
            }
            if (mode === "broken") {
                tryCompare(card, "message", "A new confirmation could not be set up. Try again.");
                refreshed(enrollReads);
                compare(card.password, "");
                return;
            }
            tryCompare(card, "password", "40213377");
            verify(passwordBox.visible);
            compare(findChild(card, "settings-secure-boot-password-digits").text, "40213377");
            compare(confirm.visible, false);
            tryVerify(() => state.explanation === "A confirmation is waiting for the next restart.");
            if (Quickshell.env("SECUREBOOT_SETTINGS_SHOT")) {
                wait(100);
                let saved = false;
                canvas.grabToImage(result => saved = result.saveToFile(Quickshell.env("SECUREBOOT_SETTINGS_SHOT")));
                tryVerify(() => saved, 3000);
            }
        }
        onCompletedChanged: if (completed)
            console.log("SECUREBOOT_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
    }
}
