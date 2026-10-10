#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Production output delegates, isolated offscreen rendering and hotplug mutations."""
import os
import importlib.util
import json
import math
from pathlib import Path
import shutil
import subprocess
import threading
import queue
import time
from urllib.parse import unquote, urlparse

from PIL import Image
from runtime_fixture import runtime_path
import reaper

reaper.guard()
ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / '.cache/evidence/n2/outputs'
DEST.mkdir(parents=True, exist_ok=True)
spec = importlib.util.spec_from_file_location('output_measure', ROOT / 'tests/measure-shell.py')
measure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure)
QML = r'''
import QtQuick
import QtQuick.Window
import Quickshell
ShellRoot {
    id: root
    property var a: ({name: "Laptop", width: 1280, height: 800, devicePixelRatio: 1})
    property var b: ({name: "External", width: 1440, height: 900, devicePixelRatio: 1.25})
    property var c: ({name: "Portrait", width: 800, height: 1280, devicePixelRatio: 1.5})
    property var laptop: null
    property var windows: []
    property int step: 0
    property int pending: 0
    property int settle: 0
    property int sampleCount: 0
    property int destroyed: 0
    property int created: 0
    property var destructionState: null
    property var managed: ({})
    function applySettings(auto, overview, dock, hide) {
        managed = {"bar.autohide": auto, "bar.overview_workspaces": overview,
            "dock.on": dock, "dock.auto_hide": hide, "bar.clock_24_hour": overview};
        SettingsBridge.values = Object.keys(managed).map(key => ({key: key, value: managed[key]}));
    }
    function check(value, message) {
        if (!value) {
            console.error("OUTPUTS_FAILED " + message);
            Qt.exit(1);
            throw new Error(message);
        }
    }
    NiriService { id: niri; binary: "" }
    ShellOutputs {
        id: outputs
        niri: niri
        screens: [root.a]
        headless: true
        borderMode: "soft"
        reservedSpace: 0
        testWidth: 0
        testHeight: 0
    }
    Component { id: windowFactory; Window { visible: true; color: "#39302e" } }
    Component {
        id: lifetimeProbe
        QtObject {
            property var window
            Component.onDestruction: {
                root.destroyed++;
                root.windows = root.windows.filter(existing => existing !== window);
                window.destroy();
            }
        }
    }
    function publish(focus) {
        const workspaces = {};
        const geometry = {};
        [a,b,c].forEach((screen, index) => {
            geometry[screen.name] = {logical: {width: screen.width, height: screen.height}};
            for (let i = 0; i <= index; ++i) {
                const id = index * 10 + i + 1;
                workspaces[id] = {id: id, idx: i + 1, output: screen.name, is_active: i === 0};
            }
        });
        niri.model = {windows: {}, workspaces: workspaces, outputs: geometry, focused_output: focus,
            overview_open: false, keyboard_layouts: {names: ["English (US)"], current_idx: 0}};
        niri.connection = "connected";
    }
    function checkTransferredPreviews() {
        const source = laptop;
        const destination = scenes().find(scene => scene.outputName === b.name);
        for (const rule of ["clear", "fullscreen", "panel", "overview", "dnd"]) {
            publish(a.name);
            source.peekCooldown = 0;
            const ordinary = source.notifications.local("Fixture", "Ordinary preview", rule);
            const state = source.exportTransient();
            check(state.ids.includes(ordinary), "source ordinary preview " + rule);
            const critical = source.notifications.localNotice("Fixture", "Critical preview", rule, true, false);
            const urgentState = source.exportTransient();
            if (rule === "fullscreen") {
                const model = Object.assign({}, niri.model);
                model.windows = {77: {id: 77, workspace_id: 11,
                    layout: {tile_size: [b.width, b.height]}}};
                niri.model = model;
                check(destination.presentationState === "covered", "fullscreen destination fixture");
            } else if (rule === "panel") {
                destination.openDrawer();
                check(destination.modalOpen, "open panel destination fixture");
            } else if (rule === "overview") {
                niri.model = Object.assign({}, niri.model, {overview_open: true});
                check(niri.overviewOpen, "overview destination fixture");
            } else if (rule === "dnd") {
                source.notifications.dnd = true;
            }
            destination.importTransient(state);
            check(destination.peekOpen === (rule === "clear"), "import ordinary preview policy " + rule);
            check(destination.peekStarted === state.started && destination.peekUntil === state.until,
                "ordinary transfer preserves deadlines " + rule);
            destination.exportTransient();
            urgentState.ids = [ordinary, critical];
            destination.importTransient(urgentState);
            check(destination.peekIds.includes(critical), "import retains critical preview " + rule);
            check(destination.peekIds.includes(ordinary) === (rule === "clear"),
                "import filters mixed previews " + rule);
            check(destination.peekStarted === urgentState.started && destination.peekUntil === urgentState.until,
                "critical transfer preserves deadlines " + rule);
            destination.exportTransient();
            destination.closeAll();
            source.notifications.dnd = false;
            source.notifications.dismiss([ordinary, critical]);
        }
        publish(a.name);
        source.peekCooldown = 0;
    }
    function checkNoKeyboard(message) {
        check(scenes().every(scene => scene.keyboardSurface === ""
            && !scene.barKeyboardActive && !scene.dock.keyboardActive && !scene.systemOpen), message);
    }
    function checkKeyboardRouting() {
        for (const name of [a.name, b.name, c.name]) {
            publish(name);
            const owner = outputs.activeScene;
            check(owner && owner.outputName === name, "keyboard active output " + name);
            for (const surface of ["bar", "dock", "sound"]) {
                check(owner.openKeyboard(surface), "keyboard entry " + surface + " on " + name);
                check(owner.keyboardSurface === surface, "keyboard owner " + surface);
                scenes().filter(scene => scene !== owner).forEach(scene => {
                    check(!scene.openKeyboard(surface), "inactive output refuses " + surface);
                    check(scene.keyboardSurface === "", "inactive output retains no keyboard request");
                });
                // Change output in the same turn, before deferred activation can run.
                publish(name === a.name ? b.name : a.name);
                checkNoKeyboard("focus change cancels pending " + surface);
                publish(name);
            }
        }
        publish(a.name);
    }
    function scenes() { return outputs.instances.map(instance => instance.scene); }
    function inspect(count) {
        const list = scenes();
        check(list.length === count, "delegate count " + count + " got " + list.length);
        check(list.filter(scene => scene.focusedOutput).length === (count ? 1 : 0), "single focused owner " + JSON.stringify(list.map(scene => [scene.outputName, scene.focusedOutput])) + " active=" + outputs.activeScene + " focused=" + JSON.stringify(outputs.focusedScreen));
        list.forEach(scene => {
            check(scene.barPolicy.autoHide === managed["bar.autohide"]
                && scene.barPolicy.overviewWorkspaces === managed["bar.overview_workspaces"], "managed bar settings");
            check(scene.dockStore.on === managed["dock.on"]
                && scene.dockStore.autoHide === managed["dock.auto_hide"], "managed dock settings");
            scene.bar.dateTime = new Date(2026, 0, 1, 15, 7);
            check(scene.bar.clockTime === (managed["bar.clock_24_hour"] ? "15:07" : "3:07 PM"), "clock format on every output");
            const descriptor = [a,b,c].find(screen => screen.name === scene.outputName);
            check(scene.viewportWidth === descriptor.width && scene.viewportHeight === descriptor.height, "own dimensions");
            check(scene.outputScale === descriptor.devicePixelRatio, "own output scale");
            check(scene.bar.workspaceStrip.entries.length === [a,b,c].indexOf(descriptor) + 1, "own workspaces");
            check(scene.notifications === list[0].notifications && scene.services === list[0].services
                && scene.dockStore === list[0].dockStore, "single service objects");
            if (!windows.some(window => window.contentItem.children.indexOf(scene) >= 0)) {
                const window = windowFactory.createObject(root, {width: descriptor.width, height: descriptor.height});
                scene.parent = window.contentItem;
                scene.width = descriptor.width;
                scene.height = descriptor.height;
                windows.push(window);
                created++;
                lifetimeProbe.createObject(scene, {window: window});
            }
        });
        if (laptop && list.some(scene => scene.outputName === a.name))
            check(list.find(scene => scene.outputName === a.name) === laptop, "laptop delegate survives hotplug and reorder");
        return list;
    }
    function capture(count) {
        if (Quickshell.env("OUTPUT_MEASURE") === "1") { sampleCount = count; settle = 1; }
        scenes().forEach(scene => {
            const descriptor = [a,b,c].find(screen => screen.name === scene.outputName);
            const scale = Number(Quickshell.env("QT_SCALE_FACTOR"));
            const filename = Quickshell.env("OUTPUT_SHOTS") + "/" + count + "-" + scene.outputName + "-" + scale + ".png";
            pending++;
            // grabToImage scales its requested logical size by the Qt screen DPR.
            // Compensate so each output gets its own descriptor DPR in one process.
            check(scene.grabToImage(result => {
                root.check(result.saveToFile(filename), "saved output render");
                root.pending--;
            }, Qt.size(Math.round(descriptor.width * descriptor.devicePixelRatio / scale), Math.round(descriptor.height * descriptor.devicePixelRatio / scale))), "grab scheduled");
        });
    }
    Timer {
        interval: 650
        running: true
        repeat: true
        onTriggered: {
            if (root.pending) return;
            if (root.settle > 0) {
                root.settle--;
                if (root.sampleCount) {
                    console.log("OUTPUT_MEASURE " + root.sampleCount);
                    root.sampleCount = 0; root.settle = 9;
                }
                return;
            }
            if (root.step === 0) {
                root.publish(root.a.name);
                root.applySettings(false, false, true, false);
                outputs.shared.dockStore.pinned = ["fixture-editor"];
            }
            else if (root.step === 1) { root.laptop = root.inspect(1)[0]; root.capture(1); }
            else if (root.step === 2) { outputs.screens = [root.b, root.a]; }
            else if (root.step === 3) { root.inspect(2); root.capture(2); }
            else if (root.step === 4) { outputs.screens = [root.c, root.b, root.a]; }
            else if (root.step === 5) { root.inspect(3); root.capture(3); }
            else if (root.step === 6) {
                root.applySettings(true, true, false, true);
                root.inspect(3);
                root.applySettings(false, false, true, false);
                root.checkKeyboardRouting();
                root.checkTransferredPreviews();
                const list = root.inspect(3);
                list[0].notifications.local("Fixture", "Output routing", "One notification");
                list[0].services.osdRequested("sound");
                root.check(list.filter(scene => scene.peekOpen).length === 1, "single notification peek");
                root.check(list.filter(scene => scene.osd.shown).length === 1, "single OSD");
                root.check(root.laptop.peekOpen && root.laptop.osd.shown, "events reach focused output");
                root.publish(root.b.name);
            } else if (root.step === 7) {
                const list = root.inspect(3);
                root.check(!root.laptop.peekOpen && !root.laptop.osd.shown, "previous output relinquishes overlays");
                root.check(outputs.activeScene.peekOpen && outputs.activeScene.osd.shown, "existing overlays follow focus");
                root.check(outputs.activeScene.openKeyboard("dock"), "removed output keyboard entry");
                root.check(outputs.activeScene.keyboardSurface === "dock", "removed output owns keyboard request");
                outputs.activeScene.peekCooldown = 0;
                list[0].notifications.local("Fixture", "Moved focus", "One notification");
                list[0].services.osdRequested("sound");
                root.check(list.filter(scene => scene.peekOpen).length === 1, "single notification after focus change");
                root.check(list.filter(scene => scene.osd.shown).length === 1, "single OSD after focus change");
                root.check(outputs.activeScene.outputName === root.b.name, "niri focus selected");
                outputs.screens = [root.a, root.c];
            } else if (root.step === 8) {
                root.inspect(2);
                root.check(root.destroyed === 1, "removed output destroyed");
                root.checkNoKeyboard("removed output leaves no keyboard state");
                root.check(outputs.activeScene.peekOpen && outputs.activeScene.osd.shown, "feedback survives focused output removal");
                root.check(root.scenes().filter(scene => scene.peekOpen).length === 1, "single notification after output removal");
                // Keep activeScene unchanged so only the delegate destruction handler
                // can save this previous owner; the focus-change fallback cannot mask it.
                const retiring = root.scenes().find(scene => scene.outputName === root.c.name);
                root.destructionState = outputs.activeScene.exportTransient();
                retiring.importTransient(root.destructionState);
                outputs.previousScene = retiring;
                outputs.screens = [root.a]; root.publish(root.a.name);
            } else if (root.step === 9) {
                root.inspect(1);
                root.check(root.destroyed === 2, "external delegates destroyed");
                root.check(outputs.previousScene === null && outputs.savedTransient !== null,
                    "destruction saves previous owner state");
                root.check(JSON.stringify(outputs.savedTransient.ids) === JSON.stringify(root.destructionState.ids)
                    && outputs.savedTransient.until === root.destructionState.until,
                    "destruction preserves preview and deadline");
                outputs.followFocus();
                root.check(outputs.activeScene.peekOpen && outputs.activeScene.osd.shown,
                    "destruction state reaches surviving output");
                root.check(outputs.activeScene.openKeyboard("bar"), "last output keyboard entry");
                outputs.screens = [];
            } else if (root.step === 10) {
                root.inspect(0);
                root.check(outputs.activeScene === null, "no keyboard owner without outputs");
                root.check(root.destroyed === 3, "all delegates destroyed");
                root.laptop = null;
                root.applySettings(true, false, false, true);
                root.check(!outputs.shared.dockStore.on && outputs.shared.dockStore.autoHide,
                    "managed settings without outputs");
                outputs.screens = [root.b]; root.publish(root.b.name);
            } else if (root.step === 11) {
                root.inspect(1);
                root.check(outputs.activeScene.outputName === root.b.name, "external alone with laptop absent");
                root.checkNoKeyboard("new output inherits no removed keyboard state");
                outputs.screens = [root.a, root.b];
            } else if (root.step === 12) {
                root.inspect(2);
                root.check(root.created === 5, "only required delegates created");
                root.publish(root.a.name);
                const reply = JSON.parse(outputs.shared.settingsController.open("panel"));
                root.check(reply.status === "opened", "settings open accepted on first output");
            } else if (root.step === 13) {
                const controller = outputs.shared.settingsController;
                const source = root.scenes().find(scene => scene.outputName === root.a.name);
                root.check(controller.activeHost === source && controller.opened
                    && controller.page === "panel" && source.launcherOpen && source.input.settingsActive,
                    "settings status follows first output after opening frame");
                root.publish(root.b.name);
                const reply = JSON.parse(controller.open("windows"));
                root.check(reply.status === "opened", "settings open accepted on newly focused output");
                root.check(!source.launcherOpen, "previous settings output closes on rerouting");
            } else if (root.step === 14) {
                const controller = outputs.shared.settingsController;
                const destination = outputs.activeScene;
                root.check(destination.outputName === root.b.name && controller.activeHost === destination
                    && controller.opened && controller.page === "windows"
                    && destination.launcherOpen && destination.input.settingsActive,
                    "settings status follows newly focused output after opening frame");
                root.check(root.scenes().filter(scene => scene.launcherOpen).length === 1,
                    "only destination launcher remains open");
                controller.dismiss();
                root.check(!controller.opened && !destination.launcherOpen,
                    "settings close and status target newly focused output");
                console.log("OUTPUTS_COMPLETE"); Qt.quit();
            }
            root.step++;
        }
    }
}
'''


def run(scale, mutation=''):
    profile = DEST / (str(scale) + ('-' + mutation if mutation else ''))
    if profile.exists():
        shutil.rmtree(profile)
    for part in ('cache', 'config', 'state', 'data', 'tmp', 'shots', 'sys'):
        (profile / part).mkdir(parents=True, mode=0o700)
    apps = profile / 'data/applications'
    apps.mkdir()
    (apps / 'fixture-editor.desktop').write_text('[Desktop Entry]\nType=Application\nName=Fixture Editor\nExec=/usr/bin/true\nTerminal=false\n')
    binaries = profile / 'bin'
    binaries.mkdir()
    niri = binaries / 'niri'
    niri.write_text("#!/bin/sh\n# Copyright (C) 2026 Artur Yakymenko\n"
                    "# SPDX-License-Identifier: GPL-3.0-or-later\n"
                    "printf '%s\\n' '{\"compositor\":\"26.04 (v26.04+emaki.12)\"}'\n")
    niri.chmod(0o755)
    qml = profile / 'qml'
    shutil.copytree(ROOT / 'shell', qml)
    (qml / 'shell.qml').write_text(QML)
    if mutation:
        path = qml / ('ShellOutputs.qml' if mutation in ('first-screen', 'destruction-handoff') else 'ShellScene.qml')
        source = path.read_text()
        if mutation == 'first-screen':
            before = 'model: outputs.enabled ? outputs.selectedScreens : []'
            after = 'model: outputs.enabled ? outputs.selectedScreens.slice(0, 1) : []'
        elif mutation == 'destruction-handoff':
            before = 'if (outputs.previousScene === scene)'
            after = 'if (false)'
        elif mutation == 'settings-hotplug':
            before = 'Component.onCompleted: applyManagedSettings()'
            after = 'Component.onCompleted: {}'
        elif mutation == 'import-policy':
            before = 'if (niri.overviewOpen || modalOpen || notes.effectiveDnd || presentationState !== "clear")\n            retainCriticalPeek();'
            after = 'if (false)\n            retainCriticalPeek();'
        else:
            before = 'if (scene.focusedOutput)'
            after = 'if (true)'
            guard = 'if (!focusedOutput)'
            assert source.count(guard) == 1, 'notification entry guard'
            source = source.replace(guard, 'if (false)')
        assert source.count(before) == 1, ('mutation anchor', mutation, before)
        path.write_text(source.replace(before, after))
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', QT_SCALE_FACTOR=str(scale), HOME=str(profile),
               EMAKI_SHELL_TRAY='0', EMAKI_TEST_SYSTEM='0', EMAKI_BIN='', EMAKI_SETTINGS_PROFILE='',
               EMAKI_TEST_MPRIS='0', EMAKI_SHELL_NOTIFICATIONS='0', EMAKI_SHELL_GLASS_RENDERER='canvas',
               EMAKI_FIXTURE_WALLPAPER='', EMAKI_SESSION_START='', EMAKI_SESSION_SKIP_INTRO='',
               OUTPUT_MEASURE='1' if scale == 1 and not mutation else '0',
               OUTPUT_SHOTS=str(profile / 'shots'), XDG_RUNTIME_DIR=str(runtime_path(profile)),
               NIRI_SOCKET='', DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(profile / 'no-system'),
               TMPDIR=str(profile / 'tmp'), PATH=str(binaries) + os.pathsep + os.environ['PATH'])
    for kind in ('CACHE', 'CONFIG', 'STATE', 'DATA'):
        env['XDG_' + kind + '_HOME'] = str(profile / kind.lower())
    env['XDG_DATA_DIRS'] = str(profile / 'data')
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_SCREEN_SCALE_FACTORS', 'QT_PLUGIN_PATH',
                 'QML_IMPORT_PATH', 'QML2_IMPORT_PATH', 'QT_LOGGING_RULES'):
        env.pop(name, None)
    lines = []
    with subprocess.Popen(['qs', '-p', str(qml), '--no-color'], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
        timeout = threading.Timer(55, process.kill)
        timeout.start()
        for line in process.stdout:
            lines.append(line)
            if 'OUTPUT_MEASURE ' in line:
                count = int(line.split('OUTPUT_MEASURE ')[1])
                report = measure.measure(process.pid, seconds=5, interval=.5, sys_root=profile / 'sys')
                (profile / f'measure-{count}.json').write_text(json.dumps(report, indent=2) + '\n')
                print(f'Offscreen {count} outputs: {report["summary"]["cpu_percent_one_core"]:.2f}% of one CPU core', flush=True)
        returncode = process.wait(timeout=30)
        timeout.cancel()
    log = ''.join(lines)
    (profile / 'qs.log').write_text(log)
    if mutation:
        expected_failure = {
            'first-screen': 'delegate count 2 got 1',
            'duplicate-notifications': 'single notification peek',
            'destruction-handoff': 'destruction saves previous owner state',
            'import-policy': 'import ordinary preview policy fullscreen',
            'settings-hotplug': 'managed bar settings',
        }[mutation]
        assert returncode != 0 and 'OUTPUTS_FAILED ' + expected_failure in log, log
        print('PASS: rejected ' + mutation)
        return
    assert returncode == 0 and 'OUTPUTS_COMPLETE' in log, log
    if 'quickshell.ipc: Failed to start IPC server' in log:
        print('Restricted environment: Unix IPC socket unavailable; self-driven offscreen assertions still ran.')
    unexpected = [line for line in log.splitlines() if any(word in line for word in ('ReferenceError', 'TypeError', 'Binding loop', 'ERROR'))
                  and 'quickshell.ipc: Failed to start IPC server' not in line]
    assert not unexpected, '\n'.join(unexpected)
    shots = sorted((profile / 'shots').glob('*.png'))
    assert len(shots) == 6, shots
    dimensions = {'Laptop': (1280, 800), 'External': (1800, 1125), 'Portrait': (1200, 1920)}
    for shot in shots:
        name = shot.name.split('-')[1]
        with Image.open(shot) as frame:
            expected = tuple(math.floor(math.floor(v / scale + .5) * scale + .5) for v in dimensions[name])
            assert frame.size == expected, (shot, frame.size, expected)
            alpha = frame.convert('RGBA').getchannel('A')
            assert alpha.crop((0, 0, frame.width, round(84 * frame.height / dimensions[name][1]))).getbbox(), ('missing panel', shot)
            assert alpha.crop((0, frame.height - round(100 * frame.height / dimensions[name][1]), frame.width, frame.height)).getbbox(), ('missing dock', shot)
    print(f'PASS: output lifecycle, ownership and six renders at scale {scale}')


def check_settings_notifications(existing_directory=True):
    """Real FileView watches: absent source, first creation, rename twice, then idle."""
    profile = DEST / ('notifications-existing' if existing_directory else 'notifications-new')
    if profile.exists():
        shutil.rmtree(profile)
    for part in ('cache', 'config/wpaperd', 'state', 'data', 'tmp'):
        (profile / part).mkdir(parents=True, mode=0o700)
    if existing_directory:
        (profile / 'config/emaki').mkdir()
    plain = profile / 'plain.png'
    selected = profile / 'selected.png'
    Image.new('RGB', (32, 20), (180, 0, 0)).save(plain)
    Image.new('RGB', (32, 20), (0, 180, 0)).save(selected)
    (profile / 'config/wpaperd/config.toml').write_text('[default]\npath = ' + json.dumps(str(plain)) + '\n')
    qml = profile / 'qml'
    shutil.copytree(ROOT / 'shell', qml)
    (qml / 'shell.qml').write_text(r"""
import QtQuick
import Quickshell
ShellRoot {
    id: root
    function report() {
        console.log("WATCH_STATE " + JSON.stringify({auto: settings.value("bar.autohide"),
            ready: wallpaper.ready, texture: wallpaper.texture, epoch: wallpaper.epoch}));
    }
    SettingsCatalog { id: settings; active: false; onValuesChanged: root.report() }
    WallpaperSource {
        id: wallpaper
        refreshManaged: true
        variant: "sharp"
        outputWidth: 32
        outputHeight: 20
        onTextureChanged: root.report()
        onReadyChanged: root.report()
        onEpochChanged: root.report()
    }
}
""")
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QML_DISABLE_DISK_CACHE='1', HOME=str(profile), EMAKI_SETTINGS_PROFILE='',
               EMAKI_BIN=str(ROOT / '.cache/target/debug/emaki'),
               XDG_RUNTIME_DIR=str(runtime_path(profile)), TMPDIR=str(profile / 'tmp'),
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'no-session'))
    for kind in ('CACHE', 'CONFIG', 'STATE', 'DATA'):
        env['XDG_' + kind + '_HOME'] = str(profile / kind.lower())
    for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'NIRI_SOCKET', 'QT_LOGGING_RULES'):
        env.pop(name, None)
    events = queue.Queue()
    lines = []
    with subprocess.Popen(['qs', '-p', str(qml), '--no-color'], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as process:
        def read():
            for line in process.stdout:
                lines.append(line)
                if 'WATCH_STATE ' in line:
                    events.put(json.loads(line.split('WATCH_STATE ', 1)[1]))
        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        def wait(auto, color):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                assert process.poll() is None, ''.join(lines)
                try:
                    state = events.get(timeout=.1)
                except queue.Empty:
                    continue
                if state['auto'] != auto or not state['ready'] or not state['texture']:
                    continue
                with Image.open(unquote(urlparse(state['texture']).path)) as image:
                    if image.convert('RGB').getpixel((16, 10)) == color:
                        return state
            raise AssertionError('notification timeout: ' + ''.join(lines))
        try:
            wait(False, (180, 0, 0))
            source = profile / 'config/emaki/settings.toml'
            source.parent.mkdir(exist_ok=True)
            for auto, path, color in [(True, selected, (0, 180, 0)),
                                      (False, plain, (180, 0, 0)),
                                      (True, selected, (0, 180, 0))]:
                temporary = source.with_suffix('.tmp')
                temporary.write_text('schema_version = 1\n[bar]\nautohide = ' + str(auto).lower()
                                     + '\n[appearance]\nwallpaper = ' + json.dumps(str(path)) + '\n')
                temporary.replace(source)
                wait(auto, color)
            # Drain queued notifications before observing a full former poll interval.
            time.sleep(.3)
            while not events.empty():
                events.get_nowait()
            time.sleep(5.3)
            assert events.empty(), 'idle settings or wallpaper work recurred'
        finally:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            reader.join(timeout=2)
            (profile / 'qs.log').write_text(''.join(lines))
    print('PASS: actual file notifications, first creation, two replacements and idle; directory=' + str(existing_directory))


if __name__ == '__main__':
    check_settings_notifications(True)
    check_settings_notifications(False)
    for factor in (1, 1.25, 1.5, 2):
        run(factor)
    for mutant in ('first-screen', 'duplicate-notifications', 'destruction-handoff', 'import-policy', 'settings-hotplug'):
        run(1, mutant)
