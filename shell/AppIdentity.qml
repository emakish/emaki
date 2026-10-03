pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// One answer to "which desktop entry is this window?" (app_id → DesktopEntry), for the dock
// and the launcher alike. Order: exact id / StartupWMClass (Quickshell), the learned map,
// then a guess that is only taken when exactly one entry fits. Learned matches come from a
// window that appeared right after a launch from Emaki, or from "Choose app…" in the dock.
Scope {
    id: identity
    required property AppCatalog catalog
    // app_id → desktop-id, $XDG_STATE_HOME/emaki/apps.json. Written only after a change.
    property var learned: ({})
    readonly property string statePath: (Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state") + "/emaki/apps.json"
    property bool restored: false
    // Launch learning: the id gtk-launch accepted and when; the first unknown window that
    // appears within learnWindowMs is taken to be that app.
    property string pendingId: ""
    property double pendingSince: 0
    readonly property int learnWindowMs: 20000
    readonly property int limit: 256
    onLearnedChanged: if (restored)
        saveTimer.restart()
    Component.onCompleted: restore()
    Connections {
        target: identity.catalog
        function onLaunched(): void {
            identity.pendingId = identity.catalog.pendingId;
            identity.pendingSince = Date.now();
        }
    }
    function validAppId(appId: var): bool {
        return typeof appId === "string" && appId.length > 0 && appId.length <= 256 && !appId.includes("\n");
    }
    function validId(id: var): bool {
        return typeof id === "string" && id.length > 0 && id.length <= 256 && !id.includes("/") && !id.includes("\n");
    }
    function restore(): void {
        try {
            const saved = JSON.parse(stateFile.text() || "{}");
            const next = {};
            let count = 0;
            if (saved.map && typeof saved.map === "object")
                for (const appId of Object.keys(saved.map))
                    if (count < limit && validAppId(appId) && validId(saved.map[appId])) {
                        next[appId] = saved.map[appId];
                        ++count;
                    }
            learned = next;
        } catch (error) {
            // Missing or damaged file: nothing learned, the next change replaces it.
        }
        restored = true;
    }
    // { entry, source } with source "exact" | "learned" | "guess" | "" (no match).
    function lookup(appId: string): var {
        if (!appId)
            return {
                entry: null,
                source: ""
            };
        const exact = DesktopEntries.byId(appId) ?? DesktopEntries.heuristicLookup(appId);
        if (exact)
            return {
                entry: exact,
                source: "exact"
            };
        const known = learned[appId] ? DesktopEntries.byId(learned[appId]) : null;
        if (known)
            return {
                entry: known,
                source: "learned"
            };
        const guessed = guess(appId);
        return {
            entry: guessed,
            source: guessed ? "guess" : ""
        };
    }
    function resolve(appId: string): DesktopEntry {
        return lookup(appId).entry;
    }
    // Each rule returns its candidates; the first rule with exactly one wins.
    function guess(appId: string): DesktopEntry {
        const lower = appId.toLocaleLowerCase();
        const entries = catalog.entries;
        const steam = /^steam_app_(\d+)$/.exec(appId);
        const rules = [
            // Steam games: the window is steam_app_<id>, the entry runs steam://rungameid/<id>.
                e => steam !== null && (e.execString.includes("steam://rungameid/" + steam[1]) || e.execString.includes("steam://run/" + steam[1])),
            // Any word of Exec whose file name is the app id: telegram-desktop, wine …\Game.exe.
                e => execWords(e.execString).includes(lower), e => e.startupClass.toLocaleLowerCase() === lower,
            // org.gnome.Nautilus ↔ nautilus
                e => e.id.split(".").pop().toLocaleLowerCase() === lower, e => e.icon.toLocaleLowerCase() === lower, e => e.name.toLocaleLowerCase() === lower];
        for (const rule of rules) {
            const found = entries.filter(rule);
            if (found.length === 1)
                return found[0];
        }
        return null;
    }
    function execWords(execString: string): var {
        const words = [];
        for (const raw of execString.split(/\s+/)) {
            const word = raw.replace(/^["']|["']$/g, "");
            if (!word || word.startsWith("%") || word.startsWith("-") || /^[A-Za-z_][A-Za-z0-9_]*=/.test(word))
                continue;
            words.push(word.split(/[\\/]/).pop().toLocaleLowerCase());
        }
        return words;
    }
    function learn(appId: string, desktopId: string): bool {
        if (!validAppId(appId) || !validId(desktopId) || !DesktopEntries.byId(desktopId))
            return false;
        if (learned[appId] === desktopId)
            return true;
        if (!(appId in learned) && Object.keys(learned).length >= limit)
            return false;
        const next = Object.assign({}, learned);
        next[appId] = desktopId;
        learned = next;
        return true;
    }
    function forget(appId: string): bool {
        if (!(appId in learned))
            return false;
        const next = Object.assign({}, learned);
        delete next[appId];
        learned = next;
        return true;
    }
    // A new window from the compositor (WindowLabels): the first unknown one after a launch
    // from Emaki is that app. A window of the launched app itself closes the question.
    function windowAppeared(appId: string): void {
        if (!pendingId || !validAppId(appId))
            return;
        if (Date.now() - pendingSince > learnWindowMs) {
            pendingId = "";
            return;
        }
        const found = lookup(appId);
        if (found.entry) {
            if (found.entry.id === pendingId)
                pendingId = "";
            return;
        }
        learn(appId, pendingId);
        pendingId = "";
    }
    FileView {
        id: stateFile
        path: identity.statePath
        blockLoading: true
        atomicWrites: true
        printErrors: false
    }
    Timer {
        id: saveTimer
        interval: 500
        onTriggered: stateFile.setText(JSON.stringify({
            version: 1,
            map: identity.learned
        }, null, 2) + "\n")
    }
}
