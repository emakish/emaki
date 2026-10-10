// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import "Calculator.js" as Calculator
import "Liquid.js" as Liquid

// Queries and titles live only in this scene; status never returns their contents.
Item {
    id: body
    property SettingsController settingsController: null
    property bool settingsActive: false
    readonly property SettingsView settingsView: settingsLoader.item as SettingsView
    function showSettings(page: string): bool {
        if (!settingsController)
            return false;
        settingsActive = true;
        settingsView?.navigate(page || "panel");
        settingsView?.takeFocus();
        return settingsView !== null;
    }
    function leaveSettings(): void {
        if (!settingsActive)
            return;
        settingsView?.flushPending();
        settingsActive = false;
        takeFocus(keyboardMode);
    }
    onSettingsActiveChanged: wake()
    property bool keyboardBoundary: true
    property bool keyboardMode: false
    TapHandler {
        acceptedButtons: Qt.AllButtons
        onPressedChanged: if (pressed)
            body.keyboardMode = false
    }
    function keyboardControls(): var {
        let controls = settingsActive ? [closeButton] : [query, closeButton];
        for (let i = 0; i < modesRepeater.count; ++i)
            controls.push(modesRepeater.itemAt(i));
        controls.push(settingsButton);
        if (settingsActive)
            controls.push(settingsView?.search);
        for (let i = 0; !settingsActive && i < categoryRepeater.count; ++i)
            controls.push(categoryRepeater.itemAt(i));
        if (!settingsActive && list.footerItem && clipActions)
            controls = controls.concat((list.footerItem as ClipboardFooter).controls);
        return controls.filter(item => item && item.visible && item.enabled);
    }
    function moveKeyboardFocus(step: int): void {
        keyboardMode = true;
        const controls = keyboardControls();
        const index = controls.findIndex(item => item.activeFocus);
        const next = controls[(index + step + controls.length) % controls.length];
        next.forceActiveFocus(Qt.TabFocusReason);
        if (list.footerItem && (list.footerItem as ClipboardFooter).controls.indexOf(next) >= 0)
            list.contentY = Math.max(0, list.contentHeight - list.height);
    }
    Keys.onPressed: event => {
        keyboardMode = true;
        if (settingsActive && settingsView?.activeFocus) {
            event.accepted = false;
            return;
        }
        if (event.key === Qt.Key_Escape) {
            if (settingsActive)
                leaveSettings();
            else
                dismissed();
        } else if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
            moveKeyboardFocus(event.key === Qt.Key_Backtab || (event.modifiers & Qt.ShiftModifier) ? -1 : 1);
        } else if (event.key === Qt.Key_Left || event.key === Qt.Key_Up) {
            moveKeyboardFocus(-1);
        } else if (event.key === Qt.Key_Right || event.key === Qt.Key_Down) {
            moveKeyboardFocus(1);
        } else {
            event.accepted = true;
            return;
        }
        event.accepted = true;
    }
    property bool liquid: false
    property string wallpaperState: "unknown"
    property string wallpaperTexture: ""
    // Dock "Choose app…": the next app picked here becomes the entry for this app_id.
    property AppIdentity identity: null
    property string assignFor: ""
    function beginAssign(appId: string): void {
        reset();
        assignFor = appId;
        takeFocus();
    }
    required property real expansion
    required property bool opened
    required property NiriService niri
    property ClipboardHistory sharedClipboard: null
    readonly property alias clipboard: clips
    // For tests/glass-shots.py: the glass draw and the layers under and on it.
    readonly property alias panelGlass: panelGlass
    readonly property alias contentLayer: layer
    readonly property alias onGlassLayer: onGlass
    readonly property alias arrowOnGlass: arrowOnGlass
    property AppCatalog sharedCatalog: null
    readonly property AppCatalog apps: sharedCatalog ?? localCatalog
    readonly property AppCatalog localCatalog: sharedCatalog ? null : (catalogFactory.createObject(body) as AppCatalog)
    readonly property AppCatalog appCatalog: apps
    signal dismissed
    signal settingsRequested
    Component {
        id: catalogFactory
        AppCatalog {}
    }
    Connections {
        target: body.apps
        function onLaunched(): void {
            // Only the originating launcher records its pending recent entry.
            if (body.pendingRecentApp) {
                recent.record("app", body.pendingRecentApp);
                body.pendingRecentApp = "";
                body.dismissed();
            } else if (!body.sharedCatalog || body.opened) {
                body.dismissed();
            }
        }
    }
    RecentFiles {
        id: files
        active: body.opened
        onLaunched: body.dismissed()
    }
    // Clipboard rows are read for the Clipboard mode and for Recent (only if a clip was
    // opened from here before, so the history is not touched without reason).
    ClipboardHistory {
        id: clips
        recorderSource: body.sharedClipboard
        active: body.opened && (body.modeIndex === 4 || (body.recentMode && recent.entries.some(e => e.kind === "clip")))
        onCopied: body.dismissed()
    }
    // An empty All is Recent — what was last opened through the launcher, of every
    // kind, grouped by kind, newest first. Typing turns it back into the mixed search.
    RecentLog {
        id: recent
        active: body.opened && body.modeIndex === 0
    }
    readonly property bool recentMode: query.text.trim().length === 0 && modeIndex === 0
    property string pendingRecentApp: ""
    function recentRows(): var {
        const groups = {
            app: "Apps",
            window: "Windows",
            file: "Files",
            clip: "Clipboard"
        };
        const rows = [];
        const seenWindows = {};
        for (const e of Array.from(recent.entries)) {
            const group = groups[e.kind];
            if (!group)
                continue;
            if (e.kind === "app") {
                const entry = DesktopEntries.byId(e.ref);
                if (entry && apps.shown(entry))
                    rows.push({
                        kind: "app",
                        group: group,
                        t: e.t,
                        entry: entry,
                        label: entry.name,
                        detail: entry.genericName || "Recently opened"
                    });
            } else if (e.kind === "file") {
                rows.push({
                    kind: "file",
                    group: group,
                    t: e.t,
                    label: e.ref.split("/").pop(),
                    detail: e.ref,
                    path: e.ref
                });
            } else if (e.kind === "window") {
                // Only the app id is stored; the row is the newest open window of that app.
                const open = niri.windows.filter(w => labels.values[w.id]?.appId === e.ref).sort((a, b) => b.id - a.id)[0];
                if (open && !seenWindows[open.id]) {
                    seenWindows[open.id] = true;
                    const metadata = labels.values[open.id];
                    const entry = DesktopEntries.heuristicLookup(e.ref);
                    const ws = niri.workspaces.find(item => item.id === open.workspace_id);
                    rows.push({
                        kind: "window",
                        group: group,
                        t: e.t,
                        id: open.id,
                        label: entry?.name || e.ref,
                        detail: metadata?.title || "Untitled window",
                        entry: entry,
                        tag: ws?.idx ?? "?"
                    });
                }
            } else if (e.kind === "clip") {
                const c = clips.entries.find(c => c.id === e.ref);
                if (c)
                    rows.push({
                        kind: "clip",
                        group: group,
                        t: e.t,
                        id: c.id,
                        image: c.image,
                        label: c.preview,
                        detail: c.image ? "Image · Enter copies, never pastes" : "Text · Enter copies, never pastes"
                    });
            }
        }
        // Groups in order of their newest entry, entries newest first inside each.
        const newest = {};
        for (const r of rows)
            newest[r.group] = Math.max(newest[r.group] ?? 0, r.t);
        return rows.sort((a, b) => a.group === b.group ? b.t - a.t : newest[b.group] - newest[a.group]);
    }
    PrivateJob {
        id: web
        onCompleted: value => {
            if (value.state === "requested")
                body.dismissed();
        }
    }
    WindowLabels {
        id: labels
        niri: body.niri
        active: body.opened
    }
    property string category: "All"
    property string selectionKey: ""
    readonly property int selected: selectionKey ? results.findIndex(row => rowKey(row) === selectionKey) : results.length ? 0 : -1
    function rowKey(row: var): string {
        return row ? row.kind + ":" + (row.kind === "window" ? niri.generation + ":" + row.id : (row.kind === "app" || row.kind === "frequent") ? row.entry.id : row.kind === "file" ? row.path : row.kind === "clip" ? row.id : row.value || row.label) : "";
    }
    function selectAt(index: int): void {
        selectionKey = rowKey(results[index]);
    }
    // ---- Layout of the body: liquid-glass/launcher.js buildView(), panel coordinates ----
    readonly property string queryText: query.text.trim()
    // Apps is always the tile grid (the mockup): typing filters it; Frequent and the
    // categories belong to the untouched grid, Frequent only to the category All.
    readonly property bool gridMode: modeIndex === 1
    readonly property bool listMode: !gridMode
    readonly property int frequentCount: gridMode && queryText.length === 0 && category === "All" ? apps.frequent.length : 0
    readonly property bool categoriesShown: !settingsActive && gridMode && queryText.length === 0
    readonly property var categories: ["All", "Development", "Internet", "Media", "Games", "Learning", "Office", "System"]
    readonly property var results: buildResults()
    readonly property int side: 22
    readonly property int bodyTop: Metrics.launcherHeader + 10
    readonly property string note: noteText()
    readonly property int noteHeight: note ? 26 : 0
    readonly property int frequentTop: bodyTop + noteHeight
    readonly property int categoriesTop: frequentTop + (frequentCount ? 122 : 0)
    readonly property int gridTop: categoriesTop + (categoriesShown ? 48 : 0)
    readonly property real gridCell: (width - 48) / 7
    // All (search and Recent) lists 40 px rows under kind headings; other lists 44 px.
    readonly property bool sectioned: listMode && modeIndex === 0 && (recentMode || queryText.length > 0)
    readonly property int rowHeight: modeIndex === 0 ? 40 : 44
    // A heading is launcher.js title(): 8 px after the previous group, an 18 px line, 4 px
    // to its rows. The first heading has no group above it, so the list starts 8 px higher.
    readonly property int sectionHeight: 30
    readonly property int sectionCount: sectioned ? results.reduce((n, r, i) => n + (i === 0 || r.group !== results[i - 1].group ? 1 : 0), 0) : 0
    readonly property int listTop: bodyTop + noteHeight - (sectioned ? 8 : 0)
    readonly property bool clipActions: modeIndex === 4 && clips.entries.length > 0
    readonly property real listContent: Math.max(0, results.length * (rowHeight + 2) - 2) + sectionCount * sectionHeight + (clipActions ? 50 : 0)
    readonly property real contentBottom: gridMode ? gridTop + Math.ceil((results.length - frequentCount) / 7) * 86 + 10 : listTop + listContent + 12
    readonly property real desiredHeight: settingsActive ? Metrics.settingsHeight : Math.min(Metrics.launcherHeader + Metrics.launcherBodyMax + Metrics.launcherFoot, Math.max(Metrics.launcherHeader + 24, contentBottom))
    function setMode(name: string): bool {
        const index = modes.indexOf(name);
        if (index < 0)
            return false;
        leaveSettings();
        modeIndex = index;
        takeFocus();
        return true;
    }
    function setQuery(text: string): void {
        query.text = text;
    }
    function searchStatus(): var {
        return {
            mode: modes[modeIndex],
            result_count: results.length,
            assigning: assignFor !== "",
            selected_index: selected,
            selected_kind: results[selected]?.kind ?? null,
            selected_window_id: results[selected]?.kind === "window" ? results[selected].id : null,
            app_count: apps.entries.length,
            launch: apps.launchState,
            launch_busy: apps.busy,
            labels: labels.state,
            files: {
                state: files.state,
                count: files.entries.length,
                limited: files.limited,
                open: files.openState,
                opening: files.opening
            },
            windows_supported: true,
            recent: {
                state: recent.state,
                count: recent.entries.length,
                groups: recentMode ? Array.from(new Set(results.map(r => r.group))) : []
            },
            frequent: apps.frequentState,
            frequent_count: apps.frequent.length,
            cases: "waiting_for_D1",
            clipboard: {
                state: clips.state,
                count: clips.entries.length,
                action: clips.actionState,
                recorder: clips.recorder
            },
            web: web.state,
            category: category
        };
    }
    // Ranked app search. Every query word must hit somewhere; the score of a word is the
    // best field it hits (name > generic name > keywords) and how it hits (whole name >
    // start of name > start of a word > anywhere). Frequently opened apps break ties first,
    // then the alphabet. Plain substring search sorted A→Z buried the wanted app behind
    // five others whose keywords merely contained the word (audit 2026-09-24).
    function fieldScore(field: string, word: string, weight: int): int {
        if (!field || !field.includes(word))
            return 0;
        const hit = field === word ? 4 : field.startsWith(word) ? 3 : field.split(/[\s\-_./()]+/).some(part => part.startsWith(word)) ? 2 : 1;
        return weight * 10 + hit;
    }
    function appScore(entry: DesktopEntry, words: var): int {
        const name = entry.name.toLocaleLowerCase();
        const generic = (entry.genericName || "").toLocaleLowerCase();
        const keywords = entry.keywords.map(k => k.toLocaleLowerCase());
        let total = 0;
        for (const word of words) {
            const score = Math.max(fieldScore(name, word, 3), fieldScore(generic, word, 2), ...keywords.map(k => fieldScore(k, word, 1)));
            if (score === 0)
                return 0;
            total += score;
        }
        return total;
    }
    function rankApps(text: string): var {
        const words = text.split(/\s+/).filter(w => w.length > 0);
        if (!words.length)
            return [];
        return apps.entries.map(entry => ({
                    entry: entry,
                    score: appScore(entry, words),
                    uses: apps.counts[entry.id] || 0
                })).filter(row => row.score > 0).sort((a, b) => b.score - a.score || b.uses - a.uses || a.entry.name.localeCompare(b.entry.name)).map(row => row.entry);
    }
    readonly property var categoryMapping: ({
            Development: ["Development"],
            Internet: ["Network"],
            Media: ["AudioVideo", "Audio", "Video", "Graphics"],
            Games: ["Game"],
            Learning: ["Education", "Science"],
            Office: ["Office"],
            System: ["System", "Settings", "Utility"]
        })
    function matchesCategory(entry: DesktopEntry): bool {
        if (category === "All")
            return true;
        return categoryMapping[category].some(c => entry.categories.includes(c));
    }
    // The launcher's own word for an app (the first category chip it falls under).
    function categoryOf(entry: DesktopEntry): string {
        return categories.slice(1).find(name => categoryMapping[name].some(c => entry.categories.includes(c))) ?? "";
    }
    function buildResults(): var {
        const text = query.text.trim().toLocaleLowerCase();
        const rows = [];
        if (recentMode)
            return recentRows();
        if (modeIndex === 0 && text) {
            const value = Calculator.calculate(text);
            if (value !== null)
                rows.push({
                    kind: "calculator",
                    group: "Calculator",
                    label: "= " + value,
                    detail: "Enter to copy",
                    value: String(value)
                });
        }
        if (frequentCount) {
            for (const entry of apps.frequent)
                rows.push({
                    kind: "frequent",
                    entry: entry,
                    label: entry.name,
                    detail: "Frequently opened"
                });
        }
        if (modeIndex < 2 && (text || modeIndex === 1)) {
            // Apps mode lists every match (the list scrolls); All keeps the five best beside
            // windows, files and the web row.
            let found = text ? rankApps(text).slice(0, modeIndex === 1 ? 64 : 5) : apps.entries.filter(a => matchesCategory(a));
            for (const entry of found)
                rows.push({
                    kind: "app",
                    group: "Apps",
                    entry: entry,
                    label: entry.name,
                    // An app row names its category, as launcher.js does ("Internet").
                    detail: entry.runInTerminal ? "Terminal app" : categoryOf(entry) || entry.genericName || ""
                });
        }
        if (modeIndex === 2 || (modeIndex === 0 && text)) {
            for (const w of niri.windows) {
                const metadata = labels.values[w.id];
                const title = metadata?.title || "Untitled window";
                const entry = metadata?.appId ? DesktopEntries.heuristicLookup(metadata.appId) : null;
                const name = entry?.name || metadata?.appId || "Window #" + w.id;
                const ws = niri.workspaces.find(item => item.id === w.workspace_id);
                if (!text || (name + " " + title).toLocaleLowerCase().includes(text))
                    rows.push({
                        kind: "window",
                        group: "Windows",
                        id: w.id,
                        label: name,
                        detail: title,
                        entry: entry,
                        tag: ws?.idx ?? "?"
                    });
            }
        }
        if (modeIndex === 3 || (modeIndex === 0 && text)) {
            const found = files.entries.filter(entry => !text || entry.name.toLocaleLowerCase().includes(text)).slice(0, text ? 4 : 7);
            for (const file of found)
                rows.push({
                    kind: "file",
                    group: "Files",
                    label: file.name,
                    detail: file.path,
                    path: file.path
                });
        }
        if (modeIndex === 4) {
            for (const c of clips.entries.filter(e => !text || e.preview.toLocaleLowerCase().includes(text)))
                rows.push({
                    kind: "clip",
                    id: c.id,
                    image: c.image,
                    label: c.preview,
                    detail: c.image ? "Image · Enter copies, never pastes" : "Text · Enter copies, never pastes"
                });
        }
        if (modeIndex === 0 && text)
            rows.push({
                kind: "web",
                group: "Web",
                label: "Search the web for “" + query.text.trim() + "”",
                detail: "DuckDuckGo · default browser",
                value: query.text.trim()
            });
        return rows;
    }
    function deleteSelectedClip(): void {
        const row = results[selected];
        if (row?.kind === "clip")
            clips.perform(row.id, true);
    }
    function moveVertical(direction: int): void {
        if (!gridMode) {
            moveSelection(direction);
            return;
        }
        if (frequentCount && selected < frequentCount) {
            if (direction > 0 && results.length > frequentCount)
                moveSelection(frequentCount + Math.min(selected, results.length - frequentCount - 1) - selected);
        } else if (direction < 0 && frequentCount && selected - frequentCount < 7) {
            moveSelection(Math.min(selected - frequentCount, frequentCount - 1) - selected);
        } else
            moveSelection(direction * 7);
    }
    function moveSelection(delta: int): void {
        if (!results.length)
            return;
        selectAt(Math.max(0, Math.min(results.length - 1, selected + delta)));
        if (gridMode) {
            if (selected >= frequentCount)
                grid.positionViewAtIndex(selected - frequentCount, GridView.Contain);
        } else
            list.positionViewAtIndex(selected, ListView.Contain);
        wake();
    }
    // Left/right walk the grids; with a query they stay in the text (launcher.js keydown).
    readonly property bool horizontalMoves: gridMode && query.text.length === 0
    // Hover selects, as the arrows do (launcher.js pointermove), but only when the pointer
    // really moved: a list scrolling under a still pointer must not steal the selection.
    property point lastHover: Qt.point(-1, -1)
    function hoverAt(index: int, item: Item, x: real, y: real): void {
        if (!opened || index < 0 || index >= results.length)
            return;
        const p = item.mapToItem(body, x, y);
        if (Math.abs(p.x - lastHover.x) < .5 && Math.abs(p.y - lastHover.y) < .5)
            return;
        lastHover = p;
        if (selected !== index)
            selectAt(index);
    }
    function activate(): void {
        if (!opened)
            return;
        const row = results[selected];
        if (!row)
            return;
        if (row.kind === "app" || row.kind === "frequent") {
            if (assignFor) {
                identity?.learn(assignFor, row.entry.id);
                assignFor = "";
                dismissed();
                return;
            }
            pendingRecentApp = row.entry.id; // recorded once the launch is accepted
            apps.launch(row.entry);
        } else if (row.kind === "clip") {
            recent.record("clip", row.id);
            clips.perform(row.id, false);
        } else if (row.kind === "web")
            web.start({
                op: "web",
                query: row.value
            });
        else if (row.kind === "file") {
            recent.record("file", row.path);
            files.open(row.path);
        } else if (row.kind === "calculator") {
            Quickshell.clipboardText = row.value;
            dismissed();
        } else if (row.kind === "window") {
            const id = row.id;
            const generation = niri.generation;
            recent.record("window", labels.values[id]?.appId ?? "");
            dismissed();
            Qt.callLater(() => {
                if (generation === body.niri.generation)
                    body.niri.focusWindow(id);
            });
        }
    }
    onModeIndexChanged: {
        leaveSettings();
        selectionKey = "";
        wake();
    }
    onCategoryChanged: {
        selectionKey = "";
        wake();
    }
    onResultsChanged: {
        // A removed selected window must not silently select the next window by index.
        if (!selectionKey && (results[0]?.kind === "window" || results[0]?.kind === "file"))
            selectAt(0);
        wake();
    }
    readonly property bool inputFocused: settingsActive ? (settingsView?.search.activeFocus ?? false) : query.activeFocus
    readonly property int queryLength: query.length
    property int modeIndex: 1 // Apps by default (23.09): All is the search + Recent view
    readonly property var modes: ["All", "Apps", "Windows", "Files", "Clipboard"]

    function filesMessage(): string {
        if (!["idle", "pending", "requested"].includes(files.openState))
            return stateNote("file-open", files.openState);
        if (files.openState === "pending")
            return "Opening file…";
        if (files.state === "loading")
            return "Reading recent files…";
        if (files.state === "absent")
            return "No recent files yet.";
        if (files.state !== "ready") {
            if (files.state === "access_denied")
                return "Permission denied reading recent files.";
            if (["source_too_large", "too_many_entries", "xml_limit"].includes(files.state))
                return "The recent-files list is too large to read safely.";
            if (["invalid_xbel", "dtd_not_allowed"].includes(files.state))
                return "The recent-files list is damaged or unsupported.";
            if (files.state === "timeout")
                return "Reading recent files took too long. Reopen to retry.";
            return "Recent files are unavailable.";
        }
        if (!results.length)
            return query.text.trim() ? "No recent filenames match." : "No existing recent files.";
        return "";
    }
    // Words for a helper's or service's state that has something to say (`what`: its source);
    // an internal code never reaches the screen.
    function stateNote(what: string, s: string): string {
        switch (what) {
        case "clipboard":
            return s === "idle" || s === "pending" ? "Reading clipboard history…" : "Clipboard history isn’t available.";
        case "clipboard-action":
            return "The clipboard action didn’t go through.";
        case "web":
            return s === "pending" ? "Opening the web search…" : "Couldn’t open the web search.";
        case "launch":
            return s === "pending" ? "Starting the app…" : "Couldn’t start the app.";
        case "windows":
            return "Open windows can’t be listed right now.";
        case "labels":
            return s === "connecting" ? "Reading window titles…" : "Window titles unavailable.";
        case "file-open":
            return ({
                    file_missing: "This file no longer exists.",
                    no_handler: "No default application for this file type.",
                    access_denied: "Permission denied opening this file.",
                    timeout: "Open was not confirmed in time; the application may still open.",
                    helper_dependency_missing: "Files can’t be opened from here on this system."
                })[s] || "Could not open this file with its default application.";
        }
        return "";
    }
    // The line under the header (launcher.js "note"): only when there is something to say.
    function noteText(): string {
        if (modeIndex === 4) {
            if (clips.state !== "ready")
                return stateNote("clipboard", clips.state);
            if (!["idle", "pending", "copied", "deleted"].includes(clips.actionState))
                return stateNote("clipboard-action", clips.actionState);
            if (clips.recorder === "paused")
                return "Recording is paused — new copies are not saved.";
            if (clips.recorder === "failed")
                return "The clipboard recorder stopped unexpectedly · Resume restarts it";
            if (!clips.entries.length)
                return "Clipboard history is empty";
            return results.length ? "" : "Nothing matches “" + queryText + "”";
        }
        if (!["idle", "requested"].includes(web.state))
            return stateNote("web", web.state);
        if (modeIndex === 3 || !["idle", "requested"].includes(files.openState))
            return filesMessage();
        if (!["idle", "requested"].includes(apps.launchState))
            return stateNote("launch", apps.launchState);
        if (modeIndex === 2 && !niri.connected)
            return stateNote("windows", niri.reason);
        if (recentMode)
            return results.length ? "" : "Nothing opened from here yet · type to search everything";
        if (!results.length)
            return queryText ? "Nothing matches “" + queryText + "”" : modeIndex === 2 ? "No windows are open." : "Nothing found.";
        if (modeIndex === 2 && labels.state !== "ready")
            return stateNote("labels", labels.state);
        return "";
    }
    function takeFocus(keyboard): void {
        keyboardMode = keyboard === true;
        if (settingsActive)
            settingsView?.takeFocus();
        else
            query.forceActiveFocus(keyboard ? Qt.TabFocusReason : Qt.OtherFocusReason);
    }
    function reset(): void {
        settingsView?.flushPending();
        settingsActive = false;
        query.text = "";
        modeIndex = 1;
        selectionKey = "";
        category = "All";
        assignFor = "";
        trailIndex = -1;
        trail = null;
        trailOnGlass = 0;
        lastSelected = -1;
    }
    onOpenedChanged: {
        if (opened)
            takeFocus();
        else
            query.focus = false;
        wake();
    }

    // ================================================================== glass
    // docs/mockups/liquid-glass/launcher.html: the panel is one Regular plate that grows out
    // of the bar's button; the active mode, the active category and the selection are Clear
    // drops on it (one bubble per role that changes shape while it flows). Everything the
    // panel shows lies under the glass (uIcons), so a drop's edge bends its neighbours; the
    // selected item, the active mode and category are drawn again on the glass with the
    // drop's alpha, so the drop never ghosts its own text (Apple: labels on the material).
    // Without a GPU the same layers are drawn flat.
    property DockBackdrop backdrop: null
    // The panel's top left on the screen: the bar's launcher button.
    property point origin: Qt.point(Metrics.logoX, Metrics.top)
    // The bar's button bubble: part of the panel while the morph is young (launcher.js).
    property var logoBubble: null
    readonly property bool glassReady: backdrop !== null && backdrop.ready && GraphicsInfo.api !== GraphicsInfo.Software
    readonly property var modeBubble: Liquid.liquid()
    readonly property var categoryBubble: Liquid.liquid()
    readonly property var selectBubble: Liquid.liquid()
    // The plate's height follows the content on a spring (k 420, ζ .8).
    readonly property var heightSpring: Liquid.spring(Metrics.islandHeight)
    property int tick: 0
    property bool animating: false
    function wake(): void {
        animating = true;
    }
    FrameAnimation {
        running: body.animating
        onTriggered: body.frame(Date.now(), frameTime)
    }
    readonly property real morph: Math.max(0, Math.min(1, expansion))
    // The content arrives on the second half of the morph.
    readonly property real contentAlpha: Liquid.smooth((morph - .55) / .45)
    // During close, the compact mark returns after the header mark has faded away.
    readonly property real compactLogoAlpha: opened ? 1 - contentAlpha : 1 - Liquid.smooth(morph / .55)
    property real animatedWidth: width
    onAnimatedWidthChanged: wake()
    Behavior on animatedWidth {
        enabled: body.morph > 0
        NumberAnimation {
            duration: Metrics.morphMs
            easing.type: Easing.BezierSpline
            easing.bezierCurve: Metrics.morphCurve
        }
    }
    property real maximumWidth: Infinity
    readonly property real shownWidth: Math.min(maximumWidth, Metrics.islandHeight + (animatedWidth - Metrics.islandHeight) * morph)
    readonly property real shownHeight: {
        tick;
        return Metrics.islandHeight + (Math.max(Metrics.islandHeight, heightSpring.x) - Metrics.islandHeight) * morph;
    }
    readonly property real shownRadius: Liquid.mix(Metrics.islandRadius, Metrics.panelRadius, morph)
    readonly property real modeAlpha: {
        tick;
        return Liquid.smooth(modeBubble.alpha.x);
    }
    readonly property real categoryAlpha: {
        tick;
        return Liquid.smooth(categoryBubble.alpha.x);
    }
    readonly property real selectAlpha: {
        tick;
        return Liquid.smooth(selectBubble.alpha.x);
    }
    readonly property real logoAlpha: {
        tick;
        return logoBubble ? Liquid.smooth(logoBubble.alpha.x) : 0;
    }
    // Where the active mode, category and selected item are, panel coordinates.
    readonly property rect modeRect: {
        const mode = settingsActive ? settingsButton : modesRepeater.itemAt(modeIndex);
        return mode ? Qt.rect(mode.x + (settingsActive ? 0 : modesRow.x), mode.y + (settingsActive ? 0 : modesRow.y), mode.width, mode.height) : Qt.rect(0, 0, 0, 0);
    }
    property rect categoryRect: Qt.rect(0, 0, 0, 0)
    property var selection: null
    // dual() of launcher.js follows the selection bubble, not the selection: an item is drawn
    // on the glass as much as the bubble covers it. With the plain `selected` flag the new
    // item jumped onto the glass and the old one back under it the moment the pointer crossed
    // into the next tile, while the bubble was still flowing over the old one — its rim then
    // bent and split the old label, and the new label stood crisp outside the bubble: the
    // dispersion "blinked" on every tile (27.09). `trail` is the item being left.
    // Worse, the item under the glass followed `selected` at once while its copy on the glass
    // followed `selection`, set in the next frame(): for one frame the new item was drawn
    // neither under nor on the glass and vanished (VM frames at 1 and 1.25). Everything here,
    // `lastSelected` included, is set in frame() together.
    property int trailIndex: -1
    property var trail: null
    property real selectOnGlass: 0
    property real trailOnGlass: 0
    property int lastSelected: -1
    function onGlassAlpha(index: int): real {
        return index === lastSelected ? selectOnGlass : index === trailIndex ? trailOnGlass : 0;
    }
    // How much of r (panel coordinates) the selection bubble covers, 0..1, per axis against the
    // smaller of the two (a tile's bubble may be narrower than its cell).
    function coverOf(r: rect): real {
        const d = Liquid.drop(selectBubble, 0, 0);
        if (!d || r.width <= 0 || r.height <= 0)
            return 0;
        const b = d.rect;
        const ox = Math.max(0, Math.min(r.x + r.width, b.x + b.z) - Math.max(r.x, b.x)) / Math.max(1, Math.min(r.width, b.z));
        const oy = Math.max(0, Math.min(r.y + r.height, b.y + b.w) - Math.max(r.y, b.y)) / Math.max(1, Math.min(r.height, b.w));
        return Liquid.smooth(Math.min(1, ox) * Math.min(1, oy));
    }
    onMorphChanged: wake()
    onSelectedChanged: wake()
    onHeightChanged: wake()
    onWidthChanged: wake()
    function itemRect(item: Item): rect {
        const p = item.mapToItem(body, 0, 0);
        return Qt.rect(p.x, p.y, item.width, item.height);
    }
    // The selected item (launcher.js updateGeometry): its rect, the viewport that clips it,
    // how it is drawn and the bubble around it. A tile's bubble is one size on every tile,
    // whatever its name (Metrics.launcherTileBubble); rows and cards stand 6 px proud. Null
    // when not on screen.
    function selectedGeometry(): var {
        return settingsActive ? null : geometryOf(selected);
    }
    function geometryOf(i: int): var {
        if (i < 0 || i >= results.length || !opened)
            return null;
        let item = null, view = null, shape = "row";
        if (gridMode) {
            shape = "tile";
            if (i < frequentCount)
                item = frequentRepeater.itemAt(i);
            else {
                view = grid;
                item = grid.itemAtIndex(i - frequentCount);
            }
        } else {
            view = list;
            item = list.itemAtIndex(i);
        }
        if (!item)
            return null;
        const r = itemRect(item);
        const clip = view ? itemRect(view) : Qt.rect(0, 0, width, height);
        let bubble, radius;
        if (shape === "tile") {
            const w = Metrics.launcherTileBubble;
            bubble = [r.x + r.width / 2 - w / 2, r.y - 2, w, 86];
            radius = 22;
        } else {
            bubble = Liquid.pad([r.x, r.y, r.width, r.height], Liquid.BUBBLE.pad);
            radius = 12 + Liquid.BUBBLE.pad;
        }
        // Cut to the viewport (with the bubble's own margin); outside it there is none.
        const m = Liquid.BUBBLE.pad;
        const x0 = Math.max(bubble[0], clip.x - m), y0 = Math.max(bubble[1], clip.y - m);
        const x1 = Math.min(bubble[0] + bubble[2], clip.x + clip.width + m), y1 = Math.min(bubble[1] + bubble[3], clip.y + clip.height + m);
        if (x1 - x0 < 12 || y1 - y0 < 12)
            return null;
        return {
            row: results[i],
            shape: shape,
            rect: r,
            clip: view ? clip : Qt.rect(r.x - m, r.y - m, r.width + 2 * m, r.height + 2 * m),
            bubble: [x0, y0, x1 - x0, y1 - y0],
            radius: radius
        };
    }
    function frame(now: real, elapsed: real): void {
        const dt = Math.min(elapsed > 0 ? elapsed : 1 / 60, .04);
        let active = false;
        heightSpring.target = height;
        active = Liquid.step(heightSpring, dt, 420, .8) || active;
        const mode = settingsActive ? settingsButton : modesRepeater.itemAt(modeIndex);
        if (mode) {
            active = Liquid.place(modeBubble, Liquid.pad([modeRect.x, modeRect.y, modeRect.width, modeRect.height], 4), 13, now) || active;
        }
        const chip = categoriesShown ? categoryRepeater.itemAt(categories.indexOf(category)) : null;
        if (chip) {
            categoryRect = itemRect(chip);
            active = Liquid.place(categoryBubble, Liquid.pad([categoryRect.x, categoryRect.y, categoryRect.width, categoryRect.height], 4), 13, now) || active;
        } else
            active = Liquid.aim(categoryBubble.alpha, 0, 160, now) || active;
        const sel = selectedGeometry();
        selection = sel;
        if (sel && morph > .6)
            active = Liquid.place(selectBubble, sel.bubble, sel.radius, now) || active;
        else if (!sel && opened && morph > .6)
            // The item is gone (filter, mode, page): the bubble sinks with it at once.
            active = Liquid.vanish(selectBubble, now) || active;
        else
            Liquid.dissolve(selectBubble, now);
        for (const b of [modeBubble, categoryBubble, selectBubble])
            active = Liquid.advance(b, now, dt) || active;
        // dual() by coverage (selectOnGlass): the item left behind stays on the glass while
        // the bubble still covers it, the new one comes onto it as the bubble arrives.
        if (selected !== lastSelected) {
            trailIndex = lastSelected >= 0 && selectOnGlass > .01 ? lastSelected : -1;
            lastSelected = selected;
        }
        trail = trailIndex >= 0 && trailIndex !== selected ? geometryOf(trailIndex) : null;
        const onGlass = Liquid.smooth(selectBubble.alpha.x);
        selectOnGlass = sel ? onGlass * coverOf(sel.rect) : 0;
        trailOnGlass = trail ? onGlass * coverOf(trail.rect) : 0;
        if (trailIndex >= 0 && trailOnGlass < .005) {
            trailIndex = -1;
            trail = null;
            trailOnGlass = 0;
        }
        // The bar advances its own button bubble; keep drawing it while the morph is young.
        if (logoBubble && logoBubble.alpha.x > .001 && morph > 0 && morph < .6)
            active = true;
        ++tick;
        if (!active)
            animating = false;
    }
    function plateDrop(bubble, kind: string): var {
        const d = Liquid.drop(bubble, origin.x, origin.y);
        if (!d)
            return null;
        const x = Math.max(origin.x, d.rect.x);
        const y = Math.max(origin.y, d.rect.y);
        const right = Math.min(origin.x + shownWidth, d.rect.x + d.rect.z);
        const bottom = Math.min(origin.y + shownHeight, d.rect.y + d.rect.w);
        const alpha = d.params.w * (kind === "logo" ? 1 : contentAlpha);
        if (right <= x || bottom <= y || alpha < .001)
            return null;
        return {
            kind: kind,
            rect: Qt.vector4d(x, y, right - x, bottom - y),
            params: Qt.vector4d(Math.min(d.params.x, (right - x) / 2, (bottom - y) / 2), d.params.y, d.params.z, alpha)
        };
    }
    // The drops in screen coordinates: mode, category, selection once the panel has grown;
    // before that the button's bubble, which the panel carries out of the bar.
    readonly property var drops: {
        tick;
        const list = [];
        if (morph > .6) {
            for (const [kind, b] of [["mode", modeBubble], ["category", categoryBubble], ["select", selectBubble]]) {
                const d = plateDrop(b, kind);
                if (d)
                    list.push(d);
            }
        } else if (logoBubble) {
            const d = plateDrop(logoBubble, "logo");
            if (d)
                list.push(d);
        }
        return list;
    }
    function glassStatus(): var {
        const rect = b => ({
                    alpha: Math.round(Liquid.smooth(b.alpha.x) * 1000) / 1000,
                    x: Math.round(b.x.x),
                    y: Math.round(b.y.x),
                    width: Math.round(b.w.x),
                    height: Math.round(b.h.x)
                });
        return {
            ready: glassReady,
            height: Math.round(shownHeight),
            plate: {
                x: 0,
                y: 0,
                width: shownWidth,
                height: shownHeight
            },
            drops: drops.map(d => ({
                        kind: d.kind,
                        x: d.rect.x - origin.x,
                        y: d.rect.y - origin.y,
                        width: d.rect.z,
                        height: d.rect.w,
                        alpha: d.params.w
                    })),
            content_width: content.width,
            texture_width: layer.width - origin.x,
            focus: {
                x: glassFocus.x,
                y: glassFocus.y,
                width: glassFocus.width,
                height: glassFocus.height,
                shown: glassFocus.shown,
                contained: glassFocus.contained,
                eligible: glassFocus.eligible
            },
            mode: rect(modeBubble),
            category: rect(categoryBubble),
            select: rect(selectBubble),
            select_shape: selection?.shape ?? null
        };
    }
    // Ink: always the light glass, whatever lies underneath (2026-09-27).
    readonly property color ink: LiquidPalette.inkOnLight
    readonly property color dim: LiquidPalette.dimOnLight
    readonly property color faint: LiquidPalette.faintOnLight
    readonly property color danger: LiquidPalette.dangerOnLight

    // ---- Flat stand-in (no GPU): the plate and the drops, under the content ----
    Rectangle {
        visible: !body.glassReady && body.morph > 0
        width: body.shownWidth
        height: body.shownHeight
        radius: body.shownRadius
        color: LiquidPalette.flatPlate
    }
    Item {
        width: body.shownWidth
        height: body.shownHeight
        clip: true
        Repeater {
            model: body.glassReady ? [] : body.drops
            Rectangle {
                required property var modelData
                x: modelData.rect.x - body.origin.x
                y: modelData.rect.y - body.origin.y
                width: modelData.rect.z
                height: modelData.rect.w
                radius: modelData.params.x
                opacity: modelData.params.w
                color: LiquidPalette.flatDrop
                border.width: 1
                border.color: LiquidPalette.flatDropRim
            }
        }
    }

    // The selected item (or the one the bubble is leaving) drawn again on the glass, cut to
    // its viewport, with its dual() alpha.
    component OnGlassItem: Item {
        id: onGlassItem
        required property var sel
        required property real alpha
        visible: sel !== null && alpha > .001
        x: sel?.clip.x ?? 0
        y: sel?.clip.y ?? 0
        width: sel?.clip.width ?? 0
        height: sel?.clip.height ?? 0
        clip: true
        opacity: alpha
        LauncherItem {
            x: (onGlassItem.sel?.rect.x ?? 0) - onGlassItem.x
            y: (onGlassItem.sel?.rect.y ?? 0) - onGlassItem.y
            width: onGlassItem.sel?.rect.width ?? 0
            height: onGlassItem.sel?.rect.height ?? 0
            row: onGlassItem.sel?.row ?? null
            shape: onGlassItem.sel?.shape ?? "row"
            ink: body.ink
            dim: body.dim
            danger: body.danger
            term: body.queryText
        }
    }

    // ---- Delegates: a result under the glass, with its input ----
    component TileDelegate: Item {
        id: tile
        required property var modelData
        required property int index
        property int offset: 0
        readonly property int resultIndex: index + offset
        LauncherItem {
            anchors.fill: parent
            row: tile.modelData
            shape: "tile"
            ink: body.ink
            dim: body.dim
            opacity: 1 - body.onGlassAlpha(tile.resultIndex)
        }
        MouseArea {
            id: tileHit
            anchors.fill: parent
            enabled: body.opened
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onPositionChanged: mouse => body.hoverAt(tile.resultIndex, tileHit, mouse.x, mouse.y)
            onClicked: {
                body.selectAt(tile.resultIndex);
                body.activate();
            }
        }
    }
    component RowDelegate: Item {
        id: line
        required property var modelData
        required property int index
        LauncherItem {
            anchors.fill: parent
            row: line.modelData
            shape: "row"
            ink: body.ink
            dim: body.dim
            danger: body.danger
            term: body.queryText
            opacity: 1 - body.onGlassAlpha(line.index)
        }
        MouseArea {
            id: lineHit
            anchors.fill: parent
            enabled: body.opened
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onPositionChanged: mouse => body.hoverAt(line.index, lineHit, mouse.x, mouse.y)
            onClicked: {
                body.selectAt(line.index);
                body.activate();
            }
        }
        // A clipboard row's "Delete" on the right removes it (Delete key: the selected one).
        MouseArea {
            visible: line.modelData.kind === "clip"
            enabled: body.opened && visible
            anchors.right: parent.right
            width: 64
            height: parent.height
            cursorShape: Qt.PointingHandCursor
            onClicked: body.clipboard.perform(line.modelData.id, true)
        }
    }
    component ClipboardFooter: Item {
        property var controls: []
    }
    component ActionText: Item {
        id: action
        required property string text
        property color color: body.ink
        signal clicked
        activeFocusOnTab: true
        Keys.onReturnPressed: clicked()
        Keys.onEnterPressed: clicked()
        Keys.onSpacePressed: clicked()
        FocusRing {
            shown: body.keyboardMode && action.activeFocus && !body.glassReady
        }
        width: actionLabel.implicitWidth + 24
        height: 30
        Text {
            id: actionLabel
            x: 12
            height: parent.height
            verticalAlignment: Text.AlignVCenter
            text: action.text
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 14
            font.weight: Font.Medium
            color: action.color
        }
        MouseArea {
            anchors.fill: parent
            enabled: body.opened
            cursorShape: Qt.PointingHandCursor
            onClicked: action.clicked()
        }
        Accessible.role: Accessible.Button
        Accessible.name: text
        Accessible.onPressAction: clicked()
    }

    // ---- Content under the glass. The layer starts at the screen origin, so its texture
    // lies on whole device pixels (the panel's x 10 is 12.5 device px at scale 1.25, and
    // text sampled between two texels blurs); the panel content sits in it at `origin`.
    Item {
        id: layer
        x: -body.origin.x
        y: -body.origin.y
        width: body.origin.x + Math.max(body.width, body.animatedWidth) + 16
        height: body.origin.y + Math.max(body.height, body.shownHeight) + 16
        // The bar's button arrow, going as the content comes (launcher.js paint()).
        Icon {
            x: body.origin.x + 9
            y: body.origin.y + 9
            width: 18
            height: 18
            kind: "logo"
            ink: ShellPalette.accent
            opacity: body.compactLogoAlpha * (1 - body.logoAlpha)
            visible: opacity > .001
        }
        Item {
            // Flat stand-in only: the content ends where the plate does.
            x: body.origin.x
            y: body.origin.y
            width: body.shownWidth
            height: body.shownHeight
            clip: body.settingsActive || !body.glassReady
            Item {
                id: content
                width: Math.max(body.width, body.animatedWidth)
                height: body.height
                opacity: body.contentAlpha
                visible: opacity > .001
                // Header: the arrow (a click closes, 23.09), the modes, a hairline.
                Icon {
                    id: closeButton
                    objectName: "launcher-close"
                    activeFocusOnTab: true
                    Keys.onReturnPressed: body.dismissed()
                    Keys.onEnterPressed: body.dismissed()
                    Keys.onSpacePressed: body.dismissed()
                    FocusRing {
                        shown: body.keyboardMode && closeButton.activeFocus && !body.glassReady
                    }
                    x: 20
                    y: 20
                    width: 20
                    height: 20
                    kind: "logo"
                    ink: ShellPalette.accent
                    MouseArea {
                        anchors.fill: parent
                        anchors.margins: -6
                        enabled: body.opened
                        cursorShape: Qt.PointingHandCursor
                        onClicked: body.dismissed()
                    }
                    Accessible.role: Accessible.Button
                    Accessible.name: "Close launcher"
                    Accessible.onPressAction: {
                        if (body.opened)
                            body.dismissed();
                    }
                }
                Row {
                    id: modesRow
                    x: body.animatedWidth - 56 - width
                    y: 16
                    height: 28
                    spacing: 2
                    Repeater {
                        id: modesRepeater
                        model: body.modes
                        Item {
                            id: modeItem
                            activeFocusOnTab: true
                            function activate(): void {
                                body.leaveSettings();
                                body.modeIndex = index;
                            }
                            Keys.onReturnPressed: activate()
                            Keys.onEnterPressed: activate()
                            Keys.onSpacePressed: activate()
                            FocusRing {
                                shown: body.keyboardMode && modeItem.activeFocus && !body.glassReady
                            }
                            required property int index
                            objectName: "launcher-tab-" + modelData.toLowerCase()
                            required property string modelData
                            width: modeLabel.implicitWidth + 22
                            height: 28
                            Text {
                                id: modeLabel
                                anchors.centerIn: parent
                                text: modeItem.modelData
                                textFormat: Text.PlainText
                                font.family: ShellPalette.uiFont
                                font.pixelSize: 13
                                font.weight: Font.Medium
                                color: !body.settingsActive && body.modeIndex === modeItem.index ? body.ink : body.dim
                                opacity: !body.settingsActive && body.modeIndex === modeItem.index ? 1 - body.modeAlpha : 1
                            }
                            MouseArea {
                                anchors.fill: parent
                                enabled: body.opened
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    modeItem.activate();
                                    body.takeFocus();
                                }
                            }
                        }
                    }
                }
                Item {
                    id: settingsButton
                    objectName: "launcher-settings"
                    x: body.animatedWidth - 46
                    y: 16
                    width: 28
                    height: 28
                    activeFocusOnTab: true
                    function activate(): void {
                        if (body.opened && !body.settingsActive)
                            body.settingsRequested();
                    }
                    Keys.onReturnPressed: activate()
                    Keys.onEnterPressed: activate()
                    Keys.onSpacePressed: activate()
                    FocusRing {
                        shown: body.keyboardMode && settingsButton.activeFocus && !body.glassReady
                    }
                    Rectangle {
                        anchors.fill: parent
                        radius: 8
                        color: gearMouse.containsMouse ? LiquidPalette.flatDrop : "transparent"
                    }
                    Image {
                        anchors.centerIn: parent
                        width: 20
                        height: 20
                        source: Qt.resolvedUrl("settings/icons/gear.svg")
                        sourceSize: Qt.size(24, 24)
                    }
                    MouseArea {
                        id: gearMouse
                        anchors.fill: parent
                        enabled: body.opened
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: settingsButton.activate()
                    }
                    Accessible.role: Accessible.Button
                    Accessible.name: "Settings"
                    Accessible.onPressAction: settingsButton.activate()
                }
                Rectangle {
                    x: body.side
                    y: Metrics.launcherHeader - 1
                    width: body.width - 2 * body.side
                    height: 1
                    color: body.faint
                }
                Item {
                    id: regularContent
                    objectName: "launcher-content"
                    anchors.fill: parent
                    visible: !body.settingsActive
                    Text {
                        x: body.side
                        y: body.bodyTop
                        width: body.width - 2 * body.side
                        height: 16
                        visible: body.note !== ""
                        verticalAlignment: Text.AlignVCenter
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                        text: body.note
                        font.family: ShellPalette.uiFont
                        font.pixelSize: 11
                        font.weight: Font.Medium
                        color: body.dim
                    }

                    // Apps: Frequent, the categories, the grid of tiles.
                    Text {
                        x: body.side
                        y: body.frequentTop
                        height: 18
                        visible: body.frequentCount > 0
                        verticalAlignment: Text.AlignVCenter
                        text: "FREQUENT"
                        font.family: ShellPalette.uiFont
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                        color: body.dim
                    }
                    Row {
                        x: body.side
                        y: body.frequentTop + 22
                        height: 76
                        Repeater {
                            id: frequentRepeater
                            model: body.frequentCount ? body.results.slice(0, body.frequentCount) : []
                            TileDelegate {
                                width: (body.width - 2 * body.side) / 6
                                height: 76
                            }
                        }
                    }
                    Row {
                        x: body.side
                        y: body.categoriesTop
                        height: 26
                        spacing: 2
                        Repeater {
                            id: categoryRepeater
                            model: body.categoriesShown ? body.categories : []
                            Item {
                                id: chip
                                activeFocusOnTab: true
                                function activate(): void {
                                    body.category = modelData;
                                }
                                Keys.onReturnPressed: activate()
                                Keys.onEnterPressed: activate()
                                Keys.onSpacePressed: activate()
                                FocusRing {
                                    shown: body.keyboardMode && chip.activeFocus && !body.glassReady
                                }
                                required property string modelData
                                readonly property bool current: body.category === modelData
                                width: chipLabel.implicitWidth + 22
                                height: 26
                                Text {
                                    id: chipLabel
                                    anchors.centerIn: parent
                                    text: chip.modelData
                                    textFormat: Text.PlainText
                                    font.family: ShellPalette.uiFont
                                    font.pixelSize: 13
                                    font.weight: Font.Medium
                                    color: chip.current ? body.ink : body.dim
                                    opacity: chip.current ? 1 - body.categoryAlpha : 1
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    enabled: body.opened
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        body.category = chip.modelData;
                                        body.takeFocus();
                                    }
                                }
                            }
                        }
                    }
                    GridView {
                        id: grid
                        visible: body.gridMode
                        x: 24
                        y: body.gridTop
                        width: body.width - 48
                        height: Math.max(0, body.height - y - 10)
                        clip: true
                        cellWidth: body.gridCell
                        cellHeight: 86
                        model: body.gridMode ? body.results.slice(body.frequentCount) : []
                        onContentYChanged: body.wake()
                        onCountChanged: body.wake()
                        delegate: TileDelegate {
                            offset: body.frequentCount
                            width: grid.cellWidth
                            height: 84
                        }
                    }

                    // Every other view: rows (All under kind headings).
                    ListView {
                        id: list
                        visible: body.listMode
                        x: body.side
                        y: body.listTop
                        width: body.width - 2 * body.side
                        height: Math.max(0, body.height - y - 12)
                        clip: true
                        spacing: 2
                        model: body.listMode ? body.results : []
                        onContentYChanged: body.wake()
                        onCountChanged: body.wake()
                        section.property: body.sectioned ? "group" : ""
                        section.delegate: Item {
                            required property string section
                            width: list.width
                            height: body.sectionHeight
                            Text {
                                anchors.bottom: parent.bottom
                                anchors.bottomMargin: 4
                                height: 18
                                verticalAlignment: Text.AlignVCenter
                                text: parent.section.toLocaleUpperCase()
                                textFormat: Text.PlainText
                                font.family: ShellPalette.uiFont
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                                color: body.dim
                            }
                        }
                        delegate: RowDelegate {
                            width: list.width
                            height: body.rowHeight
                        }
                        // Clipboard: clear everything, pause the recorder (when the shell owns it).
                        footer: ClipboardFooter {
                            controls: [clearClips, recordClips]
                            width: list.width
                            height: body.clipActions ? 50 : 0
                            visible: body.clipActions
                            Row {
                                y: 8
                                spacing: 8
                                ActionText {
                                    id: clearClips
                                    text: "Clear all"
                                    color: body.danger
                                    onClicked: body.clipboard.clear()
                                }
                                ActionText {
                                    id: recordClips
                                    visible: body.clipboard.recorderOwned
                                    text: body.clipboard.recorder === "recording" ? "Pause recording" : "Resume recording"
                                    onClicked: body.clipboard.setRecording(body.clipboard.recorder !== "recording")
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    ShaderEffectSource {
        id: contentTexture
        width: layer.width
        height: layer.height
        sourceItem: layer
        hideSource: body.glassReady
        live: true
        visible: false
    }

    // ---- The glass itself (screen coordinates; this item sits at `origin`) ----
    IslandGlass {
        id: panelGlass
        visible: body.glassReady && body.morph > 0
        backdrop: body.backdrop
        sceneOffset: body.origin
        plate: Qt.rect(body.origin.x, body.origin.y, body.shownWidth, body.shownHeight)
        drops: body.drops
        uRadius: body.shownRadius
        uIcons: contentTexture
        uSceneSize: Qt.point(layer.width, layer.height)
        uCover: body.backdrop ? body.backdrop.coverFor(Qt.point(0, 0)) : Qt.vector4d(0, 0, 1, 1)
    }

    // ---- On the glass: the button arrow while its bubble is up, then the active words,
    // the selected item and the search field ----
    Icon {
        id: arrowOnGlass
        x: 9
        y: 9
        width: 18
        height: 18
        kind: "logo"
        ink: ShellPalette.accent
        opacity: body.compactLogoAlpha * body.logoAlpha
        visible: opacity > .001
    }
    Item {
        id: onGlass
        width: body.shownWidth
        height: body.shownHeight
        clip: true
        opacity: body.contentAlpha
        visible: opacity > .001
        Text {
            x: body.modeRect.x
            y: body.modeRect.y
            width: body.modeRect.width
            height: body.modeRect.height
            visible: !body.settingsActive && body.modeAlpha > .001
            opacity: body.modeAlpha
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            text: body.modes[body.modeIndex]
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 13
            font.weight: Font.Medium
            color: body.ink
        }
        Text {
            x: body.categoryRect.x
            y: body.categoryRect.y
            width: body.categoryRect.width
            height: body.categoryRect.height
            visible: body.categoriesShown && body.categoryAlpha > .001
            opacity: body.categoryAlpha
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            text: body.category
            textFormat: Text.PlainText
            font.family: ShellPalette.uiFont
            font.pixelSize: 13
            font.weight: Font.Medium
            color: body.ink
        }
        OnGlassItem {
            visible: !body.settingsActive
            sel: body.trail
            alpha: body.trailOnGlass
        }
        OnGlassItem {
            visible: !body.settingsActive
            sel: body.selection
            alpha: body.selectOnGlass
        }
        TextInput {
            id: query
            visible: !body.settingsActive
            TapHandler {
                acceptedButtons: Qt.AllButtons
                onPressedChanged: if (pressed)
                    body.keyboardMode = false
            }
            Keys.onPressed: event => {
                body.keyboardMode = true;
                event.accepted = false;
            }
            activeFocusOnTab: true
            FocusRing {
                shown: body.keyboardMode && query.activeFocus && !body.glassReady
            }
            x: 56
            y: 14
            width: Math.max(40, modesRow.x - x - 14)
            height: 32
            verticalAlignment: TextInput.AlignVCenter
            font.family: ShellPalette.uiFont
            font.pixelSize: 17
            color: body.ink
            selectionColor: ShellPalette.accent
            selectedTextColor: ShellPalette.accentText
            clip: true
            enabled: body.opened
            focus: body.opened
            selectByMouse: true
            maximumLength: 4096
            Accessible.name: "Search everything"
            onTextChanged: {
                body.selectionKey = "";
                if (body.opened)
                    body.apps.launchState = "idle";
            }
            Keys.onDeletePressed: event => {
                body.keyboardMode = true;
                if (body.modeIndex === 4) {
                    event.accepted = true;
                    body.deleteSelectedClip();
                } else
                    event.accepted = false;
            }
            Keys.onReturnPressed: event => {
                body.keyboardMode = true;
                event.accepted = true;
                body.activate();
            }
            Keys.onEnterPressed: event => {
                body.keyboardMode = true;
                event.accepted = true;
                body.activate();
            }
            Keys.onDownPressed: event => {
                body.keyboardMode = true;
                event.accepted = true;
                body.moveVertical(1);
            }
            Keys.onUpPressed: event => {
                body.keyboardMode = true;
                event.accepted = true;
                body.moveVertical(-1);
            }
            Keys.onLeftPressed: event => {
                body.keyboardMode = true;
                if (body.horizontalMoves) {
                    event.accepted = true;
                    body.moveSelection(-1);
                } else
                    event.accepted = false;
            }
            Keys.onRightPressed: event => {
                body.keyboardMode = true;
                if (body.horizontalMoves) {
                    event.accepted = true;
                    body.moveSelection(1);
                } else
                    event.accepted = false;
            }
            Text {
                anchors.fill: parent
                verticalAlignment: Text.AlignVCenter
                visible: query.length === 0 && !query.inputMethodComposing
                text: body.assignFor ? "Choose the app for this window" : "Search everything"
                textFormat: Text.PlainText
                font: query.font
                color: body.faint
            }
            Keys.onEscapePressed: event => {
                body.keyboardMode = true;
                event.accepted = true;
                body.dismissed();
            }
            Keys.onTabPressed: event => {
                body.keyboardMode = true;
                event.accepted = true;
                if (event.modifiers & Qt.ControlModifier)
                    body.moveKeyboardFocus(1);
                else
                    body.modeIndex = (body.modeIndex + 1) % body.modes.length;
            }
            Keys.onBacktabPressed: event => {
                body.keyboardMode = true;
                event.accepted = true;
                if (event.modifiers & Qt.ControlModifier)
                    body.moveKeyboardFocus(-1);
                else
                    body.modeIndex = (body.modeIndex + body.modes.length - 1) % body.modes.length;
            }
        }
    }
    Item {
        width: body.shownWidth
        height: body.shownHeight
        clip: true
        opacity: body.contentAlpha
        visible: opacity > .001
        Loader {
            id: settingsLoader
            active: body.settingsActive && body.settingsController !== null
            y: Metrics.launcherHeader
            width: body.width
            height: Math.max(0, body.height - y)
            sourceComponent: SettingsView {
                embedded: true
                surfaceRadius: body.shownRadius
                catalog: body.settingsController.catalog
                service: body.settingsController.service
                niri: body.niri
                notificationStore: body.settingsController.notificationStore
                displays: body.settingsController.displays
                availablePages: body.settingsController.availablePages
                onCloseRequested: body.leaveSettings()
            }
        }
    }
    // Draw the outline once, above the glass, without refracting its edges.
    FocusRing {
        id: glassFocus
        anchors.fill: undefined
        readonly property Item focused: body.Window.window?.activeFocusItem ?? null
        readonly property bool belongs: focused !== null && body.keyboardControls().includes(focused)
        readonly property point position: {
            body.tick;
            list.contentY;
            return belongs ? focused.mapToItem(body, 0, 0) : Qt.point(0, 0);
        }
        x: position.x
        y: position.y
        width: belongs ? focused.width : 0
        height: belongs ? focused.height : 0
        readonly property bool contained: belongs && x >= 0 && y >= 0 && x + width <= body.shownWidth && y + height <= body.shownHeight
        readonly property bool eligible: body.keyboardMode && body.opened && contained
        shown: body.glassReady && eligible
    }
}
