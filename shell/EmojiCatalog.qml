// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
pragma ComponentBehavior: Bound
import QtQuick
import Quickshell
import Quickshell.Io

// The launcher's Emoji mode: names in the session language and in English, copy on choose,
// recently chosen first. Names come from shell/emoji/<language>.json (scripts/build-emoji-data,
// Unicode CLDR); nothing is read before load(), and the files are read off the UI thread.
// Recent emoji live in $XDG_STATE_HOME/emaki/emoji-recent.json, at most 24, newest first.
// Bindings that show search() or recentRows() also read `revision`, so they update once the
// names or the recent list arrive.
Scope {
    id: catalog
    // A Qt UI language such as "es_MX", "pt_BR" or "en_US"; the part before "_" picks the file.
    property string language: Qt.uiLanguage
    readonly property string dataLanguage: {
        const code = String(language || "").split(/[_.@-]/)[0].toLowerCase();
        return available.includes(code) ? code : "en";
    }
    readonly property var available: ["en", "es", "pt", "de", "fr", "ru"]
    property string state: "idle" // idle, loading, ready, error
    readonly property string message: state === "error" ? qsTranslate("EmojiCatalog", "Emoji names could not be loaded.") : ""
    property int revision: 0
    property int limit: 60
    property var recent: []
    readonly property string statePath: (Quickshell.env("XDG_STATE_HOME") || Quickshell.env("HOME") + "/.local/state") + "/emaki/emoji-recent.json"
    // A recent file written by a newer shell is neither read nor overwritten during this run.
    property bool foreign: false
    property var entries: []
    property var byEmoji: ({})
    property string loadedLanguage: ""
    property var pending: null
    signal copied(string emoji)

    function load(): void {
        if (state === "idle" || state === "error" || (state === "ready" && loadedLanguage !== dataLanguage)) {
            state = "loading";
            loadedLanguage = dataLanguage;
            open(englishFile, Quickshell.shellPath("emoji/en.json"));
            if (dataLanguage === "en")
                localFile.path = "";
            else
                open(localFile, Quickshell.shellPath("emoji/" + dataLanguage + ".json"));
            open(recentFile, statePath);
        }
    }
    function fold(text: string): string {
        // Lower case, accents dropped ("corazon" finds "corazón"), punctuation as spaces.
        return String(text).toLowerCase().normalize("NFD").replace(accents, "").replace(separators, " ").trim();
    }
    // Combining marks U+0300-U+036F; spaces and punctuation, with inverted marks, typographic
    // quotes and dashes U+2010-U+2015.
    readonly property var accents: new RegExp("[" + String.fromCharCode(0x300) + "-" + String.fromCharCode(0x36f) + "]", "g")
    readonly property var separators: new RegExp("[\\s:,.;!?'\"()\\[\\]/&*#_\\-" + String.fromCharCode(0xa1, 0xbf, 0x2019, 0xab, 0xbb, 0x2010) + "-" + String.fromCharCode(0x2015) + "]+", "g")
    function parse(file: var): var {
        try {
            const value = JSON.parse(file.text());
            return value && value.format === 1 && Array.isArray(value.emoji) ? value.emoji : null;
        } catch (_) {
            return null;
        }
    }
    function build(): void {
        if (state !== "loading" || pending || !englishFile.done || (localFile.path && !localFile.done) || !recentFile.done)
            return;
        const english = englishFile.failed ? null : parse(englishFile);
        if (!english) {
            state = "error";
            return;
        }
        const local = {};
        for (const row of (localFile.path && !localFile.failed ? parse(localFile) : null) || [])
            local[row[0]] = row;
        pending = {
            english: english,
            local: local,
            list: [],
            map: {}
        };
        chunk.start();
    }
    // The search index is built a few hundred emoji per event-loop turn, so opening the mode
    // never holds a frame for the whole catalogue.
    function buildChunk(): void {
        const work = pending;
        if (!work)
            return;
        const end = Math.min(work.english.length, work.list.length + 300);
        for (let index = work.list.length; index < end; index++) {
            const row = work.english[index];
            const own = work.local[row[0]];
            const name = own ? own[1] : row[1];
            const keywords = (own ? own[2] : row[2]).split("|").filter(word => word);
            const fields = [field(name, 0)].concat(keywords.map(word => field(word, 1)));
            if (own)
                fields.push(field(row[1], 2), ...row[2].split("|").filter(word => word).map(word => field(word, 3)));
            const entry = {
                emoji: row[0],
                name: name,
                keywords: keywords,
                detail: keywords.filter(word => name.toLowerCase().indexOf(word.toLowerCase()) < 0).slice(0, 4).join(", "),
                order: index,
                fields: fields,
                all: " " + fields.map(item => item.text).join(" ")
            };
            work.list.push(entry);
            work.map[entry.emoji] = entry;
        }
        if (work.list.length < work.english.length) {
            chunk.start();
            return;
        }
        pending = null;
        entries = work.list;
        byEmoji = work.map;
        recent = recent.filter(emoji => work.map[emoji] !== undefined);
        state = "ready";
        revision++;
        if (loadedLanguage !== dataLanguage)
            load();
    }
    function field(text: string, rank: int): var {
        const folded = fold(text);
        return {
            text: folded,
            padded: " " + folded + " ",
            rank: rank
        };
    }
    function readRecent(): void {
        foreign = false;
        let value = null;
        // 24 entries fit in a few hundred bytes: a larger file is damaged and is not parsed.
        const text = recentFile.text();
        try {
            value = text.length <= 16384 ? JSON.parse(text) : null;
        } catch (_) {}
        if (value && typeof value.version === "number" && value.version > 1) {
            foreign = true;
            return;
        }
        const list = value && value.version === 1 && Array.isArray(value.recent) ? value.recent : [];
        const kept = new Set();
        for (const emoji of list) {
            if (kept.size >= 24)
                break;
            if (typeof emoji === "string" && emoji.length > 0 && emoji.length <= 32)
                kept.add(emoji);
        }
        recent = Array.from(kept);
        // An older file may be readable by others: the private writer makes it 0600 again.
        persistence.submit(null);
    }
    function row(entry: var): var {
        return {
            emoji: entry.emoji,
            name: entry.name,
            keywords: entry.keywords,
            detail: entry.detail
        };
    }
    // Field score for one search word, lower is better: the whole field, a whole word, the
    // field's start, a word's start, then (three letters or more) anywhere. Within each, the
    // shown name ranks before its keywords, then the English name and keywords.
    function score(field: var, word: string): int {
        const text = field.text;
        let base = -1;
        if (text === word)
            base = 0;
        else if (field.padded.indexOf(" " + word + " ") >= 0)
            base = 1;
        else if (text.startsWith(word))
            base = 2;
        else if (text.indexOf(" " + word) >= 0)
            base = 3;
        else if (word.length >= 3 && text.indexOf(word) >= 0)
            base = 6;
        return base < 0 ? -1 : base * 4 + field.rank;
    }
    function search(query: string): var {
        const folded = fold(query);
        if (!folded)
            return recentRows();
        load();
        if (state !== "ready")
            return [];
        const words = folded.split(" ");
        // Cheap test on all of an entry's text first; most entries fail it.
        const needles = words.map(word => word.length >= 3 ? word : " " + word);
        const found = [];
        for (const entry of entries) {
            if (needles.some(needle => entry.all.indexOf(needle) < 0))
                continue;
            let total = 0;
            for (const word of words) {
                let best = -1;
                for (const field of entry.fields) {
                    const value = score(field, word);
                    if (value >= 0 && (best < 0 || value < best))
                        best = value;
                }
                if (best < 0) {
                    total = -1;
                    break;
                }
                total += best;
            }
            if (total < 0)
                continue;
            // The whole query at the start of the shown name wins over scattered words.
            if (words.length > 1 && entry.fields[0].text.startsWith(folded))
                total = 0;
            found.push({ entry: entry, total: total });
        }
        // Equal scores: the shorter name is the closer match ("red heart" before "smiling face
        // with heart-eyes"), then the Unicode order.
        found.sort((a, b) => a.total - b.total || a.entry.name.length - b.entry.name.length || a.entry.order - b.entry.order);
        return found.slice(0, limit).map(item => row(item.entry));
    }
    function recentRows(): var {
        load();
        if (state !== "ready")
            return [];
        return recent.filter(emoji => byEmoji[emoji] !== undefined).map(emoji => row(byEmoji[emoji]));
    }
    // Copies the emoji and records it as recent. False for text that is not one catalogued emoji.
    function choose(emoji: string): bool {
        if (state !== "ready" || byEmoji[emoji] === undefined)
            return false;
        Quickshell.clipboardText = emoji;
        recent = [emoji].concat(recent.filter(item => item !== emoji)).slice(0, 24);
        revision++;
        if (!foreign)
            persistence.submit(JSON.stringify({
                version: 1,
                recent: recent
            }));
        copied(emoji);
        return true;
    }
    onDataLanguageChanged: if (state === "ready")
        load()

    function open(file: var, path: string): void {
        file.done = false;
        file.failed = false;
        if (file.path === path)
            file.reload();
        else
            file.path = path;
    }

    component DataFile: FileView {
        property bool done: false
        property bool failed: false
        blockLoading: false
        printErrors: false
        onLoaded: {
            done = true;
            catalog.build();
        }
        onLoadFailed: {
            done = true;
            failed = true;
            catalog.build();
        }
    }
    Timer {
        id: chunk
        interval: 0
        onTriggered: catalog.buildChunk()
    }
    DataFile {
        id: englishFile
    }
    DataFile {
        id: localFile
    }
    FileView {
        id: recentFile
        property bool done: false
        property bool failed: false
        blockLoading: false
        printErrors: false
        onLoaded: {
            catalog.readRecent();
            done = true;
            catalog.build();
        }
        onLoadFailed: {
            done = true;
            catalog.build();
        }
    }
    // FileView creates files by umask; the recent list goes through the private writer
    // (helpers/private-state.py: emaki/ 0700, the file 0600), one save at a time, the last wins.
    Process {
        id: persistence
        property bool pending: false
        property var nextText: null
        property var activeText: null
        command: [Quickshell.env("EMAKI_PYTHON") || "python3", decodeURIComponent(Qt.resolvedUrl("helpers/private-state.py").toString().replace(/^file:\/\//, ""))]
        stdinEnabled: true
        function submit(text: var): void {
            if (pending && text === null && nextText !== null)
                return;
            nextText = text;
            pending = true;
            flush();
        }
        function flush(): void {
            if (running || !pending)
                return;
            activeText = nextText;
            pending = false;
            running = true;
        }
        onStarted: write(JSON.stringify({
            file: "emoji-recent.json",
            text: activeText
        }) + "\n")
        onExited: Qt.callLater(flush)
    }
}
