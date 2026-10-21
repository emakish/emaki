// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls as C
import QtTest
import Quickshell
import ".." as UI
import "file:@EMAKI_DATADIR@/shell" as Shell

ShellRoot {
    id: test
    property bool failed: false
    // The colours checked below are the light ones: choose light, since nothing saved is dark.
    Component.onCompleted: Shell.SettingsBridge.values = [{key: "appearance.theme", value: "light"}]
    UI.UpdateController { id: updates }
    UI.ProgramsController { id: programs; externalBusy: updates.busy || updates.restorePending }
    UI.RealProgramsController { id: real }
    // Points the window at the shipped controller once it has read its engine, then back.
    function withReal(body) {
        for (let count = 0; count < 100 && (!real.catalogReceived || !real.installedReceived || real.busy); count++) input.wait(50);
        check(real.catalogReceived && real.installedReceived && !real.busy, "the shipped controller reads its engine");
        view.programs = real; input.wait(50);
        try { body(); } finally { view.programs = programs; input.wait(50); }
    }
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
    function warningControlsFit() {
        const overlay = view.C.Overlay.overlay;
        const body = find(overlay, item => item.objectName === "aur-warning-body");
        const checkbox = find(overlay, item => item.objectName === "aur-dont-show");
        check(!!body && !!checkbox, "AUR warning explanation and checkbox exist");
        const controls = [checkbox, button("Not now"), button("Got it, open the AUR")];
        const positions = controls.map(control => control.mapToItem(window.contentItem, 0, 0));
        const flick = body.contentItem;
        for (const end of [false, true]) {
            flick.contentY = end ? Math.max(0, flick.contentHeight - flick.height) : 0;
            input.wait(30);
            for (let index = 0; index < controls.length; index++) {
                const control = controls[index];
                check(!!control && control.visible, "AUR warning control exists");
                const point = control.mapToItem(window.contentItem, 0, 0);
                check(point.x >= 0 && point.y >= 0 && point.x + control.width <= window.width
                      && point.y + control.height <= window.height,
                      "AUR warning control fits the window: " + control.text);
                check(point.x === positions[index].x && point.y === positions[index].y,
                      "AUR warning control stays in place while scrolling: " + control.text);
            }
        }
        flick.contentY = 0;
    }
    function shot(name) {
        if (!Quickshell.env("TEST_SHOTS")) return;
        if (name === "updates-bottom" || name === "restore-picker" || name === "aur-popular-scrolled" || name === "browse-from-emaki") { if (view.listPage) named("row-list").positionViewAtEnd(); else { const flick = named("managerBody").contentItem; flick.contentY = Math.max(0, flick.contentHeight - flick.height); } } else view.resetScroll(); window.visible = false; input.wait(30); window.visible = true; view.opacity = 0; input.wait(30); view.opacity = 1; input.wait(80);
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
            const store = programs.storefront;
            test.check(view.page === "browse" && view.tab === "browse" && store.featured.programs.length === 3
                && JSON.stringify(view.featured.map(row => String(row.id).toLowerCase())) === JSON.stringify(store.featured.programs), "Browse's picks are the storefront file's, in its order");
            test.check(!!test.label(store.featured.title) && !!test.label(store.featured.note) && store.featured.title === "Picked by Emaki", "the featured heading is the storefront file's");
            test.check(store.rows.length === 0 && !test.label("Graphics row") && !test.label("GRAPHICS"), "Browse has no hand-made category rows");
            // Each tab owns its top: Browse its catalog search and categories, nothing of the other tabs.
            test.check(test.named("browse-top").visible && test.named("category-strip").visible && test.named("program-search").visible
                && !test.named("aur-top").visible && !test.named("installed-search").visible && !test.named("aur-entry").visible
                && !test.button("Open the AUR…"), "Browse shows only its own search and category strip; the AUR is not a block on it");
            const shelfOrder = ["shelf-picked", "shelf-popular", "shelf-essentials", "shelf-fresh", "emaki-shelf"].map(name => test.named(name));
            test.check(shelfOrder.every(item => item && item.visible)
                && shelfOrder.every((item, index) => index === 0 || item.mapToItem(view, 0, 0).y > shelfOrder[index - 1].mapToItem(view, 0, 0).y),
                "Browse shelves: Picked by Emaki, Popular, Essentials, New and updated, then From Emaki");
            test.check(!test.label("Most installed this month") && view.popular[0].name === programs.allPrograms[0].name, "without engine popularity Popular keeps the catalog's order and claims no measure");
            test.check(view.essentials.map(row => row.package).join() === "gimp,inkscape,krita,code" && !!test.label("All 4 ›") && test.named("see-all-essentials").visible, "Essentials lists the full install's programs after a minimal install");
            test.check(view.fresh.map(row => row.package).join() === "gimp,dolphin", "New and updated lists the newest releases first");
            test.check(!!test.label("GIMP"), "a card uses the storefront's short GIMP name");
            const content = test.named("manager-content");
            test.check(Math.abs(content.mapToItem(view, 0, 0).x - view.gutter) < 3 && content.width > view.width - 2 * view.gutter - 30, "Browse fills the window's width without side fields");
            const chip = test.named("category-Graphics");
            const chipLabel = test.find(chip, item => item.objectName === "chip-label");
            test.check(Math.abs(chipLabel.mapToItem(chip, 0, 0).y + chipLabel.height / 2 - chip.height / 2) < 1.5, "a category chip's text sits in its middle");
            test.shot("browse");
            test.check(test.named("emaki-shelf").visible && !!test.label("From Emaki"), "Browse shows the From Emaki shelf the engine sent");
            for (const row of programs.shelf) {
                const card = test.named("shelf-" + row.package);
                test.check(!!test.label(row.name) && !!test.label(row.summary), "the shelf displays its full shipped title and summary");
                for (const text of [row.name, row.summary]) {
                    const item = test.label(text);
                    const position = item.mapToItem(card, 0, 0);
                    test.check(item.paintedWidth <= item.width + 1 && item.paintedHeight <= item.height + 1
                        && position.x >= 0 && position.y >= 0 && position.x + item.width <= card.width
                        && position.y + item.height <= card.height, "the full shelf text fits its card: " + text);
                }
            }
            test.check(programs.shelf.length === 3 && programs.shelf.every(row => test.named("shelf-" + row.package) && test.named("shelf-" + row.package).visible), "every shelf package the engine offers has its card, snapguard included");
            test.check(test.named("shelf-install-emaki-sudo-key").text === "Install" && test.named("shelf-install-emaki-sudo-key").enabled
                && test.named("shelf-install-snapguard").text === "Install" && test.named("shelf-install-snapguard").enabled
                && test.named("shelf-install-emaki-nvidia").text === "Installed" && !test.named("shelf-install-emaki-nvidia").enabled, "a shelf item installs with one button; an installed one says so");
            test.shot("browse-from-emaki");
            test.activate(test.named("shelf-install-emaki-sudo-key"));
            test.check(programs.previewOpen && programs.selectedEdition.key === "package:emaki/emaki-sudo-key" && programs.executions === 0, "a shelf install previews its exact repository package before anything changes");
            test.activate(test.button("Cancel"));
            const shelfRows = programs.shelf;
            programs.shelf = []; input.wait(30);
            test.check(!test.named("emaki-shelf").visible && !test.label("From Emaki"), "an empty shelf is not drawn");
            programs.shelf = shelfRows; input.wait(30);
            const catalogRows = programs.allRows;
            // Without Flathub metadata a pick is only its ID; it is left out rather than shown raw.
            programs.allRows = catalogRows.concat([{key:"flatpak:org.example.Bare",id:"org.example.Bare",kind:"flatpak",name:"org.example.Bare",summary:"",categories:[],actions:["install"],icons:[],screenshots:[]}]);
            programs.storefront = {featured: {title: "Another row", note: "Another note", programs: ["org.gimp.gimp", "org.example.bare"]}, rows: [], names: {}};
            input.wait(30);
            test.check(view.featured.length === 1 && view.featured[0].package === "gimp" && !!test.label("Another row") && !test.label("GIMP") && !view.featured.some(row => row.id === "org.example.Bare"), "another storefront file gives another Browse; a pick without metadata is left out");
            programs.allRows = catalogRows;
            programs.storefront = {featured: {title: "", note: "", programs: []}, rows: [], names: {}};
            input.wait(30);
            test.check(!test.named("shelf-picked").visible && !test.label("Our picks are not available right now. Try Refresh."), "an empty storefront shows no picks and no error");
            programs.storefront = store;
            const essentials = programs.essentials;
            programs.essentials = {minimal: false, programs: essentials.programs}; input.wait(30);
            test.check(!test.named("shelf-essentials").visible, "after a full install there is no Essentials shelf");
            programs.essentials = essentials; input.wait(30);
            test.shot("browse-bottom");
            // "See all" opens the whole list; every page below Browse has its way back.
            test.activate(test.named("see-all-popular"));
            test.check(view.page === "list" && view.tab === "browse" && test.named("back").visible && test.named("back").text === "‹  Browse"
                && !!test.label("Popular") && test.named("list-page").visible && !test.named("browse-top").visible, "See all opens the full Popular list with a way back");
            test.shot("see-all");
            view.showProgram(view.popular[1]); input.wait(30);
            test.check(view.page === "program" && test.named("back").text === "‹  Popular", "a program opened from a list goes back to that list");
            test.activate(test.named("back"));
            test.check(view.page === "list" && view.listId === "popular", "back from the program returns to the list");
            test.activate(test.named("back"));
            test.check(view.page === "browse", "back from a list returns to Browse");
            test.activate(test.named("category-Graphics"));
            test.check(view.page === "category" && view.category === "Graphics" && test.named("back").text === "‹  Browse" && !!test.label("Graphics")
                && !!test.label("3 programs") && !test.named("browse-top").visible, "a category chip opens its page with a way back to Browse");
            test.shot("category-page");
            view.showProgram(view.regularResults[0]); input.wait(30);
            test.check(view.page === "program" && view.tab === "browse" && test.named("back").text === "‹  Graphics" && test.named("tab-browse").selected, "a program from a category shows ‹ Graphics");
            test.shot("program-from-category");
            input.keyClick(Qt.Key_Escape); input.wait(30);
            test.check(view.page === "category" && view.category === "Graphics" && view.regularResults.length === 3, "Escape on a program page goes back to its category");
            const countLabel = test.label("3 programs"), rowsTop = test.named("row-list").mapToItem(view, 0, 0).y;
            test.check(!!countLabel && countLabel.mapToItem(view, 0, 0).y + countLabel.height <= rowsTop + 1 && countLabel.mapToItem(view, 0, 0).y > 0, "a category reopened keeps its heading in sight above the rows");
            view.navigate("browse"); input.wait(30);
            test.activate(test.named("category-Education"));
            test.check(view.page === "category" && test.named("category-empty").visible && !!test.label("There are no programs in Education yet."), "an empty category says so plainly");
            test.shot("category-empty");
            test.activate(test.named("back"));
            test.check(view.page === "browse", "the back control leaves an empty category");
            test.check(programs.scope === "programs", "Programs is the default search scope");
            test.activate(test.named("scope-packages"));
            test.check(view.packagesPage && test.named("program-search").placeholderText === "Search all packages", "package scope has a distinct list and search prompt");
            const list = test.named("package-list");
            test.check(list.count === 1003 && list.contentItem.children.length < 100, "full package inventory uses virtual rows");
            const firstPackage = test.named("package-row-aom");
            const packageLeft = firstPackage.mapToItem(view, 0, 0).x;
            test.check(Math.abs(packageLeft - (view.width - firstPackage.width) / 2) < 2, "package rows stay centered at each window width");
            test.check(!!test.label("aom") && !!test.label("Installed") && !!test.label("AV1 video codec · extra · 3.8"), "terminal-installed package has installed state and version");
            test.check(!!test.label("1,003 packages"), "multiple package results use the plural label");
            test.tab(test.named("program-search"));
            for (const key of [Qt.Key_A, Qt.Key_O, Qt.Key_M]) input.keyClick(key);
            input.wait(50);
            test.check(list.count === 1 && !!test.label("1 package"), "one package result uses the singular label");
            test.shot("all-packages-single");
            input.keyClick(Qt.Key_Z); input.wait(50);
            test.check(list.count === 0 && !!test.label("0 packages"), "zero package results use the plural label");
            input.keyClick(Qt.Key_A, Qt.ControlModifier); input.keyClick(Qt.Key_Backspace); input.wait(50);
            test.check(list.count === 1003 && !!test.label("1,003 packages"), "clearing search restores the plural count");
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
            test.check(test.named("back").text === "‹  Browse", "a package page goes back to Browse");
            test.activate(test.named("back"));
            test.check(view.page === "browse" && view.packagesPage, "back from a package page returns to the package list");
            test.activate(test.named("scope-packages"));
            test.check(view.page === "browse" && !view.packagesPage, "switching back restores program Browse");
            test.activate(test.named("tab-installed"));
            test.check(view.page === "installed" && view.tab === "installed" && test.named("installed-search").visible && !test.named("browse-top").visible
                && !test.named("aur-top").visible && test.named("tab-installed").selected, "keyboard Installed tab shows only its own search");
            const installedField = test.named("installed-search"), installedCount = test.label("3 programs");
            test.check(!!installedCount && installedCount.mapToItem(view, 0, 0).y - (installedField.mapToItem(view, 0, 0).y + installedField.height) < 40
                && test.named("row-list").mapToItem(view, 0, 0).y - (installedCount.mapToItem(view, 0, 0).y + installedCount.height) < 40, "Installed's heading and rows start right under its search, with no blank band");
            const dolphinRow = test.named("installed-row-dolphin"), selfRow = test.named("installed-row-emaki-update-manager");
            test.check(!!dolphinRow && test.find(dolphinRow, item => item.objectName === "part-of-emaki").visible && !test.find(dolphinRow, item => item.objectName === "remove").visible,
                "a vital program is tagged Part of Emaki and has no Remove");
            test.check(!!selfRow && test.find(selfRow, item => item.objectName === "part-of-emaki").visible && !test.find(selfRow, item => item.objectName === "remove").visible
                && !!test.label("This window"), "the store itself is listed as Part of Emaki");
            const inkscapeRow = test.named("installed-row-inkscape");
            test.check(test.find(inkscapeRow, item => item.objectName === "remove").visible && !test.find(inkscapeRow, item => item.objectName === "part-of-emaki").visible
                && !!test.label("Vector graphics · Arch"), "a removable program offers Remove and names its source");
            test.withReal(() => {
                const engineRow = real.installedPrograms.find(row => row.package === "dolphin");
                const shown = test.named("installed-row-dolphin");
                test.check(!!engineRow && engineRow.protected === true && !engineRow.actions.length && !!shown
                    && test.find(shown, item => item.objectName === "part-of-emaki").visible && !test.find(shown, item => item.objectName === "remove").visible,
                    "a protected program from the engine's own inventory reaches Installed as Part of Emaki, without Remove");
            });
            const installedBeforeDependencies = programs.allRows;
            for (const dependency of [
                {package: "cups", name: "Printers", protected: true, requiredBy: ["emaki-apps"], reason: "Part of Emaki"},
                {package: "mpv", name: "mpv", protected: false, requiredBy: ["mpvqt"], reason: "Needed by other software"}
            ]) {
                const requiredProgram = Object.assign({key: "arch:" + dependency.package, id: dependency.package,
                    kind: "arch", installed: true, removable: false, actions: [], icons: [], screenshots: [], categories: []}, dependency);
                programs.allRows = [requiredProgram]; input.wait(50);
                test.check(!!test.label(dependency.reason) && !test.button("Remove"), "Installed explains why a required program stays without offering Remove");
                view.showProgram(requiredProgram); input.wait(50);
                test.check(!!test.label(dependency.reason) && !test.button("Remove"), "the required program page explains why it stays without offering Remove");
                test.activate(test.named("tab-installed"));
            }
            programs.allRows = installedBeforeDependencies; input.wait(50);
            test.shot("installed");
            test.tab(test.named("installed-search"));
            for (const key of [Qt.Key_I, Qt.Key_N, Qt.Key_K]) input.keyClick(key); input.wait(50);
            test.check(view.installedList.length === 1 && !test.named("installed-row-dolphin") && !!test.named("installed-row-inkscape") && !!test.label("1 program"), "Installed search filters the installed list");
            test.shot("installed-search");
            input.keyClick(Qt.Key_A, Qt.ControlModifier); input.keyClick(Qt.Key_Backspace); input.wait(50);
            test.check(view.installedList.length === 3, "clearing Installed search lists everything again");
            test.activate(test.button("Remove"));
            test.check(programs.previewOpen && programs.executions === 0, "Remove opens preview before mutation");
            test.check(!!test.label("unused-library") && !!test.label("nothing else needs it"), "complete removal dependency preview always shown");
            test.shot("remove-preview");
            test.activate(test.button("Cancel"));
            test.check(!programs.previewOpen && programs.executions === 0, "cancel preserves program");
            view.showProgram(programs.allRows[4]); input.wait(50); test.shot("program-installed");
            test.check(!!test.button("Undo this install") && !!test.label("Installed " + Qt.formatDateTime(new Date("2026-10-12T19:40:00+00:00"), "d MMM yyyy, HH:mm") + " with 1 package it needed"), "a recorded install offers Undo this install with its date");
            test.activate(test.button("Undo this install"));
            test.check(programs.previewOpen && programs.planAction === "undo" && programs.executions === 0, "undo previews before any change");
            test.check(!!test.label("Undo the install of Inkscape?") && !!test.label("libinkscape") && !!test.label("came with it") && !!test.label("THESE STAY") && !!test.label("you installed it yourself"), "undo preview names what goes and what stays");
            test.check(!!test.find(view.C.Overlay.overlay, item => item.visible && String(item.text).includes("Updates that came with the install stay.")), "undo preview keeps updates");
            test.shot("undo-preview");
            const removalPlan = programs.plan;
            programs.plan = {ok: true, token: "snapshot-token", transaction: {mode: "snapshot", restart: true,
                message: "Restore the system before this install and restart."}};
            input.wait(50);
            test.check(!!test.label(programs.plan.transaction.message), "snapshot Undo shows the backend restoration warning");
            test.check(!test.label("These 2 packages go:") && !test.label("THESE STAY")
                && !test.find(view.C.Overlay.overlay, item => item.visible && String(item.text).includes("Updates that came with the install stay.")),
                "snapshot Undo never promises package removal or preserved updates");
            test.check(!!test.button("Restore and restart later"), "snapshot Undo confirms restoration and restart");
            programs.plan = removalPlan; input.wait(50);
            test.activate(test.button("Undo install"));
            test.check(programs.executions === 1 && programs.phase === "undoing", "confirmed undo reaches the controller");
            input.wait(30);
            test.check(!!test.label("Undoing the install…"), "undo shows its own progress");
            programs.mutating = false; programs.busy = false; programs.phase = "idle"; programs.executions = 0;
            programs.busy = true; programs.phase = "catalog"; programs.heldByRefresh = true;
            view.navigate("browse"); input.wait(30);
            test.check(!!test.find(view, item => item.visible && item.text === "Install" && typeof item.clicked === "function" && item.enabled),
                       "cached Browse entries stay installable during a quiet refresh");
            view.installProgram(programs.allPrograms.find(row => row.package === "gimp")); input.wait(30);
            test.check(view.page === "program" && !programs.previewOpen && !!test.label("Refreshing…"),
                       "a quiet catalog refresh opens the program and says why Install waits");
            programs.busy = false; programs.phase = "idle"; programs.heldByRefresh = false;
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
            view.navigate("browse"); input.wait(30);
            test.activate(test.named("tab-aur"));
            test.check(view.page === "aur" && view.tab === "aur" && test.named("aur-entry").visible && !test.named("browse-top").visible && !test.named("installed-search").visible
                && !test.label("WHAT YOU RISK"), "the AUR tab starts closed, with its own explanation and no Browse top");
            test.shot("aur-closed");
            test.activate(test.named("open-aur"));
            test.check(!!test.label("WHAT YOU RISK") && !!test.label("Installing from here, you are responsible for the consequences.") && view.page === "aur", "opening the AUR shows the warning first");
            test.check(!!test.find(view.C.Overlay.overlay, item => item.visible && String(item.text).startsWith("A system snapshot before every install.")), "warning names the snapshot on a snapshotted system");
            test.warningControlsFit();
            test.shot("aur-warning");
            test.activate(test.button("Not now"));
            test.check(programs.aurChanges === 0 && !programs.aurEnabled && view.page === "aur" && test.named("aur-entry").visible && !test.named("aur-top").visible, "Not now keeps the AUR closed");
            test.activate(test.named("open-aur"));
            test.activate(test.button("Got it, open the AUR"));
            test.check(programs.aurEnabled && programs.lastAur.accept === true && programs.lastAur.showWarning === true && view.page === "aur", "accepting opens the AUR section");
            test.check(!!test.label("AUR — nobody checked these programs.") && test.named("aur-top").visible && test.named("aur-search").visible && !test.named("aur-entry").visible, "the open AUR has its own search and keeps its stripe");
            input.wait(30);
            const popularFirst = test.label("yay"), popularSecond = test.label("code-minimap");
            test.check(programs.popularReads === 1 && test.named("aur-popular").visible && !!test.label("Popular on the AUR") && !!test.label("By votes") && !!popularFirst && !!popularSecond
                && popularFirst.mapToItem(view, 0, 0).y < popularSecond.mapToItem(view, 0, 0).y && !!test.label("2,649 votes"), "an open AUR section starts with the popular list by votes");
            test.check(JSON.stringify(view.aurList.slice(0, 3).map(row => row.votes)) === JSON.stringify([2649, 42, 40]), "the popular AUR list is ordered by votes only");
            const seen = [];
            (function collect(item) { if (item.visible === false) return; if (typeof item.text === "string" && item.text) seen.push(item.text); for (const child of item.children || []) collect(child); })(view);
            const endorsement = /trust|tested|recommend|verified|vetted|curated|endorse|\bsafe|picked|\bbest\b|featured|approved|reliable|editor|worth trying/i;
            test.check(seen.length > 10 && !seen.some(text => endorsement.test(text)), "the AUR page endorses nothing: " + seen.filter(text => endorsement.test(text)).join(" | "));
            test.shot("aur-popular");
            const stripe = test.named("aur-stripe");
            const body = test.named("row-list");
            const stripeY = stripe.mapToItem(view, 0, 0).y;
            let inBody = false;
            for (let item = stripe.parent; item; item = item.parent) if (item === body) inBody = true;
            const startY = body.contentY;
            body.positionViewAtEnd(); input.wait(50);
            test.check(!inBody && stripe.visible && body.contentY - startY > 200 && Math.abs(stripe.mapToItem(view, 0, 0).y - stripeY) < 1
                && stripeY + stripe.height <= body.mapToItem(view, 0, 0).y + 1 && !!test.label("AUR — nobody checked these programs."), "the AUR stripe stays pinned above the scrolled list");
            test.check(body.count === view.aurList.length && body.contentItem.children.length < body.count, "the AUR list draws only the rows in sight");
            test.shot("aur-popular-scrolled");
            body.positionViewAtBeginning(); input.wait(30);
            test.tab(test.named("aur-search"));
            for (const key of [Qt.Key_C, Qt.Key_O, Qt.Key_D, Qt.Key_E]) input.keyClick(key); input.wait(50);
            test.check(programs.aurRows.length === 1 && !test.named("aur-popular").visible && !!test.label("code-minimap") && !!test.label("42 votes") && !!test.label("no maintainer · out of date"), "AUR search replaces the popular list with results and their trust facts");
            const aurRows = programs.aurRows;
            programs.aurRows = [Object.assign({}, aurRows[0], {votes:null})]; input.wait(30);
            test.check(!!test.label("Votes unknown") && !test.label("0 votes"), "unknown list votes stay unknown");
            programs.aurRows = [Object.assign({}, aurRows[0], {votes:0})]; input.wait(30);
            test.check(!!test.label("0 votes"), "known zero votes remain zero");
            programs.aurRows = aurRows; input.wait(30);
            test.shot("aur-section");
            const aurRow = test.named("program-row-code-minimap");
            test.activate(test.find(aurRow, item => item.objectName === "install" && item.visible));
            test.check(view.page === "program" && view.tab === "aur" && programs.previewOpen && programs.planAction === "install" && programs.executions === 0
                && test.named("back").text === "‹  AUR", "Build and install on an AUR row opens its build preview over its page, with ‹ AUR behind");
            // A plan the engine refuses keeps the shipped controller's build window open and says why there.
            programs.cancelPlan(); input.wait(30);
            test.withReal(() => {
                real.openProgram(real.allRows.find(row => row.key === "aur:code-minimap"));
                real.prepareInstall();
                for (let count = 0; count < 60 && real.busy; count++) input.wait(50);
                const refusal = test.find(view.C.Overlay.overlay, item => item.objectName === "program-preview-error");
                test.check(real.planAction === "install" && real.previewOpen && !real.plan && real.message === Quickshell.env("TEST_REFUSAL")
                    && !!refusal && refusal.visible && refusal.text === real.message
                    && !test.find(view.C.Overlay.overlay, item => item.objectName === "confirm-program-operation").enabled && !test.label("These 0 packages change:"), "a refused build plan shows its reason inside the open build window");
                test.shot("aur-build-refused");
                test.activate(test.button("Cancel"));
                test.check(!real.previewOpen, "Cancel closes the refused build window");
            });
            test.activate(test.named("back"));
            test.check(view.page === "aur" && view.programs.aurQuery === "code", "back from an AUR program returns to the AUR tab and its search");
            view.showProgram(programs.aurRows[0]); input.wait(50);
            test.check(programs.selectedEdition.kind === "aur" && !!test.button("Build and install") && !!test.label("From the AUR, nobody checked it."), "AUR page installs through a build, with the unchecked line by Install");
            test.check(!!test.label("WHAT IS KNOWN ABOUT IT") && !!test.label("42") && !!test.label("Orphan — nobody maintains it") && !!test.button("See how the program is built"), "AUR page shows trust facts instead of screenshots");
            test.shot("aur-program");
            test.activate(test.button("show what will be built"));
            test.check(programs.previewOpen && !!test.label("Build code-minimap from the AUR?") && !!test.label("BUILT FROM THE AUR (2)") && !!test.label("FROM ARCH, STAYS INSTALLED (1)") && !!test.label("FROM ARCH, ONLY TO BUILD IT (2) — removed afterwards"), "AUR build preview names recipes and what they pull");
            test.check(!!test.label("rust-helper") && !!test.label("it needs this") && !!test.label("See how it is built · 0123456789") && !!test.label("0.6.8-1 · nobody maintains it · marked out of date"), "AUR build preview names every recipe by commit with its trust facts");
            const previewTexts = [];
            (function collect(item) { if (item.visible === false) return; if (typeof item.text === "string" && item.text) previewTexts.push(item.text); for (const child of item.children || []) collect(child); })(view.C.Overlay.overlay);
            test.check(previewTexts.some(text => text.includes("build instructions")) && !previewTexts.some(text => /recipe|PKGBUILD/i.test(text)), "the build preview says build instructions, never recipe or PKGBUILD");
            test.shot("aur-build-preview");
            test.activate(test.button("Cancel"));
            test.check(!programs.previewOpen && programs.executions === 0, "cancelled build changes nothing");
            view.navigate("browse");
            test.activate(test.named("tab-aur"));
            test.check(!!test.label("WHAT YOU RISK"), "the warning shows each time until the person turns it off");
            test.tab(test.find(view.C.Overlay.overlay, item => item.objectName === "aur-dont-show"));
            input.keyClick(Qt.Key_Space); input.wait(30);
            test.activate(test.button("Got it, open the AUR"));
            test.check(programs.lastAur.showWarning === false && view.page === "aur" && test.named("aur-top").visible, "Don't show this again is kept with the consent");
            view.navigate("browse");
            test.activate(test.named("tab-aur"));
            test.check(view.page === "aur" && !test.label("WHAT YOU RISK") && test.named("aur-top").visible, "a turned-off warning opens the section directly");
            view.navigate("browse");
            test.tab(test.named("program-search"));
            for (const key of [Qt.Key_C, Qt.Key_O, Qt.Key_D, Qt.Key_E]) input.keyClick(key); input.wait(50);
            test.check(view.page === "search" && view.regularResults.length === 1 && !test.named("program-row-code-minimap") && !test.named("aur-stripe").visible
                && !!test.label("1 program from Arch and Flathub"), "Browse search covers the catalog only; the AUR has its own tab");
            test.shot("search");
            view.showProgram(view.regularResults[0]); input.wait(30);
            test.check(test.named("back").text === "‹  Results", "a program from search results goes back to them");
            test.activate(test.named("back"));
            test.check(view.page === "search" && view.query === "code" && test.named("program-search").text === "code", "back returns to the same results");
            // A long query elides its heading; the count and the list stay inside the window.
            const searchField = test.named("program-search");
            searchField.text = "code ".repeat(24).trim(); searchField.textEdited(); input.wait(80);
            const longTitle = test.find(view, item => item.objectName === "heading-title" && item.visible && item.text.startsWith("Results for"));
            const longNote = test.label(view.regularResults.length.toLocaleString(Qt.locale(), "f", 0) + (view.regularResults.length === 1 ? " program from Arch and Flathub" : " programs from Arch and Flathub"));
            const searchList = test.named("row-list");
            test.check(view.page === "search" && !!longTitle && longTitle.truncated && !!longNote
                && longTitle.mapToItem(window.contentItem, 0, 0).x + longTitle.width <= window.width
                && longNote.mapToItem(window.contentItem, 0, 0).x + longNote.width <= window.width
                && (!searchList || !searchList.visible || searchList.mapToItem(window.contentItem, 0, 0).x + searchList.width <= window.width),
                "a long search elides its heading and keeps its count and list inside the window");
            searchField.text = "code"; searchField.textEdited(); input.wait(80);
            view.showProgram(programs.allRows[6]); input.wait(30);
            test.check(!!test.button("Build and install") && !test.button("Install"), "AUR page builds instead of a plain install");
            programs.selectedEdition = Object.assign({}, programs.selectedEdition, {installed:true,installedVersion:"0.6.7-1",actions:["remove","install"]});
            input.wait(30);
            test.check(!!test.button("Remove") && test.button("Remove").enabled, "installed AUR page exposes authorized removal");
            test.check(!test.button("Open") && !!test.button("Build the update · 0.6.8-1"), "an AUR tool without a launcher has no Open; its update can be built");
            programs.selectedEdition = Object.assign({}, programs.selectedEdition, {ids: ["org.kde.dolphin.desktop"]});
            input.wait(30);
            test.check(!!test.button("Open"), "an installed AUR program with a launcher can open");
            test.shot("aur-program-installed");
            // A recorded AUR program without current RPC facts keeps its identity and shows unknown facts.
            const external = {key:"legacy:handmade",id:"handmade",package:"handmade",kind:"arch",sourceLabel:"Installed outside the store",name:"Handmade",installed:true,actions:["remove"],icons:[],screenshots:[],categories:[]};
            programs.selectedEdition = external; programs.selectedProgram = Object.assign({}, external, {editions:[external]}); input.wait(30);
            test.check(!!test.button("Installed outside the store") && !test.button("Arch"), "outside-store edition chip matches its source");
            const legacyAur = {key:"legacy:paint-bin",id:"paint",package:"paint-bin",kind:"aur",sourceLabel:"AUR",name:"Paint",installed:true,installedVersion:"2.0-1",actions:["remove"],undoKey:"",icons:[],screenshots:[],categories:[]};
            programs.selectedEdition = legacyAur; programs.selectedProgram = Object.assign({}, legacyAur, {editions: [legacyAur]}); input.wait(30);
            const factTexts = [];
            (function collect(item) { if (item.visible === false) return; if (typeof item.text === "string" && item.text) factTexts.push(item.text); for (const child of item.children || []) collect(child); })(test.named("aur-facts"));
            test.check(!!test.label("From the AUR, nobody checked it.") && test.named("aur-facts").visible && test.named("aur-facts-unknown").visible
                && factTexts.filter(text => text === "Unknown").length === 6 && !factTexts.includes("0") && !factTexts.includes("No") && !test.button("Undo this install"),
                "an installed AUR program without AUR data keeps its warning and shows unknown facts, not 0 or No: " + factTexts.join(" | "));
            test.shot("aur-installed-unknown");
            programs.selectedEdition = Object.assign({}, legacyAur, {undoKey:"aur:paint-bin",actions:["remove","undo"],votes:7,popularity:0.5,outOfDate:"",maintainer:"someone",orphan:false,lastModified:"2026-08-01T10:00:00+00:00",firstSubmitted:"2021-03-04T10:00:00+00:00",
                undo:{id:"20261012T120000123456Z-" + "c".repeat(32),kind:"aur",name:"paint-bin",time:"2026-10-12T19:40:00+00:00",target:"paint-bin",added:["paint-bin"],snapshot:"18"}}); input.wait(30);
            test.check(!!test.button("Undo this install") && !test.named("aur-facts-unknown").visible && !!test.label("7") && !!test.label("No") && !!test.label("someone"), "with its AUR record the installed program shows its facts and Undo");
            // Decision of 2026-10-13: no system snapshots (ext4) means no AUR installs; the section says so instead of a softened warning.
            programs.aurSnapshots = false;
            programs.selectedEdition = programs.allRows[6]; programs.selectedProgram = Object.assign({}, programs.allRows[6], {editions: [programs.allRows[6]]}); input.wait(30);
            test.check(!!test.button("Build and install") && !test.button("Build and install").enabled && test.named("aur-no-snapshots-program").visible, "without snapshots an AUR program cannot be built and says why");
            test.shot("aur-program-no-snapshots");
            programs.aurKnown = false; input.wait(30);
            test.check(!test.named("aur-no-snapshots-program").visible && test.button("Build and install").enabled, "before the snapshot state is read nothing is called unavailable");
            programs.aurKnown = true; input.wait(30);
            view.navigate("browse"); programs.aurShowWarning = true;
            test.activate(test.named("tab-aur"));
            test.check(!!test.find(view.C.Overlay.overlay, item => item.visible && item.objectName === "aur-warning-no-snapshots")
                && !!test.find(view.C.Overlay.overlay, item => item.visible && String(item.text).startsWith("A system snapshot before every install.")), "without snapshots the agreed warning stays and adds that AUR installs are not available");
            test.warningControlsFit();
            test.shot("aur-warning-no-snapshots");
            test.activate(test.button("Got it, open the AUR"));
            test.check(view.page === "aur" && test.named("aur-no-snapshots").visible, "without snapshots the AUR section says installs are not available");
            test.shot("aur-no-snapshots");
            programs.aurSnapshots = true;
            view.navigate("search"); view.query = "code"; programs.search("code");
            input.keyClick(Qt.Key_Escape); input.wait(30);
            test.check(view.page === "browse", "Escape clears search");
            view.showCategory(view.categoryRows[2]); input.wait(30); test.shot("category");
            test.activate(test.named("tab-updates"));
            test.check(view.page === "updates" && !!test.label("Not part of Update all"), "updates keep the AUR group outside Update all");
            test.check(test.named("tab-updates").badge === 4 && test.named("tab-updates").selected && !test.named("browse-top").visible && !test.named("aur-top").visible && !test.named("installed-search").visible, "Updates shows its count on the tab and has no search");
            test.check(!test.named("whats-new").visible, "no release card without a pending Emaki release");
            const previousAurEnabled = programs.aurEnabled;
            updates.signOut = true; input.wait(30);
            test.check(!!test.label("Save your work, log out and log in again after updating."), "session update guidance matches Log out");
            updates.signOut = false; input.wait(30);
            const previousUpdateData = updates.data;
            updates.data = Object.assign({}, updates.data, {foreignPackageCount: 0});
            programs.aurKnown = true; programs.aurEnabled = false; input.wait(30);
            const aurNotice = test.named("aur-updates-disabled");
            test.check(!!aurNotice && !aurNotice.visible, "no foreign packages means no AUR-off notice");
            updates.data = Object.assign({}, updates.data, {foreignPackageCount: undefined}); input.wait(30);
            test.check(!!aurNotice && !aurNotice.visible, "older update data does not invent foreign packages");
            updates.data = Object.assign({}, updates.data, {foreignPackageCount: 2}); input.wait(30);
            test.check(!!aurNotice && aurNotice.visible && aurNotice.text === "AUR updates are not checked while the AUR is off.",
                "Updates explains unchecked foreign packages while the AUR is off");
            programs.aurEnabled = true; input.wait(30);
            test.check(!!aurNotice && !aurNotice.visible, "opening the AUR removes the disabled notice");
            programs.aurEnabled = false; programs.aurKnown = false; input.wait(30);
            test.check(!!aurNotice && !aurNotice.visible, "unknown consent does not claim the AUR is off");
            programs.aurKnown = true; input.wait(30);
            test.check(!!aurNotice && aurNotice.visible, "closing the AUR restores the notice without a new update check");
            updates.data = Object.assign({}, updates.data, {foreignPackageCount: 0}); input.wait(30);
            test.check(!!aurNotice && !aurNotice.visible, "removing the last foreign package hides the notice");
            updates.data = previousUpdateData;
            programs.aurEnabled = previousAurEnabled; input.wait(30);
            updates.data = Object.assign({}, updates.data, {release: {version: "0.8.0", url: "https://github.com/emakish/emaki/releases/tag/v0.8.0", published: true, items: ["The AUR in its own section, opened only after a plain warning.", "Undo this install takes back exactly what an install added."]}});
            input.wait(30);
            test.check(test.named("whats-new").visible && !!test.label("What’s new in Emaki 0.8.0") && !!test.label("Emaki 0.8.0 is available. Update all installs it.") && !!test.label("Undo this install takes back exactly what an install added.") && !!test.button("Read the release notes"), "a pending Emaki release shows what is new");
            test.shot("whats-new");
            updates.data = Object.assign({}, updates.data, {release: {version: "0.8.0", url: "https://github.com/emakish/emaki/releases", published: false, items: []}});
            input.wait(30);
            test.check(!!test.label("The notes for this release are not published yet.") && !!test.button("See Emaki releases"), "an unpublished release keeps the card with the releases page");
            updates.data = Object.assign({}, updates.data, {release: {version: "0.8.0", url: "https://evil.example/", published: true, items: ["x"]}});
            input.wait(30);
            test.check(!test.button("Read the release notes"), "only the Emaki releases address can open");
            updates.data = Object.assign({}, updates.data, {release: undefined});
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
            test.check(test.named("update-title").text === "Updating Emaki" && test.named("update-step").text === "Getting ready…" && !test.named("update-log").visible
                && !test.label("Updating linux"), "the update starts with a plain line, not its raw log");
            updates.output = [":: Processing package changes...", "(1/1) upgrading archlinux-keyring", ":: Running post-transaction hooks...", ":: Retrieving packages..."].join("\n");
            input.wait(30);
            test.check(test.named("update-step").text === "Step 1 of 4 · downloading" && !test.named("update-restore-point").visible && view.controller === updates
                && test.find(view, item => typeof item.fraction === "number" && typeof item.stepText === "function").fraction < 0, "a second pacman run starts the steps again; downloading pulses");
            // The keyring is updated in its own pacman run first; the full update then starts the steps again.
            updates.output = ["Updating the package signing keys first.", ":: Processing package changes...", "(1/1) upgrading archlinux-keyring",
                ":: Running post-transaction hooks...", "(1/2) Arming ConditionNeedsUpdate...", ":: Synchronizing package databases...", ":: Retrieving packages...", " mesa-25.2-1-x86_64 downloading...",
                ":: Running pre-transaction hooks...", "(1/3) Performing snapper pre snapshots for the following configurations...", "==> root: 1240", ":: Processing package changes...",
                "(40/74) upgrading libdrm", "(41/74) upgrading mesa", "==> Building image from preset: /etc/mkinitcpio.d/linux.preset: 'default'",
                "Proceed? [Y/n] y"].join("\n");
            input.wait(30);
            test.check(test.named("update-step").text === "Step 3 of 4 · installing 41 of 74" && test.named("update-restore-point").visible
                && Math.abs(test.find(test.named("update-bar"), item => item !== test.named("update-bar") && item.width > 0).width / test.named("update-bar").width - (2 + 41 / 74) / 4) < 0.01,
                "the update shows its step, its count, a filled bar and the restore point");
            test.check(!test.find(view, item => item.visible && typeof item.text === "string" && item.text.includes("Proceed?")), "the raw log stays hidden until Details");
            test.shot("updating");
            test.activate(test.named("update-details"));
            test.check(test.named("update-log").visible && !!test.find(view, item => item.visible && typeof item.text === "string" && item.text.includes("(41/74) upgrading mesa")), "Details shows the raw log");
            test.shot("updating-details");
            test.activate(test.named("update-details"));
            test.check(!test.named("update-log").visible, "Details hides the log again");
            test.check(!test.button("Restore…").enabled, "update blocks restore");
            updates.completedUpdates = updates.data.updates.filter(row => !row.aur);
            updates.completedAt = new Date().toISOString();
            updates.data = {updates: updates.data.updates.filter(row => row.aur), news: [], warnings: []};
            updates.busy = false; updates.phase = "finished"; updates.succeeded = true; updates.restart = true; updates.reminder = true;
            input.wait(30);
            test.check(!!test.label("Linux kernel") && !!test.label("✓"), "completed managed updates retain rows and ticks");
            test.check(!!test.find(view, item => item.text && item.text.startsWith("3 updates installed today at")), "completion summary retains count and time");
            updates.completedRelease = {version: "0.8.0", url: "https://github.com/emakish/emaki/releases/tag/v0.8.0", published: true, items: ["Undo this install takes back exactly what an install added."]};
            input.wait(30);
            test.check(!!test.label("Emaki 0.8.0 is installed.") && !!test.label("What’s new in Emaki 0.8.0"), "what is new stays after the update installed it");
            test.shot("whats-new-installed");
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
            updates.busy = false; updates.phase = "idle";
            updates.selectedRestore = null;
            programs.recoveryPoint = {id: "71:verified", snapshot: "71", operation: "install"};
            view.page = "program";
            input.wait(30);
            test.activate(test.button("Restore before this install…"));
            test.check(updates.selectedRestore.id === "71:verified" && updates.restores === 1,
                       "failed install opens confirmation for its exact snapshot without restoring");
            test.check(!!test.label("Restore the system as it was before this install (snapshot 71)?"),
                       "install recovery is labelled as an install baseline");
            updates.restorePoints = [{id: "72:newer", operation: "update"}];
            test.activate(test.button("Cancel"));
            test.check(!updates.selectedRestore && updates.restores === 1, "install recovery cancellation makes no changes");
            test.activate(test.button("Restore before this install…"));
            test.activate(test.button("Restore"));
            test.check(updates.selectedRestore.id === "71:verified" && updates.restores === 2,
                       "confirmed install recovery never substitutes a listed update snapshot");
            if (!test.failed) console.log("INTERACTION_OK");
            Qt.quit();
        }
    }
}
