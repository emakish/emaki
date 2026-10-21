// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// The window switcher's rules (shell/Switcher.js): order, rows, selection, held modifiers, which
// windows can show a live picture, the grid.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const source = fs.readFileSync(new URL('../shell/Switcher.js', import.meta.url), 'utf8');
const logic = vm.createContext({});
vm.runInContext(fs.readFileSync(new URL('qml-translation.js', import.meta.url), 'utf8'), logic);
vm.runInContext(source.replace(/^\.pragma library\s*$/m, ''), logic);
const plain = value => JSON.parse(JSON.stringify(value));

const layout = (pos, size = [700, 600], offset = [2, 2]) => ({
    tile_pos_in_workspace_view: pos, window_offset_in_tile: offset, window_size: size, tile_size: [size[0] + 4, size[1] + 4]
});
const win = (id, focused, workspace, pos, extra = {}) => Object.assign({
    id, is_focused: focused, workspace_id: workspace, is_floating: false, is_urgent: false, layout: layout(pos)
}, extra);

// Order: the focused window, then the most recent focus, then the newest window.
const windows = [win(1, false, 10, null), win(2, true, 10, null), win(3, false, 20, null), win(4, false, 20, null), win(5, false, 10, null)];
const times = {1: 300, 3: 900, 4: 300};
assert.deepEqual(logic.order(windows, times).map(w => w.id), [2, 3, 4, 1, 5]);
assert.deepEqual(logic.order(windows, {}).map(w => w.id), [2, 5, 4, 3, 1]);

// Rows carry labels; a window without one is numbered; the same application keeps its own.
const labels = {1: {title: 'One', appId: 'app.a'}, 2: {title: 'Two', appId: 'app.a'}, 3: {title: '', appId: 'app.b'}};
const rows = plain(logic.entries(windows, labels, times, false));
assert.deepEqual(rows.map(r => [r.id, r.title, r.appId, r.focused]),
    [[2, 'Two', 'app.a', true], [3, 'Window #3', 'app.b', false], [4, 'Window #4', '', false], [1, 'One', 'app.a', false], [5, 'Window #5', '', false]]);
assert.deepEqual(plain(logic.entries(windows, labels, times, true)).map(r => r.id), [2, 1]);
// No application known for the focused window: Alt+` lists everything rather than nothing.
assert.equal(logic.entries(windows, {}, times, true).length, 5);
assert.equal(logic.entries([win(Number.MAX_SAFE_INTEGER + 2, true, 1, null)], {}, {}, false).length, 0);

// A second Alt+Tab right after a switch, before niri reports the chosen window (3) focused:
// the chosen window counts as focused and the one just left (2, the newest focus) comes next.
assert.deepEqual(plain(logic.entries(windows, labels, {1: 300, 2: 1000, 3: 900, 4: 300}, false, 3)).map(r => [r.id, r.focused]),
    [[3, true], [2, false], [4, false], [1, false], [5, false]]);
assert.deepEqual(plain(logic.entries(windows, labels, times, false, -1)).map(r => r.id), [2, 3, 4, 1, 5]);

// Selection: Tab starts on the previous window, Shift+Tab on the oldest; wraps both ways.
assert.equal(logic.start(rows, 1), 1);
assert.equal(logic.start(rows, -1), 4);
assert.equal(logic.start(rows.slice(0, 1), 1), 0);
assert.equal(logic.start([], 1), -1);
assert.equal(logic.start([{id: 7, focused: false}, {id: 8, focused: false}], 1), 0);
assert.equal(logic.step(4, 5, 1), 0);
assert.equal(logic.step(0, 5, -1), 4);
assert.equal(logic.step(-1, 5, 1), 1);
assert.equal(logic.step(0, 0, 1), -1);

// Held: Alt, Super or Ctrl; Shift alone and "unknown" (-1) are not.
const ALT = 0x08000000, CONTROL = 0x04000000, META = 0x10000000, SHIFT = 0x02000000;
for (const [value, held] of [[ALT, true], [META, true], [CONTROL, true], [ALT | SHIFT, true], [SHIFT, false], [0, false], [-1, false], [undefined, false]])
    assert.equal(logic.held(value), held, String(value));

// Live pictures: the active workspace of this output, wholly on screen, under no floating window.
const workspaces = [
    {id: 10, output: 'A', is_active: true}, {id: 20, output: 'A', is_active: false}, {id: 30, output: 'B', is_active: true}
];
const screen = [
    win(1, true, 10, [16, 60]),                       // on screen
    win(2, false, 10, [800, 60]),                     // on screen, under the floating window 6
    win(3, false, 10, [1500, 60]),                    // beyond the right edge
    win(4, false, 20, [16, 60]),                      // another workspace
    win(5, false, 30, [16, 60]),                      // another output
    win(6, false, 10, [900, 200], {is_floating: true, layout: layout([900, 200], [300, 200])}),
    win(7, false, 10, null),                          // no position
];
const safePositions = Object.fromEntries(screen.map(w => [w.id, null]));
assert.deepEqual(Object.keys(plain(logic.visible(screen, workspaces, 'A', 1536, 960, false, safePositions))).map(Number), [1, 6]);
assert.deepEqual(plain(logic.visible(screen, workspaces, 'A', 1536, 960, false, safePositions))[1], {x: 18, y: 62, width: 700, height: 600});
assert.deepEqual(plain(logic.visible(screen, workspaces, 'A', 1536, 960, true)), {});
assert.deepEqual(plain(logic.visible(screen, workspaces, '', 1536, 960, false)), {});
assert.deepEqual(Object.keys(plain(logic.visible(screen, workspaces, 'B', 1536, 960, false, safePositions))).map(Number), [5]);
// Two floating windows over each other: neither (their order is unknown).
const floats = [win(1, true, 10, [100, 100], {is_floating: true}), win(2, false, 10, [300, 300], {is_floating: true})];
assert.deepEqual(plain(logic.visible(floats, workspaces, 'A', 1536, 960, false, {1: null, 2: null})), {});

// Tiled windows: stock niri gives them no position; Emaki's niri does (emaki_tile_pos_in_view,
// passed in as `positions`). The tabs of one column share a place: only the active one is drawn.
const tiled = [
    win(1, true, 10, null), win(2, false, 10, null), win(3, false, 10, null), win(4, false, 10, null),
];
const actives = [{id: 10, output: 'A', is_active: true, active_window_id: 1}];
assert.deepEqual(plain(logic.visible(tiled, actives, 'A', 1536, 960, false)), {});
assert.deepEqual(plain(logic.visible(tiled, actives, 'A', 1536, 960, false, {})), {});
const positions = {1: [16, 60], 2: [16, 60], 3: [740, 60], 4: [1600, 60]};
assert.deepEqual(Object.keys(plain(logic.visible(tiled, actives, 'A', 1536, 960, false, positions))).map(Number), [1, 3]);
assert.deepEqual(plain(logic.visible(tiled, actives, 'A', 1536, 960, false, positions))[3], {x: 742, y: 62, width: 700, height: 600});
// The active tab elsewhere (another column is active): neither tab of that column is known.
const elsewhere = [{id: 10, output: 'A', is_active: true, active_window_id: 3}];
assert.deepEqual(Object.keys(plain(logic.visible(tiled, elsewhere, 'A', 1536, 960, false, positions))).map(Number), [3]);
// niri's own position wins (a floating window; an inset fullscreen tile).
const own = [win(1, true, 10, [100, 100])];
assert.deepEqual(plain(logic.visible(own, actives, 'A', 1536, 960, false, {1: [500, 500]}))[1], {x: 102, y: 102, width: 700, height: 600});

// The grid: one row while it fits, then rows, then scrolling.
assert.deepEqual(plain(logic.grid(3, 216, 176, 1400, 800)), {columns: 3, rows: 1, scrolls: false});
assert.deepEqual(plain(logic.grid(9, 216, 176, 1400, 800)), {columns: 6, rows: 2, scrolls: false});
assert.deepEqual(plain(logic.grid(40, 216, 176, 1400, 800)), {columns: 6, rows: 4, scrolls: true});
assert.deepEqual(plain(logic.grid(5, 216, 176, 100, 100)), {columns: 1, rows: 1, scrolls: true});

// Capture follows the actual panel, including its fade. Arming a chord and waiting for its
// keyboard must not capture the desktop: a quick tap never presents the panel at all.
const switcher = fs.readFileSync(process.env.EMAKI_SWITCHER_SOURCE_ROOT
    ? `${process.env.EMAKI_SWITCHER_SOURCE_ROOT}/shell/WindowSwitcher.qml`
    : new URL('../shell/WindowSwitcher.qml', import.meta.url), 'utf8');
const presenting = switcher.match(/readonly property bool presenting: ([^\n]+)/)[1];
for (const [armed, visible, expected] of [
    [false, false, false], // idle
    [true, false, false],  // tap or waiting for the reveal delay
    [true, true, true],    // held panel
    [false, true, true],   // closing fade
    [false, false, false], // closed again
]) {
    assert.equal(vm.runInNewContext(presenting, {armed, panel: {visible}}), expected,
        `capture with armed=${armed}, visible=${visible}`);
}

// Execute the production event handlers: a frozen crop becomes unsafe as soon as the scene
// changes, even if the switcher is still held or fading. A queued grab must not restore it.
function body(name) {
    const signature = `function ${name}(`;
    const found = switcher.indexOf(signature);
    assert.notEqual(found, -1, `missing ${name}`);
    const start = switcher.indexOf('{', found);
    let end = start + 1, depth = 1;
    for (; depth && end < switcher.length; ++end) {
        if (switcher[end] === '{') ++depth;
        if (switcher[end] === '}') --depth;
    }
    assert.equal(depth, 0);
    return '(function(){' + switcher.slice(start + 1, end - 1) + '})()';
}
const crop = {x: 16, y: 60, width: 700, height: 600};
const pictures = vm.createContext({live: {1: crop}, recent: {}, id: 1, result: {}, width: 200, height: 125, rect: crop,
    Switcher: {visible: () => ({1: crop})}, niri: {windows: [{id: 1}]}, labels: {}, outputName: 'A', viewportWidth: 1280, viewportHeight: 800});
pictures.switcher = {
    invalidatePictures: () => vm.runInContext(body('invalidatePictures'), pictures),
    pruneRecent: () => vm.runInContext(body('pruneRecent'), pictures),
};
vm.runInContext(body('storeRecent'), pictures);
const saved = pictures.recent[1];
vm.runInContext(body('onWindowsChanged'), pictures);
assert.equal(pictures.live[1], crop, 'an unchanged snapshot must keep its safe crop');
pictures.Switcher.visible = () => ({});
for (const event of ['onWindowsChanged', 'onWorkspacesChanged', 'onOverviewOpenChanged', 'onPositionsChanged']) {
    pictures.live = {1: crop};
    vm.runInContext(body(event), pictures);
    assert.deepEqual(Object.keys(pictures.live), [], `${event} must stop obsolete crops`);
    pictures.result = {obsolete: true};
    vm.runInContext(body('storeRecent'), pictures);
    assert.equal(pictures.recent[1], saved, `${event} must reject a late grab`);
}
pictures.live = {1: {...crop, x: 800}};
vm.runInContext(body('storeRecent'), pictures);
assert.equal(pictures.recent[1], saved, 'a new opening must reject the old opening\'s grab');

// Closing a window frees its saved picture immediately, without another opening. Other
// windows' pictures remain, and an already queued grab cannot put the closed picture back.
pictures.recent = {1: saved, 2: {result: {closed: true}}};
vm.runInContext(body('onWindowsChanged'), pictures);
assert.deepEqual(Object.keys(pictures.recent), ['1'], 'window-close event must free its picture');
assert.equal(pictures.recent[1], saved);
pictures.id = 2;
pictures.rect = crop;
pictures.live = {2: crop};
vm.runInContext(body('storeRecent'), pictures);
assert.deepEqual(Object.keys(pictures.recent), ['1'], 'late grab must not retain a closed window');
console.log('switcher logic: OK');
