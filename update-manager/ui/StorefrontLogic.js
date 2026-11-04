// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
.pragma library

function canonical(value) {
    return typeof value === "string" ? value.trim().toLowerCase().replace(/\.desktop$/, "") : "";
}
function rowIds(row) {
    if (!row || typeof row !== "object") return [];
    const ids = [];
    const parts = [row].concat(Array.isArray(row.editions) ? row.editions : []);
    for (const part of parts) {
        if (!part || typeof part !== "object") continue;
        const values = [part.id, part.key, part.catalogKey, part.package]
            .concat(Array.isArray(part.ids) ? part.ids : [], Array.isArray(part.aliases) ? part.aliases : [],
                Array.isArray(part.providedIds) ? part.providedIds : []);
        for (const value of values) {
            const id = canonical(value);
            if (id && !ids.includes(id)) ids.push(id);
        }
    }
    return ids;
}
function stableKey(row) {
    return [canonical(row.id), row.kind || "", row.key].join("\n");
}
function build(catalog, installed, data, dayKey) {
    catalog = Array.isArray(catalog) ? catalog : [];
    installed = Array.isArray(installed) ? installed : [];
    data = data || {};
    // Daily candidates use catalog aliases only; installed aliases cannot change the pick.
    // These indexes have string identities only. Property lookup avoids scanning
    // large Map/Set tables in the window's JavaScript runtime.
    const parents = Object.create(null);
    function root(id) {
        if (parents[id] === undefined) parents[id] = id;
        let at = id;
        while (parents[at] !== at) at = parents[at];
        while (parents[id] !== id) {
            const next = parents[id]; parents[id] = at; id = next;
        }
        return at;
    }
    // Read identity and display eligibility once; every shelf shares these facts.
    const entries = catalog.concat(installed).map((row, position) => {
        const ids = rowIds(row);
        const isNamed = row && typeof row === "object" && typeof row.name === "string" && row.name.trim()
            && canonical(row.name) !== canonical(row.id)
            && typeof row.key === "string" && row.key.trim() && ids.length > 0;
        return {row: row, ids: ids, catalog: position < catalog.length, named: !!isNamed,
            usable: !!isNamed && row.available !== false && typeof row.summary === "string" && !!row.summary.trim(),
            key: isNamed ? stableKey(row) : ""};
    });
    function join(entry) {
        if (!entry.ids.length) return;
        for (const id of entry.ids) {
            const a = root(entry.ids[0]), b = root(id);
            if (a !== b) parents[b] = a;
        }
    }
    // Choose each group's first stable key directly instead of sorting the whole catalog.
    // Equal keys retain input order, as the previous stable sort did.
    function prefer(index, id, entry) {
        if (index[id] === undefined || entry.key < index[id].key) index[id] = entry;
    }
    for (let i = 0; i < catalog.length; ++i) join(entries[i]);
    const dailyIndex = Object.create(null);
    for (const entry of entries) {
        if (entry.catalog && entry.usable) prefer(dailyIndex, root(entry.ids[0]), entry);
    }
    function selected(ids, index) {
        const result = [], seen = Object.create(null);
        for (const value of Array.isArray(ids) ? ids : []) {
            const id = canonical(value);
            if (!id || parents[id] === undefined) continue;
            const group = root(id), row = index[group];
            if (row && !seen[group]) { result.push(row); seen[group] = true; }
        }
        return result;
    }
    function rows(ids, index) {
        return selected(ids, index).map(entry => entry.row);
    }
    const daily = selected(data.daily, dailyIndex);
    // Other shelves exclude installed programs, including installed-only aliases.
    for (let i = catalog.length; i < entries.length; ++i) join(entries[i]);
    const installedIds = Object.create(null);
    for (let i = 0; i < entries.length; ++i) {
        const entry = entries[i], row = entry.row;
        if (i >= catalog.length || (row && (row.installed === true
                || (Array.isArray(row.editions) && row.editions.some(part => part && part.installed === true))))) {
            for (const id of entry.ids) installedIds[root(id)] = true;
        }
    }
    const index = Object.create(null), sources = Object.create(null);
    for (const entry of entries) {
        if (!entry.named) continue;
        const id = root(entry.ids[0]);
        if (installedIds[id]) prefer(sources, id, entry);
        else if (entry.catalog && entry.usable) prefer(index, id, entry);
    }
    // The caller supplies a local day key; no clock or I/O occurs here.
    let hash = 0;
    const day = String(dayKey === undefined ? "" : dayKey);
    for (let i = 0; i < day.length; ++i) hash = (hash * 31 + day.charCodeAt(i)) >>> 0;
    let because = null;
    for (const rule of Array.isArray(data.related) ? data.related : []) {
        if (!rule) continue;
        const id = canonical(rule.trigger);
        const source = parents[id] !== undefined ? sources[root(id)] : null;
        const matches = rows(rule.programs, index);
        if (source && matches.length) { because = {source: source.row, rows: matches}; break; }
    }
    const collections = [];
    for (const collection of Array.isArray(data.collections) ? data.collections : []) {
        if (!collection) continue;
        const matches = rows(collection.programs, index);
        if (matches.length) collections.push({id: collection.id, title: collection.title,
            description: collection.description, rows: matches});
    }
    const pickedEntry = daily.length ? daily[hash % daily.length] : null;
    let picked = pickedEntry ? pickedEntry.row : null;
    if (pickedEntry && installedIds[root(pickedEntry.ids[0])])
        picked = Object.assign({}, picked, {installed: true});
    return {daily: picked, collections: collections, because: because};
}
