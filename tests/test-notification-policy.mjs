// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Exercise production policy and arrival routing without a desktop bus.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source = fs.readFileSync(new URL('../shell/NotificationStore.qml', import.meta.url), 'utf8');
function check(text) {
    const context = vm.createContext({Date, console, Quickshell: {env: () => ''}, entries: [], applications: [], rules: {}, arrivals: [], managedValues: [], dnd: false, dndMigrated: true});
    context.store = context;
    context.arrived = id => context.arrivals.push(id);
    const functions = {scheduleActive:'schedule,timestamp', applicationId:'n', rememberApplication:'id,name', ruleFor:'id', accept:'n', plainBody:'value', snapshot:'n,time', dismiss:'ids', policyValue:'key,fallback', policyJson:'key,fallback', manualDnd:'', migrateDnd:''};
    for (const [name,args] of Object.entries(functions)) {
        const start = text.indexOf('{', text.indexOf('function ' + name + '('));
        let depth = 1, end = start + 1;
        for (; depth && end < text.length; end++) {
            if (text[end] === '{') depth++;
            if (text[end] === '}') depth--;
        }
        context[name] = vm.runInContext('(function(' + args + '){' + text.slice(start + 1,end - 1) + '})',context);
    }
    const minute = (h,m) => new Date(2026,0,1,h,m).getTime();
    const night = {enabled:true,start:'22:00',end:'07:00'};
    for (const [h,m,active] of [[21,59,false],[22,0,true],[23,59,true],[0,0,true],[6,59,true],[7,0,false]])
        assert.equal(context.scheduleActive(night,minute(h,m)),active);
    assert.equal(context.scheduleActive({...night,enabled:false},minute(23,0)),false);
    assert.equal(context.scheduleActive({...night,start:'24:00'},minute(23,0)),false);
    assert.equal(context.scheduleActive({...night,start:'07:00'},minute(12,0)),true);
    assert.equal(context.scheduleActive({enabled:true,start:'09:00',end:'17:00'},minute(12,0)),true);
    assert.equal(context.scheduleActive({enabled:true,start:'09:00',end:'17:00'},minute(17,0)),false);
    assert.equal(context.applicationId({appName:'Mail',desktopEntry:'org.example.Mail'}),'desktop:org.example.Mail');
    assert.equal(context.applicationId({appName:'Mail'}),'name:Mail');
    function note(id,name,rule) {
        context.rules = {[`name:${name}`]:rule};
        const signal = {connect: () => {}};
        const n = {id,appName:name,appIcon:'fixture',summary:'Hello',body:'Body',expireTimeout:0,actions:[],dismissed:false,dismiss(){this.dismissed=true;}, summaryChanged:signal,bodyChanged:signal,appNameChanged:signal,actionsChanged:signal,expireTimeoutChanged:signal,closed:signal};
        context.accept(n);
        return n;
    }
    note(1,'Allowed','allow');
    assert.equal(context.entries.length,1); assert.deepEqual(context.arrivals,[1]);
    note(2,'Silent','silent');
    assert.equal(context.entries.length,2); assert.deepEqual(context.arrivals,[1]);
    assert.equal(note(3,'Blocked','off').dismissed,true);
    assert.equal(context.entries.length,2); assert.deepEqual(context.arrivals,[1]);
    assert.equal(context.applications.length,3);
    note(4,'Blocked','off'); assert.equal(context.applications.length,3);
    let saves = 0, requests = 0;
    context.restored = true; context.foreign = false; context.dndMigrated = false;
    context.saveTimer = {restart() { saves++; }};
    context.catalog = {set() { requests++; }};
    context.managedValues = [{key:'notifications.dnd',value:false,override_value:null}];
    context.migrateDnd();
    assert.equal(context.dndMigrated,true);
    assert.equal(saves,0,'pristine migration leaves the profile untouched');
    assert.equal(requests,0);
    context.dnd = true; context.dndMigrated = false; context.dndMigrationRequested = false;
    context.migrateDnd();
    assert.equal(requests,1,'legacy DND requests a managed override');
    assert.equal(saves,0,'wait for the managed commit');
    context.managedValues = [{key:'notifications.dnd',value:true,override_value:true}];
    context.migrateDnd();
    assert.equal(saves,1,'committed migration is recorded');
    context.managedValues = [{key:'notifications.dnd',value:true}];
    assert.equal(context.manualDnd(),true);
    context.managedValues = [{key:'notifications.dnd',value:false}]; context.dnd=true;
    assert.equal(context.manualDnd(),false);
}
check(source);
for (const [before,after] of [
    ['if (dnd)\n                saveTimer.restart();','if (true)\n                saveTimer.restart();'],
    ['current >= start || current < end','current >= start && current < end'],
    ['if (rule === "off" && !entry.critical)','if (rule === "never" && !entry.critical)'],
    ['if (rule === "allow" || entry.critical)','if (rule !== "off" || entry.critical)'],
]) {
    assert.ok(source.includes(before));
    assert.throws(() => check(source.replace(before,after)),`survived mutation: ${before}`);
}
console.log('Notification policy: boundaries, overnight, defaults, identity, allow/silent/off migration and 4 mutants PASS');

const scene = fs.readFileSync(new URL('../shell/ShellScene.qml', import.meta.url), 'utf8');
function transfer(text) {
    const start = text.indexOf('{', text.indexOf('function importTransient('));
    let depth = 1, end = start + 1;
    for (; depth && end < text.length; end++) {
        if (text[end] === '{') depth++;
        if (text[end] === '}') depth--;
    }
    const replays = [];
    const context = vm.createContext({Date, notes: {entries:[{id:1},{id:2}], effectiveDnd:false, peekAllowed:e => e.id === 1}, niri:{overviewOpen:false}, modalOpen:false,presentationState:'clear',peekOpen:false,osd:{importState:() => {}}, retainCriticalPeek:() => {context.peekIds=[];},
        authenticationPresent:false, deferredPeekIds:[], replayDeferredPeeks() {}, Qt:{callLater:f => replays.push(f)}});
    const imported = vm.runInContext('(function(state){' + text.slice(start+1,end-1) + '})',context);
    imported({ids:[1,2],until:Date.now()+10000,started:Date.now(),cooldown:0,osd:{}});
    assert.equal(JSON.stringify(context.peekIds),'[1]');
    context.notes.effectiveDnd=true;
    imported({ids:[1,2],until:Date.now()+10000,started:Date.now(),cooldown:0,osd:{}});
    assert.equal(JSON.stringify(context.peekIds),'[]');
    // A protected prompt on the new output holds the moved peek and the earlier deferrals
    // until it has shrunk back; the import then asks for a replay.
    context.notes.effectiveDnd=false; context.authenticationPresent=true; replays.length=0;
    imported({ids:[1,2],deferredIds:[2],until:Date.now()+10000,started:Date.now(),cooldown:0,osd:{}});
    assert.equal(JSON.stringify(context.peekIds),'[]');
    assert.equal(JSON.stringify(context.deferredPeekIds),'[2,1]');
    assert.deepEqual(replays,[context.replayDeferredPeeks]);
}
transfer(scene);
// Mutants must fail an assertion, not a missing name in the harness.
for (const [before,after,all] of [
    ['e.id === id && notes.peekAllowed(e) && (state.until', 'e.id === id && (state.until'],
    ['modalOpen || notes.effectiveDnd || presentationState', 'modalOpen || false || presentationState', true],
    ['?? [])));\n        if (authenticationPresent) {', '?? [])));\n        if (false) {'],
    ['deferredPeekIds.concat(state.deferredIds ?? [])', 'deferredPeekIds'],
]) {
    assert.ok(scene.includes(before), before);
    assert.throws(() => transfer(all ? scene.replaceAll(before,after) : scene.replace(before,after)), assert.AssertionError, `survived mutation: ${before}`);
}
console.log('Notification transfer: app policy, timed/scheduled DND and prompt deferral retained; 4 mutants PASS');

// Update warnings describe the running session, never persistent notification history.
function updateHistory(text) {
    const update = {app: 'Desktop update', summary: 'Sign out and sign in again to finish updating the desktop.', body: 'Save your work first [Emaki].', time: 1000};
    const mail = {app: 'Mail', summary: 'Keep this history', body: 'Message', time: 900};
    const similarlyNamed = {...mail, app: 'Desktop update', summary: 'Unrelated message'};
    const context = vm.createContext({heldApps: [], systemId: -1, stateFile: {text: () => JSON.stringify({version: 1, entries: [update, mail, similarlyNamed]})}});
    context.store = context;
    for (const [name, args] of [['sessionUpdateEntry', 'entry'], ['restore', '']]) {
        const at = text.indexOf('function ' + name + '(');
        if (at < 0) continue;
        const start = text.indexOf('{', at);
        let depth = 1, end = start + 1;
        for (; depth && end < text.length; end++) {
            if (text[end] === '{') depth++;
            if (text[end] === '}') depth--;
        }
        context[name] = vm.runInContext('(function(' + args + '){' + text.slice(start + 1, end - 1) + '})', context);
    }
    context.restore();
    assert.equal(context.restored, true);
    assert.equal(JSON.stringify(context.entries.map(e => e.summary)), JSON.stringify([mail.summary, similarlyNamed.summary]), 'legacy update notice must not survive a new session');
    // Both the local observer and the old transaction hook must remain memory-only.
    context.entries.push({...update, sessionUpdate: true}, update, {...mail, held: true},
                         {...mail, summary: 'Text copied', body: 'Private captured text', transient: true});
    context.manualDnd = () => false;
    let saved;
    context.persistence = {submit: text => { saved = JSON.parse(text); }};
    const saveStart = text.indexOf('persistence.submit(JSON.stringify({');
    const saveEnd = text.indexOf('\n            }))', saveStart) + '\n            }))'.length;
    assert.ok(saveStart >= 0 && saveEnd > saveStart);
    vm.runInContext(text.slice(saveStart, saveEnd), context);
    assert.equal(JSON.stringify(saved.entries.map(e => e.summary)), JSON.stringify([mail.summary, similarlyNamed.summary]), 'update notices must never be written to history');
}
updateHistory(source);
for (const before of [
    ' && !store.sessionUpdateEntry(e)).slice(0, 200)',
    ' && !store.sessionUpdateEntry(e)).map(e => ({',
    ' && e.transient !== true && !store.sessionUpdateEntry(e)).map(e => ({',
]) {
    assert.ok(source.includes(before));
    const condition = before.includes('e.transient') ? ' && e.transient !== true' : ' && !store.sessionUpdateEntry(e)';
    assert.throws(() => updateHistory(source.replace(before, before.replace(condition, ''))), assert.AssertionError);
}
console.log('Notification history: stale update cards removed, transient and current update notices unsaved, unrelated history preserved; 3 mutants PASS');
