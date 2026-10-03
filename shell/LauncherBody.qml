pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import "Calculator.js" as Calculator
import "Liquid.js" as Liquid

// Queries and titles live only in this scene; status never returns their contents.
Item {
    id: body
    property bool liquid: false
    property string wallpaperState: "unknown"
    property string wallpaperTexture: ""
    property string page: ""
    property string pageQuery: ""
    property int pageMode: 0
    property bool recording: false
    property string recordedKey: ""
    readonly property alias settings: settings
    SettingsCatalog {
        id: settings
        active: body.opened && (body.modeIndex === 5 || body.page !== "")
    }
    // Pages of Settings (liquid-glass/launcher.js SETTINGS): a card with a symbol and a line
    // on what is inside.
    readonly property var settingsPages: [
        {
            id: "keyboard",
            label: "Keyboard",
            summary: "Layouts and the switch key",
            symbol: "input-keyboard-symbolic"
        },
        {
            id: "displays",
            label: "Displays",
            summary: "Resolution, scale, position",
            symbol: "video-display-symbolic"
        },
        {
            id: "wallpaper",
            label: "Wallpaper",
            summary: "Picture behind the glass",
            symbol: "preferences-desktop-wallpaper-symbolic"
        },
        {
            id: "defaults",
            label: "Default apps",
            summary: "Browser, terminal, files, editor",
            symbol: "preferences-other-symbolic"
        },
        {
            id: "keys",
            label: "Keybindings",
            summary: "Every shortcut, editable",
            symbol: "preferences-desktop-keyboard-shortcuts-symbolic"
        },
        {
            id: "config",
            label: "Config files",
            summary: "What Emaki writes and where",
            symbol: "text-x-generic-symbolic"
        },
        {
            id: "dock",
            label: "Dock",
            summary: "Auto-hide, pinned apps",
            symbol: "view-app-grid-symbolic"
        },
        {
            id: "bar",
            label: "Top bar",
            summary: "Clock, islands, workspaces",
            symbol: "focus-top-bar-symbolic"
        },
        {
            id: "history",
            label: "History of changes",
            summary: "Undo any change",
            symbol: "document-open-recent-symbolic"
        }
    ]
    // After a committed change the page's status line reads "Applied · Undo in History" and
    // clicking it opens that page (feedback after a change, with a way over).
    property bool applied: false
    Connections {
        target: settings
        function onApplied(): void {
            body.applied = body.page !== "" && body.page !== "history";
        }
    }
    function openPage(name: string): void {
        if (!page) {
            pageMode = modeIndex;
            pageQuery = query.text;
        }
        page = name;
        applied = false;
        pageAction = settings.lastAction;
        query.text = "";
        recording = false;
        addLayoutOpen = false;
        defaultsOpen = "";
        takeFocus();
    }
    // The core's answer shown on the page only once it changed after the page opened.
    property string pageAction: ""
    function closePage(): void {
        page = "";
        applied = false;
        recording = false;
        addLayoutOpen = false;
        defaultsOpen = "";
        modeIndex = pageMode;
        query.text = pageQuery;
        takeFocus();
    }
    // Pages write core keys (SettingsCatalog.set → history/undo) when a profile exists;
    // without one the core answers isolated_profile_required and nothing changes.
    property bool addLayoutOpen: false
    property string defaultsOpen: ""
    property BarPolicy barPolicy: null
    readonly property var switchKeys: ["Super+Space", "Alt+Shift", "Caps Lock"]
    function layoutName(code: string): string {
        return Array.from(settings.xkbLayouts).find(l => l.code === code)?.name ?? code;
    }
    function keyboardRows(): var {
        const managed = settings.value("keyboard.layouts");
        const codes = Array.isArray(managed) ? Array.from(managed) : [];
        const switchKey = settings.value("keyboard.switch_key") || "Super+Space";
        const rows = [];
        if (codes.length)
            for (const [i, code] of codes.entries())
                rows.push({
                    kind: "layout",
                    value: code,
                    label: layoutName(code),
                    detail: i === (niri.layouts?.current_idx ?? 0) ? "In use now" : "Switch with " + switchKey,
                    action: codes.length > 1 ? "Remove" : ""
                });
        else
            for (const [i, name] of (niri.layouts?.names || []).entries())
                rows.push({
                    kind: "info",
                    label: name,
                    detail: (i === niri.layouts.current_idx ? "In use now" : "Layout") + " · from your niri config, not managed by Emaki yet"
                });
        rows.push({
            kind: "layout-add",
            label: addLayoutOpen ? "Choose a layout to add" : "Add layout",
            detail: addLayoutOpen ? "Type to filter" : codes.length ? "" : "The first layout you add starts the Emaki-managed list"
        });
        if (addLayoutOpen)
            for (const l of Array.from(settings.xkbLayouts).filter(l => !codes.includes(l.code)))
                rows.push({
                    kind: "layout-pick",
                    value: l.code,
                    label: "+ " + l.name,
                    detail: l.code
                });
        const count = codes.length || (niri.layouts?.names || []).length;
        if (count > 1)
            for (const k of switchKeys)
                rows.push({
                    kind: "switch-key",
                    value: k,
                    label: "Switch layouts with " + k,
                    detail: k === switchKey ? "Current" : k === "Super+Space" ? "Packaged niri bind" : "XKB option in the prepared profile",
                    action: k === switchKey ? "In use" : "Select"
                });
        else
            rows.push({
                kind: "info",
                label: "With one layout there is nothing to switch, so the bar does not show a layout label.",
                detail: ""
            });
        return rows;
    }
    function defaultsRows(): var {
        const rows = [];
        for (const v of Array.from(settings.defaults)) {
            const managed = v.key ? settings.value(v.key) : null;
            const choices = Array.from(v.choices || []);
            const current = managed ? (choices.find(c => c.id === managed)?.name ?? managed) : v.key === "defaults.terminal" ? (Quickshell.env("EMAKI_TERMINAL") || ShellTools.terminal) + " · terminal adapter" : v.value;
            rows.push({
                kind: v.key ? "default-role" : "info",
                value: v.label,
                label: v.label,
                detail: current + (v.key ? "" : " · needs core key"),
                action: !v.key ? "" : choices.length > 1 || (choices.length === 1 && choices[0].id !== managed) ? "Change" : choices.length ? "Only one installed" : "None installed"
            });
            if (v.key && defaultsOpen === v.label)
                for (const c of choices)
                    rows.push({
                        kind: "default-pick",
                        value: v.key + ":" + c.id,
                        key: v.key,
                        id: c.id,
                        label: c.name,
                        detail: c.id === managed ? "Current" : "Make it the default " + v.label.toLocaleLowerCase(),
                        action: c.id === managed ? "In use" : "Select"
                    });
        }
        return rows;
    }
    function switchRow(kind: string, key: string, fallback: bool, label: string, onText: string, offText: string): var {
        const managed = settings.profile ? settings.value(key) : null;
        const on = typeof managed === "boolean" ? managed : fallback;
        return {
            kind: kind,
            value: key,
            label: label,
            detail: on ? onText : offText,
            action: on ? "On" : "Off"
        };
    }
    function settingsRows(): var {
        if (page === "keyboard")
            return keyboardRows();
        if (page === "displays")
            return Object.values(niri.model?.outputs || {}).map(o => ({
                        kind: "info",
                        label: o.name || "Display",
                        detail: (o.logical ? o.logical.width + " × " + o.logical.height + " · scale " + (o.logical.scale ?? "unknown") : "Unavailable") + " · read-only: scale and position wait for the installer decision"
                    }));
        if (page === "wallpaper") {
            const managed = settings.value("appearance.wallpaper");
            return [
                {
                    kind: "info",
                    label: "Current wallpaper",
                    detail: managed ? "Chosen in Emaki settings · prepared profile only, the session keeps its picture" : "From wpaperd · " + wallpaperState
                }
            ].concat(Array.from(settings.wallpapers).map(w => ({
                        kind: "wallpaper-pick",
                        value: w.path,
                        label: w.name,
                        detail: w.path === managed ? "Current" : "Use this picture",
                        action: w.path === managed ? "In use" : "Select"
                    })));
        }
        if (page === "defaults")
            return defaultsRows();
        if (page === "history")
            return settings.history.slice().reverse().map(h => ({
                        kind: "undo",
                        id: h.id,
                        label: h.kind + ": " + h.changes.map(c => c.key + " → " + String(c.after)).join(", "),
                        detail: new Date(h.observed_at_unix_ms).toLocaleString() + " · Undo (prepared profile only)"
                    }));
        if (page === "keys")
            return [
                {
                    kind: "key-edit",
                    label: "Toggle window floating",
                    detail: String(settings.values.find(v => v.key === "keybindings.toggle_window_floating")?.value ?? "Unset") + " · Change"
                }
            ];
        if (page === "config")
            return settings.values.map(v => ({
                        kind: "info",
                        label: v.key,
                        detail: String(v.value) + " · " + v.source
                    }));
        if (page === "dock")
            return dockRows();
        // app.js page === 'bar': the arrows switch of the mockup is not connected (no such shell setting).
        const auto = barPolicy?.autoHide ?? false;
        const ovws = barPolicy?.overviewWorkspaces ?? true;
        return [switchRow("bar-auto", "bar.autohide", auto, "Auto-hide bar", "Shows when the cursor touches the top edge", "Always visible, windows make room for it"), switchRow("bar-ovws", "bar.overview_workspaces", ovws, "Workspaces in overview", "Only the workspaces island stays in the overview", "The whole bar hides in the overview")];
    }
    // Settings → Dock (app.js page === 'dock'): core keys dock.* with a profile,
    // otherwise the shell's own dock.json (pinned always lives there).
    function dockRows(): var {
        if (!dock)
            return [];
        const rows = [
            {
                kind: "dock-on",
                label: "Show Dock",
                detail: "Pinned and running apps",
                action: dock.on ? "On" : "Off"
            }
        ];
        if (dock.on) {
            rows.push({
                kind: "dock-auto",
                label: "Auto-hide",
                detail: dock.autoHide ? "Shows when the cursor touches the bottom edge" : "Always visible, windows make room for it",
                action: dock.autoHide ? "On" : "Off"
            });
            rows.push({
                kind: "info",
                label: "Drag icons to reorder. Drag an icon away from the Dock to unpin it.",
                detail: "Right-click an icon for more."
            });
        }
        return rows;
    }
    property DockStore dock: null
    // Dock "Choose app…": the next app picked here becomes the entry for this app_id.
    property AppIdentity identity: null
    property string assignFor: ""
    function beginAssign(appId: string): void {
        reset();
        assignFor = appId;
        takeFocus();
    }
    // With a settings profile the dock keys go through the core (history/undo) and
    // come back to DockStore via ShellScene; without one dock.json changes at once.
    function dockChange(key: string, value: string, local: var): void {
        if (settings.profile)
            settings.set(key, value);
        else
            local();
    }
    function saveRecorded(): void {
        if (recording && recordedKey && !settings.busy) {
            settings.setFloating(recordedKey);
            recording = false;
            takeFocus();
        }
    }
    function captureKey(event: var): void {
        if (event.key === Qt.Key_Escape) {
            recording = false;
            takeFocus();
            return;
        }
        const mods = [];
        if (event.modifiers & Qt.MetaModifier)
            mods.push("Mod");
        if (event.modifiers & Qt.ControlModifier)
            mods.push("Ctrl");
        if (event.modifiers & Qt.AltModifier)
            mods.push("Alt");
        if (event.modifiers & Qt.ShiftModifier)
            mods.push("Shift");
        const special = ({
                32: "Space",
                1.67772e+07: "Left",
                1.67772e+07: "Up",
                1.67772e+07: "Right",
                1.67772e+07: "Down"
            });
        const key = event.key >= Qt.Key_A && event.key <= Qt.Key_Z ? String.fromCharCode(event.key) : event.key >= Qt.Key_F1 && event.key <= Qt.Key_F35 ? "F" + (event.key - Qt.Key_F1 + 1) : special[event.key];
        if (key)
            recordedKey = mods.concat([key]).join("+");
    }
    required property real expansion
    required property bool opened
    required property NiriService niri
    readonly property alias clipboard: clips
    // For tests/glass-shots.py: the glass draw and the layers under and on it.
    readonly property alias panelGlass: panelGlass
    readonly property alias contentLayer: layer
    readonly property alias onGlassLayer: onGlass
    readonly property alias arrowOnGlass: arrowOnGlass
    readonly property alias appCatalog: apps
    signal dismissed
    AppCatalog {
        id: apps
        // The dock launches through the same catalog; only launcher rows set pendingRecentApp.
        onLaunched: {
            if (body.pendingRecentApp)
                recent.record("app", body.pendingRecentApp);
            body.pendingRecentApp = "";
            body.dismissed();
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
        active: body.opened && (body.modeIndex === 4 || (body.recentMode && recent.entries.some(e => e.kind === "clip")))
        onCopied: body.dismissed()
    }
    // An empty All is Recent — what was last opened through the launcher, of every
    // kind, grouped by kind, newest first. Typing turns it back into the mixed search.
    RecentLog {
        id: recent
        active: body.opened && body.modeIndex === 0
    }
    readonly property bool recentMode: !page && query.text.trim().length === 0 && modeIndex === 0
    property string pendingRecentApp: ""
    function recentRows(): var {
        const groups = {
            app: "Apps",
            window: "Windows",
            file: "Files",
            page: "Settings",
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
                if (entry && !entry.noDisplay)
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
            } else if (e.kind === "page") {
                const p = settingsPages.find(p => p.id === e.ref);
                if (p)
                    rows.push({
                        kind: "page",
                        group: group,
                        t: e.t,
                        value: p.id,
                        label: p.label,
                        detail: p.summary,
                        summary: p.summary,
                        symbol: p.symbol
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
        return row ? row.kind + ":" + (row.kind === "window" ? niri.generation + ":" + row.id : (row.kind === "app" || row.kind === "frequent") ? row.entry.id : row.kind === "file" ? row.path : (row.kind === "clip" || row.kind === "undo") ? row.id : row.value || row.label) : "";
    }
    function selectAt(index: int): void {
        selectionKey = rowKey(results[index]);
    }
    // ---- Layout of the body: liquid-glass/launcher.js buildView(), panel coordinates ----
    readonly property string queryText: query.text.trim()
    // Apps is always the tile grid (the mockup): typing filters it; Frequent and the
    // categories belong to the untouched grid, Frequent only to the category All.
    readonly property bool gridMode: !page && modeIndex === 1
    readonly property bool pagesMode: !page && modeIndex === 5
    readonly property bool listMode: !gridMode && !pagesMode
    readonly property int frequentCount: gridMode && queryText.length === 0 && category === "All" ? apps.frequent.length : 0
    readonly property bool categoriesShown: gridMode && queryText.length === 0
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
    readonly property real pageCell: (width - 2 * side - 16) / 3
    // All (search and Recent) lists 40 px rows under kind headings; other lists 44 px.
    readonly property bool sectioned: listMode && !page && modeIndex === 0 && (recentMode || queryText.length > 0)
    readonly property int rowHeight: modeIndex === 0 && !page ? 40 : 44
    // A heading is launcher.js title(): 8 px after the previous group, an 18 px line, 4 px
    // to its rows. The first heading has no group above it, so the list starts 8 px higher.
    readonly property int sectionHeight: 30
    readonly property int sectionCount: sectioned ? results.reduce((n, r, i) => n + (i === 0 || r.group !== results[i - 1].group ? 1 : 0), 0) : 0
    // A page: "‹ Back" (28 + 6), its note (16 + 10), then the rows.
    readonly property int listTop: page ? bodyTop + 60 : bodyTop + noteHeight - (sectioned ? 8 : 0)
    readonly property bool clipActions: modeIndex === 4 && !page && clips.entries.length > 0
    readonly property real listContent: Math.max(0, results.length * (rowHeight + 2) - 2) + sectionCount * sectionHeight + (clipActions ? 50 : 0)
    readonly property real contentBottom: gridMode ? gridTop + Math.ceil((results.length - frequentCount) / 7) * 86 + 10 : pagesMode ? bodyTop + noteHeight + Math.ceil(results.length / 3) * 66 + 10 : listTop + listContent + (recording ? 84 : 0) + 12
    readonly property real desiredHeight: Math.min(Metrics.launcherHeader + Metrics.launcherBodyMax + Metrics.launcherFoot, Math.max(Metrics.launcherHeader + 24, contentBottom))
    function setMode(name: string): bool {
        const index = modes.indexOf(name);
        if (index < 0)
            return false;
        page = "";
        recording = false;
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
            page: page,
            settings: {
                state: settings.state,
                action: settings.lastAction,
                history_count: settings.history.length,
                busy: settings.busy
            },
            result_count: results.length,
            applied: applied,
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
        if (page)
            return settingsRows().filter(r => !text || (r.label + " " + r.detail).toLocaleLowerCase().includes(text));
        if (recentMode)
            return recentRows();
        if (modeIndex === 5 || (modeIndex === 0 && text)) {
            for (const p of settingsPages.filter(p => !text || (p.label + " " + p.summary).toLocaleLowerCase().includes(text)))
                rows.push({
                    kind: "page",
                    group: "Settings",
                    value: p.id,
                    label: p.label,
                    detail: p.summary,
                    summary: p.summary,
                    symbol: p.symbol
                });
        }
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
        if (pagesMode) {
            // Settings cards: three per row; stay in the column when the row ends short.
            const target = selected + direction * 3;
            if (target >= 0 && target < results.length)
                moveSelection(direction * 3);
            return;
        }
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
        } else if (pagesMode)
            pagesGrid.positionViewAtIndex(selected, GridView.Contain);
        else
            list.positionViewAtIndex(selected, ListView.Contain);
        wake();
    }
    // Left/right walk the grids; with a query they stay in the text (launcher.js keydown).
    readonly property bool horizontalMoves: (gridMode || pagesMode) && query.text.length === 0
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
        if (row.kind === "page") {
            recent.record("page", row.value);
            openPage(row.value);
        } else if (row.kind === "dock-on")
            dockChange("dock.on", String(!dock.on), () => dock.on = !dock.on);
        else if (row.kind === "dock-auto")
            dockChange("dock.auto_hide", String(!dock.autoHide), () => dock.autoHide = !dock.autoHide);
        else if (row.kind === "bar-auto" || row.kind === "bar-ovws")
            settings.set(row.value, row.action === "On" ? "false" : "true");
        else if (row.kind === "layout") {
            const codes = Array.from(settings.value("keyboard.layouts") || []).filter(c => c !== row.value);
            if (codes.length)
                settings.set("keyboard.layouts", codes.join(","));
        } else if (row.kind === "layout-add") {
            addLayoutOpen = !addLayoutOpen;
            query.text = "";
        } else if (row.kind === "layout-pick") {
            addLayoutOpen = false;
            query.text = "";
            settings.set("keyboard.layouts", Array.from(settings.value("keyboard.layouts") || []).concat([row.value]).join(","));
        } else if (row.kind === "switch-key") {
            if (row.action !== "In use")
                settings.set("keyboard.switch_key", row.value);
        } else if (row.kind === "wallpaper-pick") {
            if (row.action !== "In use")
                settings.set("appearance.wallpaper", row.value);
        } else if (row.kind === "default-role") {
            defaultsOpen = defaultsOpen === row.value ? "" : row.value;
            query.text = "";
        } else if (row.kind === "default-pick") {
            defaultsOpen = "";
            if (row.action !== "In use")
                settings.set(row.key, row.id);
        } else if (row.kind === "undo")
            settings.undo(row.id);
        else if (row.kind === "key-edit") {
            recording = true;
            recordedKey = "";
            recorder.forceActiveFocus();
        } else if (row.kind === "app" || row.kind === "frequent") {
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
        selectionKey = "";
        page = "";
        recording = false;
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
    readonly property bool inputFocused: query.activeFocus
    readonly property int queryLength: query.length
    property int modeIndex: 1 // Apps by default (23.09): All is the search + Recent view
    readonly property var modes: ["All", "Apps", "Windows", "Files", "Clipboard", "Settings"]

    function filesMessage(): string {
        const failures = {
            file_missing: "This file no longer exists.",
            no_handler: "No default application for this file type.",
            access_denied: "Permission denied opening this file.",
            timeout: "Open was not confirmed in time; the application may still open.",
            helper_dependency_missing: "File opening needs Python GObject / GIO."
        };
        if (!["idle", "pending", "requested"].includes(files.openState))
            return failures[files.openState] || "Could not open this file with its default application.";
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
    // The line under the header (launcher.js "note"): only when there is something to say.
    function noteText(): string {
        if (page)
            return "";
        if (modeIndex === 4) {
            if (clips.state !== "ready")
                return "Clipboard: " + clips.state;
            if (!["idle", "pending", "copied", "deleted"].includes(clips.actionState))
                return "Clipboard: " + clips.actionState;
            if (clips.recorder === "paused")
                return "Recording is paused — new copies are not saved.";
            if (clips.recorder === "failed")
                return "The clipboard recorder stopped unexpectedly · Resume restarts it";
            if (!clips.entries.length)
                return "Clipboard history is empty";
            return results.length ? "" : "Nothing matches “" + queryText + "”";
        }
        if (!["idle", "requested"].includes(web.state))
            return "Web: " + web.state;
        if (modeIndex === 3 || !["idle", "requested"].includes(files.openState))
            return filesMessage();
        if (!["idle", "requested"].includes(apps.launchState))
            return "Launch: " + apps.launchState;
        if (modeIndex === 2 && !niri.connected)
            return "niri unavailable · " + niri.reason;
        if (recentMode)
            return results.length ? "" : "Nothing opened from here yet · type to search everything";
        if (!results.length)
            return queryText ? "Nothing matches “" + queryText + "”" : modeIndex === 2 ? "No windows are open." : "Nothing found.";
        if (modeIndex === 2 && labels.state !== "ready")
            return "Window titles unavailable · " + labels.state;
        return "";
    }
    // The line under "‹ Back" on a Settings page: what the page holds, or what just happened.
    function pageNote(): string {
        if (applied)
            return "Applied · Undo in History";
        let text = settingsPages.find(p => p.id === page)?.summary ?? page;
        if (settings.lastAction !== pageAction && settings.lastAction !== "idle")
            text += " · " + settings.lastAction.replace(/_/g, " ");
        if (!settings.profile)
            text += " · preview, not applied to this session";
        return text;
    }

    function takeFocus(): void {
        query.forceActiveFocus(Qt.OtherFocusReason);
    }
    function reset(): void {
        page = "";
        recording = false;
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
    readonly property real shownWidth: Metrics.islandHeight + (width - Metrics.islandHeight) * morph
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
    property rect modeRect: Qt.rect(0, 0, 0, 0)
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
    onPageChanged: wake()
    function itemRect(item: Item): rect {
        const p = item.mapToItem(body, 0, 0);
        return Qt.rect(p.x, p.y, item.width, item.height);
    }
    // The selected item (launcher.js updateGeometry): its rect, the viewport that clips it,
    // how it is drawn and the bubble around it. A tile's bubble is one size on every tile,
    // whatever its name (Metrics.launcherTileBubble); rows and cards stand 6 px proud. Null
    // when not on screen.
    function selectedGeometry(): var {
        return geometryOf(selected);
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
        } else if (pagesMode) {
            shape = "page";
            view = pagesGrid;
            item = pagesGrid.itemAtIndex(i);
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
            radius = (shape === "page" ? 14 : 12) + Liquid.BUBBLE.pad;
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
        const mode = modesRepeater.itemAt(modeIndex);
        if (mode) {
            modeRect = itemRect(mode);
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
    // The drops in screen coordinates: mode, category, selection once the panel has grown;
    // before that the button's bubble, which the panel carries out of the bar.
    readonly property var drops: {
        tick;
        const list = [];
        if (morph > .6) {
            for (const b of [modeBubble, categoryBubble, selectBubble]) {
                const d = Liquid.drop(b, origin.x, origin.y);
                if (d)
                    list.push(d);
            }
        } else if (logoBubble) {
            const d = Liquid.drop(logoBubble, origin.x, origin.y);
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
    component PageDelegate: Item {
        id: card
        required property var modelData
        required property int index
        LauncherItem {
            anchors.fill: parent
            row: card.modelData
            shape: "page"
            ink: body.ink
            dim: body.dim
            opacity: 1 - body.onGlassAlpha(card.index)
        }
        MouseArea {
            id: cardHit
            anchors.fill: parent
            enabled: body.opened
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onPositionChanged: mouse => body.hoverAt(card.index, cardHit, mouse.x, mouse.y)
            onClicked: {
                body.selectAt(card.index);
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
    component ActionText: Item {
        id: action
        required property string text
        property color color: body.ink
        signal clicked
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
        width: body.origin.x + body.width + 16
        height: body.origin.y + Math.max(body.height, body.shownHeight) + 16
        // The bar's button arrow, going as the content comes (launcher.js paint()).
        Icon {
            x: body.origin.x + 9
            y: body.origin.y + 9
            width: 18
            height: 18
            kind: "logo"
            ink: ShellPalette.accent
            opacity: (1 - body.contentAlpha) * (1 - body.logoAlpha)
            visible: opacity > .001
        }
        Item {
            // Flat stand-in only: the content ends where the plate does.
            x: body.origin.x
            y: body.origin.y
            width: body.shownWidth
            height: body.shownHeight
            clip: !body.glassReady
            Item {
                id: content
                width: body.width
                height: body.height
                opacity: body.contentAlpha
                visible: opacity > .001
                // Header: the arrow (a click closes, 23.09), the modes, a hairline.
                Icon {
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
                    x: body.width - 20 - width
                    y: 16
                    height: 28
                    spacing: 2
                    Repeater {
                        id: modesRepeater
                        model: body.modes
                        Item {
                            id: modeItem
                            required property int index
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
                                color: body.modeIndex === modeItem.index ? body.ink : body.dim
                                opacity: body.modeIndex === modeItem.index ? 1 - body.modeAlpha : 1
                            }
                            MouseArea {
                                anchors.fill: parent
                                enabled: body.opened
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    body.modeIndex = modeItem.index;
                                    body.takeFocus();
                                }
                            }
                        }
                    }
                }
                Rectangle {
                    x: body.side
                    y: Metrics.launcherHeader - 1
                    width: body.width - 2 * body.side
                    height: 1
                    color: body.faint
                }
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

                // Settings: nine cards, three to a row.
                GridView {
                    id: pagesGrid
                    visible: body.pagesMode
                    x: body.side
                    y: body.bodyTop + body.noteHeight
                    width: 3 * cellWidth
                    height: Math.max(0, body.height - y - 10)
                    clip: true
                    cellWidth: body.pageCell + 8
                    cellHeight: 66
                    model: body.pagesMode ? body.results : []
                    onContentYChanged: body.wake()
                    onCountChanged: body.wake()
                    delegate: PageDelegate {
                        width: body.pageCell
                        height: 58
                    }
                }

                // A Settings page: back, what it holds, its rows.
                ActionText {
                    visible: body.page !== ""
                    x: body.side
                    y: body.bodyTop
                    height: 28
                    text: "‹ " + (body.settingsPages.find(p => p.id === body.page)?.label ?? body.page)
                    onClicked: body.closePage()
                }
                Text {
                    x: body.side
                    y: body.bodyTop + 34
                    width: body.width - 2 * body.side
                    height: 16
                    visible: body.page !== ""
                    verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                    textFormat: Text.PlainText
                    text: body.page ? body.pageNote() : ""
                    font.family: ShellPalette.uiFont
                    font.pixelSize: 11
                    font.weight: Font.Medium
                    color: body.dim
                    MouseArea {
                        anchors.fill: parent
                        enabled: body.applied
                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: body.openPage("history")
                    }
                }

                // Every other view: rows (All under kind headings).
                ListView {
                    id: list
                    visible: body.listMode
                    x: body.side
                    y: body.listTop
                    width: body.width - 2 * body.side
                    height: Math.max(0, body.height - y - 12 - (body.recording ? 84 : 0))
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
                    footer: Item {
                        width: list.width
                        height: body.clipActions ? 50 : 0
                        visible: body.clipActions
                        Row {
                            y: 8
                            spacing: 8
                            ActionText {
                                text: "Clear all"
                                color: body.danger
                                onClicked: body.clipboard.clear()
                            }
                            ActionText {
                                visible: body.clipboard.recorderOwned
                                text: body.clipboard.recorder === "recording" ? "Pause recording" : "Resume recording"
                                onClicked: body.clipboard.setRecording(body.clipboard.recorder !== "recording")
                            }
                        }
                    }
                }
                // Keybindings: the new shortcut is typed here.
                FocusScope {
                    id: recorder
                    visible: body.recording
                    z: 5
                    x: body.side
                    y: Math.min(body.listTop + body.listContent + 8, body.height - 84)
                    width: body.width - 2 * body.side
                    height: 72
                    Keys.onPressed: event => {
                        event.accepted = true;
                        body.captureKey(event);
                    }
                    Rectangle {
                        anchors.fill: parent
                        radius: 12
                        color: Qt.alpha(body.ink, .08)
                    }
                    Text {
                        x: 14
                        y: 10
                        height: 18
                        verticalAlignment: Text.AlignVCenter
                        text: body.recordedKey || "Press the new shortcut…"
                        textFormat: Text.PlainText
                        font.family: ShellPalette.uiFont
                        font.pixelSize: 14
                        font.weight: Font.Medium
                        color: body.ink
                    }
                    Row {
                        x: 2
                        y: 38
                        ActionText {
                            text: "Cancel"
                            color: body.dim
                            onClicked: {
                                body.recording = false;
                                body.takeFocus();
                            }
                        }
                        ActionText {
                            text: "Save"
                            enabled: body.recordedKey !== "" && !settings.busy
                            opacity: enabled ? 1 : .4
                            onClicked: body.saveRecorded()
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
        opacity: (1 - body.contentAlpha) * body.logoAlpha
        visible: opacity > .001
    }
    Item {
        id: onGlass
        width: body.width
        height: body.height
        opacity: body.contentAlpha
        visible: opacity > .001
        Text {
            x: body.modeRect.x
            y: body.modeRect.y
            width: body.modeRect.width
            height: body.modeRect.height
            visible: body.modeAlpha > .001
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
            sel: body.trail
            alpha: body.trailOnGlass
        }
        OnGlassItem {
            sel: body.selection
            alpha: body.selectOnGlass
        }
        TextInput {
            id: query
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
                    apps.launchState = "idle";
            }
            Keys.onDeletePressed: event => {
                if (body.modeIndex === 4) {
                    event.accepted = true;
                    body.deleteSelectedClip();
                } else
                    event.accepted = false;
            }
            Keys.onReturnPressed: event => {
                event.accepted = true;
                body.activate();
            }
            Keys.onEnterPressed: event => {
                event.accepted = true;
                body.activate();
            }
            Keys.onDownPressed: event => {
                event.accepted = true;
                body.moveVertical(1);
            }
            Keys.onUpPressed: event => {
                event.accepted = true;
                body.moveVertical(-1);
            }
            Keys.onLeftPressed: event => {
                if (body.horizontalMoves) {
                    event.accepted = true;
                    body.moveSelection(-1);
                } else
                    event.accepted = false;
            }
            Keys.onRightPressed: event => {
                if (body.horizontalMoves) {
                    event.accepted = true;
                    body.moveSelection(1);
                } else
                    event.accepted = false;
            }
            // Backspace in an empty field leaves a Settings page (launcher.js keydown).
            Keys.onPressed: event => {
                if (event.key === Qt.Key_Backspace && query.text.length === 0 && body.page) {
                    event.accepted = true;
                    body.closePage();
                }
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
                event.accepted = true;
                if (body.page)
                    body.closePage();
                else
                    body.dismissed();
            }
            Keys.onTabPressed: event => {
                event.accepted = true;
                body.modeIndex = (body.modeIndex + 1) % body.modes.length;
            }
            Keys.onBacktabPressed: event => {
                event.accepted = true;
                body.modeIndex = (body.modeIndex + body.modes.length - 1) % body.modes.length;
            }
        }
    }
}
