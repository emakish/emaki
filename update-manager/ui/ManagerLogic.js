// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later

function identity(row) {
    if (String(row.package || '').endsWith('-git')) return 'package:' + row.package;
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
function catalogIndex(rows) {
    // The catalog consolidated once, with each edition's identity read once, for the update rows'
    // lookups: consolidating a real catalog of 5,642 programs for every lookup took about 450 ms in
    // a VM, twice for each pending update and again whenever the update list was rebuilt.
    const consolidated = consolidate(rows || []);
    return {rows: consolidated, ids: consolidated.map(identity)};
}
function catalogProgram(catalog, packageOrId, launchEntries, kind) {
    // The catalog's rows, or catalogIndex(rows) when the same catalog is asked many times.
    const index = Array.isArray(catalog) ? catalogIndex(catalog) : catalog;
    const name = String(packageOrId || "");
    const wantedId = identity({id: name});
    let metadata = null;
    for (let at = 0; at < index.rows.length && !metadata; ++at) {
        const row = index.rows[at];
        if ((!kind || row.kind === kind) && (row.package === name || index.ids[at] === wantedId)) metadata = row;
    }
    const fallback = {id: name, icons: []};
    const program = metadata || fallback;
    const ids = launchIds(program).concat([name.replace(/\.desktop$/, "")]);
    const entry = (launchEntries || []).find(row => ids.includes(String(row.id).replace(/\.desktop$/, "")));
    return Object.assign({}, program, {icon: program.icon || entry?.icon || ""});
}
function merge(rows) {
    const groups = {};
    rows = consolidate(rows);
    // An installed AUR entry carries its catalog edition's facts and Undo: one AUR edition, not two.
    const absorbed = new Set(rows.map(row => row.catalogKey).filter(Boolean));
    for (const row of rows.filter(row => !absorbed.has(row.key))) {
        const id = identity(row);
        if (!groups[id]) groups[id] = [];
        if (!groups[id].some(other => other.key === row.key)) groups[id].push(row);
    }
    return Object.keys(groups).map(id => {
        const editions = groups[id].sort((a, b) => {
            const rank = {arch: 0, flatpak: 1, aur: 2};
            return rank[a.kind] - rank[b.kind] || String(a.key).localeCompare(String(b.key));
        });
        // A card shows the best-counted edition's stars, as the program page does; counts are never added.
        const rated = editions.filter(row => row.rating && row.rating.count > 0);
        const rating = rated.length ? rated.reduce((a, b) => b.rating.count > a.rating.count ? b : a).rating : editions[0].rating;
        return Object.assign({}, editions[0], {editions: editions, installed: editions.some(row => row.installed), rating: rating});
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
    // An AUR plan lists its recipes and the Arch packages they pull separately.
    if (transaction.kind === 'aur') return [];
    if (transaction.packages) return transaction.packages.map(row => typeof row === 'string'
        ? {name: row, detail: ''} : {name: row.package, detail: row.version || ''});
    return (transaction.operations || []).map(row => ({name: row.ref, detail: row.action || ''}));
}
function edition(program, kind) {
    if (!program) return null;
    const editions = program.editions || [program];
    if (!kind) return editions.find(row => row.installed && row.kind === "arch")
        || editions.find(row => row.installed) || editions.find(row => row.kind === "arch") || editions[0];
    // A key also finds the installed entry that stands for that catalog edition.
    return editions.find(row => row.key === kind || row.catalogKey === kind) || editions.find(row => row.kind === kind)
        || editions[0];
}
function installedRows(rows, legacy) {
    // The removal inventory is authoritative for native launchable programs.
    // Catalog metadata alone also contains required and protected packages.
    rows = consolidate(rows);
    const installed = rows.filter(row => row.kind === 'flatpak' && row.installed);
    for (const row of legacy) {
        const metadata = rows.find(item => item.package === row.package && item.kind === 'arch')
            || rows.find(item => item.package === row.package && item.kind === 'aur');
        const foreign = metadata?.kind === 'aur';
        const base = {key: 'legacy:' + row.package, id: (row.ids || [])[0] || row.package,
            name: (row.names || [])[0] || row.package, icons: [], screenshots: [],
            categories: [], summary: '', description: '', licence: ''};
        // A recorded install can be taken back from Installed too, through its catalog edition.
        const undoKey = metadata && (metadata.actions || []).includes('undo') ? metadata.key : '';
        // Facts the catalog row lacks stay absent: the page shows them as unknown.
        // A newer AUR version is built through the catalog edition too (catalogKey below).
        const update = foreign && metadata?.kind === 'aur' && (metadata.actions || []).includes('install');
        const entry = Object.assign(base, metadata || {}, row, {
            kind: foreign ? 'aur' : 'arch', installed: true, undoKey: undoKey,
            actions: row.protected === true ? []
                : (row.removable === false ? [] : ['remove']).concat(update ? ['install'] : [], undoKey ? ['undo'] : [])});
        // The inventory's version is the installed one; the AUR row's is what an update would build.
        // An AUR row is named by its package: the desktop entry's name is the program's.
        if (foreign && metadata?.kind === 'aur')
            Object.assign(entry, {version: metadata.version || row.version, installedVersion: row.version,
                                  name: (row.names || [])[0] || metadata.name});
        entry.sourceLabel = foreign ? 'AUR' : metadata?.kind === 'arch' ? 'Arch'
            : row.foreign === true ? 'Installed outside the store'
            : metadata ? 'Arch' : 'Installed';
        // AUR discovery never executes a package action; removal retains its
        // existing native preview and exact confirmed-package path.
        if (foreign) entry.key = 'legacy:' + row.package;
        // It stands for its AUR catalog edition on the program page: merge() keeps one of the two.
        entry.catalogKey = foreign && metadata?.kind === 'aur' ? metadata.key : '';
        installed.push(entry);
    }
    return installed.sort((a, b) => a.name.localeCompare(b.name));
}
function removalStatus(row) {
    return row.protected ? 'Part of Emaki'
        : (row.requiredBy || []).length ? 'Needed by other software' : '';
}
function aurBuilds(plan) {
    const transaction = plan && plan.transaction || {};
    if (transaction.kind !== 'aur') return [];
    return (transaction.builds || []).map(build => ({
        name: build.packageBase, version: build.version || '',
        packages: (build.packages || []).map(row => row.package).join(', '),
        commit: String(build.commit || '').slice(0, 10),
        maintainer: build.orphan ? 'nobody maintains it' : build.maintainer ? 'by ' + build.maintainer : '',
        outOfDate: build.outOfDate ? 'marked out of date' : '',
        recipe: /^https:\/\/aur\.archlinux\.org\//.test(build.recipe || '') ? build.recipe : '',
        target: (build.packages || []).some(row => row.package === transaction.target)}));
}
function aurPulls(plan, purpose) {
    const transaction = plan && plan.transaction || {};
    if (transaction.kind !== 'aur') return [];
    return (transaction.packages || []).filter(row => row.purpose === purpose)
        .map(row => ({name: row.package, detail: row.version || '', repository: row.repository || ''}));
}
function undoRows(plan) {
    const transaction = plan && plan.transaction || {};
    const reasons = {system: 'part of the system', emaki: 'part of Emaki', explicit: 'you installed it yourself',
                     'in-use': 'other programs use it'};
    return Object.keys(transaction.keep || {}).sort().map(name => ({name: name, detail: reasons[transaction.keep[name]] || 'stays'}));
}
function undoRefusal(event) {
    // Why an undo was refused, in the words of its preview.
    if (!event || event.code !== 'cannot-undo') return '';
    const blockers = event.requiredBy || [];
    if (blockers.length) return ' ' + blockers.join(', ') + (blockers.length === 1 ? ' needs' : ' need') + ' what it added.';
    const reasons = {system: 'It is now part of the system.', emaki: 'It is now part of Emaki.',
                     explicit: 'You installed it yourself since.', 'not-installed': 'It is no longer installed.'};
    return reasons[event.reason] ? ' ' + reasons[event.reason] : '';
}
function day(value) {
    const parsed = new Date(value);
    return isNaN(parsed.getTime()) ? '' : Qt.formatDateTime(parsed, 'd MMM yyyy, HH:mm');
}
function launchIds(row) {
    return Array.from(new Set((row.ids || []).concat([row.id || ''])
        .filter(Boolean).map(id => String(id).replace(/\.desktop$/, ''))));
}
function progressStart() {
    return {step: 0, steps: 4, label: 'Getting ready', done: 0, total: 0, planned: 0,
            restorePoint: false, preSnapshots: false};
}
function progressLine(state, raw) {
    // Folds one line of the update's own log (pacman and snap-pac lines relayed from the
    // journal) into four plain steps; the raw lines stay for Details. The last step marker
    // wins, so a second pacman run (the keyring before the full update) starts the steps again.
    // Returns the same object when the line changes nothing, a new one otherwise.
    const line = String(raw || '').trim();
    if (!line) return state;
    const steps = ['Getting ready', 'Downloading', 'Making a restore point', 'Installing', 'Finishing'];
    const next = Object.assign({}, state);
    const go = step => Object.assign(next, {step: step, label: steps[step], done: 0, total: 0});
    // A terminal shows "(2/5) upgrading foo". The update service pipes pacman, which then has
    // no progress bar: it prints "upgrading foo..." and announces the count once, in the
    // transaction summary "Packages (5) ..." ("Package (5)  Old Version ..." with VerbosePkgLists).
    const count = /^\(\s*(\d+)\/(\d+)\)\s+(upgrading|installing|reinstalling|downgrading|removing)\s/.exec(line);
    const plain = /^(upgrading|installing|reinstalling|downgrading|removing) \S+\.\.\.$/.test(line);
    const planned = /^Packages? \((\d+)\)/.exec(line);
    // Hook lines carry their own counter: "(1/3) Performing snapper pre snapshots…".
    const text = line.replace(/^\(\s*\d+\/\d+\)\s+/, '');
    if (count) {
        if (next.step !== 3) go(3);
        Object.assign(next, {done: Number(count[1]), total: Number(count[2])});
    } else if (plain) {
        if (next.step !== 3) go(3);
        next.done += 1;
        // Without an announced count the total stays unknown (0), never guessed from done.
        next.total = next.planned ? Math.max(next.planned, next.done) : 0;
    } else if (planned) {
        // A new transaction summary: its count belongs to this pacman run only.
        Object.assign(next, {planned: Number(planned[1]), done: 0, total: 0});
    } else if (/^:: Retrieving packages/.test(text)) {
        go(1);
    } else if (/^Performing \w+ pre snapshots/.test(text)) {
        next.preSnapshots = true;
        go(2);
    } else if (next.preSnapshots && /^==> \S+: \d+$/.test(text)) {
        Object.assign(next, {restorePoint: true, preSnapshots: false});
    } else if (/^:: Processing package changes/.test(text)) {
        next.preSnapshots = false;
        go(3);
    } else if (/^:: Running post-transaction hooks/.test(text) || /^Updating Flatpak applications/.test(text)) {
        next.preSnapshots = false;
        go(4);
    } else {
        return state;
    }
    return next;
}
function updateProgress(output) {
    return String(output || '').split('\n').reduce(progressLine, progressStart());
}
function measured(row) { return typeof row.popularity === 'number' && isFinite(row.popularity); }
function hasPopularity(rows) { return (rows || []).some(measured); }
function popular(rows) {
    // The engine's popularity when it sends one; otherwise the catalog's own order.
    rows = (rows || []).slice();
    if (!hasPopularity(rows)) return rows;
    return rows.filter(measured).sort((a, b) => b.popularity - a.popularity || String(a.name).localeCompare(String(b.name)));
}
function fresh(rows) {
    // Newest release first; only rows whose release date the engine knows. Each date is read once,
    // and the engine's YYYY-MM-DD without new Date(text): that parse costs about 0.3 ms here, and
    // repeated inside the comparison it took over a second for a real catalog of 1,400 programs.
    const time = value => {
        const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
        return iso ? Date.UTC(Number(iso[1]), Number(iso[2]) - 1, Number(iso[3])) : value ? new Date(value).getTime() : NaN;
    };
    return (rows || []).map(row => ({row: row, time: time(String(row.releaseDate || ''))}))
        .filter(item => !isNaN(item.time)).sort((a, b) => b.time - a.time).map(item => item.row);
}
function byVotes(rows) {
    // A neutral order: the AUR's own vote count, nothing promoted.
    const votes = row => typeof row.votes === 'number' ? row.votes : -1;
    return (rows || []).slice().sort((a, b) => votes(b) - votes(a) || String(a.name).localeCompare(String(b.name)));
}
function essentials(rows, ids) {
    // The full install's programs, in the order given, matched by package or program ID.
    const wanted = (ids || []).map(id => String(id).toLowerCase());
    const rank = row => {
        const keys = (row.editions || [row]).map(item => String(item.package || '').toLowerCase()).concat([identity(row)]);
        const found = keys.map(key => wanted.indexOf(key)).filter(index => index >= 0);
        return found.length ? Math.min.apply(null, found) : -1;
    };
    return (rows || []).filter(row => rank(row) >= 0).sort((a, b) => rank(a) - rank(b));
}
function partOfEmaki(row) { return !!row && (row.protected === true || row.self === true); }
function installedWithSelf(rows, query, self) {
    // The store itself is part of Emaki: listed, never removable from its own window.
    rows = rows || [];
    const present = rows.some(row => identity(row) === identity(self) || row.package === self.package);
    return filter(present ? rows : rows.concat([self]), query, '').sort((a, b) => String(a.name).localeCompare(String(b.name)));
}
