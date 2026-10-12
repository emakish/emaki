// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later

function identity(row) {
    return String(row.id || row.key).replace(/\.desktop$/, '').toLowerCase();
}
function consolidate(rows) {
    const editions = new Map();
    for (const row of rows) {
        const key = row.kind + ":" + row.key;
        const previous = editions.get(key);
        if (!previous) { editions.set(key, Object.assign({}, row)); continue; }
        // Installed metainfo and catalog metadata can describe the same edition.
        // Keep its action identity while collecting all available icon sources.
        const icons = (previous.icons || []).concat(row.icons || []);
        previous.icons = icons.filter((icon, index) => icons.findIndex(other => JSON.stringify(other) === JSON.stringify(icon)) === index);
        previous.icon = previous.icon || row.icon || "";
    }
    return Array.from(editions.values());
}
function catalogProgram(rows, packageOrId, launchEntries, kind) {
    const name = String(packageOrId || "");
    const wantedId = identity({id: name});
    const metadata = consolidate(rows).find(row => (!kind || row.kind === kind)
        && (row.package === name || identity(row) === wantedId));
    const fallback = {id: name, icons: []};
    const program = metadata || fallback;
    const ids = launchIds(program).concat([name.replace(/\.desktop$/, "")]);
    const entry = (launchEntries || []).find(row => ids.includes(String(row.id).replace(/\.desktop$/, "")));
    return Object.assign({}, program, {icon: program.icon || entry?.icon || ""});
}
function merge(rows) {
    const groups = {};
    for (const row of consolidate(rows)) {
        const id = identity(row);
        if (!groups[id]) groups[id] = [];
        if (!groups[id].some(other => other.key === row.key)) groups[id].push(row);
    }
    return Object.keys(groups).map(id => {
        const editions = groups[id].sort((a, b) => {
            const rank = {arch: 0, flatpak: 1, aur: 2};
            return rank[a.kind] - rank[b.kind] || String(a.key).localeCompare(String(b.key));
        });
        return Object.assign({}, editions[0], {editions: editions, installed: editions.some(row => row.installed)});
    }).sort((a, b) => String(a.name).localeCompare(String(b.name)));
}
function filter(rows, query, category) {
    const terms = String(query || '').toLowerCase().trim().split(/\s+/).filter(Boolean);
    return rows.filter(row => (!category || (row.categories || []).includes(category)) && terms.every(term =>
        [row.name, row.summary, row.description, row.package, row.id].join(' ').toLowerCase().includes(term)));
}
function categoryCounts(rows) {
    const counts = {};
    for (const row of rows) for (const category of new Set(row.categories || [])) counts[category] = (counts[category] || 0) + 1;
    return Object.keys(counts).sort().map(name => ({name: name, count: counts[name]}));
}
function planRows(plan) {
    const transaction = plan && plan.transaction || {};
    if (transaction.packages) return transaction.packages.map(row => typeof row === 'string'
        ? {name: row, detail: ''} : {name: row.package, detail: row.version || ''});
    return (transaction.operations || []).map(row => ({name: row.ref, detail: row.action || ''}));
}
function edition(program, kind) {
    if (!program) return null;
    const editions = program.editions || [program];
    if (!kind) return editions.find(row => row.installed && row.kind === "arch")
        || editions.find(row => row.installed) || editions.find(row => row.kind === "arch") || editions[0];
    return editions.find(row => row.key === kind) || editions.find(row => row.kind === kind) || editions[0];
}
function installedRows(rows, legacy) {
    // The removal inventory is authoritative for native launchable programs.
    // Catalog metadata alone also contains required and protected packages.
    rows = consolidate(rows);
    const installed = rows.filter(row => row.kind === 'flatpak' && row.installed);
    for (const row of legacy) {
        const metadata = rows.find(item => item.package === row.package && item.kind === 'arch')
            || rows.find(item => item.package === row.package && item.kind === 'aur');
        const foreign = row.foreign === true || row.repository === 'aur' || row.kind === 'aur'
            || metadata?.kind === 'aur';
        const base = {key: 'legacy:' + row.package, id: (row.ids || [])[0] || row.package,
            name: (row.names || [])[0] || row.package, icons: [], screenshots: [],
            categories: [], summary: '', description: '', licence: ''};
        const entry = Object.assign(base, metadata || {}, row, {
            kind: foreign ? 'aur' : 'arch', installed: true, actions: ['remove']});
        entry.sourceLabel = foreign ? 'AUR' : metadata ? 'Arch' : 'Installed';
        // AUR discovery never executes a package action; removal retains its
        // existing native preview and exact confirmed-package path.
        if (foreign) entry.key = 'legacy:' + row.package;
        installed.push(entry);
    }
    return installed.sort((a, b) => a.name.localeCompare(b.name));
}
function launchIds(row) {
    return Array.from(new Set((row.ids || []).concat([row.id || ''])
        .filter(Boolean).map(id => String(id).replace(/\.desktop$/, ''))));
}
