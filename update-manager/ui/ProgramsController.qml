// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io
import "file:@EMAKI_DATADIR@/shell" as Shell
import "ManagerLogic.js" as Logic

Item {
    id: root
    property var allRows: []
    readonly property var launchEntries: DesktopEntries.applications.values
    readonly property var allPrograms: Logic.merge(allRows.filter(row => row.kind !== "aur"))
    readonly property var programs: query ? Logic.merge(searchRows) : Logic.filter(allPrograms, "", category)
    property var legacyPrograms: []
    readonly property var installedPrograms: Logic.installedRows(allRows, legacyPrograms).map(row => {
        const ids = Logic.launchIds(row);
        const entry = launchEntries.find(item => ids.includes(item.id));
        return Object.assign({}, row, {icon: row.icon || entry?.icon || ""});
    })
    readonly property var categories: Logic.categoryCounts(allPrograms)
    property var searchRows: []
    property string scope: "programs"
    property var packageRows: []
    property int packageTotal: 0
    property bool packagesReceived: false
    property string query: ""
    property string category: ""
    property var selectedProgram: null
    property var selectedEdition: null
    readonly property var selectedLauncher: {
        if (!selectedEdition?.installed) return null;
        for (const id of Logic.launchIds(selectedEdition)) {
            const entry = launchEntries.find(item => item.id === id) || DesktopEntries.byId(id);
            if (entry) return entry;
        }
        return selectedEdition.id ? DesktopEntries.heuristicLookup(selectedEdition.id) : null;
    }
    property string phase: "idle"
    readonly property bool busy: phase !== "idle"
    readonly property bool mutating: phase === "installing" || phase === "removing"
    property bool externalBusy: false
    property string message: ""
    property bool messageIsError: true
    property string output: ""
    property real percent: -1
    property var warnings: []
    property var plan: null
    property string planAction: ""
    readonly property var planRows: Logic.planRows(plan)
    property bool previewOpen: false
    property bool succeeded: false
    property int serial: 0
    property var pending: null
    property var collected: []
    property var searchCollected: []
    property int searchGeneration: 0
    property int activeSearchGeneration: 0
    property bool catalogReceived: false
    property bool installedReceived: false
    property bool inventoryLoading: false
    property string inventoryError: ""
    property int inventoryRetriesRemaining: 0
    property bool ready: false
    property bool refreshPending: false
    onBusyChanged: if (!busy && refreshPending) Qt.callLater(drainRefresh)
    onExternalBusyChanged: if (!externalBusy && refreshPending) Qt.callLater(drainRefresh)
    onInventoryLoadingChanged: if (!inventoryLoading && refreshPending) Qt.callLater(drainRefresh)
    signal transactionFinished(bool ok)

    function request(method: string, params: var, nextPhase: string): void {
        if (busy) return;
        if (nextPhase === "planning" || nextPhase === "installing" || nextPhase === "removing") {
            message = ""; messageIsError = true;
        }
        phase = nextPhase;
        pending = {version: 1, id: ++serial, method: method, params: params};
        if (params.key && params.key.startsWith("legacy:")) {
            const packageName = params.key.slice(7);
            legacyOperation.command = method === "plan"
                ? ["emaki-programs", "preview", packageName, "--json"]
                : ["emaki-programs", "remove", packageName, params.token, "--json"];
            legacyOperation.requestId = pending.id;
            legacyOperation.completed = false;
            legacyStartWatch.restart();
            legacyOperation.running = true;
            return;
        }
        if (ready) service.write(JSON.stringify(pending) + "\n");
        else { service.running = true; startWatch.restart(); }
    }
    function refresh(): void {
        packagesReceived = false;
        refreshPending = true;
        drainRefresh();
    }
    function drainRefresh(): void {
        // A presentation can arrive while a request or update is still running.
        // Keep one refresh queued until all readers can take a fresh snapshot.
        if (!refreshPending || busy || externalBusy || inventoryLoading) return;
        refreshPending = false;
        plan = null; planAction = ""; previewOpen = false;
        searchRows = []; packageRows = []; packageTotal = 0;
        collected = [];
        catalogReceived = false;
        installedReceived = false;
        packagesReceived = false;
        inventoryRetry.stop();
        inventoryRetriesRemaining = 3;
        readInventory();
        request("catalog", {limit: 500, offset: 0, refresh: true}, "catalog");
    }
    function readInventory(): void {
        if (installedList.running) return;
        inventoryLoading = true;
        installedList.completed = false;
        installedList.reply = "";
        inventoryStartWatch.restart();
        installedList.running = true;
    }
    function search(text: string): void {
        query = text; category = "";
        ++searchGeneration;
        searchRows = [];
        packageRows = []; packageTotal = 0; packagesReceived = false;
        searchDelay.restart();
    }
    function selectScope(name: string): void {
        if (name !== "programs" && name !== "packages") return;
        scope = name;
        search(query);
    }
    function selectCategory(name: string): void {
        query = ""; category = name; ++searchGeneration; searchRows = [];
        if (scope === "packages") { packageRows = []; packagesReceived = false; searchDelay.restart(); }
    }
    function openPackage(row: var): void {
        const program = allRows.find(item => item.key === row.programKey);
        if (program) openProgram(program);
    }
    function preparePackage(row: var, action: string): void {
        if (busy || externalBusy || refreshPending) return;
        // A package name can also be an AppStream ID. Keep the repository
        // target selected by the row, independent of program edition merging.
        selectedEdition = row;
        selectedProgram = Object.assign({}, row, {editions: [row]});
        clearPlan();
        prepare(action);
    }
    function openProgram(row: var): void {
        if (busy) return;
        const merged = Logic.merge(installedPrograms.concat(allRows, searchRows)).find(item => Logic.identity(item) === Logic.identity(row));
        selectedProgram = merged || Object.assign({}, row, {editions: row.editions || [row]});
        selectedEdition = row.editions ? Logic.edition(selectedProgram) : (selectedProgram.editions.find(item => item.key === row.key) || selectedProgram.editions[0]);
        clearPlan();
    }
    function selectEdition(kind: string): void {
        if (busy || !selectedProgram) return;
        selectedEdition = Logic.edition(selectedProgram, kind);
        clearPlan();
    }
    function clearPlan(): void { plan = null; planAction = ""; previewOpen = false; message = ""; messageIsError = true; }
    function prepareInstall(): void { prepare("install"); }
    function prepareRemove(row: var): void {
        if (row) openProgram(row);
        prepare("remove");
    }
    function prepare(action: string): void {
        if (busy || externalBusy || refreshPending || !selectedEdition || !(selectedEdition.actions || []).includes(action)) return;
        plan = null; planAction = action; previewOpen = true;
        request("plan", {action: action, key: selectedEdition.key}, "planning");
    }
    function executePlan(): void {
        if (busy || externalBusy || refreshPending || !plan || !plan.ok || !plan.token || !selectedEdition || plan.key !== selectedEdition.key) return;
        output = ""; percent = -1; succeeded = false;
        request("execute", {action: planAction, key: plan.key, token: plan.token}, planAction === "install" ? "installing" : "removing");
    }
    function cancelPlan(): void { if (!busy) clearPlan(); }
    function openInstalled(): void {
        if (!selectedEdition || !selectedEdition.installed || launcher.running) return;
        const entry = selectedLauncher;
        if (!entry) { messageIsError = true; message = "This program cannot be opened from here."; return; }
        launcher.command = [Shell.Platform.python, "-B", Shell.Platform.dataDir + "/shell/helpers/app_scope.py",
                            "--exec", entry.id, Quickshell.env("EMAKI_GTK_LAUNCH") || "gtk-launch", "--", entry.id + ".desktop"];
        launcher.completed = false;
        launchStartWatch.restart();
        launcher.running = true;
    }
    function reconcileSelection(): void {
        if (!catalogReceived || (!installedReceived && !inventoryError) || !selectedEdition) return;
        const previous = selectedEdition;
        // Filtered package results cannot establish whether a selected package
        // is still installed. Refresh its exact identity through details.
        if (previous.packageOnly) return;
        // A fresh catalog can reconcile the open page even while the separate
        // removable-program inventory is unavailable. Keep its last good list.
        const rows = installedReceived ? installedPrograms.concat(Logic.consolidate(allRows)) : Logic.consolidate(allRows);
        const current = rows.find(row => row.key === previous.key)
            || rows.find(row => previous.package && row.package === previous.package && row.kind === previous.kind);
        if (current) {
            selectedProgram = Logic.merge(rows).find(row => (row.editions || []).some(item => item.key === current.key));
            selectedEdition = current;
        } else if (installedReceived) {
            selectedEdition = Object.assign({}, previous, {installed: false, actions: []});
            selectedProgram = Object.assign({}, selectedEdition, {editions: [selectedEdition]});
        }
    }
    function accept(line: string): void {
        let event;
        try { event = JSON.parse(line); } catch (_) { fail("Something went wrong in Programs & Updates. Try again."); return; }
        if (!pending || event.version !== 1 || event.id !== pending.id) return;
        if (event.type === "progress") { percent = typeof event.percent === "number" ? event.percent : -1; output = (output + (event.message || "") + "\n").slice(-32768); return; }
        if (event.type !== "result") return;
        const previous = phase;
        const params = pending.params;
        pending = null; phase = "idle";
        if (!event.ok) {
            messageIsError = true;
            message = event.message || "The install or removal could not finish. See the details below.";
            if (previous === "packageDetails" && selectedEdition?.key === params.key)
                selectedEdition = Object.assign({}, selectedEdition, {actions: []});
            if (previous === "planning" || previous === "installing" || previous === "removing") { plan = null; previewOpen = false; }
            if (previous === "installing" || previous === "removing") { transactionFinished(false); refreshAfterTransaction(); }
            return;
        }
        warnings = event.warnings || warnings;
        if (previous === "catalog") {
            collected = collected.concat(event.programs || []);
            if (collected.length < event.total && (event.programs || []).length) {
                request("catalog", {limit: 500, offset: collected.length}, "catalog");
            } else {
                allRows = collected;
                catalogReceived = true;
                reconcileSelection();
                if (selectedEdition?.packageOnly)
                    request("details", {key: selectedEdition.key}, "packageDetails");
                if (query || scope === "packages") searchDelay.restart();
            }
        } else if (previous === "packageDetails") {
            if (event.program?.key === params.key && selectedEdition?.key === params.key) {
                selectedEdition = event.program;
                selectedProgram = Object.assign({}, event.program, {editions: [event.program]});
            }
        } else if (previous === "packages") {
            if (scope !== "packages" || params.query !== query || activeSearchGeneration !== searchGeneration) {
                searchDelay.restart();
            } else {
                searchCollected = searchCollected.concat(event.programs || []);
                packageRows = searchCollected;
                packageTotal = event.total || 0;
                if (searchCollected.length < event.total && (event.programs || []).length)
                    request("catalog", {scope: "packages", query: query, limit: 500, offset: searchCollected.length}, "packages");
                else { packagesReceived = true; reconcileSelection(); }
            }
        } else if (previous === "search") {
            if (scope !== "programs" || params.query !== query || activeSearchGeneration !== searchGeneration) {
                searchDelay.restart();
            } else {
                searchCollected = searchCollected.concat(event.programs || []);
                searchRows = searchCollected;
                if (searchCollected.length < event.total && (event.programs || []).length)
                    request("search", {query: query, includeAur: true, limit: 500, offset: searchCollected.length}, "search");
            }
        } else if (previous === "planning") {
            plan = event;
        } else if (previous === "installing" || previous === "removing") {
            succeeded = true; previewOpen = false; plan = null;
            messageIsError = false;
            message = event.message || "";
            transactionFinished(true);
            refreshAfterTransaction();
        }
    }
    function refreshAfterTransaction(): void {
        // Even a failed operation may have changed the installed state.
        Qt.callLater(() => root.refresh());
    }
    function fail(text: string): void {
        const changing = mutating;
        pending = null; phase = "idle"; plan = null; previewOpen = false; messageIsError = true; message = text;
        if (changing) { transactionFinished(false); refreshAfterTransaction(); }
    }
    Timer {
        id: searchDelay
        interval: 250
        onTriggered: {
            if (!root.query && root.scope !== "packages") return;
            if (root.busy) { restart(); return; }
            root.searchCollected = [];
            root.activeSearchGeneration = root.searchGeneration;
            if (root.scope === "packages")
                root.request("catalog", {scope: "packages", query: root.query, limit: 500, offset: 0}, "packages");
            else root.request("search", {query: root.query, includeAur: true, limit: 500, offset: 0}, "search");
        }
    }
    Timer {
        id: startWatch
        interval: 3000
        onTriggered: if (!root.ready) root.fail("Programs & Updates could not start. Close the window and open it again.")
    }
    Timer {
        id: legacyStartWatch
        interval: 3000
        onTriggered: root.fail("Programs & Updates could not start. Close the window and open it again.")
    }
    Timer {
        id: launchStartWatch
        interval: 3000
        onTriggered: { root.messageIsError = true; root.message = "The program could not open."; }
    }
    Process {
        id: service
        command: ["emaki-programs", "serve", "--json"]
        stdinEnabled: true
        onStarted: {
            root.ready = true; startWatch.stop();
            if (root.pending) write(JSON.stringify(root.pending) + "\n");
        }
        stdout: SplitParser { onRead: line => root.accept(line) }
        stderr: SplitParser { onRead: line => root.output = (root.output + line + "\n").slice(-32768) }
        onExited: code => {
            root.ready = false;
            if (root.pending) root.fail("The work stopped before it finished. Look at Installed before trying again.");
        }
    }
    function inventoryFailed(text: string): void {
        inventoryStartWatch.stop();
        inventoryLoading = false;
        inventoryError = text || "Your installed programs could not be loaded. Try again.";
        reconcileSelection();
        if (inventoryRetriesRemaining > 0 && !inventoryRetry.running) inventoryRetry.restart();
    }
    Timer {
        id: inventoryRetry
        interval: 1000
        onTriggered: {
            if (installedList.running) { restart(); return; }
            if (root.inventoryRetriesRemaining <= 0) return;
            --root.inventoryRetriesRemaining;
            root.readInventory();
        }
    }
    Timer {
        id: inventoryStartWatch
        interval: 3000
        onTriggered: root.inventoryFailed("Your installed programs could not be loaded. Try again.")
    }
    Process {
        id: installedList
        property string reply: ""
        property bool completed: false
        command: ["emaki-programs", "list", "--json"]
        onStarted: inventoryStartWatch.stop()
        onRunningChanged: if (!running) Qt.callLater(() => {
            if (!installedList.running && !installedList.completed && root.inventoryLoading)
                root.inventoryFailed("Your installed programs could not be loaded. Try again.");
        })
        stdout: StdioCollector { onStreamFinished: installedList.reply = text }
        onExited: code => {
            completed = true;
            inventoryStartWatch.stop();
            try {
                const result = JSON.parse(reply);
                if (code !== 0 || result.ok !== true || !Array.isArray(result.programs)) {
                    root.inventoryFailed(result.message || "Your installed programs could not be loaded. Try again.");
                    return;
                }
                root.legacyPrograms = result.programs;
                root.inventoryLoading = false;
                root.inventoryError = "";
                inventoryRetry.stop();
                root.inventoryRetriesRemaining = 0;
                root.installedReceived = true;
                root.reconcileSelection();
            } catch (_) { root.inventoryFailed("Your installed programs could not be loaded. Try again."); }
        }
    }
    Process {
        id: legacyOperation
        property string reply: ""
        property int requestId: 0
        property bool completed: false
        onStarted: { reply = ""; legacyStartWatch.stop(); }
        onRunningChanged: if (!running) Qt.callLater(() => {
            if (!legacyOperation.running && !legacyOperation.completed && root.pending?.id === legacyOperation.requestId) {
                legacyStartWatch.stop();
                root.fail("Programs & Updates could not start. Close the window and open it again.");
            }
        })
        stdout: StdioCollector { onStreamFinished: legacyOperation.reply = text }
        onExited: code => {
            completed = true;
            legacyStartWatch.stop();
            if (!root.pending) return;
            try {
                const result = JSON.parse(reply);
                const request = root.pending;
                let event = Object.assign({}, result, {version: 1, id: request.id, type: "result", ok: result.ok === true && code === 0});
                if (request.method === "plan" && event.ok) {
                    event = Object.assign(event, {key: request.params.key, action: "remove", transaction: result});
                }
                root.accept(JSON.stringify(event));
            } catch (_) { root.fail("Something went wrong in Programs & Updates. Try again."); }
        }
    }
    Process {
        id: launcher
        property bool completed: false
        stderr: SplitParser {
            onRead: line => Shell.AppLaunch.diagnostic(line)
        }
        onStarted: launchStartWatch.stop()
        onRunningChanged: if (!running) Qt.callLater(() => {
            if (!launcher.running && !launcher.completed) { launchStartWatch.stop(); root.messageIsError = true; root.message = "The program could not open."; }
        })
        onExited: code => { completed = true; launchStartWatch.stop(); if (code !== 0) { root.messageIsError = true; root.message = "The program could not open."; } }
    }
    Component.onCompleted: refresh()
}
