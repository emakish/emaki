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
    readonly property bool mutating: phase === "installing" || phase === "removing" || phase === "undoing"
    // A reopen refreshes a loaded catalog behind the pages; reading them stays possible.
    readonly property bool quietRefresh: phase === "catalog" && catalogLoaded
    // Reads behind a page already on screen: an AUR edition joining an open program page, and the
    // AUR popular list read again over the one shown. They need no loading line.
    readonly property bool quietRead: phase === "related" || (phase === "aurPopular" && aurPopularLoaded && aurPopular.length > 0)
    // Neither greys out what is on screen; an install asked for meanwhile waits for it (queuedAction).
    readonly property bool backgroundBusy: quietRefresh || quietRead
    // Something the person asked for (results, an install) waits for that refresh.
    property bool heldByRefresh: false
    // The engine answers one request at a time: an action asked for during background work starts after it.
    property string queuedAction: ""
    // Written from phase directly, not from busy/quietRead/quietRefresh: when phase changes those
    // bindings update one after another, and a mix of old and new values showed the loading line
    // for an instant (tests/ProgramsControllerTest "an AUR edition joins an open page…").
    readonly property bool foregroundBusy: phase !== "idle"
        && phase !== "installing" && phase !== "removing" && phase !== "undoing"
        && phase !== "related" && !(phase === "aurPopular" && aurPopularLoaded && aurPopular.length > 0)
        && !(phase === "catalog" && catalogLoaded && !heldByRefresh)
    // Catalog pages follow one another; only the end of the whole refresh releases the hold.
    onPhaseChanged: if (phase !== "catalog") Qt.callLater(() => { if (root.phase !== "catalog") root.heldByRefresh = false; })
    // The AUR section stays closed until this person accepts its warning (engine-side consent).
    property bool aurKnown: false
    property bool aurEnabled: false
    property bool aurShowWarning: true
    property bool aurSnapshots: false
    property string aurQuery: ""
    property var aurRows: []
    property bool aurSearched: false
    property var aurPopular: []
    property bool aurPopularLoaded: false
    // Browse's picks arrive with the catalog (update-manager/storefront.json).
    property var storefront: ({featured: {title: "", note: "", programs: []}, rows: [], names: {}})
    // The full install's programs, offered on Browse only after a minimal install. Sent by the engine
    // with the catalog as {minimal, programs}; until it is, nothing is offered.
    property var essentials: ({minimal: false, programs: []})
    // Emaki's own optional packages for this computer (update-manager/emaki-shelf.json, read by the engine).
    property var shelf: []
    property var developmentKits: []
    property var websiteApps: []
    signal websiteChanged(string action)
    property bool developmentLoaded: false
    property bool websitesLoaded: false
    property string planTarget: ""
    property bool dockerAccess: false
    function readDevelopment(): void { later("development", {}, "development"); }
    function readWebsites(): void { later("websites", {}, "websites"); }
    function prepareKit(row: var, action: string, access: bool): void {
        if (busy || externalBusy || refreshPending) return;
        clearPlan();
        selectedEdition = Object.assign({}, row, {key: "development:" + row.id, kind: "development"});
        planTarget = row.id; dockerAccess = access;
        planKey = selectedEdition.key; planAction = action; previewOpen = true;
        request("development-plan", {id: row.id, action: action, dockerAccess: access}, "planning");
    }
    function addWebsite(url: string, name: string): void {
        if (busy || externalBusy || refreshPending) return;
        message = "";
        request("website-add", {url: url, name: name}, "websiteChange");
    }
    function removeWebsite(id: string): void {
        if (busy || externalBusy || refreshPending) return;
        message = "";
        request("website-remove", {id: id}, "websiteChange");
    }
    // Background reads (consent state, AUR editions) wait for the single request slot.
    property var deferred: []
    property bool externalBusy: false
    property var recoveryPoint: null
    property string message: ""
    property bool messageIsError: true
    property string output: ""
    property real percent: -1
    property var warnings: []
    property var plan: null
    property string planAction: ""
    property string planKey: ""
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
    // Retain the last complete catalog while a new snapshot is being collected.
    property bool catalogLoaded: false
    property bool installedReceived: false
    property bool inventoryLoading: false
    property string inventoryError: ""
    property int inventoryRetriesRemaining: 0
    property bool ready: false
    property bool refreshPending: false
    onPreviewOpenChanged: if (!previewOpen && refreshPending) Qt.callLater(drainRefresh)
    onBusyChanged: if (!busy) Qt.callLater(refreshPending ? drainRefresh : drainDeferred)
    // A refresh asked for meanwhile goes first; an install queued during background work waits for
    // an update to finish instead of being dropped.
    onExternalBusyChanged: if (!externalBusy) Qt.callLater(refreshPending ? drainRefresh : drainDeferred)
    onInventoryLoadingChanged: if (!inventoryLoading && refreshPending) Qt.callLater(drainRefresh)
    signal transactionFinished(bool ok)
    signal restoreFinished(var result)

    function later(method: string, params: var, nextPhase: string): void {
        if (!busy && !refreshPending) { request(method, params, nextPhase); return; }
        // One pending read per method: a newer page or choice replaces an older one.
        deferred = deferred.filter(item => item.method !== method).concat([{method: method, params: params, phase: nextPhase}]);
    }
    function drainDeferred(): void {
        if (busy || refreshPending) return;
        // prepare() refuses during an update; keep the queued action for when it ends.
        if (queuedAction && !externalBusy) {
            const action = queuedAction;
            queuedAction = "";
            prepare(action);
            if (busy) return;
        }
        if (!deferred.length) return;
        const next = deferred[0];
        deferred = deferred.slice(1);
        request(next.method, next.params, next.phase);
    }
    function request(method: string, params: var, nextPhase: string): void {
        if (busy) return;
        if (nextPhase === "planning" || nextPhase === "installing" || nextPhase === "removing" || nextPhase === "undoing") {
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
        if (!refreshPending || busy || externalBusy || inventoryLoading || previewOpen) return;
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
        if (quietRefresh && (text || scope === "packages")) heldByRefresh = true;
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
        if (scope === "packages") {
            if (quietRefresh) heldByRefresh = true;
            packageRows = []; packagesReceived = false; searchDelay.restart();
        }
    }
    function openPackage(row: var): void {
        const program = allRows.find(item => item.key === row.programKey);
        if (program) openProgram(program);
    }
    function preparePackage(row: var, action: string): void {
        if ((busy && !backgroundBusy) || externalBusy || refreshPending) return;
        // A package name can also be an AppStream ID. Keep the repository
        // target selected by the row, independent of program edition merging.
        selectedEdition = row;
        selectedProgram = Object.assign({}, row, {editions: [row]});
        clearPlan();
        prepare(action);
    }
    function openProgram(row: var): void {
        // The page opens from the retained catalog; the refresh reconciles it (and asks a
        // package's details again) when it arrives, and installs wait for it.
        if (busy && !backgroundBusy) return;
        if (quietRefresh) heldByRefresh = true;
        const merged = Logic.merge(installedPrograms.concat(allRows, searchRows, aurRows, aurPopular)).find(item => Logic.identity(item) === Logic.identity(row));
        selectedProgram = merged || Object.assign({}, row, {editions: row.editions || [row]});
        selectedEdition = row.editions ? Logic.edition(selectedProgram) : Logic.edition(selectedProgram, row.key);
        clearPlan();
        loadRelated();
    }
    function loadRelated(): void {
        // The same program built from the AUR joins its page once the section is open.
        const program = selectedProgram;
        const base = (program?.editions || []).find(row => (row.kind === "arch" || row.kind === "flatpak") && !row.packageOnly && !String(row.key).startsWith("legacy:"));
        // After the caller's own request (a removal preview opens the page first).
        if (aurEnabled && base && !(program.editions || []).some(row => row.kind === "aur"))
            Qt.callLater(() => root.later("related", {key: base.key}, "related"));
    }
    function selectEdition(kind: string): void {
        if (busy || !selectedProgram) return;
        selectedEdition = Logic.edition(selectedProgram, kind);
        clearPlan();
    }
    function clearPlan(): void { planTarget = ""; dockerAccess = false; plan = null; planAction = ""; planKey = ""; previewOpen = false; message = ""; messageIsError = true; queuedAction = ""; }
    function prepareInstall(): void { prepare("install"); }
    function prepareRemove(row: var): void {
        if (row) openProgram(row);
        prepare("remove");
    }
    function prepareUndo(): void { prepare("undo"); }
    function prepare(action: string): void {
        if (backgroundBusy && !externalBusy && selectedEdition && (selectedEdition.actions || []).includes(action)) {
            // Its preview opens when that work ends (drainDeferred); a quiet refresh says "Refreshing…" meanwhile.
            if (quietRefresh) heldByRefresh = true;
            queuedAction = action;
            return;
        }
        if (busy || externalBusy || refreshPending || !selectedEdition || !(selectedEdition.actions || []).includes(action)) return;
        plan = null; planAction = action; previewOpen = true;
        // An Installed row keeps its removal identity; undo goes to the recorded catalog edition.
        planKey = action === "undo" && selectedEdition.undoKey ? selectedEdition.undoKey
            : action === "install" && selectedEdition.catalogKey ? selectedEdition.catalogKey : selectedEdition.key;
        request("plan", {action: action, key: planKey}, "planning");
    }
    function executePlan(): void {
        if (busy || externalBusy || refreshPending || !plan || !plan.ok || !plan.token || !selectedEdition || plan.key !== planKey) return;
        output = ""; percent = -1; succeeded = false;
        if (planTarget) {
            request("development-execute", {id: planTarget, action: planAction, dockerAccess: dockerAccess, token: plan.token},
                    (planAction === "install" || planAction === "finish") ? "installing" : planAction === "undo" ? "undoing" : "removing");
            return;
        }
        request("execute", {action: planAction, key: plan.key, token: plan.token},
                planAction === "install" ? "installing" : planAction === "undo" ? "undoing" : "removing");
    }
    function readAur(): void { later("aur", {}, "aur"); }
    function setAur(accept: bool, showWarning: bool): void {
        if (externalBusy && accept) return;
        later("aur", accept ? {accept: true, showWarning: showWarning} : {accept: false}, "aur");
    }
    function readPopular(): void { later("popular", {}, "aurPopular"); }
    function searchAur(text: string): void {
        aurQuery = text; aurRows = []; aurSearched = false;
        aurDelay.restart();
    }
    function cancelPlan(): void { if (!busy) clearPlan(); }
    function openInstalled(): void {
        if (!selectedEdition || !selectedEdition.installed || launcher.running) return;
        const entry = selectedLauncher;
        if (!entry) { messageIsError = true; message = qsTranslate("ProgramsController", "This program cannot be opened from here."); return; }
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
        const current = rows.find(row => row.key === previous.key || row.catalogKey === previous.key)
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
        try { event = JSON.parse(line); } catch (_) { fail(qsTranslate("ProgramsController", "Something went wrong in Programs & Updates. Try again.")); return; }
        if (!pending || event.version !== 1 || event.id !== pending.id) return;
        if (event.type === "progress") { percent = typeof event.percent === "number" ? event.percent : -1; output = (output + (event.message || "") + "\n").slice(-32768); return; }
        if (event.type !== "result") return;
        const previous = phase;
        const params = pending.params;
        const method = pending.method;
        pending = null; phase = "idle";
        if (previous === "undoing" && event.restart === true) restoreFinished(event);
        if (previous === "installing" || previous === "removing" && !!planTarget) {
            recoveryPoint = !event.ok && event.restoreToken
                ? {id: String(event.restoreToken), snapshot: String(event.snapshot || ""), operation: previous === "removing" ? "remove" : "install"}
                : null;
        }
        if (!event.ok) {
            // A missing AUR edition is not an error of the page the person is reading.
            if (previous === "related") return;
            if (previous === "aurSearch") aurSearched = true;
            if (previous === "aur") aurKnown = true;
            if (previous === "aurPopular") { aurPopular = []; aurPopularLoaded = true; }
            messageIsError = true;
            message = (Logic.undoRefusal(event) || event.message || qsTranslate("ProgramsController", "The install or removal could not finish. See the details below.")) + signOutNotice(event);
            if (previous === "packageDetails" && selectedEdition?.key === params.key)
                selectedEdition = Object.assign({}, selectedEdition, {actions: []});
            if (previous === "planning") plan = null;
            if (mutatingPhase(previous)) { plan = null; previewOpen = false; }
            if (mutatingPhase(previous)) { transactionFinished(false); refreshAfterTransaction(); }
            if (previous === "installing" && !event.notStarted && !installRefusals.includes(event.code)) offerReport();
            if (event.code === "aur-closed") { aurEnabled = false; aurRows = []; }
            return;
        }
        if (previous === "development") { developmentKits = event.kits || []; developmentLoaded = true; return; }
        if (previous === "websites" || previous === "websiteChange") {
            websiteApps = event.apps || []; websitesLoaded = true;
            if (previous === "websiteChange") { messageIsError = false; message = event.message || ""; websiteChanged(method === "website-add" ? "add" : "remove"); }
            return;
        }
        if (previous === "aur") {
            aurKnown = true;
            aurEnabled = event.enabled === true;
            aurShowWarning = event.showWarning !== false;
            aurSnapshots = event.snapshots === true;
            if (!aurEnabled) { aurRows = []; aurSearched = false; aurPopular = []; aurPopularLoaded = false; }
            return;
        }
        if (previous === "aurPopular") {
            aurPopular = event.programs || [];
            aurPopularLoaded = true;
            return;
        }
        if (previous === "related") {
            const found = (event.programs || []).filter(row => row.kind === "aur" && row.sameAs === params.key);
            const editions = selectedProgram?.editions || [];
            if (found.length && editions.some(row => row.key === params.key))
                selectedProgram = Object.assign({}, selectedProgram, {editions: editions.concat(found.filter(row => !editions.some(other => other.key === row.key)))});
            return;
        }
        if (previous === "aurSearch") {
            if (params.query === aurQuery) { aurRows = event.programs || []; aurSearched = true; }
            else aurDelay.restart();
            return;
        }
        warnings = event.warnings || warnings;
        if (previous === "catalog") {
            collected = collected.concat(event.programs || []);
            if (collected.length < event.total && (event.programs || []).length) {
                request("catalog", {limit: 500, offset: collected.length}, "catalog");
            } else {
                allRows = collected;
                if (event.storefront) storefront = event.storefront;
                shelf = Array.isArray(event.shelf) ? event.shelf : [];
                essentials = event.essentials?.minimal === true && Array.isArray(event.essentials.programs)
                    ? {minimal: true, programs: event.essentials.programs} : {minimal: false, programs: []};
                catalogReceived = true;
                catalogLoaded = true;
                reconcileSelection();
                if (selectedEdition?.packageOnly)
                    request("details", {key: selectedEdition.key}, "packageDetails");
                if (query || scope === "packages") searchDelay.restart();
                if (aurEnabled && aurQuery) aurDelay.restart();
                // Installed marks on the popular list follow the fresh catalog.
                if (aurEnabled && aurPopularLoaded) readPopular();
                if (!aurKnown) readAur();
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
                    request("search", {query: query, includeAur: false, limit: 500, offset: searchCollected.length}, "search");
            }
        } else if (previous === "planning") {
            plan = event;
        } else if (mutatingPhase(previous)) {
            succeeded = true; previewOpen = false; plan = null;
            if (previous === "installing" && method === "execute") showInstalled(params.key);
            messageIsError = false;
            message = event.message || "";
            message += signOutNotice(event);
            transactionFinished(true);
            refreshAfterTransaction();
        }
    }
    function showInstalled(key: string): void {
        // The open page shows a finished install at once. The refresh after it (a repository sync and
        // an AUR read, often many seconds) brings the exact state: version, Remove, Undo. Until then
        // it offers nothing; before, it kept "Build and install" under "The program from the AUR is
        // installed." (VID11).
        const shown = selectedEdition;
        if (!shown || (shown.key !== key && shown.catalogKey !== key)) return;
        const done = Object.assign({}, shown, {installed: true, actions: []});
        selectedEdition = done;
        if (selectedProgram)
            selectedProgram = Object.assign({}, selectedProgram, {installed: true,
                editions: (selectedProgram.editions || [shown]).map(row => row.key === shown.key ? done : row)});
    }
    function signOutNotice(event: var): string {
        return planTarget === "docker" && event.signOut === true
            ? " " + qsTranslate("ManagerView", "The group takes effect after you sign out and sign back in.") : "";
    }
    function mutatingPhase(name: string): bool { return name === "installing" || name === "removing" || name === "undoing"; }
    function refreshAfterTransaction(): void {
        // Even a failed operation may have changed the installed state.
        if (planTarget) readDevelopment();
        Qt.callLater(() => root.refresh());
    }
    function fail(text: string): void {
        const changing = mutating, installing = phase === "installing", planning = phase === "planning";
        pending = null; phase = "idle"; plan = null;
        if (!planning) previewOpen = false;
        messageIsError = true; message = text;
        if (changing) { transactionFinished(false); refreshAfterTransaction(); }
        if (installing) offerReport();
    }
    // Install results that are refusals by design or a stale plan (authorization, consent,
    // snapshot policy, a plan that no longer holds): nothing broke, so nothing is offered.
    readonly property var installRefusals: ["not-authorized", "changed", "plan-changed", "refused", "aur-closed",
        "snapshots-unavailable", "metadata-unavailable", "not-found", "up-to-date", "missing-dependency", "dependency-cycle",
        "recipe-unavailable", "too-large"]
    // Offer, never send, a problem report; the shell's answer changes nothing here.
    function offerReport(): void { if (!reportOffer.running) reportOffer.running = true; }
    Process { id: reportOffer; command: ["emaki-shell", "call", "report", "offer", "install"] }
    Timer { interval: 5000; running: reportOffer.running; onTriggered: reportOffer.running = false }
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
            else root.request("search", {query: root.query, includeAur: false, limit: 500, offset: 0}, "search");
        }
    }
    Timer {
        id: aurDelay
        interval: 300
        onTriggered: {
            if (!root.aurEnabled || root.aurQuery.trim().length < 2) { root.aurRows = []; root.aurSearched = false; return; }
            root.later("catalog", {query: root.aurQuery, source: "aur", limit: 100, offset: 0}, "aurSearch");
        }
    }
    Timer {
        id: startWatch
        interval: 3000
        onTriggered: if (!root.ready) root.fail(qsTranslate("ProgramsController", "Programs & Updates could not start. Close the window and open it again."))
    }
    Timer {
        id: legacyStartWatch
        interval: 3000
        onTriggered: root.fail(qsTranslate("ProgramsController", "Programs & Updates could not start. Close the window and open it again."))
    }
    Timer {
        id: launchStartWatch
        interval: 3000
        onTriggered: { root.messageIsError = true; root.message = qsTranslate("ProgramsController", "The program could not open."); }
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
            if (root.pending) root.fail(qsTranslate("ProgramsController", "The work stopped before it finished. Look at Installed before trying again."));
        }
    }
    function inventoryFailed(text: string): void {
        inventoryStartWatch.stop();
        inventoryLoading = false;
        inventoryError = text || qsTranslate("ProgramsController", "Your installed programs could not be loaded. Try again.");
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
        onTriggered: root.inventoryFailed(qsTranslate("ProgramsController", "Your installed programs could not be loaded. Try again."))
    }
    Process {
        id: installedList
        property string reply: ""
        property bool completed: false
        command: ["emaki-programs", "list", "--json"]
        onStarted: inventoryStartWatch.stop()
        onRunningChanged: if (!running) Qt.callLater(() => {
            if (!installedList.running && !installedList.completed && root.inventoryLoading)
                root.inventoryFailed(qsTranslate("ProgramsController", "Your installed programs could not be loaded. Try again."));
        })
        stdout: StdioCollector { onStreamFinished: installedList.reply = text }
        onExited: code => {
            completed = true;
            inventoryStartWatch.stop();
            try {
                const result = JSON.parse(reply);
                if (code !== 0 || result.ok !== true || !Array.isArray(result.programs)) {
                    root.inventoryFailed(result.message || qsTranslate("ProgramsController", "Your installed programs could not be loaded. Try again."));
                    return;
                }
                root.legacyPrograms = result.programs;
                root.inventoryLoading = false;
                root.inventoryError = "";
                inventoryRetry.stop();
                root.inventoryRetriesRemaining = 0;
                root.installedReceived = true;
                root.reconcileSelection();
            } catch (_) { root.inventoryFailed(qsTranslate("ProgramsController", "Your installed programs could not be loaded. Try again.")); }
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
                root.fail(qsTranslate("ProgramsController", "Programs & Updates could not start. Close the window and open it again."));
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
            } catch (_) { root.fail(qsTranslate("ProgramsController", "Something went wrong in Programs & Updates. Try again.")); }
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
            if (!launcher.running && !launcher.completed) { launchStartWatch.stop(); root.messageIsError = true; root.message = qsTranslate("ProgramsController", "The program could not open."); }
        })
        onExited: code => { completed = true; launchStartWatch.stop(); if (code !== 0) { root.messageIsError = true; root.message = qsTranslate("ProgramsController", "The program could not open."); } }
    }
    Component.onCompleted: refresh()
}
