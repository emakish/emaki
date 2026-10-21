// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const root = process.env.EMAKI_TEST_ROOT || new URL('../', import.meta.url).pathname;
const read = path => fs.readFileSync(`${root}/${path}`, 'utf8');
const logic = vm.createContext({});
vm.runInContext(fs.readFileSync(new URL('qml-translation.js', import.meta.url), 'utf8'), logic);
vm.runInContext(read('shell/Switcher.js').replace(/^\.pragma library\s*$/m, ''), logic);
const labels = read('shell/WindowLabels.qml');
const place = labels.match(/function place\([^]*?\n    }/)[0].replaceAll(': var', '').replace(': void', '');
vm.runInContext(place, logic);
const layout = {emaki_tile_pos_in_view: [20, 30], emaki_capture_safe: true,
    tile_pos_in_workspace_view: [20, 30], window_offset_in_tile: [0, 0], window_size: [100, 100]};
const windows = [{id: 1, workspace_id: 10, layout},
    {id: 2, workspace_id: 10, is_floating: true, layout: {...layout, tile_pos_in_workspace_view: [300, 30]}}];
const workspaces = [{id: 10, output: 'A', is_active: true}];
const visible = positions => Object.keys(logic.visible(windows, workspaces, 'A', 1000, 800, false, positions));
const positions = {};
logic.place(positions, 1, layout);
logic.place(positions, 2, {...layout, emaki_tile_pos_in_view: null});
assert.deepEqual(visible(positions), ['1', '2']);
// Beginning a scroll/workspace transition invalidates all crops, including stationary
// floating windows; upstream's floating coordinates must not bypass the safety channel.
logic.place(positions, 1, {...layout, emaki_capture_safe: false});
assert.deepEqual(visible(positions), []);
assert.equal(logic.windowRect(windows[0], positions), null);
logic.place(positions, 1, layout);
logic.place(positions, 2, {...layout, emaki_tile_pos_in_view: null, emaki_capture_safe: false});
assert.deepEqual(visible(positions), []);
// An older compositor has no safety field: fail closed for both tiled and floating.
for (const emaki_tile_pos_in_view of [null, [20, 30]]) {
    logic.place(positions, 2, {...layout, emaki_tile_pos_in_view, emaki_capture_safe: undefined});
    assert.deepEqual(visible(positions), []);
}
logic.place(positions, 2, {...layout, emaki_tile_pos_in_view: null});
assert.deepEqual(visible(positions), ['1', '2']);
// Before the private snapshot arrives no capture has trustworthy safety information.
assert.deepEqual(visible(undefined), []);
assert.deepEqual(visible({}), []);
console.log('Switcher animation crop safety passed');
