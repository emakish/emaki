// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const context = vm.createContext({qsTr: value => value});
for (const filename of ['StorefrontData.js', 'StorefrontLogic.js']) {
    const source = fs.readFileSync(new URL(`../update-manager/ui/${filename}`, import.meta.url), 'utf8');
    vm.runInContext(source.replace(/^\.pragma library\s*$/m, ''), context);
}
// The runtime receives data only; host I/O, clocks and network APIs are absent.
vm.runInContext('Date = undefined; fetch = undefined; XMLHttpRequest = undefined;', context);
const plain = value => JSON.parse(JSON.stringify(value));
const build = (catalog, installed, data, day = '2026-11-01') => plain(context.build(catalog, installed, data, day));
const row = (id, extras = {}) => ({id, key: `flatpak:${id}`, kind: 'flatpak', name: `Program ${id.split('.').pop()}`, summary: 'Catalog summary', ...extras});
const a = row('org.example.A');
const b = row('org.example.B', {screenshots: [{url: 'file:preview.png'}]});
const c = row('org.example.C');
const collection = programs => ({id: 'sample', title: 'Sample', description: 'Catalog programs', programs});
const data = {
    daily: [a.id, b.id, c.id],
    collections: [collection([a.id, b.id, c.id, 'missing'])],
    related: [{trigger: a.id, programs: [b.id, c.id]}]
};
const catalog = [a, b, c];
const before = JSON.stringify({catalog, data});
const empty = build([], [], data);
assert.deepEqual(empty, {daily: null, collections: [], because: null});
assert.deepEqual(build(null, null, null), empty);
const first = build(catalog, [], data);
assert.equal(first.collections[0].rows.length, 3);
assert.equal(first.because, null);
assert.deepEqual(build(catalog.slice().reverse(), [], data), first);
assert.deepEqual(build(catalog, [], data), first);
assert.notEqual(build(catalog, [], data, '2026-11-02').daily.id, first.daily.id);
assert.equal(JSON.stringify({catalog, data}), before);
assert.deepEqual(build([a], [], data).daily, a); // Artwork is optional.
const typical = build(catalog, [a], data);
assert.deepEqual(typical.collections[0].rows, [b, c]);
assert.deepEqual(typical.because, {source: a, rows: [b, c]});
assert.deepEqual(build(catalog, catalog, data), {...empty, daily: {...first.daily, installed: true}});
assert.deepEqual(build(catalog, [row('unknown')], data), first);
assert.deepEqual(build([null, {}, {id: a.id}, row('bad', {available: false}), a], [], data).collections[0].rows, [a]);
assert.deepEqual(build(catalog, [], {daily: ['missing'], collections: [null], related: [null]}), empty);

// Catalog placeholders and absent descriptions do not become recommendations.
for (const placeholder of [row(a.id, {name: a.id}), row(a.id, {summary: ''}), row(a.id, {summary: undefined})]) {
    assert.deepEqual(build([placeholder], [], data), empty);
}
const installedNameOnly = row(a.id, {summary: undefined});
assert.deepEqual(build([b, c], [installedNameOnly], data).because, plain({source: installedNameOnly, rows: [b, c]}));
const provided = row('org.example.Renamed', {providedIds: [a.id]});
assert.deepEqual(build([provided, b], [], data).collections[0].rows, [provided, b]);
assert.deepEqual(build([provided, b], [a], data).collections[0].rows, [b]);

const alias = row('org.example.A.desktop', {ids: ['ORG.EXAMPLE.ALT.desktop'], aliases: ['old-a'], package: 'example-a'});
const native = row('org.example.Alt', {key: 'arch:example-a', kind: 'arch', package: 'example-a'});
const grouped = row('org.example.wrapper', {editions: [alias, native]});
const aliasData = {...data, daily: [a.id], collections: [collection([a.id, 'old-a', 'ORG.EXAMPLE.ALT.desktop', b.id])]};
const aliases = [alias, native, grouped, b];
assert.equal(build(aliases, [], aliasData).collections[0].rows.length, 2);
assert.deepEqual(build(aliases, [], aliasData), build(aliases.slice().reverse(), [], aliasData));
assert.deepEqual(build(aliases, [{package: 'example-a'}], aliasData).collections[0].rows, [b]);
assert.deepEqual(build([grouped, b], [native], aliasData).collections[0].rows, [b]);
assert.deepEqual(build([row(a.id, {editions: [row(a.id, {installed: true})]}), b], [], aliasData).collections[0].rows, [b]);
const duplicate = row(a.id, {key: 'arch:example-a', kind: 'arch', summary: 'Another summary'});
assert.deepEqual(build([a, duplicate, b], [], data), build([b, duplicate, a], [], data));
assert.equal(build([a, duplicate, b], [], data).collections[0].rows.length, 2);
assert.deepEqual(build(catalog, [a, c], data), build(catalog.slice().reverse(), [c, a], data));

// Identity indexes must treat property names as ordinary program identities.
const propertyRows = ['__proto__', 'constructor', 'toString', 'hasOwnProperty'].map(id => row(id));
const propertyData = {daily: ['__proto__'], collections: [collection(propertyRows.map(item => item.id).concat('missing'))]};
assert.deepEqual(build(propertyRows, [], propertyData).collections[0].rows, propertyRows);
assert.deepEqual(build(propertyRows, [], propertyData).daily, propertyRows[0]);
assert.deepEqual(build(propertyRows, [{aliases: ['__proto__']}], propertyData).collections[0].rows, propertyRows.slice(1));

// Equal preference keys keep the first input row, including installed source descriptions.
const sameKey = {...a, summary: 'Another description'};
assert.deepEqual(build([a, sameKey], [], {daily: [a.id], collections: [collection([a.id])]}).collections[0].rows, [a]);
assert.deepEqual(build([sameKey, a], [], {daily: [a.id]}).daily, sameKey);
assert.deepEqual(build([b], [sameKey, a], data).because, {source: sameKey, rows: [b]});

// Installed-only aliases may bridge catalog entries, but never change the daily pick.
const bridge = row('org.example.Bridge', {aliases: [a.id, b.id]});
for (let date = 1; date <= 31; ++date) {
    const day = `2026-11-${String(date).padStart(2, '0')}`;
    const expected = build(catalog, [], data, day).daily;
    for (const inventory of [[a], [b], [c], catalog, [bridge], [bridge, c]]) {
        const result = build(catalog, inventory, data, day).daily;
        assert.equal(result.id, expected.id);
        const installed = inventory.some(entry => entry.id === expected.id
            || (entry.aliases || []).includes(expected.id));
        assert.equal(result.installed === true, installed);
    }
    const marked = catalog.map(entry => ({...entry, installed: true}));
    assert.equal(build(marked, [], data, day).daily.id, expected.id);
    assert.equal(build(marked, [], data, day).daily.installed, true);
}
assert.equal(build([grouped, b], [native], aliasData).daily.installed, true);
assert.equal(build([provided, b], [a], aliasData).daily.installed, true);
assert.deepEqual(build(catalog, [bridge], data).collections[0].rows, [c]);

// Sorting must not traverse descriptions, screenshots or arbitrary metadata.
const metadata = {};
Object.defineProperty(metadata, 'nested', {enumerable: true, get() {
    throw new Error('Recommendation sorting traversed metadata');
}});
const heavy = row(a.id, {metadata, screenshots: [metadata], description: metadata});
const originalStringify = vm.runInContext('JSON.stringify', context);
vm.runInContext('JSON.stringify = function () { throw new Error("Rows must not be serialized"); };', context);
try {
    const result = context.build([heavy, b, c], [], data, '2026-11-01');
    assert.equal(result.collections[0].rows[0], heavy);
    assert.ok(result.daily);
} finally {
    context.JSONStringify = originalStringify;
    vm.runInContext('JSON.stringify = JSONStringify; delete JSONStringify;', context);
}

// Draft data keeps all its editorial lists together and translates on each use.
let translations = 0;
context.qsTr = text => { ++translations; return text; };
const drafts = context.content();
assert.ok(translations >= 15);
assert.ok(drafts.collections.every(collection => collection.programs.length > 0));
assert.ok(drafts.daily.length && drafts.related.length);
assert.equal(Object.hasOwn(drafts, 'popular'), false);
assert.equal(Object.hasOwn(drafts, 'popularTitle'), false);
assert.equal(Object.hasOwn(drafts, 'popularNote'), false);
assert.equal(Object.hasOwn(first, 'popular'), false);
assert.equal(context.fetch, undefined);
assert.equal(context.XMLHttpRequest, undefined);
console.log('Storefront local recommendations: passed');
