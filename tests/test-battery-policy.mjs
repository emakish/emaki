// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Execute the production policy functions without a desktop or a session bus.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import path from 'node:path';

const source = file => fs.readFileSync(process.env.EMAKI_POLICY_SOURCE
    ? path.join(process.env.EMAKI_POLICY_SOURCE, file)
    : new URL('../shell/' + file, import.meta.url), 'utf8');

function body(file, name) {
    const text = source(file);
    const start = text.indexOf('{', text.indexOf('function ' + name + '('));
    let depth = 1, end = start + 1;
    for (; depth; end++) {
        if (text[end] === '{') depth++;
        if (text[end] === '}') depth--;
    }
    return text.slice(start + 1, end - 1);
}
const battery = vm.createContext({backend: {batteryPower: true, charging: false},
    batteryPercent: 50, lowSent: false, criticalSent: false});
const readings = [];
battery.lowBattery = p => readings.push(p);
function check(percent, charging = false) {
    battery.batteryPercent = percent;
    battery.backend.charging = charging;
    vm.runInContext('(function(){' + body('SystemService.qml', 'checkBattery') + '})()', battery);
}
// Required discharge/recharge sequence: both thresholds fire, then re-arm above them.
[11, 10, 9, 5, 4].forEach(p => check(p));
assert.deepEqual(readings, [10, 5]);
check(15, true); check(10);
assert.deepEqual(readings, [10, 5, 10]);
check(5); check(4, true); check(4); check(-1);
assert.deepEqual(readings, [10, 5, 10, 5], 'brief charging below thresholds must not re-arm');
battery.backend.batteryPower = false; check(4);
battery.backend.batteryPower = true; check(4);
assert.deepEqual(readings, [10, 5, 10, 5], 'plugging in below thresholds must not re-arm');
[6, 5, 11, 10, 13, 10, 19, 5].forEach(p => check(p));
assert.equal(readings.length, 4, 'discharge jitter below 20% must not re-arm either threshold');
check(6, true); check(6); check(5); check(4);
assert.deepEqual(readings, [10, 5, 10, 5, 5], 'each threshold re-arms independently');
check(10, true); check(10); check(5);
assert.deepEqual(readings, [10, 5, 10, 5, 5, 5], 'charging to the threshold is insufficient');
check(15, true); check(4);
assert.deepEqual(readings, [10, 5, 10, 5, 5, 5, 4], 'first reading below 5 emits only one notice');
battery.backend.batteryPower = false; check(15);
battery.backend.batteryPower = true; check(10);
assert.equal(readings.at(-1), 10, 'external power above threshold re-arms even when not actively charging');

// Charge and unplug while asleep: the shell sees only battery readings on resume.
readings.length = 0; battery.lowSent = false; battery.criticalSent = false;
[11, 10, 9, 5, 4, 100, 50, 11, 10, 9, 5, 4].forEach(p => check(p));
assert.deepEqual(readings, [10, 5, 10, 5], 'charging while asleep must re-arm both warnings');
check(20); check(10); check(5);
assert.deepEqual(readings, [10, 5, 10, 5, 10, 5], '20% battery recovery re-arms both warnings');

// The battery marker is assigned before arrival, and cannot be supplied by a client name.
const local = vm.createContext({systemId: -1, entries: [], Date, arrived(id) {
    assert.equal(local.entries[0].id, id);
    assert.equal(local.entries[0].batteryWarning, true);
}});
local.applications = []; local.rules = {};
for (const [name, args] of [['rememberApplication', 'id,name'], ['ruleFor', 'id'], ['peekAllowed', 'entry']])
    local[name] = vm.runInContext('(function(' + args + '){' + body('NotificationStore.qml', name) + '})', local);
local.localNotice = vm.runInContext('(function(app,summary,body,batteryWarning,sessionUpdate){' + body('NotificationStore.qml', 'localNotice') + '})', local);
const systemBattery = vm.runInContext('(function(percent){' + body('NotificationStore.qml', 'systemBattery') + '})', local);
systemBattery(10); systemBattery(5);
assert.deepEqual(Array.from(local.entries, e => [e.app, e.critical, e.batteryWarning]),
    [['Battery critical', true, true], ['Battery low', true, true]]);
assert.deepEqual(Array.from(local.applications), [], 'battery warnings have no ineffective application rules');
local.arrived = () => {};
local.localNotice('Desktop update', 'Finish updating.', '', false, true);
assert.deepEqual(Array.from(local.applications), [], 'desktop update has no ineffective application rule');
for (const rule of ['silent', 'off']) {
    local.rules = {'name:Battery low': rule, 'name:Battery critical': rule, 'name:Desktop update': rule};
    let arrived = 0;
    local.arrived = () => arrived++;
    systemBattery(10); systemBattery(5);
    local.localNotice('Desktop update', 'Finish updating.', '', false, true);
    assert.equal(arrived, 3, 'legacy rules cannot suppress internal warnings');
    assert.ok(local.entries.slice(0, 3).every(entry => local.peekAllowed(entry)));
    assert.deepEqual(Array.from(local.applications), []);
}
local.rules = {}; local.arrived = () => {};
vm.runInContext('(function(){' + body('NotificationStore.qml', 'local') + '})',
    vm.createContext({app: 'Battery low', summary: '', body: '', localNotice: local.localNotice}))();
assert.equal(local.entries[0].batteryWarning, false);
assert.deepEqual(Array.from(local.applications, app => app.id), ['name:Battery low'],
    'an ordinary notice with the same name keeps its application policy');

// Old stored internal senders disappear while real applications and history remain.
const saved = {version: 1, applications: [
    {id: 'name:Battery low', name: 'Battery low'},
    {id: 'name:Battery critical', name: 'Battery critical'},
    {id: 'name:Desktop update', name: 'Desktop update'},
    {id: 'desktop:mail', name: 'Mail'}
], entries: [{app: 'Battery low', summary: 'Low battery.', time: 1000}]};
const restored = vm.createContext({stateFile: {text: () => JSON.stringify(saved)}, systemId: -1});
restored.store = restored;
vm.runInContext('(function(){' + body('NotificationStore.qml', 'restore') + '})()', restored);
assert.deepEqual(Array.from(restored.applications, app => app.id), ['desktop:mail']);
assert.equal(restored.entries[0].summary, 'Low battery.');
assert.equal(restored.entries[0].object, null);

// Snapshot the exact persistent notification protocol used for numeric confirmation.
const store = vm.createContext({n: {id: 3, appName: 'blueman', appIcon: 'blueman',
    summary: 'Bluetooth', body: '123456', expireTimeout: 0,
    actions: [{identifier: 'confirm', text: 'Confirm'}, {identifier: 'deny', text: 'Deny'}]}, time: 1000});
store.plainBody = value => vm.runInContext('(function(value){' + body('NotificationStore.qml', 'plainBody') + '})', store)(value);
function snapshot() {
    return vm.runInContext('(function(){' + body('NotificationStore.qml', 'snapshot') + '})()', store);
}
const pairing = snapshot();
assert.equal(pairing.critical, true);
assert.equal(pairing.deadline, 0);
assert.deepEqual(Array.from(pairing.actions, a => a.id), ['confirm', 'deny']);
store.n.expireTimeout = 4000; assert.equal(snapshot().critical, false);
store.n.expireTimeout = 0; store.n.appName = 'Ordinary'; assert.equal(snapshot().critical, false);
store.n.appName = 'Battery low'; assert.equal(snapshot().batteryWarning, undefined);
store.n.appName = 'blueman'; store.n.appIcon = 'other'; assert.equal(snapshot().critical, false);

// The compatible external update notice is also exempt from application controls.
const incoming = vm.createContext({entries: [], applications: [], rules: {}, Date,
    Quickshell: {env: () => ''}, arrived() {}});
incoming.store = incoming;
for (const [name, args] of [['applicationId', 'n'], ['rememberApplication', 'id,name'],
        ['ruleFor', 'id'], ['snapshot', 'n,time'], ['plainBody', 'value'], ['accept', 'n']])
    incoming[name] = vm.runInContext('(function(' + args + '){' + body('NotificationStore.qml', name) + '})', incoming);
const update = {id: 8, appName: 'Desktop update', appIcon: '', body: '', actions: [],
    summary: 'Sign out and sign in again to finish updating the desktop.', expireTimeout: 0};
for (const signal of ['summaryChanged', 'bodyChanged', 'appNameChanged', 'actionsChanged', 'expireTimeoutChanged', 'closed'])
    update[signal] = {connect() {}};
incoming.accept(update);
assert.equal(incoming.entries[0].critical, true);
assert.deepEqual(Array.from(incoming.applications), []);

let now = 100000;
const popup = vm.createContext({notes: {dnd: true, entries: [{id: 1, critical: true, batteryWarning: true}, {id: 2}, pairing]},
    focusedOutput: true, systemOpen: false, launcherOpen: false, drawerOpen: false, privacyOpen: false,
    niri: {overviewOpen: false}, presentationState: 'covered', modalOpen: false,
    Date: {now: () => now}, peekCooldown: 0, peekStarted: 0, peekUntil: 0,
    peekIds: [], peekTimer: {restart() {}}, endPeek() { popup.peekIds = []; }});
popup.notes.peekAllowed = local.peekAllowed;
Object.defineProperty(popup.notes, 'effectiveDnd', {get() { return this.dnd; }});
Object.defineProperty(popup, 'peekOpen', {get() { return popup.peekIds.length > 0; }});
Object.defineProperty(popup, 'pairingPeekOpen', {get() {
    return expression('ShellScene.qml', 'readonly property bool pairingPeekOpen:', popup);
}});
popup.retainCriticalPeek = () => vm.runInContext('(function(){' + body('ShellScene.qml', 'retainCriticalPeek') + '})()', popup);
function show(id) {
    popup.id = id;
    vm.runInContext('(function(){' + body('ShellScene.qml', 'showNotification') + '})()', popup);
}
const ids = () => Array.from(popup.peekIds);
// Each privacy gate stands alone: cooldown must not hide a missing gate.
for (const [dnd, presentation] of [[true, 'clear'], [false, 'covered'], [false, 'unknown'], [true, 'covered']]) {
    popup.notes.dnd = dnd; popup.presentationState = presentation; popup.peekIds = [];
    show(2); assert.deepEqual(ids(), [], `ordinary privacy: dnd=${dnd}, presentation=${presentation}`);
    for (const id of [1, 3]) {
        popup.peekIds = [2]; show(id); assert.deepEqual(ids(), [id]);
        assert.equal(popup.peekTimer.interval, 15000);
    }
}
popup.peekIds = [1, 2]; popup.retainCriticalPeek(); assert.deepEqual(ids(), [1]);
for (const panel of ['launcherOpen', 'drawerOpen', 'systemOpen', 'privacyOpen']) {
    popup.modalOpen = true; popup[panel] = true;
    popup.peekIds = []; show(2); assert.deepEqual(ids(), []);
    show(1); assert.deepEqual(ids(), [1], panel);
    popup[panel] = false;
}
popup.modalOpen = false; popup.niri.overviewOpen = true;
popup.peekIds = []; show(2); assert.deepEqual(ids(), []);
show(3); assert.deepEqual(ids(), [3], 'pairing bypasses the overview gate');
popup.peekIds = [];
show(1); assert.deepEqual(ids(), [1]);
popup.peekIds = [];
popup.niri.overviewOpen = false; popup.notes.dnd = false; popup.presentationState = 'clear';
show(2); assert.deepEqual(ids(), [2]); assert.equal(popup.peekTimer.interval, 4000);
now += 3000; show(2); assert.equal(popup.peekTimer.interval, 4000);
now += 3000; show(2); assert.equal(popup.peekTimer.interval, 2000);
now += 2000; show(2); assert.deepEqual(ids(), []);
popup.peekCooldown = now + 1000; show(2); assert.deepEqual(ids(), []);
show(1); assert.deepEqual(ids(), [1]); assert.equal(popup.peekTimer.interval, 15000);
const criticalUntil = popup.peekUntil;
now += 9000; show(2); assert.deepEqual(ids(), [1]);
assert.equal(popup.peekUntil, criticalUntil);
assert.equal(popup.peekTimer.interval, 15000); // Ordinary arrivals leave the running timer untouched.
// Execute the actual drawer and layer bindings, including returning to an open drawer.
function expression(file, prefix, context) {
    const line = source(file).split('\n').find(l => l.trim().startsWith(prefix));
    assert.ok(line, prefix);
    return vm.runInNewContext(line.slice(line.indexOf(':') + 1).trim(), context);
}
for (const active of [false, true]) {
    const scene = {batteryPeekOpen: active, pairingPeekOpen: false, peekOpen: active, systemOpen: false, drawerOpen: true};
    const panel = source('ShellScene.qml').split('id: clockPanel')[1];
    const z = panel.match(/\n\s*z: ([^\n]+)/)[1];
    assert.equal(vm.runInNewContext(z, {scene}), active ? 40 : 0);
    assert.equal(vm.runInNewContext(panel.match(/\n\s*opened: ([^\n]+)/)[1], {scene}), !active);
    assert.equal(expression('ShellScene.qml', 'readonly property real headTarget:',
        {drawerOpen: true, batteryPeekOpen: active, pairingPeekOpen: false}), active ? 0 : 1);
}
// The overview closes modal panels but retains a running battery timer and warning.
const transition = vm.createContext({batteryPeekOpen: true, pairingPeekOpen: false, drawerOpen: true,
    tip: {hide() {}}, closeLauncher() {}, closeClock() { throw Error('battery peek was closed'); },
    systemBody: {reset() {}}, dock: {closePopup() {}, leaveKeyboard() {}}, closeAllRequested() {}});
const closePanels = vm.runInContext('(function(preserveBattery){' + body('ShellScene.qml', 'closePanels') + '})', transition);
closePanels(true); assert.equal(transition.drawerOpen, false);
let closed = 0; transition.closeClock = () => closed++;
closePanels(false); assert.equal(closed, 1, 'explicit dismissal still works');
transition.batteryPeekOpen = false; closePanels(true); assert.equal(closed, 2);
assert.match(source('ShellScene.qml'), /onOverviewOpenChanged\(\)[\s\S]*?scene\.closePanels\(true\)/);
const openedHandler = source('ClockPanel.qml').match(/onOpenedChanged: \{([\s\S]*?)\n    \}/)[1];
const clock = {body: {keyboardMode: false}, opened: false, drawerContent: true, peekIds: [1], snapIfClosed() {}, wake() {}};
vm.runInNewContext(openedHandler, clock); assert.equal(clock.drawerContent, false);
clock.opened = true; vm.runInNewContext(openedHandler, clock); assert.equal(clock.drawerContent, true);
console.log('Battery discharge/recharge, private marker, overlay/modal gates, drawer and overview state: PASS');
