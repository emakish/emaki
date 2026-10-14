// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
const logic = vm.createContext({});
vm.runInContext(readFileSync(new URL('../shell/AppearanceLogic.js', import.meta.url), 'utf8'), logic);
const at = (hour, minute) => new Date(2026, 0, 1, hour, minute);
const dark = (date, light = '07:00', night = '19:00') => logic.isDark('automatic', light, night, date);
assert.equal(logic.isDark('dark', '', '', at(12, 0)), true);
assert.equal(logic.isDark('light', '', '', at(0, 0)), false);
for (const [hour, minute, expected] of [[0,0,true],[6,59,true],[7,0,false],[18,59,false],[19,0,true],[23,59,true]])
    assert.equal(dark(at(hour, minute)), expected);
assert.equal(dark(at(1, 0), '19:00', '07:00'), false);
assert.equal(dark(at(12, 0), '19:00', '07:00'), true);
assert.equal(dark(at(19, 0), '19:00', '07:00'), false);
assert.equal(dark(at(12, 0), '07:00', '07:00'), false);
assert.equal(dark(at(12, 0), 'invalid', '25:90'), false);
// No previous timestamp is retained: arbitrarily large forward/backward jumps recover.
for (const hour of [6,20,10,1,23,8])
    assert.equal(dark(at(hour, 15)), hour < 7 || hour >= 19);
// Both hemispheres: skipped and repeated hours use the current local minute.
for (const [zone, spring, fall] of [
    ['Europe/Berlin', ['2026-03-29T00:59:00Z', '2026-03-29T01:00:00Z'],
        ['2026-10-25T00:45:00Z', '2026-10-25T01:15:00Z', '2026-10-25T01:45:00Z']],
    ['Australia/Sydney', ['2026-10-03T15:59:00Z', '2026-10-03T16:00:00Z'],
        ['2026-04-04T15:45:00Z', '2026-04-04T16:15:00Z', '2026-04-04T16:45:00Z']]
]) {
    process.env.TZ = zone;
    assert.equal(dark(new Date(spring[0]), '02:30', '19:00'), true);
    assert.equal(dark(new Date(spring[1]), '02:30', '19:00'), false);
    for (const [index, expected] of [[0, false], [1, true], [2, false]])
        assert.equal(dark(new Date(fall[index]), '02:30', '19:00'), expected);
}
assert.equal(logic.setting([], 'appearance.theme', 'light'), 'light');
assert.equal(logic.setting([{key:'appearance.theme',value:'dark'}], 'appearance.theme', 'light'), 'dark');
assert.equal(logic.setting([{key:'appearance.theme',value:false}], 'appearance.theme', 'light'), 'light');
console.log('appearance schedule: modes, boundaries, overnight, invalid/equal times, clock jumps, DST passed');
