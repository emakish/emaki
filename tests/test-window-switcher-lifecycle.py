#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Socket-free Qt keyboard/cache regression; --source-root accepts an exported baseline.

--load pins the probe and two CPU workers to one available CPU. This tests Qt event ordering,
not compositor delivery or the window chosen by a real compositor after keyboard release.
"""
from runtime_fixture import runtime_path
import argparse
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
HEADER = '\n'.join((ROOT / 'tests/fixtures/WindowSwitcherLifecycle.qml').read_text().splitlines()[:2]) + '\n'


def check_focus_route(source_root):
    """Run the production send function with a socket adapter while the action queue is busy."""
    source = (source_root / 'shell/NiriService.qml').read_text()
    match = re.search(r'    function focusWindowImmediate\(id: double\): int \{(.*?)\n    \}', source, re.S)
    assert match, 'missing persistent immediate focus route'
    scene = (source_root / 'shell/ShellScene.qml').read_text()
    entry = re.search(r'    function switchWindows\(delta: int, sameApp: bool, sent: double\): bool \{(.*?)\n    \}', scene, re.S)
    assert entry, 'missing switcher entry point'
    script = """
const assert = require('node:assert/strict');
let immediateFocusReady = true, windows = [{id: 7}], focusSerial = 0, focusReplies = [];
let pendingRequest = ['switch-layout', '--index', '1'], closeQueue = [9];
const action = {running: true}, writes = [];
const focusSocket = {write: line => writes.push(line), flush: () => {}};
function request() { throw Error('focus entered queued helper'); }
function focusWindowImmediate(id) { BODY }
assert.equal(focusWindowImmediate(7), 1);
assert.equal(focusWindowImmediate(7), 2);
assert.deepEqual(writes.map(JSON.parse), [{Action:{FocusWindow:{id:7}}}, {Action:{FocusWindow:{id:7}}}]);
assert.deepEqual(focusReplies, [1, 2]);
assert.equal(action.running, true);
assert.deepEqual(pendingRequest, ['switch-layout', '--index', '1']);
assert.deepEqual(closeQueue, [9]);
assert.equal(focusWindowImmediate(8), -1);
assert.equal(focusWindowImmediate(2 ** 53), -1);
immediateFocusReady = false;
assert.equal(focusWindowImmediate(7), -1);
assert.equal(writes.length, 2);
immediateFocusReady = true;
focusReplies = Array(32).fill(1);
assert.equal(focusWindowImmediate(7), -1);
assert.equal(writes.length, 2);
let switcher = {closedAt: 0}, switcherOpen = false, enabled = true, focusedOutput = true;
let output = {}, headless = false, authenticationPresent = false, firstRunPresent = false;
let niri = {connected: true, immediateFocusReady: false};
function switchWindows(delta, sameApp, sent) { ENTRY }
assert.equal(switchWindows(1, false, 0), false);
console.log('PASS persistent focus writes bypass busy action queue; invalid/unavailable requests rejected');
""".replace('BODY', match.group(1)).replace('ENTRY', entry.group(1))
    subprocess.run(['node', '-e', script], check=True)



def check_reacquisition(source_root):
    """Run the real surface handshake with reply and Wayland leave delivered in either order."""
    source = (source_root / 'shell/Surfaces.qml').read_text()

    def body(name, indent=4):
        padding = ' ' * indent
        match = re.search(padding + r'function ' + name + r'\([^)]*\): void \{(.*?)\n' + padding + r'\}', source, re.S)
        assert match, f'missing surface handshake {name}'
        return match.group(1)

    script = """
const assert = require('node:assert/strict');
let armedLayer, pendingLayer, restartingSwitcher, switcherPreviousTicket, switcherPreviousCompleted;
let active, acquisitions, closes;
const controller = {niri:{layerFocusSupported:true, focusReplies:[]}, switcher:{armed:true, cancel:() => {controller.switcher.armed = false;}},
                    switcherOpen:true, focusedOutput:true, closeAll:() => ++closes};
const surfaces = {
 get restartingSwitcher() {return restartingSwitcher;},
 set restartingSwitcher(value) {restartingSwitcher = value;},
 controller,
 get switcherPreviousTicket() {return switcherPreviousTicket;},
 set switcherPreviousCompleted(value) {switcherPreviousCompleted = value;},
 resumeSwitcher:() => resumeSwitcher()
};
function layerActive() {return active;}
function acquireKeyboard(layer) {++acquisitions; ACQUIRE}
function restartSwitcher(ticket) {RESTART}
function resumeSwitcher() {RESUME}
function keyboardActiveChanged(layer, active) {ACTIVE}
function complete(ticket, successful = true) {COMPLETE}
function reset() {
 armedLayer = 'panel'; pendingLayer = ''; restartingSwitcher = false;
 switcherPreviousTicket = -1; switcherPreviousCompleted = false;
 active = true; acquisitions = 0; closes = 0;
 controller.switcherOpen = true; controller.switcher.armed = true;
 controller.niri.focusReplies = [11];
}
for (const first of ['reply', 'leave']) {
 reset(); restartSwitcher(11);
 assert.equal(armedLayer, ''); assert.equal(acquisitions, 0);
 complete(10); assert.equal(acquisitions, 0);
 if (first === 'reply') {complete(11);} else {active = false; keyboardActiveChanged('panel', false);}
 assert.equal(acquisitions, 0); assert.equal(restartingSwitcher, true); assert.equal(closes, 0);
 if (first === 'reply') {active = false; keyboardActiveChanged('panel', false);} else {complete(11);}
 assert.equal(acquisitions, 1); assert.equal(pendingLayer, 'panel');
 assert.equal(restartingSwitcher, false); assert.equal(closes, 0);
}
for (const cancelled of ['closed', 'timeout']) {
 reset(); restartSwitcher(11); active = false;
 if (cancelled === 'closed') controller.switcherOpen = false;
 else controller.switcher.armed = false;
 complete(11);
 assert.equal(acquisitions, 0); assert.equal(restartingSwitcher, false);
}
reset(); restartSwitcher(11); active = false; complete(11, false);
assert.equal(acquisitions, 0); assert.equal(restartingSwitcher, false); assert.equal(controller.switcher.armed, false);
reset(); active = false; controller.niri.focusReplies = []; restartSwitcher(11);
assert.equal(acquisitions, 1);
console.log('PASS pending chord re-acquisition: reply/leave orders, stale reply, close, timeout');
"""
    for token, name, indent in [('ACQUIRE', 'acquireKeyboard', 4), ('RESTART', 'restartSwitcher', 4),
                                ('RESUME', 'resumeSwitcher', 4), ('ACTIVE', 'keyboardActiveChanged', 4),
                                ('COMPLETE', 'onImmediateFocusCompleted', 8)]:
        script = script.replace(token, body(name, indent))
    subprocess.run(['node', '-e', script], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    parser.add_argument('--load', action='store_true')
    args = parser.parse_args()
    check_focus_route(args.source_root)
    check_reacquisition(args.source_root)
    (ROOT / '.cache').mkdir(exist_ok=True)
    workers = []
    affinity = None
    if args.load:
        cpu = min(os.sched_getaffinity(0))
        affinity = lambda: os.sched_setaffinity(0, {cpu})
        print(f'probe and two CPU workers share CPU {cpu}', flush=True)
    try:
        with tempfile.TemporaryDirectory(prefix='switcher-lifecycle-', dir=ROOT / '.cache') as directory:
            base = Path(directory)
            qml = base / 'qml'
            shutil.copytree(args.source_root / 'shell', qml)
            # Platform is generated by `make paths` and absent from an exported baseline.
            if not (qml / 'Platform.qml').exists():
                shutil.copyfile(ROOT / 'shell/Platform.qml', qml / 'Platform.qml')
            fixtures = {
                'NiriService.qml': '''import QtQuick
QtObject {
 property var windows: [{id: 1, is_focused: true, workspace_id: 1}, {id: 2, is_focused: false, workspace_id: 1}]
 property var workspaces: []
 property bool overviewOpen: false
}
''',
                'WindowLabels.qml': '''import QtQuick
QtObject { property var values: ({}); property var focusTimes: ({}); property var positions: ({}) }
''',
                'AppIdentity.qml': '''import QtQuick
QtObject { function resolve(appId: string): var { return null; } }
''',
            }
            for name, source in fixtures.items():
                (qml / name).write_text(HEADER + source)
            shutil.copyfile(ROOT / 'tests/fixtures/WindowSwitcherLifecycle.qml', qml / 'shell.qml')
            env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                       QML_DISABLE_DISK_CACHE='1', NIRI_SOCKET='',
                       DBUS_SESSION_BUS_ADDRESS='unix:path=/nonexistent')
            for name in ('HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME',
                         'XDG_CACHE_HOME', 'XDG_RUNTIME_DIR', 'TMPDIR'):
                path = base / name.lower()
                path.mkdir(mode=0o700)
                env[name] = str(path)
            env['XDG_RUNTIME_DIR'] = str(runtime_path(base))
            assert len(env['XDG_RUNTIME_DIR'] + '/quickshell/by-id/1234567/ipc.sock') < 108
            for name in ('DISPLAY', 'WAYLAND_DISPLAY'):
                env.pop(name, None)
            if args.load:
                for _ in range(2):
                    workers.append(subprocess.Popen([sys.executable, '-c', 'while True: pass'],
                                                    preexec_fn=affinity))
            result = subprocess.run(['qs', '-p', str(qml), '--no-color'], env=env,
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    timeout=30, preexec_fn=affinity)
            print(result.stdout, end='')
            assert result.returncode == 0 and 'PASS switcher lifecycle' in result.stdout and 'FAIL ' not in result.stdout
    finally:
        for worker in workers:
            worker.terminate()
        for worker in workers:
            worker.wait(timeout=5)


if __name__ == '__main__':
    main()
