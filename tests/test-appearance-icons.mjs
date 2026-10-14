// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const read = path => fs.readFileSync(new URL('../' + path, import.meta.url), 'utf8');
const icons = {};
vm.createContext(icons);
vm.runInContext(read('shell/SystemIcons.js').replace(/^\.pragma library\s*/, ''), icons);
const svg = uri => decodeURIComponent(uri.split(',').slice(1).join(','));
// The shipped Settings icon is the pixel gear (proposal 2) in one ink: no tile, no
// gradient, no stroke, and no other drawing left behind a switch.
const gear = svg(icons.settingsSvg('#123456'));
assert.match(gear, /<path fill="#123456" fill-rule="evenodd" d="M10 4h4v3h3V6h2v4h1v4h-3v3h1v2h-4v1h-4v-3H7v1H5v-4H4v-4h3V7H6V5h4zM10 10v4h4v-4z"\/>/);
assert.equal((gear.match(/<path /g) || []).length, 1);
assert.doesNotMatch(gear, /<rect|linearGradient|stroke|<circle/);
// A QML colour with alpha (the launcher's grey label ink) keeps its alpha as fill-opacity.
const grey = svg(icons.settingsSvg({r: 36 / 255, g: 16 / 255, b: 24 / 255, a: .55}));
assert.match(grey, /fill="#241018" fill-opacity="0\.55"/);
const source = read('shell/SystemIcons.js');
assert.doesNotMatch(source, /settingsGearCandidate|settingsControlSvg|settingsPath|candidate/);
for (const file of ['shell/LauncherBody.qml', 'shell/SymbolIcon.qml', 'shell/Icon.qml'])
    assert.doesNotMatch(read(file), /gearCandidate|settingsControlSvg|accentSoft\)/, file);
// The launcher's Settings button: the gear in the grey of the mode labels beside it.
{
    const launcher = read('shell/LauncherBody.qml');
    assert.match(launcher, /source: SystemIcons\.settingsSvg\(body\.dim\)/);
    assert.match(launcher, /color: !body\.settingsActive && body\.modeIndex === modeItem\.index \? body\.ink : body\.dim/);
    assert.match(read('shell/SymbolIcon.qml'), /SystemIcons\.isSettings\(name\) \? SystemIcons\.settingsSvg\(ink\) : ""/);
}
assert.doesNotMatch(read('shell/SettingsView.qml'), /settingsSvg|settingsGearCandidate/);
assert.match(read('shell/SettingsView.qml'), /settings\/icons\/.*root\.section\.id/);
for (const file of ['shell/Dock.qml', 'shell/LauncherItem.qml', 'update-manager/ui/ManagerView.qml', 'shell/ClockBody.qml'])
    assert.match(read(file), /programsSvg\(.*accentShade/);
// The update reminder names its icon (reminder.py app_icon); the notice resolves both that
// name and a desktop entry's icon through the accent-drawn icon, never the static orange SVG.
{
    const clock = read('shell/ClockBody.qml');
    assert.match(read('update-manager/reminder.py'), /'Programs & Updates', '0', 'emaki-programs-updates'/);
    assert.match(clock, /function themedIcon\(name: string\): string \{\s+if \(name === "emaki-programs-updates"\)\s+return SystemIcons\.programsSvg\(/);
    const iconOf = clock.slice(clock.indexOf('function iconOf('), clock.indexOf('\n    }\n', clock.indexOf('function iconOf(')));
    assert.doesNotMatch(iconOf, /Quickshell\.iconPath/);
    assert.equal((iconOf.match(/themedIcon\(/g) || []).length, 2);
}
assert.match(read('update-manager/ui/ProgramIcon.qml'), /value\.startsWith\("data:image\/svg\+xml;"\)/);
// The default app icon retains every path, paint and SVG rendering attribute.
const staticSvg = read('update-manager/emaki-programs-updates.svg');
const paints = [...staticSvg.matchAll(/<path fill="([^"]+)"/g)].map(match => match[1]);
const dynamicSvg = svg(icons.programsSvg(paints[1], paints[0], paints[2], paints[3], paints[4]));
const normalize = value => value.replace(/<!--[\s\S]*?-->/g, '').replace(/>\s+</g, '><').trim();
assert.equal(normalize(dynamicSvg), normalize(staticSvg));
const alternate = svg(icons.programsSvg('#102030', '#203040', '#304050', '#405060', '#ffffff'));
for (const paint of paints.slice(0, 4)) assert.ok(!alternate.includes(paint));
console.log('PASS: the grey Settings gear is the only Settings drawing; app icon geometry and live accent consumers');
