// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
// Execute production functions and bindings; no desktop bus or real device is used.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';

const files = ['ShellScene.qml', 'ShellOutputs.qml', 'Surfaces.qml', 'ClockPanel.qml', 'ClockBody.qml', 'NotificationStore.qml', 'SystemService.qml'];
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
            niri: {overviewOpen: false, casts: [], layerFocusSupported: true}, presentationState: 'clear',
            keyboardSurface: '', systemOpen: open, systemPage: 'bt', systemExpansion: open ? 1 : 0,
            launcherOpen: false, drawerOpen: false, privacyOpen: false, shortcutsOpen: false,
            Date: {now: () => 1000}, peekIds: [], peekCooldown: 0, peekStarted: 0,
            Metrics: {morphMs: 350}, peekTimer: {restart() {}, stop() {}}, closeTimer: {restart() {}}, systemBody: {rows: [], reset() {}}, services: {pendingKind: ''},
            enabled: true, output: {}, focusedOutput: true,
            WlrKeyboardFocus: {None: 'none', OnDemand: 'on-demand', Exclusive: 'exclusive'}});
        state.scene = state;
        state.surfaces = {controller: state, pendingLayer: ''};
        const surface = vm.createContext({controller: state});
        Object.defineProperty(state.surfaces, 'pairingKeyboard', {get() {
            return binding('Surfaces.qml', 'readonly property bool pairingKeyboard', surface);
        }});
        Object.defineProperty(state.surfaces, 'keyboardPanel', {get() {
            return binding('Surfaces.qml', 'readonly property bool keyboardPanel', surface);
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
    // Execute the production acquisition and acknowledgement against retained windows.
    function focusFixture() {
        const state = fixture(true), later = [];
        let focused = 0;
        state.systemPanel = {forceActiveFocus() { focused++; }, takeFocus() { focused++; }};
        state.bar = {takeFocus() { focused++; }};
        state.barKeyboardActive = false;
        state.barPolicy = {barVisible: true, fullscreen: false};
        state.dockStore = {on: true};
        state.dockPolicy = {dockVisible: true};
        state.dock = {keyboardActive: false, takeFocus() { focused++; }};
        state.closeAll = () => {
            state.barKeyboardActive = false;
            state.dock.keyboardActive = false;
            state.keyboardSurface = '';
            state.systemOpen = false;
        };
        const window = () => ({contentItem: {Window: {active: false}}});
        const surface = vm.createContext({controller: state, pendingLayer: '', armedLayer: '',
            top: window(), dockWindow: window(), overlay: window(),
            WlrKeyboardFocus: state.WlrKeyboardFocus, Qt: {callLater: fn => later.push(fn)}});
        surface.surfaces = surface;
        for (const property of ['pairingKeyboard', 'keyboardPanel', 'pairingReady'])
            Object.defineProperty(surface, property, {get() {
                return binding('Surfaces.qml', 'readonly property bool ' + property, surface);
            }});
        state.surfaces = surface;
        for (const [name, parameters] of [['layerActive', 'layer'], ['acquireKeyboard', 'layer'], ['releaseKeyboard', 'layer'],
            ['keyboardActiveChanged', 'layer,active'], ['acquirePairingKeyboard', '']])
            surface[name] = production('Surfaces.qml', name, parameters, surface);
        const outputs = vm.createContext({headless: false,
            instances: [{surfaces: {pairingKeyboard: false, pairingReady: true}}, {surfaces: surface}]});
        outputs.outputs = outputs;
        Object.defineProperty(outputs, 'pairingOwner', {get() {
            return binding('ShellOutputs.qml', 'readonly property var pairingOwner', outputs);
        }});
        function sharedBinding(property) {
            const block = sources['ShellOutputs.qml'].match(new RegExp('Binding \\{\\s+target: shared.services\\s+property: "' + property + '"\\s+value: ([^\\n]+)'));
            assert.ok(block, 'shared service binding ' + property);
            return vm.runInContext(block[1], outputs);
        }
        const layerSources = {bar: sources['Surfaces.qml'].split('id: top')[1],
            dock: sources['Surfaces.qml'].split('id: dockWindow')[1], panel: overlaySources};
        return {state, surface, outputs, acquire: surface.acquirePairingKeyboard, later, focused: () => focused,
            ready: () => sharedBinding('pairingFocusReady'),
            managed: () => sharedBinding('pairingFocusManaged'),
            policy: layer => vm.runInContext(layerSources[layer].match(/WlrLayershell.keyboardFocus: (.*)/)[1], surface),
            visible: layer => vm.runInContext(layerSources[layer].match(/visible: (.*)/)[1], surface)};
    }
    function assertRetained(focus) {
        for (const layer of ['bar', 'dock', 'panel'])
            assert.equal(focus.visible(layer), true, layer + ' stays mapped throughout acquisition');
    }
    for (const layer of ['bar', 'dock', 'panel']) {
        const focus = focusFixture();
        if (layer === 'bar') focus.state.barKeyboardActive = true;
        else if (layer === 'dock') focus.state.dock.keyboardActive = true;
        else focus.state.keyboardSurface = 'system';
        assertRetained(focus);
        focus.surface.acquireKeyboard(layer);
        assertRetained(focus);
        assert.equal(focus.policy(layer), 'exclusive', layer + ' requests compositor focus');
        focus.surface.keyboardActiveChanged(layer, false);
        assertRetained(focus);
        assert.equal(focus.surface.pendingLayer, layer, 'pre-activation loss does not cancel acquisition');
        focus.surface[{bar: 'top', dock: 'dockWindow', panel: 'overlay'}[layer]].contentItem.Window.active = true;
        focus.surface.keyboardActiveChanged(layer, true);
        assertRetained(focus);
        assert.equal(focus.surface.pendingLayer, '', layer + ' acknowledgement clears request');
        assert.equal(focus.policy(layer), 'on-demand', layer + ' yields exclusivity after acknowledgement');
        focus.surface.keyboardActiveChanged(layer, false);
        assertRetained(focus);
        assert.equal(focus.policy(layer), 'none', layer + ' releases ownership after focus loss');
    }
    const legacy = focusFixture();
    legacy.state.niri.layerFocusSupported = false;
    legacy.state.services.pendingKind = 'bt-pair';
    legacy.acquire();
    assert.equal(legacy.surface.pendingLayer, '', 'older compositor never arms an exclusive request');
    assert.equal(legacy.policy('panel'), 'on-demand', 'older pairing retains its native-dialog policy');
    legacy.state.services.pendingKind = '';
    legacy.state.keyboardSurface = 'sound';
    legacy.surface.acquireKeyboard('panel');
    legacy.surface.keyboardActiveChanged('panel', true);
    assert.equal(legacy.policy('panel'), 'exclusive', 'older ordinary panel retains its original policy');
    for (const layer of ['bar', 'dock']) {
        legacy.state.barKeyboardActive = true;
        legacy.state.dock.keyboardActive = true;
        legacy.surface.acquireKeyboard(layer);
        assert.equal(legacy.policy(layer), 'none', 'older compositor never captures ' + layer);
    }
    assertRetained(legacy);
    const focus = focusFixture();
    assert.equal(focus.ready(), true);
    assert.equal(focus.managed(), true, 'live outputs manage shared readiness');
    focus.outputs.headless = true;
    assert.equal(focus.managed(), false, 'headless outputs do not await native focus');
    focus.outputs.headless = false;
    focus.acquire();
    assert.equal(focus.surface.pendingLayer, '', 'idle browsing does not request focus');
    focus.state.services.pendingKind = 'bt-pair';
    assert.equal(focus.ready(), false, 'unfocused overlay is not ready');
    focus.acquire();
    assertRetained(focus);
    assert.equal(focus.policy('panel'), 'exclusive');
    focus.surface.overlay.contentItem.Window.active = true;
    assert.equal(focus.ready(), false, 'active window waits for acknowledgement');
    focus.surface.keyboardActiveChanged('panel', true);
    assert.equal(focus.policy('panel'), 'on-demand');
    assert.equal(focus.ready(), true);
    assert.equal(focus.focused(), 1, 'acknowledgement restores panel keyboard handler');
    assertRetained(focus);
    focus.surface.overlay.contentItem.Window.active = false;
    focus.surface.keyboardActiveChanged('panel', false);
    assert.equal(focus.state.systemOpen, true, 'native prompt keeps pairing panel open');
    assert.equal(focus.ready(), false, 'native prompt owns focus');
    assertRetained(focus);
    focus.surface.overlay.contentItem.Window.active = true;
    focus.acquire();
    assert.equal(focus.surface.pendingLayer, '', 'already-active layer acknowledges immediately');
    assert.equal(focus.ready(), true);
    assert.equal(focus.later.length, 0, 'acquisition never schedules a remap');
    focus.outputs.instances = [];
    assert.equal(focus.ready(), true, 'removed pairing owner releases readiness');
    assert.match(sources['Surfaces.qml'], /onPairingKeyboardChanged: acquirePairingKeyboard\(\)/);
    assert.match(sources['Surfaces.qml'], /property: "pairingFocusManaged"\s+value: true/);

    function serviceFixture(result = () => false) {
        const focus = focusFixture(), calls = [];
        const state = vm.createContext({pendingCheck: null, pendingKind: '', pendingValue: null,
            pairingFocusManaged: true, pairingQueued: false, action: {busy: false},
            wifiRestartRunning: false, wifiRestart: {running: false},
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
    for (const flag of ['authorization', 'process']) {
        for (const kind of ['volume', 'mute', 'mic', 'brightness', 'profile', 'bt-power', 'lock', 'session']) {
            const {state, calls} = serviceFixture(() => true);
            state.helpersEnabled = true;
            state.action.start = request => calls.push(request);
            state.wifiRestartRunning = flag === 'authorization';
            state.wifiRestart.running = flag === 'process';
            for (const wifiKind of ['wifi-restart', 'wifi-power', 'wifi-connect', 'hidden']) {
                assert.equal(state.act(wifiKind, null), false, wifiKind + ' remains gated during ' + flag);
                assert.equal(state.actionState, 'busy');
            }
            assert.equal(state.act(kind, kind === 'session' ? 'suspend' : 42), true,
                kind + ' remains available during ' + flag);
            assert.equal(calls.length, 1, kind + ' reaches its isolated backend');
        }
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
        focus.acquire();
        assertRetained(focus);
        state.tick();
        assert.equal(calls.length, 0, 'exclusive request alone does not launch native pairing');
        focus.surface.overlay.contentItem.Window.active = true;
        state.tick();
        assert.equal(calls.length, 0, 'active state still waits for focus acknowledgement');
        if (outcome === 'cancel') {
            assert.equal(state.act('bt-cancel-pair', 'device'), true);
            assert.equal(state.pairingQueued, false);
        }
        focus.surface.keyboardActiveChanged('panel', true);
        assertRetained(focus);
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
    waiting.focus.acquire();
    waiting.focus.surface.overlay.contentItem.Window.active = true;
    waiting.focus.surface.keyboardActiveChanged('panel', true);
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
    sources = {...sources, 'entry-handler': sources['ShellScene.qml'].split('function onEntriesChanged(): void')[1]
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
        ...['bar', 'dock', 'panel'].map(layer => [layer + ' stuck exclusive', 'Surfaces.qml',
            'surfaces.pendingLayer === "' + layer + '" ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.OnDemand',
            'surfaces.pendingLayer === "' + layer + '" ? WlrKeyboardFocus.Exclusive : WlrKeyboardFocus.Exclusive']),
        ['idle Bluetooth exclusivity', 'Surfaces.qml', 'surfaces.pairingKeyboard || surfaces.keyboardPanel ?', 'surfaces.controller.systemPage === "bt" ?'],
        ...['bar', 'dock', 'panel'].map(layer => {
            const id = {bar: 'top', dock: 'dockWindow', panel: 'overlay'}[layer];
            const scope = original['Surfaces.qml'].split('id: ' + id)[1];
            const prefix = 'id: ' + id + scope.slice(0, scope.indexOf('visible: '));
            const visible = scope.match(/visible: (.*)/)[0];
            return [layer + ' remap', 'Surfaces.qml', prefix + visible,
                prefix + visible + ' && surfaces.pendingLayer !== "' + layer + '"'];
        }),
        ['active readiness', 'Surfaces.qml', '&& overlay.contentItem.Window.active', '&& true'],
        ['pending readiness', 'Surfaces.qml', 'surfaces.pendingLayer !== "panel" && overlay.contentItem.Window.active', 'overlay.contentItem.Window.active'],
        ['missing acknowledgement', 'Surfaces.qml', 'if (pendingLayer === layer)\n                pendingLayer = "";', 'if (false)\n                pendingLayer = "";'],
        ['missing exclusive request', 'Surfaces.qml', 'pendingLayer = layer;', 'pendingLayer = "";'],
        ['shared readiness', 'ShellOutputs.qml', 'outputs.pairingOwner?.surfaces.pairingReady ?? true', 'true'],
        ['shared owner selection', 'ShellOutputs.qml', 'instances.find(i => i.surfaces?.pairingKeyboard) ?? null', 'instances[0] ?? null'],
        ['missing pairing owner', 'ShellOutputs.qml', 'outputs.pairingOwner?.surfaces.pairingReady ?? true', 'outputs.pairingOwner?.surfaces.pairingReady ?? false'],
        ['shared focus management', 'ShellOutputs.qml', 'value: !outputs.headless', 'value: false'],
        ['deferred native request', 'SystemService.qml', 'if (!service.pairingFocusReady)', 'if (false)'],
        ['authorization blocks unrelated actions', 'SystemService.qml',
            '(wifiAction && (wifiRestartRunning || wifiRestart.running))',
            '(wifiRestartRunning || wifiRestart.running)'],
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
console.log('Pairing: idle exclusivity, persistent focus acquisition/readiness, deferred native requests, cancellation/errors, notification gates and sharing: PASS');
