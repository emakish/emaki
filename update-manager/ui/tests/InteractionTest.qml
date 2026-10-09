// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls as C
import QtTest
import Quickshell
import ".." as UI

ShellRoot {
    id: test
    property bool failed: false
    property int checks: 0
    property int applies: 0
    property int restarts: 0
    property int hides: 0
    property string opened: ""
    QtObject {
        id: controller
        property var data: ({updates: [{source: "Arch core", name: "linux", old: "6.1-1", new: "6.2-1", downloadSize: 100, aur: false}], groups: [{name: "Arch core", count: 1}], warnings: Array.from({length: 24}, (_, index) => "Review change " + index + "."), news: [{title: "Manual intervention required", link: "https://archlinux.org/news/example/"}], downloadSize: 100})
        property bool busy: false
        property string phase: "idle"
        property int repositoryCount: 1
        property bool restart: false
        property bool signOut: false
        property string message: ""
        property string output: ""
        function check(): void { test.checks++; }
        function apply(): void { test.applies++; busy = true; phase = "applying"; }
        function reboot(): void { test.restarts++; }
    }
    function find(item: Item, name: string): Item {
        if (item.objectName === name) return item;
        for (const child of item.children) {
            const result = find(child, name);
            if (result) return result;
        }
        return null;
    }
    function check(ok: bool, message: string): void {
        if (!ok) { failed = true; console.error("ASSERTION_FAILED " + message); Qt.quit(); }
    }
    function tab(name: string): void {
        const item = find(view, name);
        check(!!item, "control exists " + name);
        let count = 0;
        do { input.keyClick(Qt.Key_Tab); count++; } while (!item.activeFocus && count < 40);
        check(item.activeFocus, "Tab reaches " + name);
        input.wait(30);
        if (name === "news0") {
            const viewport = (find(view, "updateBody") as C.ScrollView).contentItem;
            const rect = item.mapToItem(viewport, 0, 0, item.width, item.height);
            check(rect.y >= 0 && rect.y + rect.height <= viewport.height + 1, "news focus scrolls into view");
        }
    }
    FloatingWindow {
        implicitWidth: Number(Quickshell.env("TEST_WIDTH") || 860)
        implicitHeight: Number(Quickshell.env("TEST_HEIGHT") || 680)
        UI.UpdateView {
            id: view
            anchors.fill: parent
            controller: controller
            onHideRequested: test.hides++
            onOpenNews: link => test.opened = link
        }
        TestCase { id: input; when: false; name: "UpdateKeyboard" }
    }
    Timer {
        running: true
        interval: 250
        onTriggered: {
            const scroll = test.find(view, "updateBody") as C.ScrollView;
            const flick = scroll.contentItem as Flickable;
            scroll.forceActiveFocus();
            input.keyClick(Qt.Key_End); input.wait(30);
            test.check(flick.contentY > 0 && Math.abs(flick.contentY - (flick.contentHeight - flick.height)) < 2, "End reaches bottom");
            input.keyClick(Qt.Key_Home); input.wait(30);
            test.check(flick.contentY === 0, "Home reaches top");
            input.keyClick(Qt.Key_Down); input.wait(30);
            test.check(flick.contentY > 0, "Down scrolls plain package rows");
            input.keyClick(Qt.Key_Up); input.wait(30);
            test.check(flick.contentY === 0, "Up scrolls back");
            input.keyClick(Qt.Key_PageDown); input.wait(30);
            test.check(flick.contentY > 100, "PageDown scrolls a page");
            input.keyClick(Qt.Key_PageUp); input.wait(30);
            test.check(flick.contentY === 0, "PageUp scrolls back");
            test.tab("news0"); input.keyClick(Qt.Key_Return);
            test.check(test.opened === "https://archlinux.org/news/example/", "news keyboard activation");
            view.scrollTo(100);
            const newsPosition = flick.contentY;
            test.tab("check");
            test.check(flick.contentY === newsPosition, "footer focus does not scroll the body");
            input.keyClick(Qt.Key_Enter);
            test.check(test.checks === 1, "check keyboard activation");
            test.tab("apply"); input.keyClick(Qt.Key_Return);
            test.check(test.applies === 1 && controller.busy, "apply keyboard activation");
            test.check(!test.find(view, "apply").enabled && !test.find(view, "check").enabled, "busy blocks another operation");
            test.check(!test.find(view, "updateCatalog").visible, "progress replaces the package list");
            input.wait(30);
            input.keyClick(Qt.Key_Escape);
            test.check(test.hides === 0, "Escape cannot close during upgrade");
            controller.output = Array(150).fill("Package progress.").join("\n"); input.wait(100);
            test.check(Math.abs(flick.contentY - (flick.contentHeight - flick.height)) < 2, "progress follows newest output");
            scroll.forceActiveFocus();
            input.keyClick(Qt.Key_PageUp); input.wait(30);
            test.check(!view.followTail, "PageUp pauses following");
            const heldPosition = flick.contentY;
            controller.output += "\nNewest output."; input.wait(100);
            test.check(flick.contentY === heldPosition, "scrolling up pauses tail follow");
            input.keyClick(Qt.Key_End); input.wait(30);
            controller.output += "\nAnother output."; input.wait(100);
            test.check(Math.abs(flick.contentY - (flick.contentHeight - flick.height)) < 2, "End resumes tail follow");
            test.tab("hide"); input.keyClick(Qt.Key_Space);
            test.check(test.hides === 1 && controller.busy, "hide preserves running update");
            controller.busy = false; controller.phase = "finished"; controller.restart = true;
            controller.message = "Update complete."; controller.signOut = true;
            input.wait(30);
            test.check(!test.find(view, "updateCatalog").visible, "result replaces the package list");
            const viewport = (test.find(view, "updateBody") as C.ScrollView).contentItem;
            const result = test.find(view, "resultMessage");
            const resultRect = result.mapToItem(viewport, 0, 0, result.width, result.height);
            test.check(result.visible && resultRect.y >= 0 && resultRect.y + resultRect.height <= viewport.height, "result is immediately visible");
            scroll.forceActiveFocus(); input.keyClick(Qt.Key_End); input.wait(30);
            view.present(); input.wait(30);
            test.check(flick.contentY === 0, "reopening finished result reveals its message");
            test.tab("restart"); input.keyClick(Qt.Key_Space);
            test.check(test.restarts === 1, "restart keyboard activation");
            test.tab("hide"); input.keyClick(Qt.Key_Space);
            test.check(test.hides === 2 && test.find(view, "hide").text === "Later", "later keyboard activation");
            input.keyClick(Qt.Key_Escape);
            test.check(test.hides === 3, "Escape closes when idle");
            controller.output = ""; controller.message = "Could not check updates.";
            controller.phase = "idle";
            controller.data = {updates: [], groups: [], news: [], warnings: [], error: "Could not check updates.", errorDetails: "Network detail."};
            input.wait(50);
            test.check(!test.find(view, "errorDetails").visible, "error details start folded");
            test.tab("detailsToggle"); input.keyClick(Qt.Key_Return);
            test.check(test.find(view, "errorDetails").visible, "keyboard expands error details");
            test.tab("hide"); input.wait(30);
            test.check(flick.contentY >= 0, "short body never scrolls negative");
            controller.repositoryCount = 0;
            test.check(!test.find(view, "apply").enabled, "AUR-only cannot apply");
            if (!test.failed) console.log("INTERACTION_OK");
            Qt.quit();
        }
    }
}
