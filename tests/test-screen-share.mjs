// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// The privacy pill's screen half: which casts mean another program sees the screen now.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const source = fs.readFileSync(new URL('../shell/ScreenShare.js', import.meta.url), 'utf8');
const share = vm.createContext({});
vm.runInContext(source.replace(/^\.pragma library\s*$/m, ''), share);

const names = {firefox: 'Firefox', obs: 'OBS Studio'};
const lookup = word => names[word] || '';
function cast(id, extra) {
    return Object.assign({stream_id: id, session_id: 1, kind: 'pipewire', target: 'output', output: 'eDP-1',
        window_id: null, is_active: true, by_parent: false, pw_node_id: 40 + id, client: null}, extra);
}
const plain = rows => JSON.parse(JSON.stringify(rows));

// The idle desktop of 0.8.0: only the shell's own glass capture. Never a share.
assert.deepEqual(plain(share.shares([cast(1, {kind: 'wlr_screencopy', by_parent: true, pw_node_id: null})], [], [-1], lookup)), []);
// A portal session that is open but not streaming (paused or idle) is not a share.
assert.deepEqual(plain(share.shares([cast(2, {is_active: false})], [], [-1], lookup)), []);
// A dynamic cast with nothing chosen shows nothing to anyone.
assert.deepEqual(plain(share.shares([cast(3, {target: 'nothing', output: null})], [], [-1], lookup)), []);
// The shell's own recording (its node) is left to its Stop control.
assert.deepEqual(plain(share.shares([cast(4)], [], [44], lookup)), []);

// A real portal screencast: named after the program reading its node, not "Screen eDP-1".
const readers = [{source: 45, name: 'Firefox', binary: 'firefox'}, {source: 99, name: 'Camera app', binary: 'cheese'}];
assert.deepEqual(plain(share.shares([cast(5)], readers, [-1], lookup)), [{key: 5, name: 'Firefox', window: false}]);
// The desktop entry's name wins over a raw stream name; the raw name is the fallback.
assert.deepEqual(plain(share.shares([cast(6)], [{source: 46, name: 'obs-pipewire', binary: 'obs'}], [], lookup))[0].name, 'OBS Studio');
assert.equal(share.shares([cast(7)], [{source: 47, name: 'gst-launch-1.0', binary: 'gst-launch-1.0'}], [], lookup)[0].name, 'gst-launch-1.0');
// A window share says so; nobody reading yet (or no PipeWire data) is still a share, by an unnamed app.
assert.deepEqual(plain(share.shares([cast(8, {target: 'window', output: null, window_id: 3})], [], [], lookup)), [{key: 8, name: 'An app', window: true}]);
// Another program's screencopy is named from its process (the core's client field).
assert.equal(share.shares([cast(9, {kind: 'wlr_screencopy', pw_node_id: null, client: 'obs'})], [], [], lookup)[0].name, 'OBS Studio');
assert.equal(share.shares([cast(10, {kind: 'wlr_screencopy', pw_node_id: null, client: 'wf-recorder'})], [], [], lookup)[0].name, 'wf-recorder');
// An older core without the new fields still lights the pill for a foreign active cast.
assert.equal(share.shares([{stream_id: 11, kind: 'pipewire', target: 'output', is_active: true}], [], [-1], lookup).length, 1);

// Nodes casts stream to, active or not: a video stream reading one is not a camera.
assert.deepEqual(plain(share.castNodes([cast(1, {pw_node_id: null}), cast(2, {is_active: false}), cast(3)])), [42, 43]);
console.log('screen share: ok');
