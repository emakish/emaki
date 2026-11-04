// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const read = name => fs.readFileSync(new URL('../shell/' + name, import.meta.url), 'utf8').replace(/^\.pragma library\s*/m, '');
const icons = vm.createContext({});
vm.runInContext(read('SystemIcons.js'), icons);
const commands = vm.createContext({QT_TRANSLATE_NOOP: (_context, text) => text});
vm.runInContext(read('Commands.js') + '\nthis.entries = actions;', commands);

for (const entry of commands.entries) {
    const description = commands.description(entry);
    assert.ok(description && !description.includes('\n'), entry.id + ': missing one-line description');
    assert.notEqual(description, entry.keywords, entry.id);
    const uri = icons.launcherSvg(entry.icon, {r: .2, g: .3, b: .4, a: .75});
    assert.ok(uri.startsWith('data:image/svg+xml;utf8,'), entry.id + ': missing built-in icon');
    const svg = decodeURIComponent(uri.split(',').slice(1).join(','));
    assert.match(svg, /<(path|rect|circle|ellipse) /, entry.id);
    assert.match(svg, /(?:fill|stroke)-opacity="0.75"/, entry.id);
    assert.ok(!svg.includes('#fffaf5'), entry.id + ': filled details must use the requested ink');
}
for (const name of ['All', 'Apps', 'Windows', 'Files', 'Clipboard', 'Commands', 'Emoji', 'search'])
    assert.ok(icons.launcherSvg(name, '#123456'), name);
assert.equal(icons.launcherSvg('missing-icon', '#123456'), '');
console.log('PASS: every command has a one-line description and a built-in icon; all launcher modes and search have icons');
