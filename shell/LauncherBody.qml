// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import "SystemIcons.js" as SystemIcons
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
    function headerControls(): var {
        const controls = [closeButton];
        if (!settingsActive)
            controls.push(query);
        for (let i = 0; i < modesRepeater.count; ++i)
            controls.push(modesRepeater.itemAt(i));
        controls.push(settingsButton);
        return controls.filter(item => item && item.visible && item.enabled);
    }
    function categoryControls(): var {
        const controls = [];
        for (let i = 0; i < categoryRepeater.count; ++i)
            controls.push(categoryRepeater.itemAt(i));
        return controls.filter(item => item && item.visible && item.enabled);
    }
    // A mode tab or a category chip: its focus takes the shape of the drop it may carry.
    function dropShaped(item: Item): bool {
        for (let i = 0; i < modesRepeater.count; ++i)
            if (modesRepeater.itemAt(i) === item)
                return true;
        return categoryControls().includes(item);
    }
    function footerControls(): var {
        return !settingsActive && list.footerItem && clipActions ? (list.footerItem as ClipboardFooter).controls.filter(item => item.visible && item.enabled) : [];
    }
    function keyboardControls(): var {
        let controls = headerControls();
        if (settingsActive)
            controls.push(settingsView?.search);
        else {
            controls = controls.concat(categoryControls());
            if (results.length)
                controls.push(resultFocus);
            controls = controls.concat(footerControls());
        }
        return controls.filter(item => item && item.visible && item.enabled);
    }
    function focusControl(item: Item): void {
        if (!item)
            return;
        keyboardMode = true;
        item.forceActiveFocus(Qt.TabFocusReason);
        if (footerControls().includes(item))
            list.contentY = Math.max(0, list.contentHeight - list.height);
        wake();
    }
    function focusResults(): void {
        if (!results.length) {
            focusControl(query);
            return;
        }
        moveSelection(0);
        focusControl(resultFocus);
    }
    // While the search has focus the highlighted result is what Enter opens; Down moves that
    // highlight on to the next result and enters the results there, as before arrow
    // navigation (0.8.0). Up from the first result still returns to the search.
    function enterResultsBelow(): void {
        if (!results.length)
            return;
        moveVertical(1);
        focusControl(resultFocus);
    }
    function moveKeyboardFocus(step: int): void {
        const controls = keyboardControls();
        const index = controls.findIndex(item => item.activeFocus);
        focusControl(controls[(index + step + controls.length) % controls.length]);
        if (resultFocus.activeFocus)
            moveSelection(0);
    }
    function moveArrow(key: int): void {
        const headers = headerControls();
        const categories = categoryControls();
        const footers = footerControls();
        const header = headers.findIndex(item => item.activeFocus);
        const chip = categories.findIndex(item => item.activeFocus);
        const footer = footers.findIndex(item => item.activeFocus);
        const backwards = key === Qt.Key_Left || key === Qt.Key_Up;
        if (resultFocus.activeFocus) {
            if (key === Qt.Key_Up) {
                if ((!gridMode && selected <= 0) || (gridMode && selected < frequentCount))
                    focusControl(query);
                else if (gridMode && selected - frequentCount < 7)
                    focusControl(categoryRepeater.itemAt(body.categories.indexOf(category)) || categories[0] || query);
                else
                    moveVertical(-1);
            } else if (key === Qt.Key_Down) {
                if (gridMode && selected < frequentCount && categories.length)
                    focusControl(categories[0]);
                else if (selected >= results.length - (gridMode ? 7 : 1) && footers.length)
                    focusControl(footers[0]);
                else
                    moveVertical(1);
            } else if (gridMode) {
                const first = selected < frequentCount ? 0 : frequentCount + Math.floor((selected - frequentCount) / 7) * 7;
                const last = selected < frequentCount ? frequentCount - 1 : Math.min(first + 6, results.length - 1);
                moveSelection(Math.max(first, Math.min(last, selected + (backwards ? -1 : 1))) - selected);
            }
        } else if (header >= 0) {
            if (key === Qt.Key_Down) {
                if (settingsActive)
                    settingsView?.takeFocus();
                else if (categories.length && !frequentCount)
                    focusControl(categories[0]);
                else
                    focusResults();
            } else if (key === Qt.Key_Left || key === Qt.Key_Right)
                focusControl(headers[Math.max(0, Math.min(headers.length - 1, header + (backwards ? -1 : 1)))]);
        } else if (chip >= 0) {
            if (key === Qt.Key_Up) {
                if (frequentCount) {
                    selectAt(Math.min(chip, frequentCount - 1));
                    focusResults();
                } else
                    focusControl(query);
            } else if (key === Qt.Key_Down) {
                if (results.length > frequentCount)
                    selectAt(frequentCount + Math.min(chip, results.length - frequentCount - 1));
                focusResults();
            } else
                focusControl(categories[Math.max(0, Math.min(categories.length - 1, chip + (backwards ? -1 : 1)))]);
        } else if (footer >= 0) {
            if (key === Qt.Key_Up)
                focusResults();
            else if (key === Qt.Key_Left || key === Qt.Key_Right)
                focusControl(footers[Math.max(0, Math.min(footers.length - 1, footer + (backwards ? -1 : 1)))]);
        }
    }
    Keys.onPressed: event => handleKey(event)
    function handleKey(event): void {
        keyboardMode = true;
        if (event.key === Qt.Key_Escape) {
            if (settingsActive)
                leaveSettings();
            else if (query.text.length) {
                query.text = "";
                focusControl(query);
            } else if (!query.activeFocus)
                focusControl(query);
            else
                backRequested();
        } else if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
            moveKeyboardFocus(event.key === Qt.Key_Backtab || (event.modifiers & Qt.ShiftModifier) ? -1 : 1);
        } else if ([Qt.Key_Left, Qt.Key_Up, Qt.Key_Right, Qt.Key_Down].includes(event.key)) {
            moveArrow(event.key);
        } else if (!settingsActive && event.text && !(event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)) && event.text.charCodeAt(0) >= 32) {
            focusControl(query);
            query.insert(query.cursorPosition, event.text);
        } else {
            event.accepted = false;
            return;
        }
        event.accepted = true;
    }
    // Results retain their logical selection while list delegates are recycled.
    Item {
        id: resultFocus
        objectName: "launcher-results"
        Accessible.role: Accessible.ListItem
        Accessible.name: body.results[body.selected]?.label ?? ""
        Accessible.selected: body.selected >= 0 && body.selected < body.results.length
        Accessible.onPressAction: body.activate()
        visible: !body.settingsActive && body.results.length > 0
        activeFocusOnTab: activeFocus || visible
        Keys.onPressed: event => body.handleKey(event)
        Keys.onReturnPressed: body.activate()
        Keys.onEnterPressed: body.activate()
        Keys.onSpacePressed: body.activate()
        Keys.onDeletePressed: body.deleteSelectedClip()
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
    signal backRequested
    signal dismissed
    signal settingsRequested
    signal openFailed(string reason)
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
            }
        }
        function onFailed(id: string, name: string, reason: string): void {
            if (body.pendingRecentApp === id)
                body.pendingRecentApp = "";
        }
    }
    RecentFiles {
        id: files
        active: body.opened
        onOpeningRequested: body.dismissed()
        onOpenStateChanged: if (!["idle", "pending", "requested"].includes(openState))
            body.openFailed(body.stateNote("file-open", openState))
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
            app: qsTranslate("LauncherBody", "Apps"),
            window: qsTranslate("LauncherBody", "Windows"),
            file: qsTranslate("LauncherBody", "Files"),
            clip: qsTranslate("LauncherBody", "Clipboard")
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
                        detail: entry.genericName || qsTranslate("LauncherBody", "Recently opened")
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
                        detail: metadata?.title || qsTranslate("LauncherBody", "Untitled window"),
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
                        detail: c.image ? qsTranslate("LauncherBody", "Image · Enter copies, never pastes") : qsTranslate("LauncherBody", "Text · Enter copies, never pastes")
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
        onStateChanged: if (!["idle", "pending", "requested"].includes(state))
            body.openFailed(body.stateNote("web", state))
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
    readonly property var categoryLabels: ({All: qsTranslate("LauncherBody", "All"), Development: qsTranslate("LauncherBody", "Development"), Internet: qsTranslate("LauncherBody", "Internet"), Media: qsTranslate("LauncherBody", "Media"), Games: qsTranslate("LauncherBody", "Games"), Learning: qsTranslate("LauncherBody", "Learning"), Office: qsTranslate("LauncherBody", "Office"), System: qsTranslate("LauncherBody", "System")})
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
    function settingsScore(words: var): int {
        let total = 0;
        for (const word of words) {
            if (word.length < 3)
                return 0;
            const aliases = ["settings", "preferences", "control", "panel"];
            const matches = aliases.filter(alias => alias.startsWith(word));
            if (!matches.length)
                return 0;
            total += Math.max(...matches.map(alias => fieldScore(alias, word, alias === "settings" ? 3 : 1)));
        }
        return total;
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
        const id = categories.slice(1).find(name => categoryMapping[name].some(c => entry.categories.includes(c)));
        return categoryLabels[id] || "";
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
                    group: qsTranslate("LauncherBody", "Calculator"),
                    label: "= " + value,
                    detail: qsTranslate("LauncherBody", "Enter to copy"),
                    value: String(value)
                });
        }
        if (frequentCount) {
            for (const entry of apps.frequent)
                rows.push({
                    kind: "frequent",
                    entry: entry,
                    label: entry.name,
                    detail: qsTranslate("LauncherBody", "Frequently opened")
                });
        }
        if (modeIndex < 2 && (text || modeIndex === 1)) {
            const words = text.split(/\s+/).filter(word => word.length > 0);
            const found = text ? apps.entries : apps.entries.filter(entry => matchesCategory(entry));
            const appRows = found.map(entry => ({
                        kind: "app",
                        group: qsTranslate("LauncherBody", "Apps"),
                        entry: entry,
                        label: entry.name,
                        score: text ? appScore(entry, words) : 0,
                        uses: apps.counts[entry.id] || 0,
                        // An app row names its category, as launcher.js does ("Internet").
                        detail: entry.runInTerminal ? qsTranslate("LauncherBody", "Terminal app") : categoryOf(entry) || entry.genericName || ""
                    })).filter(row => !text || row.score > 0);
            const score = text && !assignFor ? settingsScore(words) : 0;
            if (score > 0)
                appRows.push({
                    kind: "settings",
                    group: qsTranslate("LauncherBody", "Apps"),
                    label: qsTranslate("LauncherBody", "Settings"),
                    detail: "",
                    score: score,
                    uses: 0
                });
            // Score Settings alongside apps before applying the shared result limit.
            if (text)
                appRows.sort((a, b) => b.score - a.score || b.uses - a.uses || a.label.localeCompare(b.label));
            rows.push(...(text ? appRows.slice(0, modeIndex === 1 ? 64 : 5) : appRows));
        }
        if (modeIndex === 2 || (modeIndex === 0 && text)) {
            for (const w of niri.windows) {
                const metadata = labels.values[w.id];
                const title = metadata?.title || qsTranslate("LauncherBody", "Untitled window");
                const entry = metadata?.appId ? DesktopEntries.heuristicLookup(metadata.appId) : null;
                const name = entry?.name || metadata?.appId || qsTranslate("LauncherBody", "Window #%1").arg(w.id);
                const ws = niri.workspaces.find(item => item.id === w.workspace_id);
                if (!text || (name + " " + title).toLocaleLowerCase().includes(text))
                    rows.push({
                        kind: "window",
                        group: qsTranslate("LauncherBody", "Windows"),
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
                    group: qsTranslate("LauncherBody", "Files"),
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
                    detail: c.image ? qsTranslate("LauncherBody", "Image · Enter copies, never pastes") : qsTranslate("LauncherBody", "Text · Enter copies, never pastes")
                });
        }
        if (modeIndex === 0 && text)
            rows.push({
                kind: "web",
                group: qsTranslate("LauncherBody", "Web"),
                label: qsTranslate("LauncherBody", "Search the web for “%1”").arg(query.text.trim()),
                detail: qsTranslate("LauncherBody", "DuckDuckGo · default browser"),
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
        keyboardMode = false;
        if (selected !== index)
            selectAt(index);
    }
    function activate(): void {
        if (!opened)
            return;
        const row = results[selected];
        if (!row)
            return;
        if (row.kind === "settings") {
            settingsRequested();
        } else if (row.kind === "app" || row.kind === "frequent") {
            if (assignFor) {
                identity?.learn(assignFor, row.entry.id);
                assignFor = "";
                dismissed();
                return;
            }
            if (!row.entry || apps.busy)
                return;
            pendingRecentApp = row.entry.id; // recorded once the launch is accepted
            // Release the modal layer before a fast app can map its first window.
            // Waiting for the helper reply leaves the old panel holding keyboard focus.
            dismissed();
            apps.launch(row.entry);
        } else if (row.kind === "clip") {
            recent.record("clip", row.id);
            clips.perform(row.id, false);
        } else if (row.kind === "web") {
            if (web.busy)
                return;
            dismissed();
            web.start({
                op: "web",
                query: row.value
            });
        } else if (row.kind === "file") {
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
    readonly property var modeLabels: [qsTranslate("LauncherBody", "All"), qsTranslate("LauncherBody", "Apps"), qsTranslate("LauncherBody", "Windows"), qsTranslate("LauncherBody", "Files"), qsTranslate("LauncherBody", "Clipboard")]

    function filesMessage(): string {
        if (!["idle", "pending", "requested"].includes(files.openState))
            return stateNote("file-open", files.openState);
        if (files.openState === "pending")
            return qsTranslate("LauncherBody", "Opening file…");
        if (files.state === "loading")
            return qsTranslate("LauncherBody", "Reading recent files…");
        if (files.state === "absent")
            return qsTranslate("LauncherBody", "No recent files yet.");
        if (files.state !== "ready") {
            if (files.state === "access_denied")
                return qsTranslate("LauncherBody", "Permission denied reading recent files.");
            if (["source_too_large", "too_many_entries", "xml_limit"].includes(files.state))
                return qsTranslate("LauncherBody", "The recent-files list is too large to read safely.");
            if (["invalid_xbel", "dtd_not_allowed"].includes(files.state))
                return qsTranslate("LauncherBody", "The recent-files list is damaged or unsupported.");
            if (files.state === "timeout")
                return qsTranslate("LauncherBody", "Reading recent files took too long. Reopen to retry.");
            return qsTranslate("LauncherBody", "Recent files are unavailable.");
        }
        if (!results.length)
            return query.text.trim() ? qsTranslate("LauncherBody", "No recent filenames match.") : qsTranslate("LauncherBody", "No existing recent files.");
        return "";
    }
    // Words for a helper's or service's state that has something to say (`what`: its source);
    // an internal code never reaches the screen.
    function stateNote(what: string, s: string): string {
        switch (what) {
        case "clipboard":
            return s === "idle" || s === "pending" ? qsTranslate("LauncherBody", "Reading clipboard history…") : qsTranslate("LauncherBody", "Clipboard history isn’t available.");
        case "clipboard-action":
            return qsTranslate("LauncherBody", "The clipboard action didn’t go through.");
        case "web":
            return s === "pending" ? qsTranslate("LauncherBody", "Opening the web search…") : qsTranslate("LauncherBody", "Couldn’t open the web search.");
        case "launch":
            return s === "pending" ? qsTranslate("LauncherBody", "Starting the app…") : qsTranslate("LauncherBody", "Couldn’t start the app.");
        case "windows":
            return qsTranslate("LauncherBody", "Open windows can’t be listed right now.");
        case "labels":
            return s === "connecting" ? qsTranslate("LauncherBody", "Reading window titles…") : qsTranslate("LauncherBody", "Window titles unavailable.");
        case "file-open":
            return ({
                    file_missing: qsTranslate("LauncherBody", "This file no longer exists."),
                    no_handler: qsTranslate("LauncherBody", "No default application for this file type."),
                    access_denied: qsTranslate("LauncherBody", "Permission denied opening this file."),
                    timeout: qsTranslate("LauncherBody", "Open was not confirmed in time; the application may still open."),
                    helper_dependency_missing: qsTranslate("LauncherBody", "Files can’t be opened from here on this system.")
                })[s] || qsTranslate("LauncherBody", "Could not open this file with its default application.");
        }
        return "";
    }
    // The line under the header (launcher.js "note"): only when there is something to say.
    function noteText(): string {
        if (apps.busy)
            return stateNote("launch", "pending");
        if (modeIndex === 4) {
            if (clips.state !== "ready")
                return stateNote("clipboard", clips.state);
            if (!["idle", "pending", "copied", "deleted"].includes(clips.actionState))
                return stateNote("clipboard-action", clips.actionState);
            if (clips.recorder === "paused")
                return qsTranslate("LauncherBody", "Recording is paused — new copies are not saved.");
            if (clips.recorder === "failed")
                return qsTranslate("LauncherBody", "The clipboard recorder stopped unexpectedly · Resume restarts it");
            if (!clips.entries.length)
                return qsTranslate("LauncherBody", "Clipboard history is empty");
            return results.length ? "" : qsTranslate("LauncherBody", "Nothing matches “%1”").arg(queryText);
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
            return results.length ? "" : qsTranslate("LauncherBody", "Nothing opened from here yet · type to search everything");
        if (!results.length)
            return queryText ? qsTranslate("LauncherBody", "Nothing matches “%1”").arg(queryText) : modeIndex === 2 ? qsTranslate("LauncherBody", "No windows are open.") : qsTranslate("LauncherBody", "Nothing found.");
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
    // selected item, the active mode and category are drawn fully on the glass as soon as
    // their drop exists, so a growing drop never ghosts its own text.
    // Without a GPU the same layers are drawn flat.
    property DockBackdrop backdrop: null
    // The panel's top left on the screen: the bar's launcher button.
    property point origin: Qt.point(Metrics.logoX, Metrics.top)
    // The bar's button bubble: part of the panel while the morph is young (launcher.js).
    property var logoBubble: null
    readonly property bool glassReady: backdrop !== null && backdrop.ready && GraphicsInfo.api !== GraphicsInfo.Software
    readonly property var modeBubble: Liquid.liquid()
    // The mode and category drops stand 4 px proud of their tab; keyboard focus does too.
    readonly property int tabFocusPad: 4
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
        return Liquid.onGlass(modeBubble);
    }
    readonly property real categoryAlpha: {
        tick;
        return Liquid.onGlass(categoryBubble);
    }
    readonly property real selectAlpha: {
        tick;
        return Liquid.smooth(selectBubble.alpha.x);
    }
    readonly property real logoAlpha: {
        tick;
        return Liquid.onGlass(logoBubble);
    }
    // Where the active mode, category and selected item are, panel coordinates.
    // itemAt() is not a dependency: the binding first runs before the Repeater has made its
    // tabs and got an empty rect, which nothing re-ran until the mode changed, so the first
    // open put the mode drop on that empty rect (8x8 at -4, -4) and hid the tab's label.
    // The count re-runs it once the tabs exist; from then on their geometry is tracked.
    readonly property rect modeRect: {
        modesRepeater.count;
        const mode = settingsActive ? settingsButton : modesRepeater.itemAt(modeIndex);
        return mode ? Qt.rect(mode.x + (settingsActive ? 0 : modesRow.x), mode.y + (settingsActive ? 0 : modesRow.y), mode.width, mode.height) : Qt.rect(0, 0, 0, 0);
    }
    property rect categoryRect: Qt.rect(0, 0, 0, 0)
    property var selection: null
    // The selection is fully on the glass while its drop exists, including growth and melt.
    // The item being left (`trail`) follows coverage while the drop flows away. Both copies
    // and their originals use the same frame's selection: updating one from `selected`
    // immediately would leave a frame with neither copy drawn. Everything here, including
    // `lastSelected`, is set in frame() together.
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
    onKeyboardModeChanged: wake()
    onMorphChanged: wake()
    onSelectedChanged: wake()
    onHeightChanged: wake()
    onWidthChanged: wake()
    onModeRectChanged: wake()
    function itemRect(item: Item): rect {
        const p = item.mapToItem(body, 0, 0);
        return Qt.rect(p.x, p.y, item.width, item.height);
    }
    // The selected item (launcher.js updateGeometry): its rect, the viewport that clips it,
    // how it is drawn and the bubble around it. A tile's bubble is one size on every tile,
    // whatever its name (Metrics.launcherTileBubble); rows and cards stand 6 px proud. Null
    // when not on screen.
    function selectedGeometry(): var {
        return settingsActive || (keyboardMode && !resultFocus.activeFocus && !query.activeFocus) ? null : geometryOf(selected);
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
        // No drop on a tab without geometry: it would stand on nothing and fade its label out.
        if (modeRect.width > 0 && modeRect.height > 0) {
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
        // The selected item is fully lifted while its drop exists; the item left behind
        // remains on the glass in proportion to the bubble's departing coverage.
        if (selected !== lastSelected) {
            trailIndex = lastSelected >= 0 && selectOnGlass > .01 ? lastSelected : -1;
            lastSelected = selected;
        }
        trail = trailIndex >= 0 && trailIndex !== selected ? geometryOf(trailIndex) : null;
        const onGlass = Liquid.smooth(selectBubble.alpha.x);
        selectOnGlass = sel ? Liquid.onGlass(selectBubble) : 0;
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
    // Ink follows the selected glass theme.
    readonly property color ink: LiquidPalette.ink
    readonly property color dim: LiquidPalette.plateDim
    readonly property color faint: LiquidPalette.faint
    readonly property color danger: LiquidPalette.danger

    // ---- Flat stand-in (no GPU): the plate and the drops, under the content ----
    Rectangle {
        visible: !body.glassReady && body.morph > 0
        width: body.shownWidth
        height: body.shownHeight
        radius: body.shownRadius
        color: LiquidPalette.flatPlate
    }
    // The Settings canvas of the flat stand-in: over the plate and under the drops, which lie
    // under the content here (with the glass the drops lie above the content layer and its tint).
    CanvasTint {
        objectName: "launcher-canvas-flat"
        shown: LiquidPalette.sharedCanvas && !body.settingsActive && !body.glassReady && body.morph > 0
        y: Metrics.launcherHeader
        width: body.shownWidth
        height: Math.max(0, body.shownHeight - y)
        bottomLeftRadius: body.shownRadius
        bottomRightRadius: body.shownRadius
        opacity: body.contentAlpha
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
        objectName: "launcher-result-" + resultIndex
        Accessible.role: Accessible.ListItem
        Accessible.name: modelData.label ?? ""
        Accessible.selected: body.selected === resultIndex
        Accessible.onPressAction: {
            body.selectAt(resultIndex);
            body.activate();
        }
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
        objectName: "launcher-result-" + index
        Accessible.role: Accessible.ListItem
        Accessible.name: modelData.label ?? ""
        Accessible.selected: body.selected === index
        Accessible.onPressAction: {
            body.selectAt(index);
            body.activate();
        }
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
        Keys.onPressed: event => body.handleKey(event)
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
            // The Settings canvas under the header, as Settings has it (SettingsView draws its
            // own while it is shown).
            CanvasTint {
                objectName: "launcher-canvas"
                shown: LiquidPalette.sharedCanvas && !body.settingsActive && body.glassReady
                y: Metrics.launcherHeader
                width: body.shownWidth
                height: Math.max(0, body.shownHeight - y)
                bottomLeftRadius: body.shownRadius
                bottomRightRadius: body.shownRadius
                opacity: body.contentAlpha
            }
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
                    Keys.onPressed: event => body.handleKey(event)
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
                    Accessible.name: qsTranslate("LauncherBody", "Close launcher")
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
                            Accessible.role: Accessible.PageTab
                            Accessible.name: body.modeLabels[index]
                            Accessible.selected: !body.settingsActive && body.modeIndex === index
                            Accessible.onPressAction: activate()
                            activeFocusOnTab: true
                            Keys.onPressed: event => body.handleKey(event)
                            function activate(): void {
                                body.leaveSettings();
                                body.modeIndex = index;
                            }
                            Keys.onReturnPressed: activate()
                            Keys.onEnterPressed: activate()
                            Keys.onSpacePressed: activate()
                            // The mode drop's shape (pad 4, radius 13): on the open mode it is
                            // the same bubble, on another one the bubble the arrow moved.
                            FocusRing {
                                anchors.margins: -body.tabFocusPad
                                radius: 13
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
                                text: body.modeLabels[modeItem.index]
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
                    Keys.onPressed: event => body.handleKey(event)
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
                        // The gear in the grey of the mode labels beside it (All, Windows, Files).
                        source: SystemIcons.settingsSvg(body.dim)
                        opacity: body.settingsActive ? 1 - body.modeAlpha : 1
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
                    Accessible.name: qsTranslate("LauncherBody", "Settings")
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
                        text: qsTranslate("LauncherBody", "FREQUENT")
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
                                Accessible.role: Accessible.PageTab
                                Accessible.name: body.categoryLabels[modelData]
                                Accessible.selected: current
                                Accessible.onPressAction: activate()
                                activeFocusOnTab: true
                                Keys.onPressed: event => body.handleKey(event)
                                function activate(): void {
                                    body.category = modelData;
                                }
                                Keys.onReturnPressed: activate()
                                Keys.onEnterPressed: activate()
                                Keys.onSpacePressed: activate()
                                FocusRing {
                                    anchors.margins: -body.tabFocusPad
                                    radius: 13
                                    shown: body.keyboardMode && chip.activeFocus && !body.glassReady
                                }
                                required property string modelData
                                readonly property bool current: body.category === modelData
                                width: chipLabel.implicitWidth + 22
                                height: 26
                                Text {
                                    id: chipLabel
                                    anchors.centerIn: parent
                                    text: body.categoryLabels[chip.modelData]
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
                                    text: qsTranslate("LauncherBody", "Clear all")
                                    color: body.danger
                                    onClicked: body.clipboard.clear()
                                }
                                ActionText {
                                    id: recordClips
                                    visible: body.clipboard.recorderOwned
                                    text: body.clipboard.recorder === "recording" ? qsTranslate("LauncherBody", "Pause recording") : qsTranslate("LauncherBody", "Resume recording")
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
        objectName: "launcher-glass"
        // The settings gear has the same thin, tight drop as a workspace button.
        uBulge: Qt.vector4d(body.settingsActive ? 5 : Liquid.BUBBLE.restUnion, 0, 1, 0)
        uRefraction: body.settingsActive ? 0 : Liquid.BUBBLE.refraction
        uDispersion: body.settingsActive ? 0 : Liquid.BUBBLE.dispersion
        uThickness: body.settingsActive ? 1.5 : Liquid.BUBBLE.thickness
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
        Image {
            objectName: "launcher-settings-ink"
            x: body.modeRect.x + (body.modeRect.width - width) / 2
            y: body.modeRect.y + (body.modeRect.height - height) / 2
            width: 20
            height: 20
            visible: body.settingsActive && body.modeAlpha > .001
            opacity: body.modeAlpha
            source: SystemIcons.settingsSvg(body.ink)
            sourceSize: Qt.size(24, 24)
        }
        Text {
            x: body.modeRect.x
            y: body.modeRect.y
            width: body.modeRect.width
            height: body.modeRect.height
            visible: !body.settingsActive && body.modeAlpha > .001
            opacity: body.modeAlpha
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            text: body.modeLabels[body.modeIndex]
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
            text: body.categoryLabels[body.category]
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
            objectName: "launcher-search"
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
            // Keyboard focus on Search everything: the hover bubble (plain fill, light rim),
            // under the text.
            Rectangle {
                objectName: "launcher-search-focus"
                anchors.fill: parent
                radius: 8
                color: LiquidPalette.flatDrop
                border.width: 1
                border.color: LiquidPalette.flatDropRim
                visible: body.keyboardMode && query.activeFocus
                z: -1
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
            Accessible.name: qsTranslate("LauncherBody", "Search everything")
            onTextChanged: {
                body.selectionKey = "";
                if (body.opened && !body.apps.busy)
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
                body.enterResultsBelow();
                event.accepted = true;
            }
            Keys.onUpPressed: event => {
                body.moveArrow(Qt.Key_Up);
                event.accepted = true;
            }
            Keys.onLeftPressed: event => {
                if (query.text.length)
                    event.accepted = false;
                else {
                    body.moveArrow(Qt.Key_Left);
                    event.accepted = true;
                }
            }
            Keys.onRightPressed: event => {
                if (query.text.length)
                    event.accepted = false;
                else {
                    body.moveArrow(Qt.Key_Right);
                    event.accepted = true;
                }
            }
            Text {
                anchors.fill: parent
                verticalAlignment: Text.AlignVCenter
                visible: query.length === 0 && !query.inputMethodComposing
                text: body.assignFor ? qsTranslate("LauncherBody", "Choose the app for this window") : qsTranslate("LauncherBody", "Search everything")
                textFormat: Text.PlainText
                font: query.font
                color: body.faint
            }
            Keys.onEscapePressed: event => {
                if (query.text.length)
                    query.text = "";
                else
                    body.backRequested();
                event.accepted = true;
            }
            Keys.onTabPressed: event => {
                body.moveKeyboardFocus(event.modifiers & Qt.ShiftModifier ? -1 : 1);
                event.accepted = true;
            }
            Keys.onBacktabPressed: event => {
                body.moveKeyboardFocus(-1);
                event.accepted = true;
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
        readonly property bool belongs: focused !== null && focused !== query && focused !== resultFocus && body.keyboardControls().includes(focused)
        // Tabs and category chips take the drop's shape (pad 4, radius 13), like the flat rings.
        readonly property real pad: belongs && body.dropShaped(focused) ? body.tabFocusPad : 0
        readonly property point position: {
            body.tick;
            list.contentY;
            return belongs ? focused.mapToItem(body, 0, 0) : Qt.point(0, 0);
        }
        x: position.x - pad
        y: position.y - pad
        width: belongs ? focused.width + 2 * pad : 0
        height: belongs ? focused.height + 2 * pad : 0
        radius: pad ? 13 : 8
        readonly property bool contained: belongs && x >= 0 && y >= 0 && x + width <= body.shownWidth && y + height <= body.shownHeight
        readonly property bool eligible: body.keyboardMode && body.opened && contained
        shown: body.glassReady && eligible
    }
}
