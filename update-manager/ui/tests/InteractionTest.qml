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
    UI.UpdateController { id: updates }
    UI.ProgramsController { id: programs; externalBusy: updates.busy || updates.restorePending }
    function find(item, predicate) {
        if (predicate(item)) return item;
        for (const child of item.children || []) { const result = find(child, predicate); if (result) return result; }
        return null;
    }
    function named(name) { return find(view, item => item.objectName === name); }
    function button(text) { return find(view.C.Overlay.overlay, item => item.visible && item.text === text && typeof item.clicked === "function") || find(view, item => item.visible && item.text === text && typeof item.clicked === "function"); }
    function label(text) { return find(view.C.Overlay.overlay, item => item.visible && item.text === text) || find(view, item => item.visible && item.text === text); }
    function check(ok, message) { if (!ok) { failed = true; console.error("ASSERTION_FAILED " + message); Qt.quit(); throw new Error(message); } }
    function tab(item) {
        check(!!item, "control exists");
        if (!item) return;
        let count = 0;
        while (!item.activeFocus && count++ < 100) input.keyClick(Qt.Key_Tab);
        check(item.activeFocus, "Tab reaches " + item.text);
    }
    function activate(item) { tab(item); input.keyClick(Qt.Key_Return); input.wait(50); }
    function shot(name) {
        if (!Quickshell.env("TEST_SHOTS")) return;
        if (name === "updates-bottom" || name === "restore-picker") { const flick = named("managerBody").contentItem; flick.contentY = Math.max(0, flick.contentHeight - flick.height); } else view.resetScroll(); window.visible = false; input.wait(30); window.visible = true; view.opacity = 0; input.wait(30); view.opacity = 1; input.wait(80);
        const header = named("tab-browse");
        check(header.mapToItem(window.contentItem, 0, 0).y >= 0, "header stays visible on " + name);
        const image = input.grabImage(window.contentItem);
        image.save(Quickshell.env("TEST_SHOTS") + "/" + name + "-" + window.width + ".png");
        check(image.width === window.width && image.height === window.height, "full window screenshot " + name);
    }
    FloatingWindow {
        id: window
        implicitWidth: Number(Quickshell.env("TEST_WIDTH"))
        implicitHeight: Number(Quickshell.env("TEST_HEIGHT"))
        UI.ManagerView { id: view; anchors.fill: parent; controller: updates; programs: programs; brandIconSource: Quickshell.env("TEST_ICON") }
        TestCase { id: input; when: false; name: "ManagerInteraction" }
    }
    Timer {
        running: true; interval: 300
        onTriggered: {
            test.check(view.page === "browse" && view.featured.length === 3, "three catalog picks on Browse");
            test.check(view.shelf("Graphics")[0].package === "krita", "Browse shelf uses approved program order");
            test.check(!!test.label("GIMP"), "Graphics shelf uses the approved short GIMP name");
            test.shot("browse");
            test.check(programs.scope === "programs", "Programs is the default search scope");
            test.activate(test.named("scope-packages"));
            test.check(view.packagesPage && test.named("program-search").placeholderText === "Search all packages", "package scope has a distinct list and search prompt");
            const list = test.named("package-list");
            test.check(list.count === 1003 && list.contentItem.children.length < 100, "full package inventory uses virtual rows");
            const firstPackage = test.named("package-row-aom");
            const packageLeft = firstPackage.mapToItem(view, 0, 0).x;
            test.check(Math.abs(packageLeft - (view.width - firstPackage.width) / 2) < 2, "package rows stay centered at each window width");
            test.check(!!test.label("aom") && !!test.label("Installed") && !!test.label("3.8"), "terminal-installed package has installed state and version");
            test.check(!!test.label("1003 packages"), "multiple package results use the plural label");
            test.tab(test.named("program-search"));
            for (const key of [Qt.Key_A, Qt.Key_O, Qt.Key_M]) input.keyClick(key);
            input.wait(50);
            test.check(list.count === 1 && !!test.label("1 package"), "one package result uses the singular label");
            test.shot("all-packages-single");
            input.keyClick(Qt.Key_Z); input.wait(50);
            test.check(list.count === 0 && !!test.label("0 packages"), "zero package results use the plural label");
            input.keyClick(Qt.Key_A, Qt.ControlModifier); input.keyClick(Qt.Key_Backspace); input.wait(50);
            test.check(list.count === 1003 && !!test.label("1003 packages"), "clearing search restores the plural count");
            test.shot("all-packages");
            test.activate(test.button("Remove"));
            test.check(programs.previewOpen && programs.selectedEdition.key === "package:extra/aom" && programs.executions === 0, "package removal previews its exact engine identity");
            test.activate(test.button("Cancel"));
            test.activate(test.button("Install"));
            test.check(programs.previewOpen && programs.selectedEdition.key === "package:extra/gcc" && programs.executions === 0, "package installation previews before execution");
            test.activate(test.button("Cancel"));
            const packageProgram = test.named("package-row-krita");
            test.activate(test.find(packageProgram, item => typeof item.clicked === "function"));
            test.check(view.page === "program" && programs.selectedEdition.key === "arch:krita", "package with metadata opens its program page");
            programs.selectedEdition = programs.packageInventory[0];
            programs.selectedProgram = Object.assign({}, programs.selectedEdition, {editions:[programs.selectedEdition]});
            input.wait(30);
            test.check(!test.button("Open"), "installed package without a launcher has no Open action");
            test.check(!!test.button("Remove") && test.button("Remove").enabled && !!test.label("Installed · version 3.8"), "launcher-less installed package retains removal and version");
            test.shot("package-installed-no-launcher");
            programs.selectedEdition = Object.assign({}, programs.selectedEdition, {ids:["missing.desktop", "org.kde.dolphin.desktop"]});
            input.wait(30);
            test.check(!!test.button("Open"), "installed package with a resolved launcher offers Open");
            const opened = programs.opens;
            test.activate(test.button("Open"));
            test.check(programs.opens === opened + 1, "package Open reaches the launch action");
            programs.launchEntries = [];
            input.wait(30);
            test.check(!test.button("Open"), "Open disappears when the package launcher is removed");
            programs.launchEntries = [{id:"org.kde.dolphin",icon:"system-file-manager"}];
            test.activate(test.named("scope-programs"));
            test.check(view.page === "browse" && !view.packagesPage, "switching back restores program Browse");
            test.activate(test.named("tab-installed"));
            test.check(view.page === "installed", "keyboard Installed tab");
            test.shot("installed");
            test.activate(test.button("Remove"));
            test.check(programs.previewOpen && programs.executions === 0, "Remove opens preview before mutation");
            test.check(!!test.label("unused-library") && !!test.label("nothing else needs it"), "complete removal dependency preview always shown");
            test.shot("remove-preview");
            test.activate(test.button("Cancel"));
            test.check(!programs.previewOpen && programs.executions === 0, "cancel preserves program");
            view.showProgram(programs.allRows[4]); input.wait(50); test.shot("program-installed");
            view.showProgram(programs.allPrograms.find(row => row.package === "krita")); input.wait(50);
            test.check(programs.selectedEdition.kind === "arch", "opening source identity defaults Arch first");
            test.shot("program");
            test.check(!!test.find(view, item => String(item.source || "").endsWith("program-screen.svg") && item.status === Image.Ready), "catalog screenshot image loaded");
            programs.mutating = true; programs.busy = true; programs.phase = "installing";
            input.wait(30); test.shot("program-installing");
            programs.mutating = false; programs.busy = false; programs.phase = "idle";
            test.activate(test.button("Flathub"));
            test.check(programs.selectedEdition.kind === "flatpak", "edition switch uses Flathub");
            test.activate(test.button("Arch"));
            test.activate(test.button("show what changes"));
            test.check(programs.previewOpen && !!test.label("linux"), "Arch preview includes full upgrade");
            test.check(!!test.label("These 2 packages change:") && !!test.label("the program") && !!test.label("3 MB to download in total"), "install preview counts full transaction, marks target and sums download");
            const targetLabel = test.label("krita");
            const dependencyLabel = test.label("linux");
            test.check(targetLabel.mapToItem(window.contentItem, 0, 0).y < dependencyLabel.mapToItem(window.contentItem, 0, 0).y, "install target appears before dependencies");
            test.check(!!test.label("5.2"), "install target retains its planned version");
            test.shot("install-preview");
            test.activate(test.button("Cancel"));
            programs.message = "The program was installed."; programs.messageIsError = false;
            test.check(String(test.label(programs.message).color) === "#426448", "successful messages use success color");
            view.navigate("browse"); input.wait(30);
            test.check(programs.message === "", "navigation clears completed transaction message");
            programs.message = "Installation failed."; programs.messageIsError = true;
            view.navigate("installed"); input.wait(30);
            test.check(programs.message === "", "navigation clears transaction error");
            view.navigate("browse");
            test.tab(test.named("program-search"));
            for (const key of [Qt.Key_C, Qt.Key_O, Qt.Key_D, Qt.Key_E]) input.keyClick(key); input.wait(50);
            test.check(view.page === "search" && view.regularResults.length === 1 && view.aurResults.length === 1, "search separates AUR results");
            test.check(!!test.label("1 program from Arch and Flathub, 1 from AUR"), "singular search result");
            test.shot("search");
            view.showProgram(programs.allRows[6]); input.wait(30); test.shot("aur-program");
            test.check(!test.button("Install"), "AUR page has no install action");
            programs.selectedEdition = Object.assign({}, programs.selectedEdition, {installed:true,actions:["remove"]});
            input.wait(30);
            test.check(!!test.button("Remove") && test.button("Remove").enabled, "installed AUR page exposes authorized removal");
            test.check(!!test.button("Open"), "installed AUR page can open its launcher");
            view.navigate("search"); view.query = "code"; programs.search("code");
            input.keyClick(Qt.Key_Escape); input.wait(30);
            test.check(view.page === "browse", "Escape clears search");
            view.showCategory(view.categoryRows[2]); input.wait(30); test.shot("category");
            test.activate(test.named("tab-updates"));
            test.check(view.page === "updates" && !!test.label("Updated in the terminal"), "updates show AUR terminal group");
            const restorePage = test.find(view, item => typeof item.restoreTitle === "function");
            const initialPoints = updates.restorePoints;
            updates.restorePoints = [{id:"43:automatic",timestamp:"2026-10-12T12:00:00Z"}].concat(initialPoints);
            input.wait(30);
            test.check(restorePage.point.id === "43:automatic", "automatic restore default follows newest point without a user choice");
            updates.restorePoints = initialPoints;
            updates.restorePoints = [updates.restorePoints[0], {id:"41:older",timestamp:"2026-10-10T12:00:00Z"}];
            input.wait(30);
            const picker = test.find(view, item => item.displayText === "2 dates");
            test.check(!!picker && String(picker.background.color) === "#fff9f5", "restore picker uses manager surface");
            picker.popup.open(); input.wait(30);
            test.check(String(picker.popup.background.color) === "#fff9f5", "restore popup uses manager surface");
            test.shot("restore-picker");
            picker.popup.close();
            picker.activated(1);
            const chosenId = updates.restorePoints[1].id;
            updates.restorePoints = [{id:"43:newest",timestamp:"2026-10-12T12:00:00Z"}].concat(updates.restorePoints);
            input.wait(30);
            test.check(restorePage.point.id === chosenId, "restore selection retains receipt identity when newer point arrives");
            picker.popup.open(); input.wait(30);
            const offeredDates = picker.model.slice();
            updates.restorePoints = [{id:"44:newer",timestamp:"2026-10-13T12:00:00Z"}].concat(updates.restorePoints);
            input.wait(30);
            test.check(JSON.stringify(picker.model) === JSON.stringify(offeredDates), "restore popup freezes its offered dates during refresh");
            picker.activated(1); picker.popup.close(); input.wait(30);
            test.check(restorePage.point.id === "42:digest", "restore activation resolves the date offered before refresh");
            updates.restorePoints = updates.restorePoints.filter(point => point.id !== "42:digest");
            input.wait(30);
            test.check(restorePage.point.id === "44:newer", "missing explicit restore selection falls back to newest point");
            updates.restorePoints = [{id:"45:latest",timestamp:"2026-10-14T12:00:00Z"}].concat(updates.restorePoints);
            input.wait(30);
            test.check(restorePage.point.id === "45:latest", "fallback resumes following newest point");
            picker.popup.open(); input.wait(30);
            const automaticDates = picker.model.slice();
            updates.restorePoints = [{id:"46:latest",timestamp:"2026-10-15T12:00:00Z"}].concat(updates.restorePoints);
            input.wait(30);
            test.check(restorePage.point.id === "45:latest" && JSON.stringify(picker.model) === JSON.stringify(automaticDates), "open picker freezes automatic selection and offered dates");
            picker.popup.close(); input.wait(30);
            test.check(restorePage.point.id === "46:latest", "closing picker without a choice resumes newest default");
            const updatePage = test.find(view, item => typeof item.catalogProgram === "function");
            const oldCatalog = programs.allRows;
            programs.allRows = [
                {key:"arch:dolphin",kind:"arch",id:"org.kde.dolphin.desktop",package:"dolphin",icons:[]},
                {key:"arch:dolphin",kind:"arch",id:"org.kde.dolphin.desktop",package:"dolphin",icons:[{kind:"stock",name:"system-file-manager"}]}
            ];
            const dolphin = updatePage.catalogProgram({name:"dolphin",source:"Arch"});
            test.check(dolphin.icons.length === 1, "update row combines duplicate catalog icons");
            programs.allRows = [programs.allRows[0]];
            test.check(updatePage.catalogProgram({name:"dolphin",source:"Arch"}).icon === "system-file-manager", "update row uses launcher icon fallback");
            programs.allRows = oldCatalog;
            test.shot("updates"); test.shot("updates-bottom");
            test.activate(test.button("Check again"));
            test.check(updates.checks === 1, "check action wired");
            programs.mutating = true; input.wait(30);
            test.check(!test.button("Update all").enabled, "program transaction excludes update mutation");
            programs.mutating = false;
            test.activate(test.button("Update all"));
            test.check(updates.applies === 1 && updates.busy, "update action starts transaction");
            test.shot("updating");
            test.check(!test.button("Restore…").enabled, "update blocks restore");
            updates.completedUpdates = updates.data.updates.filter(row => !row.aur);
            updates.completedAt = new Date().toISOString();
            updates.data = {updates: updates.data.updates.filter(row => row.aur), news: [], warnings: []};
            updates.busy = false; updates.phase = "finished"; updates.succeeded = true; updates.restart = true; updates.reminder = true;
            input.wait(30);
            test.check(!!test.label("Linux kernel") && !!test.label("✓"), "completed managed updates retain rows and ticks");
            test.check(!!test.find(view, item => item.text && item.text.startsWith("3 updates installed today at")), "completion summary retains count and time");
            const completedNow = updates.completedAt;
            updates.completedAt = "2020-01-02T12:00:00Z";
            test.check(updatePage.completedSummary().startsWith("3 updates installed on "), "older completion summary includes on");
            updates.completedAt = completedNow;
            test.shot("restart");
            test.activate(test.button("Restart now"));
            test.check(updates.reboots === 0 && !!test.label("Restart now?"), "restart banner opens confirmation without restarting");
            test.activate(test.button("Cancel"));
            test.check(updates.reboots === 0, "cancel does not restart");
            test.activate(test.button("Restart now"));
            test.activate(test.button("Restart now"));
            test.check(updates.reboots === 1, "confirmed restart reaches controller");
            test.activate(test.button("Later"));
            test.check(updates.dismissals === 1 && !updates.reminder && window.visible && view.page === "updates", "Later dismisses reminder without closing manager");
            updates.phase = "idle";
            test.activate(test.button("Restore…"));
            test.check(!!updates.selectedRestore && updates.restores === 0, "restore requires confirmation");
            test.check(!!test.label("The current system is kept, so you can undo this."), "restore explains undo");
            const originalPoints = updates.restorePoints;
            const question = test.find(view.C.Overlay.overlay, item => item.text && item.text.startsWith("Restore the system as it was before the update of") && item.text.endsWith("?"));
            const questionText = question.text;
            updates.restorePoints = [{id: "newer", date: "2026-10-12T12:00:00Z"}];
            input.wait(30);
            test.check(question.text === questionText, "confirmation retains selected date after index refresh");
            updates.restorePoints = originalPoints;
            test.shot("restore-preview");
            test.activate(test.button("Cancel"));
            test.check(!updates.selectedRestore && updates.restores === 0, "restore cancel preserves system");
            test.activate(test.button("Restore…"));
            test.activate(test.button("Restore"));
            test.check(updates.restores === 1, "confirmed restore reaches controller");
            if (!test.failed) console.log("INTERACTION_OK");
            Qt.quit();
        }
    }
}
