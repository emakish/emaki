// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Execute production functions and bindings; no desktop bus or real device is used.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const files = ['ShellScene.qml', 'Surfaces.qml', 'ClockPanel.qml', 'ClockBody.qml', 'NotificationStore.qml', 'SystemService.qml'];
const original = Object.fromEntries(files.map(file => [file,
    fs.readFileSync(new URL('../shell/' + file, import.meta.url), 'utf8')]));
function run(sources) {
    function production(file, name, parameters, context) {
        const source = sources[file];
        const marker = source.indexOf('function ' + name + '(');
        assert.notEqual(marker, -1, name);
        const start = source.indexOf('{', marker);
        let depth = 1, end = start + 1;
        for (; depth && end < source.length; end++) {
            if (source[end] === '{') depth++;
            if (source[end] === '}') depth--;
        }
        assert.equal(depth, 0);
        return vm.runInContext('(function(' + parameters + '){' + source.slice(start + 1, end - 1) + '})', context);
    }
    function binding(file, property, context) {
        const lines = sources[file].split('\n');
        const index = lines.findIndex(line => line.trim().startsWith(property + ':'));
        assert.notEqual(index, -1, property);
        let expression = lines[index].slice(lines[index].indexOf(':') + 1);
        for (let next = index + 1; next <= lines.length; next++) {
            let script;
            try { script = new vm.Script(expression); }
            catch (error) {
                if (!(error instanceof SyntaxError) || next === lines.length) throw error;
                expression += '\n' + lines[next];
                continue;
            }
            return script.runInContext(context);
        }
    }
    const store = vm.createContext({});
    store.plainBody = production('NotificationStore.qml', 'plainBody', 'value', store);
    const snapshot = production('NotificationStore.qml', 'snapshot', 'n,time', store);
    function request(typedPin) {
        return snapshot({id: 1, appName: 'blueman', appIcon: 'blueman',
            summary: 'Bluetooth Authentication', expireTimeout: typedPin ? -1 : 0,
            body: typedPin ? 'Pairing request for <b>Keys</b> (00:11:22:33:44:55)' :
                'Pairing request for:\n<b>Keys</b> (00:11:22:33:44:55)\nConfirm value for authentication: <b>001234</b>',
            actions: typedPin ? [] : [{identifier: 'confirm', text: 'Confirm'}, {identifier: 'deny', text: 'Deny'}]}, 1000);
    }
    function fixture(open, typedPin = false) {
        const state = vm.createContext({notes: {dnd: false, entries: [request(typedPin), {id: 2}]},
            niri: {overviewOpen: false, casts: []}, presentationState: 'clear',
            systemOpen: open, systemPage: 'bt', systemExpansion: open ? 1 : 0,
            launcherOpen: false, drawerOpen: false, privacyOpen: false,
            Date: {now: () => 1000}, peekIds: [], peekCooldown: 0, peekStarted: 0,
            Metrics: {morphMs: 350}, peekTimer: {restart() {}, stop() {}}, closeTimer: {restart() {}}, systemBody: {rows: [], reset() {}}, services: {pendingKind: ''},
            enabled: true, output: {},
            WlrKeyboardFocus: {None: 'none', OnDemand: 'on-demand', Exclusive: 'exclusive'}});
        state.scene = state;
        state.surfaces = {controller: state};
        const surface = vm.createContext({controller: state});
        Object.defineProperty(state.surfaces, 'pairingKeyboard', {get() {
            return binding('Surfaces.qml', 'readonly property bool pairingKeyboard', surface);
        }});
        Object.defineProperty(state, 'modalOpen', {get() {
            return binding('ShellScene.qml', 'readonly property bool modalOpen', state);
        }});
        Object.defineProperty(state, 'peekOpen', {get() { return state.peekIds.length > 0; }});
        for (const property of ['pairingPeekOpen', 'batteryPeekOpen'])
            Object.defineProperty(state, property, {get() {
                return binding('ShellScene.qml', 'readonly property bool ' + property, state);
            }});
        for (const name of ['showNotification', 'closeSystem', 'closeClock', 'endPeek', 'retainCriticalPeek'])
            state[name] = production('ShellScene.qml', name, name === 'showNotification' ? 'id' : '', state);
        return state;
    }
    // Find the overlay binding by its real source scope.
    const overlaySources = sources['Surfaces.qml'].split('id: overlay')[1];
    const keyboard = state => vm.runInContext(overlaySources.match(/WlrLayershell.keyboardFocus: (.*)/)[1], state);
    // Both independent sources of pairing activity release exclusivity; idle browsing does not.
    for (const activity of ['idle', 'row', 'pending']) {
        const state = fixture(true);
        if (activity === 'row') state.systemBody.rows = [{action: 'bt-connect'}, {action: 'bt-cancel-pair'}];
        if (activity === 'pending') state.services.pendingKind = 'bt-pair';
        assert.equal(keyboard(state), activity === 'idle' ? 'exclusive' : 'on-demand', activity);
        state.systemPage = 'wifi';
        assert.equal(keyboard(state), 'exclusive', 'another settings page');
        state.systemPage = 'bt';
        state.systemOpen = false;
        assert.equal(keyboard(state), 'none', 'closed pairing panel');
        state.systemOpen = true;
        assert.equal(keyboard(state), activity === 'idle' ? 'exclusive' : 'on-demand', 'reopen');
        state.systemBody.rows = []; state.services.pendingKind = '';
        assert.equal(keyboard(state), 'exclusive', 'completed or cancelled pairing');
    }
    // Execute the real remap function and readiness binding with a controllable event queue.
    function focusFixture() {
        const state = fixture(true), later = [];
        let focused = 0;
        state.systemPanel = {forceActiveFocus() { focused++; }};
        const surface = vm.createContext({controller: state, pairingRemap: false,
            overlay: {contentItem: {Window: {active: false}}}, Qt: {callLater: fn => later.push(fn)}});
        surface.surfaces = surface;
        Object.defineProperty(surface, 'pairingKeyboard', {get() { return state.surfaces.pairingKeyboard; }});
        const readySource = sources['Surfaces.qml'].split('property: "pairingFocusReady"')[1].match(/value: (.*)/)[1];
        const acquire = production('Surfaces.qml', 'acquirePairingKeyboard', '', surface);
        return {state, surface, acquire, later, focused: () => focused,
            ready: () => vm.runInContext(readySource, surface),
            visible: () => vm.runInContext(overlaySources.match(/visible: (.*)/)[1], surface)};
    }
    const focus = focusFixture();
    assert.equal(focus.ready(), true);
    focus.acquire();
    assert.equal(focus.later.length, 0, 'idle browsing does not remap');
    focus.state.services.pendingKind = 'bt-pair';
    assert.equal(focus.ready(), false, 'unfocused overlay is not ready');
    focus.surface.overlay.contentItem.Window.active = true;
    focus.acquire();
    assert.equal(focus.visible(), false, 'pairing unmaps overlay');
    assert.equal(focus.ready(), false, 'old focus during remap is not ready');
    focus.surface.overlay.contentItem.Window.active = false;
    focus.later.shift()();
    assert.equal(focus.visible(), true, 'next turn maps overlay');
    assert.equal(focus.focused(), 1, 'remap restores panel keyboard handler');
    assert.equal(focus.ready(), false, 'mapping alone does not prove compositor focus');
    focus.surface.overlay.contentItem.Window.active = true;
    assert.equal(focus.ready(), true);
    focus.state.systemOpen = false;
    focus.acquire();
    focus.state.systemOpen = true;
    focus.acquire();
    assert.equal(focus.visible(), false, 'reopening pending pairing remaps');
    focus.state.systemOpen = false;
    focus.later.shift()();
    assert.equal(focus.focused(), 1, 'closing during remap does not refocus settings');
    assert.match(sources['Surfaces.qml'], /onPairingKeyboardChanged: acquirePairingKeyboard\(\)/);
    assert.match(sources['Surfaces.qml'], /property: "pairingFocusManaged"\s+value: true/);

    function serviceFixture(result = () => false) {
        const focus = focusFixture(), calls = [];
        const state = vm.createContext({pendingCheck: null, pendingKind: '', pendingValue: null,
            pairingFocusManaged: true, pairingQueued: false, action: {busy: false},
            actionState: 'idle', attempts: 0, ticks: 25, confirmationTicks: 25,
            confirmation: {start() {}}, backend: {act(...args) { calls.push(args); return result; }}});
        state.service = state;
        focus.state.services = state;
        Object.defineProperty(state, 'pairingFocusReady', {get: focus.ready});
        state.act = production('SystemService.qml', 'act', 'kind,value', state);
        // Reuse the production block extractor for the timer's actual completion path.
        const timerSource = sources['SystemService.qml'].split('id: confirmation')[1];
        const timerFile = 'confirmation-handler';
        sources = {...sources, [timerFile]: timerSource.replace('onTriggered:', 'function tick()')};
        state.stop = () => {};
        state.tick = production(timerFile, 'tick', '', state);
        return {state, focus, calls};
    }
    for (const outcome of ['confirmed', 'failed', 'throws', 'cancel']) {
        const result = outcome === 'failed' ? 'unavailable' : outcome === 'throws' ?
            () => { throw Error('device disappeared'); } : () => true;
        const {state, focus, calls} = serviceFixture(result);
        assert.equal(state.act('bt-pair', 'device'), true);
        assert.equal(state.pendingKind, 'bt-pair');
        assert.equal(state.pairingQueued, true);
        assert.equal(calls.length, 0, 'no native request before focus handoff');
        state.tick();
        assert.equal(calls.length, 0, 'unfocused confirmation tick waits');
        focus.surface.overlay.contentItem.Window.active = true;
        focus.acquire();
        state.tick();
        assert.equal(calls.length, 0, 'remap waits even with stale active state');
        focus.surface.overlay.contentItem.Window.active = false;
        focus.later.shift()();
        state.tick();
        assert.equal(calls.length, 0, 'newly mapped but unfocused surface waits');
        if (outcome === 'cancel') {
            assert.equal(state.act('bt-cancel-pair', 'device'), true);
            assert.equal(state.pairingQueued, false);
        }
        focus.surface.overlay.contentItem.Window.active = true;
        state.tick();
        assert.equal(calls.length, outcome === 'cancel' ? 0 : 1);
        assert.equal(state.actionState, outcome === 'failed' ? 'unavailable' :
            outcome === 'throws' ? 'target_gone' : 'confirmed');
        assert.equal(state.pendingCheck, null);
        assert.equal(state.pendingKind, '');
        assert.equal(state.pairingQueued, false);
        assert.equal(keyboard(focus.state), 'exclusive', 'completion restores exclusivity');
    }
    const closed = serviceFixture(() => false);
    closed.state.act('bt-pair', 'device');
    closed.state.tick();
    assert.equal(closed.calls.length, 0);
    closed.focus.state.closeSystem();
    assert.equal(closed.focus.ready(), true, 'closing settings releases focus readiness');
    closed.state.tick(); closed.state.tick();
    assert.deepEqual(closed.calls, [['bt-pair', 'device']], 'closing starts the queued request once');
    assert.equal(closed.state.pairingQueued, false);
    assert.equal(keyboard(closed.focus.state), 'none');

    const timedOut = serviceFixture(() => true);
    timedOut.state.act('bt-pair', 'device');
    for (let tick = 0; tick < 299; tick++) timedOut.state.tick();
    assert.equal(timedOut.state.actionState, 'pending', 'focus wait retains the pairing deadline');
    assert.equal(timedOut.state.pairingQueued, true);
    timedOut.state.tick();
    assert.equal(timedOut.state.actionState, 'confirmation_timeout');
    assert.equal(timedOut.state.pendingCheck, null);
    assert.equal(timedOut.state.pendingKind, '');
    assert.equal(timedOut.state.pendingValue, null);
    assert.equal(timedOut.state.pairingQueued, false);
    assert.equal(timedOut.calls.length, 0, 'focus timeout never starts native pairing');
    assert.equal(keyboard(timedOut.focus.state), 'exclusive');

    const waiting = serviceFixture(() => false);
    waiting.state.act('bt-pair', 'device');
    waiting.focus.surface.overlay.contentItem.Window.active = true;
    waiting.state.tick(); waiting.state.tick(); waiting.state.tick();
    assert.deepEqual(waiting.calls, [['bt-pair', 'device']], 'confirmation polls never restart native pairing');
    for (const open of [false, true]) {
        for (const typedPin of [false, true]) {
            const state = fixture(open, typedPin);
            state.services.pendingKind = 'bt-pair';
            state.showNotification(1);
            assert.equal(keyboard(state), open ? 'on-demand' : 'none', `PIN keyboard, panel=${open}`);
            if (!typedPin) {
                assert.deepEqual(Array.from(state.peekIds), [1], `confirm code, panel=${open}`);
                assert.equal(state.peekTimer.interval, 15000);
                assert.ok(state.notes.entries[0].body.includes('001234'));
                state.showNotification(2);
                assert.deepEqual(Array.from(state.peekIds), [1], 'ordinary arrival must not replace code with a count');
                state.closeSystem();
                assert.deepEqual(Array.from(state.peekIds), [1], 'closing settings preserves request');
                assert.equal(keyboard(state), 'none');
            }
        }
    }
    for (const [dnd, presentation] of [[true, 'clear'], [false, 'covered'], [false, 'unknown']]) {
        const state = fixture(true);
        state.notes.dnd = dnd; state.presentationState = presentation;
        state.showNotification(1);
        assert.deepEqual(Array.from(state.peekIds), [1]);
    }
    for (const modal of ['launcherOpen', 'drawerOpen', 'privacyOpen']) {
        const state = fixture(true); state[modal] = true; state.showNotification(1);
        assert.deepEqual(Array.from(state.peekIds), [1], modal);
    }
    for (const open of [false, true]) {
        const successive = fixture(open);
        successive.notes.dnd = true; successive.presentationState = 'covered';
        successive.showNotification(1);
        const second = Object.assign(request(false), {id: 4, body: 'Confirm value: 005678'});
        successive.notes.entries.push(second);
        successive.showNotification(4);
        assert.deepEqual(Array.from(successive.peekIds), [4], 'second pairing request replaces the first preview');
        const view = vm.createContext({store: successive.notes, peekIds: successive.peekIds, hidePreviewBodies: false});
        const [preview] = binding('ClockBody.qml', 'readonly property var peekNotes', view);
        assert.equal(preview.body, second.body);
        assert.deepEqual(Array.from(preview.actions, action => action.id), ['confirm', 'deny']);
        assert.ok(successive.notes.entries[0].object, 'first request remains available in the drawer');
        successive.endPeek();
        assert.deepEqual(Array.from(successive.peekIds), [4], 'second request survives the peek timeout');
    }
    const retained = fixture(false);
    retained.showNotification(1);
    retained.notes.entries.push({id: 3, critical: true, batteryWarning: true});
    retained.showNotification(3);
    assert.deepEqual(Array.from(retained.peekIds), [1], 'battery leaves the pairing actions visible');
    retained.endPeek(); // shared timeout and dismissal path
    assert.deepEqual(Array.from(retained.peekIds), [1], 'peek timeout cannot dismiss a live request');
    assert.equal(retained.clockExpansion, 1, 'timeout keeps request expanded');
    retained.closeClock();
    assert.deepEqual(Array.from(retained.peekIds), [1], 'closing panels preserves a live request');
    assert.equal(retained.clockExpansion, 1, 'preserved request remains expanded');
    retained.retainCriticalPeek();
    assert.deepEqual(Array.from(retained.peekIds), [1], 'presentation and DND preserve request');
    // Execute the production entry-change handler: expired copies remain in history,
    // while answered requests are removed entirely by NotificationStore.
    sources = {...sources, 'entry-handler': sources['ShellScene.qml'].split('onEntriesChanged:')[1]
        .replace(/^\s*{/, 'function entriesChanged() {')};
    const changed = production('entry-handler', 'entriesChanged', '', retained);
    retained.notes.entries[0].object = null;
    changed();
    assert.deepEqual(Array.from(retained.peekIds), [], 'expired request leaves the peek');
    assert.equal(retained.clockExpansion, 0);
    retained.notes.entries[0] = request(false);
    retained.showNotification(1);
    retained.notes.entries = retained.notes.entries.filter(note => note.id !== 1);
    changed();
    assert.deepEqual(Array.from(retained.peekIds), [], 'answered request leaves the peek');
    retained.showNotification(3);
    assert.deepEqual(Array.from(retained.peekIds), [3], 'battery still peeks without a pending request');
    retained.endPeek();
    assert.deepEqual(Array.from(retained.peekIds), [], 'battery keeps its bounded timeout');
    const ordinary = fixture(true); ordinary.showNotification(2);
    assert.deepEqual(Array.from(ordinary.peekIds), [], 'ordinary modal gate');
    const overview = fixture(true); overview.niri.overviewOpen = true; overview.showNotification(1);
    assert.deepEqual(Array.from(overview.peekIds), [1], 'overview gate');
    const overlapping = fixture(true); overlapping.showNotification(1);
    const clock = sources['ShellScene.qml'].split('id: clockPanel')[1];
    assert.ok(vm.runInContext(clock.match(/\bz: (.*)/)[1], overlapping) > 35, 'preview stacks above open panels');
    overlapping.drawerOpen = true;
    assert.equal(vm.runInContext(clock.match(/\bopened: (.*)/)[1], overlapping), false, 'drawer keeps pairing actions in preview');
    assert.equal(binding('ShellScene.qml', 'readonly property real headTarget', overlapping), 0);
    overlapping.drawerOpen = false;
    const toggle = production('ShellScene.qml', 'openSystem', 'page', overlapping);
    overlapping.closeAll = () => { throw Error('closing settings must preserve the preview'); };
    toggle('bt');
    assert.equal(overlapping.systemOpen, false);
    assert.deepEqual(Array.from(overlapping.peekIds), [1]);
    assert.match(sources['ShellScene.qml'], /Keys.onEscapePressed: scene.closeSystem\(\)/);
    const outside = fixture(true); outside.showNotification(1);
    outside.bar = {logo: {x: 0, y: 0, width: 40, height: 40}};
    outside.launcherPresent = false; outside.dockPressAt = () => false;
    outside.closeAll = () => { throw Error('outside press must preserve request'); };
    production('ShellScene.qml', 'outsidePressButton', 'x,y,button', outside)(800, 700, 1);
    assert.equal(outside.systemOpen, false);
    assert.deepEqual(Array.from(outside.peekIds), [1]);

    assert.match(clock, /hidePreviewBodies: scene.privacyCast/);
    assert.match(sources['ClockPanel.qml'], /hidePreviewBodies: panel.hidePreviewBodies/);
    for (const casts of [[], [{is_active: true}], [{is_active: false}]]) {
        const state = fixture(false); state.niri.casts = casts;
        const sharing = binding('ShellScene.qml', 'readonly property bool privacyCast', state);
        const view = vm.createContext({store: {entries: [request(false)]}, peekIds: [1], hidePreviewBodies: sharing});
        const [note] = binding('ClockBody.qml', 'readonly property var peekNotes', view);
        assert.equal(note.body, casts.length ? '' : request(false).body, 'active/paused sharing redacts body');
        assert.equal(note.actions.length, casts.length ? 0 : 2, 'redacted code cannot be confirmed blindly');
        assert.equal(view.store.entries[0].body, request(false).body, 'drawer retains original request');
    }
}
run(original);
if (process.argv.includes('--mutations')) {
    const mutations = [
        ['modal gate', 'ShellScene.qml', 'if (!critical && (niri.overviewOpen || modalOpen', 'if ((niri.overviewOpen || modalOpen'],
        ['battery replacement', 'ShellScene.qml', '(pairingPeekOpen && !pairing)', 'false'],
        ['second pairing request', 'ShellScene.qml', '(pairingPeekOpen && !pairing)', 'pairingPeekOpen'],
        ['pairing lifetime', 'ShellScene.qml', 'if (pairingPeekOpen)\n            return;', 'if (false)\n            return;'],
        ['PIN keyboard', 'Surfaces.qml', 'surfaces.pairingKeyboard ? WlrKeyboardFocus.OnDemand : WlrKeyboardFocus.Exclusive', 'surfaces.pairingKeyboard ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.Exclusive'],
        ['idle Bluetooth exclusivity', 'Surfaces.qml', 'surfaces.pairingKeyboard ? WlrKeyboardFocus.OnDemand', 'surfaces.controller.systemPage === "bt" ? WlrKeyboardFocus.OnDemand'],
        ['remap visibility', 'Surfaces.qml', '&& !surfaces.pairingRemap', '&& true'],
        ['active readiness', 'Surfaces.qml', '&& overlay.contentItem.Window.active', '&& true'],
        ['remap readiness', 'Surfaces.qml', '!surfaces.pairingRemap && overlay.contentItem.Window.active', 'overlay.contentItem.Window.active'],
        ['deferred native request', 'SystemService.qml', 'if (!service.pairingFocusReady)', 'if (false)'],
        ['preview stacking', 'ShellScene.qml', 'z: scene.batteryPeekOpen || scene.pairingPeekOpen ? 40 : 0', 'z: 0'],
        ['panel closing', 'ShellScene.qml', 'closeTimer.restart();\n    }\n    Behavior on systemExpansion', 'peekIds = [];\n        closeTimer.restart();\n    }\n    Behavior on systemExpansion'],
        ['sharing privacy', 'ClockBody.qml', 'hidePreviewBodies ? Object.assign', 'false ? Object.assign']
    ];
    for (const [name, file, before, after] of mutations) {
        assert.ok(original[file].includes(before), name + ' mutation anchor');
        const changed = {...original, [file]: original[file].replace(before, after)};
        assert.throws(() => run(changed), undefined, name + ' mutation survived');
        console.log('Rejected mutation: ' + name);
    }
}
console.log('Pairing: idle exclusivity, focus remap/readiness, deferred native requests, cancellation/errors, notification gates and sharing: PASS');
