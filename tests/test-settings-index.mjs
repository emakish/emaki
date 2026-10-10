// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const root = new URL('../shell/settings/', import.meta.url);
const source = fs.readFileSync(new URL('SettingsIndex.js', root), 'utf8');
const index = vm.createContext({});
vm.runInContext(source.replace(/^\.pragma library\s*$/m, ''), index);
const ids = ['wifi', 'bluetooth', 'network', 'appearance', 'wallpaper', 'panel',
    'windows', 'displays', 'sound', 'keyboard', 'mouse', 'notifications', 'battery',
    'lock', 'apps', 'updates', 'privacy', 'region', 'users', 'storage',
    'accessibility', 'about', 'assistants'];
assert.deepEqual(Array.from(index.sections, section => section.id), ids);
assert.deepEqual([...new Set(index.sections.map(section => section.group))],
    ['Network', 'Personal', 'Devices', 'System']);
assert.equal(index.section('missing'), null);
assert.equal(index.section('panel').title, 'Panel & Dock');
const registry = fs.readFileSync(new URL('../SettingsPageRegistry.qml', root), 'utf8');
const available = Array.from(registry.matchAll(/pageId: "([^"]+)"/g), match => match[1]);
const targets = Array.from(registry.matchAll(/"([a-z]+-[a-z-]+)":\s*"/g), match => match[1]);
for (const match of registry.matchAll(/keys: \[(.*?)\]/g))
    targets.push(...JSON.parse('[' + match[1] + ']'));
// Numeric and device-name explanations need prose that does not imply a live value.
const valueFreeExplanations = {
    'windows-window-gaps': 'Wallpaper between windows, in pixels.',
    'displays-brightness': 'Built-in backlight level.',
    'sound-volume': 'Output level. Reset restores 100%.',
    'sound-input-volume': 'Input level. Reset restores 100%.',
    'sound-output': 'The device used to play sound.',
    'sound-input': 'The device used to record sound.',
    'keyboard-key-repeat-delay': 'Time before a held key starts repeating.',
    'keyboard-key-repeat-rate': 'Characters per second while a key is held.',
    'mouse-pointer-speed': 'Pointer speed as a percentage of the adjustment range.',
    'mouse-trackpad-speed': 'Pointer speed as a percentage of the adjustment range.',
    'battery-battery-health': 'Percentage of the original full-charge capacity.',
};
const itemIds = new Set();
for (const section of index.sections) {
    assert.ok(section.description);
    assert.equal(section.colors.length, 2);
    if (!available.includes(section.id)) assert.equal(section.items.length, 0);
    for (const item of section.items) {
        assert.ok(!itemIds.has(item.id), item.id);
        itemIds.add(item.id);
        assert.ok(targets.includes(item.key || item.id), item.id + ' must have a reveal target');
        const qml = fs.readFileSync(new URL('../' + item.source, root), 'utf8');
        const literals = Array.from(qml.matchAll(/"([^"\\]*(?:\\.[^"\\]*)*)"/g), match =>
            JSON.parse('"' + match[1] + '"').trim());
        assert.ok(literals.includes(item.title), item.id + ': title must match visible page text');
        if (item.staticExplanation) {
            const staticExplanations = Array.from(qml.matchAll(/^\s*explanation: ("(?:[^"\\]|\\.)*")\s*,?\s*$/gm),
                match => JSON.parse(match[1]));
            assert.ok(staticExplanations.includes(item.explanation), item.id + ': display only an intact static explanation');
        }
        if (item.searchExplanation !== undefined) {
            assert.ok(item.searchExplanation, item.id + ': dynamic rows keep their own explanation');
            if (Object.hasOwn(valueFreeExplanations, item.id))
                assert.equal(item.searchExplanation, valueFreeExplanations[item.id]);
            else
                assert.ok(literals.some(text => text.includes(item.searchExplanation)),
                    item.id + ': reuse an intact sentence from this row');
        }
        const visibleWords = literals.join(' ').split(/\s+/);
        for (const word of item.explanation.split(/\s+/).filter(Boolean))
            assert.ok(visibleWords.includes(word), item.id + ': explanation drift: ' + word);
        assert.ok(index.search(item.title, available, targets).some(result =>
            result.title === item.title && result.page === section.id && result.enabled), item.title);
    }
    const svg = fs.readFileSync(new URL('icons/' + section.id + '.svg', root), 'utf8');
    assert.match(svg, /viewBox="0 0 24 24"/);
    assert.match(svg, /Copyright \(C\) 2026 Artur Yakymenko/);
    assert.match(svg, /SPDX-License-Identifier: GPL-3.0-or-later/);
    assert.doesNotMatch(svg, /<(?:filter|mask|image|script)\b/);
}
const coreKeys = ['appearance.gaps', 'appearance.wallpaper', 'windows.default_column_width', 'windows.focus_follows_mouse', 'keybindings.toggle_window_floating',
    'keyboard.layouts', 'keyboard.switch_key', 'bar.autohide', 'bar.overview_workspaces',
    'dock.on', 'dock.auto_hide', 'defaults.terminal', 'defaults.browser', 'defaults.files'];
for (const key of coreKeys) {
    const results = index.search(key, available, targets);
    assert.equal(results.length, 1, key);
    assert.equal(results[0].key, key);
    assert.equal(results[0].enabled, true);
}
const plain = value => JSON.parse(JSON.stringify(value));
assert.deepEqual(plain(index.search('  DOCK\t Auto-hide\n', ['panel'])),
    plain(index.search('dock auto-hide', ['panel'])));
assert.equal(index.search('hide the panel', available)[0].key, 'bar.autohide');
assert.equal(index.search('show the dock', available)[0].key, 'dock.on');
assert.equal(index.search('bottom edge', available)[0].key, 'dock.auto_hide');
assert.equal(index.search('default browser', available)[0].key, 'defaults.browser');
assert.equal(index.search('layouts', available).filter(result => !result.section).length, 2);
assert.ok(!index.search('dock', available).some(result => result.key === 'bar.overview_workspaces'));
assert.equal(index.search('fingerprint', ['lock']).length, 0);
for (const query of ['slideshow', 'animations', 'panel position', 'dark', 'installed programs'])
    assert.equal(index.search(query, available).length, 0, query);
for (const query of ['', ' \t\n', 'unfindable-word', 'dock fingerprint'])
    assert.equal(index.search(query, available).length, 0, query);
assert.equal(index.search('keyboard').length, 0);
assert.equal(index.search('volume', []).length, 0);
const sectionResults = index.search('Panel & Dock', ['panel'], []);
assert.equal(sectionResults.length, 1);
assert.equal(sectionResults[0].section, true);
assert.equal(sectionResults[0].key, '');
assert.equal(sectionResults[0].explanation, index.section('panel').description);
assert.equal(index.search('show the dock', available)[0].explanation,
    'Your pinned and running apps appear in the dock.');
assert.equal(index.search('hide the dock', available)[0].explanation,
    'Move to the bottom edge to show your apps.');
assert.equal(index.search('DNS servers', available)[0].explanation,
    'Separate server addresses with commas. Empty uses automatic DNS.');
for (const page of index.sections) {
    for (const item of page.items) {
        const result = index.search(item.title, available, [item.key || item.id]).find(result =>
            !result.section && result.id === item.id);
        assert.ok(result, item.id + ': each row can be searched independently');
        assert.equal(result.explanation, item.searchExplanation ?? item.explanation,
            item.id + ': use this row’s explanation, including an intentional empty value');
        assert.notEqual(result.explanation, page.description,
            item.id + ': page descriptions belong only to section results');
    }
}
assert.equal(index.search('hide the panel', ['panel'], []).length, 0);
assert.equal(index.search('hide the panel', ['panel'], ['bar.autohide']).length, 1);
const before = JSON.stringify(index.sections);
const pages = ['panel'];
const first = index.search('dock', pages);
first[0].enabled = false;
index.search('dock', available);
assert.equal(JSON.stringify(index.sections), before);
assert.deepEqual(pages, ['panel']);
assert.ok(index.search('dock', pages).every(result => result.enabled));
console.log('Settings index: visible titles, explanations, reveal targets, search and availability passed');
