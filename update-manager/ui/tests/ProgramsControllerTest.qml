// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import QtTest
import Quickshell
import Quickshell.Io
import "ManagerLogic.js" as Logic
import "file:@EMAKI_DATADIR@/shell" as Shell

ShellRoot {
    id: test
    readonly property bool translationsReady: Shell.Translation.initialize()
    property bool failed: false
    property int restoreResults: 0
    property var websiteActions: []
    property var lastRestore: null
    property bool sawInitialLoading: false
    property int quietReadLines: 0
    ProgramsController {
        id: controller
        onForegroundBusyChanged: {
            if (foregroundBusy && phase === "catalog" && !catalogLoaded) test.sawInitialLoading = true;
            if (foregroundBusy && phase === "related") test.quietReadLines++;
        }
        onWebsiteChanged: action => test.websiteActions = test.websiteActions.concat([action])
        onRestoreFinished: result => { test.restoreResults++; test.lastRestore = result; }
    }
    Item {
        id: windowHook
        property var programs: controller
        property string page: "browse"
        function resetScroll(): void {}
        function present(): void { /* PRESENT_BODY */ }
    }
    Process { id: terminalChange }
    function terminal(action: string): void {
        terminalChange.command = [Quickshell.env("TEST_BACKEND"), "terminal-" + action];
        terminalChange.running = true;
        let left = 2000;
        while (terminalChange.running && left > 0) { input.wait(10); left -= 10; }
        check(!terminalChange.running, "external package change completes");
    }
    function presentationScenario(): void {
        check(sawInitialLoading, "the first catalog load still displays progress");
        const cachedRows = controller.allRows;
        const cachedStorefront = controller.storefront;
        windowHook.present();
        check(controller.phase === "catalog" && controller.catalogLoaded && !controller.foregroundBusy,
              "reopening refreshes a loaded catalog without the loading line");
        check(controller.allRows === cachedRows && controller.storefront === cachedStorefront,
              "reopening immediately retains the complete catalog and Browse picks");
        controller.openProgram(cachedRows[0]);
        check(!!controller.selectedProgram && controller.heldByRefresh && controller.foregroundBusy && controller.quietRefresh,
              "a program opens during the quiet refresh and shows that it waits");
        controller.prepareInstall();
        check(!controller.previewOpen && controller.phase === "catalog", "install waits for the refreshed catalog");
        settle(); input.wait(50);
        check(controller.catalogReceived && controller.catalogLoaded, "background refresh replaces the catalog");
        check(!controller.heldByRefresh && !!controller.selectedEdition, "the refresh releases the wait and keeps the open program");
        settle();
        check(controller.previewOpen && controller.planAction === "install" && controller.plan?.ok === true && controller.queuedAction === "",
              "the install asked for during the refresh opens its preview after it");
        controller.cancelPlan();
        // An update that starts before the quiet refresh ends must not drop the queued install.
        windowHook.present();
        controller.openProgram(controller.allRows[0]);
        controller.prepareInstall();
        check(controller.queuedAction === "install" && !controller.previewOpen, "an install queues behind a second quiet refresh");
        controller.externalBusy = true;
        settle(); input.wait(50); settle();
        check(controller.queuedAction === "install" && !controller.previewOpen,
              "the queued install survives the refresh ending while an update runs");
        controller.externalBusy = false; input.wait(100); settle();
        check(controller.previewOpen && controller.planAction === "install" && controller.queuedAction === "",
              "the queued install opens its preview once the update ends");
        controller.cancelPlan();
        controller.selectScope("packages"); controller.search("compression"); packagesReady();
        const original = controller.packageRows[0];
        controller.preparePackage(original, "install"); settle();
        check(controller.previewOpen && !controller.selectedEdition.installed, "uninstalled package has install preview");
        terminal("install"); windowHook.present(); input.wait(100);
        check(controller.previewOpen && controller.refreshPending && !controller.selectedEdition.installed,
              "presentation waits for the open preview to close before refreshing");
        controller.cancelPlan(); input.wait(600); packagesReady();
        check(controller.packageRows[0].installed && controller.packageRows[0].actions.includes("remove"),
              "presentation refresh observes terminal install in persistent controller");
        check(controller.selectedEdition.installed && controller.selectedEdition.actions.includes("remove") && !controller.plan,
              "presentation reconciles selected package and discards stale preview");
        controller.executePlan();
        check(!controller.mutating, "stale preview cannot execute after external install");
        controller.search("old"); requestStarted("packages");
        const pendingId = controller.pending.id;
        terminal("remove"); windowHook.present();
        check(controller.pending.id === pendingId && !controller.packagesReceived,
              "presentation preserves in-flight request and marks package snapshot stale");
        controller.search("compression"); input.wait(1000); packagesReady();
        check(!controller.packageRows[0].installed && controller.packageRows[0].actions.includes("install"),
              "presentation queued during busy request observes terminal removal");
        check(!controller.selectedEdition.installed && controller.selectedEdition.actions.includes("install"),
              "queued refresh reconciles selected package after removal");
        terminal("install"); controller.externalBusy = true;
        const serial = controller.serial;
        windowHook.present(); input.wait(350);
        check(controller.serial === serial && !controller.packagesReceived,
              "presentation refresh waits for external update and marks package snapshot stale");
        controller.preparePackage(controller.packageRows[0], "install");
        check(!controller.previewOpen && !controller.busy, "external update still blocks package planning");
        controller.externalBusy = false; input.wait(600); packagesReady();
        check(controller.packageRows[0].installed && controller.selectedEdition.installed,
              "presentation queued during external update runs after it finishes");
    }
    function aurScenario(): void {
        check(controller.aurKnown && !controller.aurEnabled, "AUR section starts closed and its state is read");
        controller.searchAur("editor"); input.wait(400); settle();
        check(controller.aurRows.length === 0 && !controller.aurSearched, "a closed AUR section sends no AUR search");
        controller.setAur(true, true); settle();
        check(controller.aurEnabled && controller.aurShowWarning, "accepting the warning opens the section");
        check(controller.storefront.featured.programs[0] === "org.example.editor", "Browse's picks arrive with the catalog");
        controller.readPopular(); settle();
        check(controller.aurPopularLoaded && controller.aurPopular.map(row => row.key).join() === "aur:yay,aur:editor-tools", "the popular list keeps the engine's order");
        controller.readPopular();
        check(controller.phase === "aurPopular" && controller.busy && !controller.foregroundBusy && controller.backgroundBusy,
              "reading the shown popular list again draws no loading line and keeps Install available");
        settle();
        controller.searchAur("editor"); input.wait(400); settle();
        check(controller.aurRows.length === 1 && controller.aurRows[0].kind === "aur" && controller.aurSearched, "AUR search lists AUR rows");
        const app = controller.allPrograms.find(row => row.id === "org.example.Editor.desktop");
        const linesBefore = test.quietReadLines;
        controller.openProgram(app); input.wait(50); settle();
        check(test.quietReadLines === linesBefore, "an AUR edition joins an open page without a loading line");
        check(controller.selectedProgram.editions.some(row => row.key === "aur:editor-bin"), "the same program from the AUR joins its page");
        controller.selectEdition("arch");
        check(controller.selectedEdition.key === "arch:editor" && controller.selectedEdition.actions.includes("undo"), "a recorded install offers undo");
        controller.prepareUndo(); settle();
        check(controller.previewOpen && controller.planAction === "undo" && controller.planRows.length === 2
              && Logic.undoRows(controller.plan)[0].detail === "you installed it yourself", "undo preview lists what goes and what stays");
        controller.executePlan();
        check(controller.phase === "undoing" && controller.mutating, "undo is a program transaction");
        settle(); input.wait(100); settle();
        check(controller.succeeded && controller.message === "The install was taken back." && !controller.messageIsError, "undo finishes");
        for (const ok of [true, false]) {
            controller.phase = "undoing";
            controller.pending = {id: "snapshot-" + ok, params: {}};
            controller.accept(JSON.stringify({version: 1, id: controller.pending.id, type: "result", ok: ok,
                restart: true, code: ok ? "restored" : "restore-failed", message: "Snapshot restore result"}));
            check(lastRestore?.ok === ok && lastRestore.restart === true, "snapshot restart propagates on success and partial failure");
            input.wait(100); settle();
        }
        check(restoreResults === 2, "both snapshot restore results reached the window");
        for (const failureCode of ["partial", "failed", "unknown", "cancelled"]) {
            controller.phase = "installing";
            controller.pending = {id: "failed-install", params: {}};
            controller.accept(JSON.stringify({version: 1, id: "failed-install", type: "result", ok: false,
                code: failureCode, restoreToken: "71:verified", snapshot: "71", message: "Use the pre-install snapshot."}));
            check(controller.recoveryPoint?.id === "71:verified" && controller.recoveryPoint.operation === "install",
                  "failed install keeps its exact recovery receipt independently of the restore index");
            input.wait(100); settle();
        }
        controller.phase = "installing";
        controller.pending = {id: "next-install", params: {}};
        controller.accept(JSON.stringify({version: 1, id: "next-install", type: "result", ok: false,
            code: "refused", message: "Nothing was installed."}));
        check(controller.recoveryPoint === null, "next install result clears the previous recovery offer");
        input.wait(100); settle();
        controller.setAur(false, true); settle();
        check(!controller.aurEnabled && controller.aurRows.length === 0 && controller.aurPopular.length === 0 && !controller.aurPopularLoaded, "closing the section forgets its results");
        controller.readPopular(); settle();
        check(controller.aurPopular.length === 0 && controller.aurPopularLoaded && controller.message === "The AUR section is off.", "a closed section gets no popular list");
        check(Logic.undoRefusal({code: "cannot-undo", requiredBy: ["base-devel"]}) === "This install can no longer be taken back as it was. base-devel needs what it added."
              && Logic.undoRefusal({code: "cannot-undo", reason: "explicit"}) === "This install can no longer be taken back as it was. You installed it yourself since."
              && Logic.undoRefusal({code: "changed"}) === "", "a refused undo says what blocks it");
    }
    function aurInstallScenario(): void {
        // VID11: after an AUR build was installed, its page said so above a "Build and install" button.
        controller.setAur(true, false); settle();
        controller.readPopular(); settle();
        const popular = controller.aurPopular[0];
        check(popular?.key === "aur:code-bin" && !popular.installed, "the AUR program is on the popular list, not installed");
        controller.openProgram(popular); settle();
        check(controller.selectedEdition?.key === "aur:code-bin" && !controller.selectedEdition.installed, "its page opens on the AUR edition");
        controller.prepareInstall(); settle();
        check(controller.previewOpen && controller.plan?.ok === true, "the build preview opens");
        controller.executePlan();
        let left = 5000;
        while (!controller.succeeded && left > 0) { input.wait(10); left -= 10; }
        check(controller.succeeded && controller.message === "The program from the AUR is installed.", "the build reports it is installed");
        check(controller.phase === "catalog" && !controller.catalogReceived, "the refresh after the build is still on its way");
        const meanwhile = controller.selectedEdition || {};
        check(!!meanwhile.installed && !(meanwhile.actions || []).includes("install"),
              "while the refresh runs the page shows the program installed, without Build and install");
        left = 5000;
        while ((controller.busy || controller.refreshPending || controller.inventoryLoading || !controller.catalogReceived) && left > 0) { input.wait(20); left -= 20; }
        check(!controller.busy && controller.catalogReceived && !controller.inventoryLoading, "the refresh after the build finishes");
        const shown = controller.selectedEdition || {};
        check(!!shown.installed && !(shown.actions || []).includes("install") && (shown.actions || []).includes("remove"),
              "after the refresh the page offers Remove, not Build and install");
        check(controller.message === "The program from the AUR is installed.", "the result line stays after the refresh");
    }
    TestCase { id: input; when: false; name: "ProgramsProcess" }
    // The longest time the event loop went without running a 16 ms timer: a window cannot draw or
    // answer its IPC for that long.
    property double lastTick: Date.now()
    property double longestStall: 0
    Timer {
        interval: 16; running: true; repeat: true
        onTriggered: { const now = Date.now(); test.longestStall = Math.max(test.longestStall, now - test.lastTick); test.lastTick = now; }
    }
    function waitIdle(limit: int): int {
        const start = Date.now();
        while ((controller.busy || controller.refreshPending || controller.inventoryLoading) && Date.now() - start < limit) input.wait(20);
        return Date.now() - start;
    }
    // A real catalog has thousands of programs. Reopening the window refreshes it quietly, with a
    // program page open; the window must keep drawing, and the refresh must end in seconds.
    function largeScenario(): void {
        const rows = Number(Quickshell.env("TEST_ROWS"));
        waitIdle(60000);
        check(controller.allRows.length === rows && controller.installedReceived, "the large catalog loads: " + controller.allRows.length);
        test.lastTick = Date.now(); test.longestStall = 0;
        const start = Date.now();
        windowHook.present();
        check(controller.quietRefresh, "reopening refreshes the large catalog quietly");
        controller.openProgram(controller.allPrograms[5]);
        const opened = controller.selectedEdition?.key;
        check(!!opened && controller.heldByRefresh, "a program opens during the quiet refresh");
        waitIdle(60000);
        const took = Date.now() - start;
        input.wait(50);
        console.log("LARGE refresh of " + rows + " rows took " + took + " ms; longest stall " + test.longestStall + " ms");
        check(controller.catalogReceived && controller.allRows.length === rows && !controller.busy, "the quiet refresh completes");
        check(!controller.heldByRefresh && controller.selectedEdition?.key === opened,
              "the refresh releases the wait and keeps the open program");
        check(took < 6000, "the quiet refresh of " + rows + " rows ends within 6 s: " + took + " ms");
        check(test.longestStall < 2000, "no single stall of the window over 2 s: " + test.longestStall + " ms");
        // The Updates page looks every pending update up in this catalog, twice a row (name and icon).
        const lookupStart = Date.now();
        const index = Logic.catalogIndex(controller.allRows);
        const named = controller.allRows.filter(row => typeof row.package === "string" && row.package);
        let found = 0;
        for (let i = 0; i < 60; ++i) {
            const row = named[(i * 97) % named.length];
            const hit = Logic.catalogProgram(index, row.package, [], row.kind);
            if (hit.kind === row.kind && (hit.package === row.package || Logic.identity(hit) === Logic.identity({id: row.package}))) ++found;
        }
        const lookups = Date.now() - lookupStart;
        console.log("LARGE 60 update lookups took " + lookups + " ms");
        check(found === 60, "update lookups find their programs: " + found);
        check(lookups < 1500, "60 update lookups in a catalog of " + rows + " rows take under 1.5 s: " + lookups + " ms");
    }
    function check(ok: bool, text: string): void {
        if (!ok) { failed = true; console.error("ASSERTION_FAILED " + text); }
    }
    function settle(): void {
        let left = 5000;
        while (controller.busy && left > 0) { input.wait(20); left -= 20; }
        check(!controller.busy, "request completes");
    }
    function packagesReady(): void {
        let left = 5000;
        while ((!controller.packagesReceived || controller.busy) && left > 0) { input.wait(20); left -= 20; }
        check(controller.packagesReceived && !controller.busy, "package pages complete");
    }
    // The installed list is its own process, not a catalog request: settle() does not wait for it.
    function inventoryReady(): void {
        let left = 5000;
        while ((controller.inventoryLoading || !controller.installedReceived && !controller.inventoryError) && left > 0) { input.wait(20); left -= 20; }
        check(!controller.inventoryLoading && (controller.installedReceived || !!controller.inventoryError), "installed inventory answered");
    }
    function requestStarted(phase: string): void {
        let left = 2000;
        while (controller.phase !== phase && left > 0) { input.wait(10); left -= 10; }
        check(controller.phase === phase, "delayed request started: " + phase);
    }
    function packagesScenario(): void {
        check(controller.scope === "programs" && controller.packageRows.length === 0 && !controller.packagesReceived,
              "Programs remains default without loading package inventory");
        controller.selectScope("packages"); packagesReady();
        check(controller.query === "" && controller.packageRows.length === 505 && controller.packageTotal === 505,
              "empty package query fetches every page beyond 500 rows");
        const terminal = controller.packageRows.find(row => row.package === "pkg-0");
        check(terminal.installed && terminal.actions.includes("remove"), "terminal-installed package retains engine installed state");
        const mapped = controller.packageRows.find(row => row.programKey === "arch:editor");
        controller.openPackage(mapped);
        check(controller.selectedEdition.key === "arch:editor" && controller.selectedProgram.editions.length === 2,
              "package program key opens exact Arch edition of a merged program page");
        controller.search("pkg-504"); packagesReady();
        check(controller.packageRows.length === 1 && controller.packageRows[0].package === "pkg-504",
              "package name search finds entries beyond first inventory page");
        const catalogBeforeCollision = controller.allRows;
        controller.allRows = controller.allRows.concat([{key: "arch:other/pkg-504", id: "pkg-504",
            package: "pkg-504", name: "Conflicting program", kind: "arch", repository: "other",
            installed: false, actions: ["install"]}]);
        controller.preparePackage(controller.packageRows[0], "install"); settle();
        check(controller.selectedEdition.key === "package:extra/pkg-504" && controller.plan?.key === "package:extra/pkg-504",
              "generic package identity cannot merge into another repository's AppStream edition");
        controller.cancelPlan();
        controller.allRows = catalogBeforeCollision;
        controller.search("compression"); packagesReady();
        check(controller.packageRows.length === 1 && controller.packageRows[0].package === "pkg-2",
              "summary-only package search comes from engine results");
        const generic = controller.packageRows[0];
        controller.externalBusy = true;
        controller.preparePackage(generic, "install");
        check(!controller.busy && !controller.plan, "external update blocks package planning");
        controller.externalBusy = false;
        controller.preparePackage(generic, "install"); settle();
        check(controller.plan?.key === generic.key && controller.previewOpen && controller.planRows.length === 2,
              "generic package uses existing reviewed transaction preview");
        controller.externalBusy = true;
        controller.executePlan();
        check(!controller.busy && controller.previewOpen, "external update blocks package execution");
        controller.externalBusy = false;
        controller.executePlan(); controller.selectScope("programs");
        settle(); input.wait(350); settle();
        check(controller.selectedEdition.installed && controller.selectedEdition.actions.includes("remove")
              && controller.selectedEdition.key === generic.key && !controller.packagesReceived,
              "exact package details refresh installed actions after leaving package scope during execution");
        controller.selectScope("packages"); packagesReady();
        check(controller.packageRows[0].installed && controller.packageRows[0].actions.includes("remove")
              && controller.selectedEdition.installed && controller.selectedEdition.actions.includes("remove"),
              "install refresh reconciles both package row and selected action state");
        const catalogBeforeHidden = controller.allRows;
        controller.allRows = controller.allRows.concat([{key: "arch:other/pkg-2", id: "pkg-2", package: "pkg-2",
            name: "Conflicting package", kind: "arch", installed: false, actions: ["install"]}]);
        controller.search("no-such-package"); packagesReady();
        check(controller.packageRows.length === 0 && controller.selectedEdition.key === generic.key
              && controller.selectedEdition.installed && controller.selectedEdition.actions.includes("remove"),
              "hidden filtered package retains installed state and cannot migrate to another repository");
        controller.allRows = catalogBeforeHidden;
        controller.search("compression"); packagesReady();
        controller.preparePackage(controller.packageRows[0], "remove"); settle();
        check(controller.plan?.action === "remove" && controller.previewOpen, "generic removal uses same preview path");
        controller.executePlan(); controller.search("no-such-package");
        settle(); input.wait(100); packagesReady();
        check(controller.packageRows.length === 0 && controller.selectedEdition.key === generic.key
              && !controller.selectedEdition.installed && controller.selectedEdition.actions.includes("install"),
              "exact details reconcile removal even when current query hides the package");
        controller.search("compression"); packagesReady();
        check(!controller.packageRows[0].installed && controller.packageRows[0].actions.includes("install")
              && !controller.selectedEdition.installed && controller.selectedEdition.actions.includes("install"),
              "remove refresh restores install state on row and selection");
        controller.search("old"); requestStarted("packages");
        controller.search("new");
        check(controller.packageRows.length === 0 && !controller.packagesReceived, "new query clears stale package results");
        packagesReady();
        check(controller.packageRows.length === 1 && controller.packageRows[0].name === "New response", "late package query cannot replace current results");
        controller.search("old"); requestStarted("packages");
        controller.selectScope("programs"); controller.search("new");
        input.wait(900); settle();
        check(controller.packageRows.length === 0 && !controller.packagesReceived && controller.programs[0]?.key === "arch:new",
              "late package response cannot enter Programs scope");
        controller.search("old"); requestStarted("search");
        controller.selectScope("packages"); controller.search(""); packagesReady();
        check(controller.searchRows.length === 0 && controller.packageRows.length === 505,
              "late Programs response cannot enter All packages scope");
        check(controller.allRows.length === 4 && controller.allPrograms.length === 3, "package browsing preserves Programs catalog");
    }
    function developmentWebsitesScenario(): void {
        controller.readDevelopment(); settle();
        check(controller.developmentKits.length === 1 && !controller.developmentKits[0].installed, "development inventory comes from engine data");
        controller.externalBusy = true;
        controller.prepareKit(controller.developmentKits[0], "install", true);
        check(!controller.previewOpen, "external updates block kit planning");
        controller.externalBusy = false;
        controller.prepareKit(controller.developmentKits[0], "install", true); settle();
        check(controller.previewOpen && controller.planTarget === "docker" && controller.dockerAccess, "kit preview retains explicit Docker consent");
        check(controller.plan.key === "development:docker", "kit plan binds its exact identity");
        controller.executePlan(); settle(); input.wait(250); settle();
        check(controller.succeeded && controller.developmentKits[0].installed, "kit transaction refreshes installed state");
        check(controller.output.includes("Preparing tools"), "kit setup progress remains available");
        check(controller.message.includes("sign out"), "Docker success retains sign-out notice after refresh");
        controller.prepareKit(controller.developmentKits[0], "remove", false); settle();
        controller.executePlan(); settle(); input.wait(250); settle();
        check(!controller.succeeded && controller.recoveryPoint?.id === "restore-kit"
              && controller.recoveryPoint.operation === "remove" && controller.recoveryPoint.snapshot === "23",
              "failed kit removal retains its recovery point after refresh");
        check(controller.message.includes("Removal failed") && controller.message.includes("sign out"),
              "failed Docker removal preserves error and group session notice");
        controller.readWebsites(); settle();
        check(controller.websiteApps.length === 0, "website listing starts empty");
        controller.addWebsite("https://example.com", "Example"); settle();
        check(controller.websiteApps[0].name === "Example", "explicit Add publishes website result");
        check(test.websiteActions.join() === "add", "successful Add tells the page to clear its form");
        controller.removeWebsite("site-one"); settle();
        check(controller.websiteApps.length === 0, "website removal updates list");
        check(test.websiteActions.join() === "add,remove", "successful removal tells the page to restore focus");
    }
    Timer {
        interval: 25; running: true
        onTriggered: {
            if (Quickshell.env("TEST_SCENARIO") === "large" || Quickshell.env("TEST_SCENARIO") === "replay") {
                test.largeScenario();
                if (!test.failed) console.log("PROGRAMS_OK");
                Qt.quit(); return;
            }
            // Startup is the catalog and the installed list; every scenario begins after both.
            test.settle();
            test.inventoryReady();
            test.check(controller.allRows.length === 4, "all catalog pages received");
            test.check(controller.allPrograms.length === 3, "source editions merged");
            test.check(!controller.installedPrograms.some(row => row.package === "emaki-config"), "protected catalog programs do not become removable rows");
            const scenario = Quickshell.env("TEST_SCENARIO");
            if (scenario === "development-websites") {
                test.developmentWebsitesScenario();
                if (!test.failed) console.log("PROGRAMS_OK");
                Qt.quit(); return;
            }
            if (scenario.startsWith("plan-")) {
                const aur = {key: "aur:editor-tools", id: "editor-tools", name: "Editor tools", kind: "aur", installed: false, actions: ["install"]};
                controller.openProgram(aur);
                controller.prepareInstall();
                if (scenario === "plan-refresh") controller.refresh();
                test.settle(); input.wait(100);
                const expected = (scenario === "plan-refused" || scenario === "plan-refresh") ? "The build instructions could not be read."
                    : scenario === "plan-disconnect" ? "The work stopped before it finished. Look at Installed before trying again."
                    : "Something went wrong in Programs & Updates. Try again.";
                test.check(controller.previewOpen && !controller.plan && !controller.busy,
                           "failed planning keeps preview open without a confirmable plan");
                test.check(controller.messageIsError && controller.message === expected, "preview retains the real refusal or transport error");
                if (scenario === "plan-refresh") test.check(controller.refreshPending, "refresh remains queued until failed preview closes");
                controller.executePlan(); input.wait(100);
                test.check(!controller.busy && controller.previewOpen && !controller.plan, "failed plan cannot execute");
                controller.cancelPlan(); input.wait(100); test.settle();
                test.check(!controller.refreshPending, "queued refresh drains after Cancel");
                test.check(!controller.previewOpen && !controller.message, "Cancel dismisses failed preview");
                if (!test.failed) console.log("PROGRAMS_OK");
                Qt.quit(); return;
            }
            if (scenario === "presentation") {
                test.presentationScenario();
                if (!test.failed) console.log("PROGRAMS_OK");
                Qt.quit(); return;
            }
            if (scenario === "aur") {
                test.aurScenario();
                if (!test.failed) console.log("PROGRAMS_OK");
                Qt.quit(); return;
            }
            if (scenario === "aur-install") {
                test.aurInstallScenario();
                if (!test.failed) console.log("PROGRAMS_OK");
                Qt.quit(); return;
            }
            if (scenario === "packages") {
                test.packagesScenario();
                if (!test.failed) console.log("PROGRAMS_OK");
                Qt.quit(); return;
            }
            const bare = {key: "arch:calc", id: "org.example.Calc.desktop", package: "calc", kind: "arch", name: "Calc", icons: [], installed: true, actions: ["remove"]};
            const rich = Object.assign({}, bare, {icons: [{kind: "stock", name: "calculator"}]});
            const flatpak = Object.assign({}, rich, {key: "flatpak:calc", kind: "flatpak", installed: false, actions: ["install"], icons: [{kind: "stock", name: "flat-calculator"}]});
            const duplicates = [bare, rich, flatpak];
            const merged = Logic.merge(duplicates)[0];
            test.check(merged.editions.length === 2 && merged.editions[0].icons.length === 1 && merged.editions[0].icons[0].name === "calculator", "same-edition duplicate metadata retains catalog icons");
            test.check(merged.editions[0].actions[0] === "remove" && merged.editions[1].actions[0] === "install", "duplicate metadata never crosses edition action identities");
            const installed = Logic.installedRows(duplicates, [{package: "calc", ids: ["org.example.Calc.desktop"], names: ["Calc"]}]);
            test.check(installed.length === 1 && installed[0].icons.length === 1, "installed inventory retains later catalog icons");
            if (typeof Logic.catalogProgram === "function") {
                const entries = [{id: "org.example.Calc", icon: "launch-calc"}];
                const lookup = Logic.catalogProgram(duplicates, "calc", entries, "arch");
                test.check(lookup.icons.length === 1 && lookup.icon === "launch-calc" && lookup.key === bare.key, "update lookup merges icons and launch fallback");
                test.check(Logic.catalogProgram([], "org.example.Calc.desktop", entries, "arch").icon === "launch-calc", "update without catalog uses desktop icon");
                test.check(Logic.catalogProgram(duplicates, "org.example.Calc", entries, "flatpak").key === flatpak.key, "update lookup retains requested edition");
                const index = Logic.catalogIndex(duplicates);
                test.check(JSON.stringify(Logic.catalogProgram(index, "calc", entries, "arch")) === JSON.stringify(lookup)
                    && Logic.catalogProgram(index, "org.example.Calc", entries, "flatpak").key === flatpak.key, "an indexed catalog answers as its rows do");
            } else test.check(false, "shared update lookup is available");

            if (scenario === "inventory-empty") {
                test.check(!controller.inventoryError && !controller.inventoryLoading && controller.installedReceived && controller.installedPrograms.length === 0, "successful empty inventory is distinct from failure");
                if (!test.failed) console.log("PROGRAMS_OK");
                Qt.quit(); return;
            }
            if (scenario === "inventory-missing") {
                input.wait(350);
                test.check(!!controller.inventoryError && !controller.inventoryLoading && !controller.installedReceived, "missing inventory service is distinct from an empty list");
                if (!test.failed) console.log("PROGRAMS_OK");
                Qt.quit(); return;
            }
            const unknown = controller.installedPrograms.find(row => row.package === "local-editor");
            if (!unknown) {
                // Fail now with a message instead of a TypeError that leaves the window open.
                test.check(false, "the installed inventory lists local-editor");
                Qt.quit();
                return;
            }
            test.check(unknown.sourceLabel === "Installed", "unknown native source is not called Arch or AUR");
            test.check(unknown.icon === "editor-icon", "legacy entry receives its real desktop icon");
            const foreign = Logic.installedRows([{key: "aur:foreign", id: "foreign", package: "foreign", kind: "aur", name: "Foreign", installed: true}], [{package: "foreign", ids: ["foreign.desktop"], names: ["Foreign"], preinstalled: true}])[0];
            test.check(foreign.kind === "aur" && foreign.sourceLabel === "AUR" && foreign.key === "legacy:foreign" && foreign.actions.includes("remove"), "known installed AUR uses native removal");
            test.check(foreign.preinstalled && Logic.launchIds(foreign).includes("foreign"), "native metadata and launch identities retained");
            if (scenario.startsWith("launch-")) {
                controller.selectedEdition = {key:"package:extra/local-editor", id:"local-editor", packageOnly:true, installed:true};
                test.check(controller.selectedLauncher?.id === "local-editor", "package Open resolves its desktop entry");
                controller.message = "";
                controller.messageIsError = false;
                controller.openInstalled(); input.wait(1500);
                if (scenario === "launch-nonzero")
                    test.check(controller.messageIsError && controller.message === "The program could not open.", "launcher nonzero exit remains visible");
                else
                    test.check(!controller.message, "accepted launch does not report an error");
                if (!test.failed) console.log("PROGRAMS_OK");
                Qt.quit(); return;
            }
            if (scenario.startsWith("missing-")) {
                controller.openProgram(unknown);
                if (scenario === "missing-launcher") {
                    controller.selectedEdition = {key:"package:extra/library", id:"library", packageOnly:true, installed:true};
                    test.check(!controller.selectedLauncher, "launcher-less package has no launch target");
                    controller.selectedEdition = Object.assign({}, controller.selectedEdition, {ids:["missing-entry.desktop", "local-editor.desktop"]});
                    test.check(controller.selectedLauncher?.id === "local-editor", "package launcher resolves alternate desktop identity");
                    controller.selectedEdition = Object.assign({}, controller.selectedEdition, {ids:["hidden-editor.desktop"]});
                    test.check(controller.selectedLauncher?.id === "hidden-editor", "alternate NoDisplay desktop entry remains launchable");
                    controller.selectedEdition = Object.assign({}, controller.selectedEdition, {installed:false});
                    test.check(!controller.selectedLauncher, "uninstalled package has no launch target");
                    controller.openProgram(unknown);
                    controller.messageIsError = false;
                    controller.openInstalled();
                    // The helper and its fallback worker are separate Python processes: under load
                    // their exit takes longer than any fixed pause, so wait for the result instead.
                    for (let left = 2500; !controller.messageIsError && left > 0; left -= 20) input.wait(20);
                    test.check(controller.messageIsError, "launch failure resets success message severity");
                    test.check(controller.message === "The program could not open.", "missing launcher reports failure after exact alternate desktop identity lookup: " + controller.message);
                } else {
                    controller.prepareRemove(); test.settle();
                    if (scenario === "missing-remove") { controller.executePlan(); test.settle(); }
                    test.check(!controller.busy && controller.message === "Programs & Updates could not start. Close the window and open it again.", "missing native operation releases busy state");
                }
                if (!test.failed) console.log("PROGRAMS_OK");
                Qt.quit();
                return;
            }
            const app = controller.allPrograms.find(row => row.id === "org.example.Editor.desktop");
            test.check(app && app.editions.length === 2 && app.kind === "arch", "Arch first");
            const originalRows = controller.allRows;
            controller.allRows = originalRows.map(row => Object.assign({}, row, {installed: row.kind === "flatpak" || row.installed}));
            controller.openProgram(controller.allPrograms.find(row => row.editions.length === 2));
            test.check(controller.selectedEdition.kind === "flatpak" && controller.selectedEdition.installed, "merged page opens installed Flathub edition");
            controller.selectEdition("arch");
            test.check(controller.selectedEdition.kind === "arch", "explicit switch still selects uninstalled Arch edition");
            controller.allRows = originalRows;
            controller.openProgram(app);
            test.check(controller.selectedEdition.kind === "arch", "uninstalled page defaults to Arch");
            controller.selectEdition("flatpak");
            test.check(controller.selectedEdition.kind === "flatpak", "source switch selects real edition");
            controller.selectEdition("arch");
            controller.externalBusy = true;
            controller.prepareInstall();
            test.check(!controller.busy && !controller.plan, "updates block program mutation plans");
            controller.externalBusy = false;
            controller.search("Editor"); input.wait(350); test.settle();
            controller.prepareInstall(); test.settle();
            test.check(controller.previewOpen && controller.planRows.length === 2, "full upgrade plan visible");
            controller.executePlan();
            test.check(controller.mutating, "execution progress state");
            controller.selectEdition("flatpak");
            test.check(controller.selectedEdition.kind === "arch", "edition stays bound during execution");
            test.settle(); input.wait(100); test.settle();
            if (scenario.startsWith("inventory-")) {
                test.check(!!controller.inventoryError && !controller.inventoryLoading && !controller.installedReceived, "failed inventory is reported separately");
                test.check(controller.installedPrograms.some(row => row.package === "local-editor"), "last known inventory survives failed refresh");
                test.check(controller.selectedEdition.installed && controller.selectedEdition.actions.includes("remove"), "fresh catalog reconciles installed page despite failed removal inventory");
                input.wait(3600); test.settle();
                if (scenario === "inventory-permanent") {
                    test.check(!!controller.inventoryError && !controller.inventoryLoading && !controller.installedReceived, "bounded retries leave persistent inventory error visible");
                    test.check(controller.installedPrograms.some(row => row.package === "local-editor"), "persistent failure preserves last good list");
                    if (!test.failed) console.log("PROGRAMS_OK");
                    Qt.quit(); return;
                }
                test.check(!controller.inventoryError && controller.installedReceived && !controller.inventoryLoading, "successful retry clears inventory failure");
                if (!test.failed) console.log("PROGRAMS_OK");
                Qt.quit(); return;
            }
            if (scenario === "success") {
                test.check(controller.succeeded && controller.selectedEdition.installed, "completion refreshes installed state");
                test.check(controller.installedPrograms.find(row => row.key === "arch:editor")?.preinstalled === true, "matched catalog keeps preinstalled marker");
                test.check(controller.selectedEdition.ids[0] === "editor-launch.desktop", "selection retains native launcher identities after refresh");
                controller.prepareRemove(); test.settle();
                test.check(controller.planRows.length === 2 && controller.previewOpen, "removal preview includes dependencies");
                controller.cancelPlan();
                controller.executePlan();
                test.check(!controller.busy, "cancelled plan cannot execute");
            } else {
                test.check(!controller.succeeded && !controller.plan && !controller.previewOpen, "failed execution closes preview and cannot reuse plan");
                const error = controller.message;
                input.wait(700); test.settle();
                test.check(!!error && controller.message === error && controller.messageIsError, "failure remains visible after background catalog and search refresh");
            }
            const local = controller.installedPrograms.find(row => row.package === "local-editor");
            test.check(!!local, "installed desktop programs without AppStream remain listed");
            controller.prepareRemove(local); test.settle();
            test.check(controller.plan && controller.planRows.length === 2, "legacy removal preview retains exact dependencies");
            controller.executePlan(); test.settle(); input.wait(100); test.settle();
            test.check(!controller.installedPrograms.some(row => row.package === "local-editor"), "removed legacy program leaves inventory");
            test.check(!controller.selectedEdition.installed && !controller.selectedEdition.actions.length, "removed synthetic selection cannot remain open or removable");
            controller.search("Editor"); input.wait(350); test.settle();
            test.check(controller.programs.some(row => row.kind === "aur"), "AUR search results visible");
            test.check(!controller.allPrograms.some(row => row.kind === "aur"), "AUR excluded from Browse");
            controller.search("needle"); input.wait(350); test.settle();
            test.check(controller.programs.length === 505, "all search pages and keyword-only matches survive");
            controller.search("old"); input.wait(280);
            controller.search("new");
            test.check(controller.programs.length === 0, "new query hides previous search immediately");
            input.wait(550); test.settle();
            test.check(controller.programs.length === 1 && controller.programs[0].key === "arch:new", "late query cannot replace newer category-only engine match");
            if (!test.failed) console.log("PROGRAMS_OK");
            Qt.quit();
        }
    }
}
