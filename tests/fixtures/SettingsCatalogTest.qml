// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell
import "../../shell" as Shell

ShellRoot {
    Shell.SettingsCatalog {
        id: catalog
        active: false
        property int commits: 0
        onApplied: commits++
    }
    TestCase {
        name: "SettingsCatalog"
        when: true
        onCompletedChanged: if (completed)
            console.log("SETTINGS_CATALOG_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
        function equal(actual, expected) {
            if (actual !== expected)
                console.error("Compare " + actual + " expected " + expected + " " + new Error().stack);
            compare(actual, expected);
        }
        function settled(count) {
            let ready = false;
            for (let i = 0; i < 300; i++) {
                if (!catalog.busy && catalog.history.length === count) {
                    ready = true;
                    break;
                }
                wait(100);
            }
            if (!ready)
                console.error(JSON.stringify({
                    count: count,
                    busy: catalog.busy,
                    history: catalog.history,
                    state: catalog.state,
                    status: catalog.lastStatus,
                    action: catalog.currentAction,
                    queue: catalog.queue
                }));
            verify(ready);
            equal(catalog.lastStatus, "committed");
            equal(catalog.sessionApplied, false);
            equal(catalog.commits, count);
        }
        function test_durable_transactions() {
            tryVerify(() => !catalog.busy && catalog.row("bar.autohide") !== null, 15000);
            equal(catalog.value("bar.autohide"), false);
            equal(catalog.row("bar.autohide").override_value, null);
            catalog.send("list", []);
            verify(catalog.busy);
            verify(!catalog.writing);
            catalog.set("bar.autohide", "true");
            verify(catalog.writing);
            settled(1);
            verify(!catalog.writing);
            equal(catalog.value("bar.autohide"), true);
            equal(catalog.row("bar.autohide").override_value, true);
            catalog.send("list", []);
            tryVerify(() => !catalog.busy, 15000);
            equal(catalog.history.length, 1);
            equal(catalog.lastStatus, "committed");
            catalog.reset("bar.autohide");
            settled(2);
            equal(catalog.value("bar.autohide"), false);
            equal(catalog.row("bar.autohide").override_value, null);
            const reset = catalog.history.find(entry => entry.changes.some(change => change.key === "bar.autohide" && change.before === true && change.after === null));
            verify(reset !== undefined);
            catalog.undo(reset.id);
            settled(3);
            equal(catalog.value("bar.autohide"), true);
            // Queue both commands before the first helper can finish. The reset
            // must operate on the newly committed override, even at its default.
            catalog.set("bar.autohide", "false");
            catalog.reset("bar.autohide");
            settled(5);
            equal(catalog.value("bar.autohide"), false);
            equal(catalog.row("bar.autohide").override_value, null);
            catalog.reset("bar.autohide");
            tryVerify(() => !catalog.busy && catalog.lastStatus === "unchanged", 30000);
            equal(catalog.commits, 5);
            equal(catalog.history.length, 5);
            equal(catalog.sessionApplied, false);
        }
    }
}
