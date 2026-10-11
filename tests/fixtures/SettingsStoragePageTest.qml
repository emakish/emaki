// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell

ShellRoot {
    SettingsStoragePage {
        id: page
        width: 780
    }
    TestCase {
        name: "SettingsStoragePage"
        when: true
        function test_preview_contract() {
            const mode = Quickshell.env("STORAGE_FIXTURE_MODE");
            if (mode === "missing") {
                tryCompare(page, "message", "[PLACEHOLDER] The storage helper could not be started.");
                compare(page.pending, false);
                return;
            }
            tryVerify(() => !page.pending && page.inventory.filesystems.length === 2);
            compare(findChild(page, "settings-storage-snapshots").controlItem, null);
            compare(page.inventory.filesystems[1].shared_with, "/");
            page.confirmPreview();
            page.requestPreview("snapshots");
            compare(page.pending, false);
            compare(page.inventory.categories.trash.bytes, 100);
            page.refresh(true);
            tryCompare(page, "pending", false);
            compare(page.inventory.categories.trash.bytes, 100);
            compare(page.inventory.categories.snapshots.bytes, 200);
            findChild(page, "settings-storage-trash").controlItem.clicked();
            page.requestPreview("cache"); // Duplicate input while pending is ignored.
            tryVerify(() => page.preview !== null);
            compare(page.preview.entries.length, 20000);
            compare(page.unsharedBytes(page.preview), "[PLACEHOLDER] At least 50 B; 1 files unknown");
            compare(page.unsharedBytes({
                reclaimable_bytes: 100
            }), "100 B");
            compare(page.unsharedBytes({
                reclaimable_bytes: null
            }), "[PLACEHOLDER] Unknown");
            const entries = findChild(page, "settings-storage-preview-entries");
            tryCompare(entries, "count", 20000);
            verify(entries.height <= 320 && entries.height > 0);
            verify(entries.contentItem.children.length < 100);
            entries.positionViewAtIndex(19999, ListView.End);
            tryVerify(() => entries.itemAtIndex(19999) !== null);
            compare(entries.itemAtIndex(19999).modelData.path, "/fixture/trash/19999");
            verify(entries.contentItem.children.length < 100);
            compare(page.preview.entries[1].path, "/fixture/trash/line\nbreak<literal>");
            findChild(page, "storage-cancel").clicked();
            compare(page.preview, null);
            page.confirmPreview();
            page.requestPreview("cache");
            tryVerify(() => page.preview !== null);
            findChild(page, "storage-confirm").clicked();
            page.confirmPreview();
            tryVerify(() => !page.pending && page.message.startsWith("[PLACEHOLDER] Fixture cleaned."));
            compare(page.preview, null);
            compare(page.inventory.filesystems.length, 2);
            compare(page.inventory.categories.trash.bytes, 99);
            page.requestPreview("logs");
            tryVerify(() => page.preview !== null);
            page.refresh(false);
            compare(page.preview, null);
            tryCompare(page, "pending", false);
            page.requestPreview("logs");
            tryVerify(() => page.preview !== null);
            page.confirmPreview(); // Backend rejects stale inventory.
            tryCompare(page, "message", "[PLACEHOLDER] Fixture stale preview.");
            compare(page.preview, null);
            page.requestPreview("logs");
            tryVerify(() => page.preview !== null);
            page.confirmPreview(); // A partial cleanup must retain the backend disclosure.
            tryCompare(page, "message", "[PLACEHOLDER] Cleanup stopped after removing 1 entries. Preview again.");
            compare(page.preview, null);
            page.requestPreview("trash"); // Malformed preview cannot enable deletion.
            tryCompare(page, "message", "[PLACEHOLDER] Storage could not be confirmed. Refresh and preview again.");
            compare(page.preview, null);
            page.requestPreview("cache"); // Authentication cancelled.
            tryCompare(page, "message", "[PLACEHOLDER] Authentication was cancelled. Preview again to continue.");
            compare(page.preview, null);
            page.requestPreview("trash");
            tryVerify(() => page.preview !== null);
            page.confirmPreview();
            tryVerify(() => !page.pending && page.message.startsWith("[PLACEHOLDER] Fixture cleaned."));
            compare(page.preview, null);
            compare(page.inventory.filesystems.length, 2);
            compare(page.inventory.categories.trash.bytes, 98);
            page.requestPreview("logs"); // Nonzero status without a valid error remains generic.
            tryCompare(page, "message", "[PLACEHOLDER] Storage could not be confirmed. Refresh and preview again.");
            compare(page.preview, null);
        }
        onCompletedChanged: if (completed)
            console.log("STORAGE_PAGE_RESULT " + qtest_results.passCount + " " + qtest_results.failCount)
    }
}
