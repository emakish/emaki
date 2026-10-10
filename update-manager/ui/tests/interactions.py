#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keyboard events in the production update view, plus behavioral mutants."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

UI = Path(__file__).resolve().parents[1]
ROOT = UI.parents[1]
MUTANTS = {
    'tab': ('focusPolicy: Qt.StrongFocus', 'focusPolicy: Qt.NoFocus'),
    'apply': ('onClicked: view.controller.apply()', 'onClicked: {}'),
    'news': ('view.openNews(modelData.link)', 'view.openNews("")'),
    'busy': ('enabled: !view.controller.busy && view.controller.repositoryCount', 'enabled: view.controller.repositoryCount'),
    'restart': ('onClicked: view.controller.reboot()', 'onClicked: {}'),
    'later': ('onClicked: view.hideRequested()', 'onClicked: {}'),
    'scroll': ('if (activeFocus) view.reveal(action)', 'if (false) view.reveal(action)'),
    'enter': ('Keys.onEnterPressed: if (enabled) clicked()', 'Keys.onEnterPressed: {}'),
    'return': ('Keys.onReturnPressed: if (enabled) clicked()', 'Keys.onReturnPressed: {}'),
    'escape': ('if (controller.phase !== "applying") hideRequested()', 'hideRequested()'),
    'keyboard-scroll': ('Keys.onPressed: event => view.scrollKey(event)', 'Keys.onPressed: event => { event.accepted = true; }'),
    'footer-scroll': ('if (!ancestor) return;', ''),
    'tail': ('view.controller.phase === "applying" && view.followTail', 'false'),
    'pause-tail': ('followTail = flick.contentY >=', 'followTail = true || flick.contentY >='),
    'progress': ('visible: !view.controller.busy && view.controller.phase !== "finished"', 'visible: true'),
}


REOPEN_CONTROLLER = """// Copyright (C) 2026 Artur Yakymenko
// SPDX-License-Identifier: GPL-3.0-or-later
import QtQuick
QtObject {
    property var data: ({updates: [], groups: [], news: [], warnings: [], downloadSize: 0})
    property string phase: "idle"
    readonly property bool busy: phase === "applying"
    property int repositoryCount: 0
    property bool restart: false
    property bool signOut: false
    property string message: ""
    property string output: ""
    property int checks: 0
    function check(): void { checks++; }
    function apply(): void {}
    function reboot(): void {}
}
"""
REOPEN_TEST = """
    property bool testFailed: false
    function testCheck(ok: bool, message: string): void {
        if (!ok) { testFailed = true; console.error("ASSERTION_FAILED " + message); }
    }
    function testFind(item: Item, name: string): Item {
        if (item.objectName === name) return item;
        for (const child of item.children) {
            const found = testFind(child, name);
            if (found) return found;
        }
        return null;
    }
    TestCase { id: input; when: false; name: "UpdateReopen" }
    Timer {
        running: true
        interval: 250
        onTriggered: {
            const window = windowLoader.item;
            window.visible = false;
            root.testCheck(root.present() && controller.checks === 1, "idle reopen refreshes the list");
            controller.phase = "finished";
            controller.message = "Update complete.";
            controller.output = Array(150).fill("Package detail.").join("\\n");
            input.wait(80);
            const flick = root.testFind(window.contentItem, "updateBody").contentItem;
            flick.contentY = flick.contentHeight - flick.height;
            root.testCheck(flick.contentY > 0, "result fixture scrolls");
            window.visible = false;
            root.testCheck(root.present(), "finished reopen presents window");
            input.wait(80);
            root.testCheck(controller.checks === 1 && controller.message === "Update complete.", "finished reopen preserves result");
            root.testCheck(flick.contentY === 0, "finished reopen reveals result text");
            controller.phase = "applying";
            controller.output = "Running package operation.";
            input.wait(30);
            window.visible = false;
            root.testCheck(root.present(), "active reopen presents window");
            root.testCheck(controller.checks === 1 && controller.busy && controller.output === "Running package operation.", "active reopen preserves operation and output");
            controller.output = Array(140).fill("Updating package.").join("\\n");
            windowLoader.active = false;
            root.testCheck(root.present(), "active view can be recreated");
            input.wait(100);
            const recreated = root.testFind(windowLoader.item.contentItem, "updateBody").contentItem;
            root.testCheck(recreated.contentY > 0 && Math.abs(recreated.contentY - (recreated.contentHeight - recreated.height)) < 2, "recreated active view follows existing output tail");
            controller.output = controller.output.slice(controller.output.indexOf("\\n") + 1) + "\\nAnother package.";
            input.wait(50);
            root.testCheck(Math.abs(recreated.contentY - (recreated.contentHeight - recreated.height)) < 2, "bounded output keeps newest line visible");
            if (!root.testFailed) console.log("INTERACTION_OK");
            Qt.quit();
        }
    }
"""
REOPEN_MUTANTS = {
    'reopen-refresh': ('if (controller.phase === "idle") controller.check();', ''),
    'reopen-result': ('onVisibleChanged: if (visible) updateView.present()', ''),
    'reopen-busy': ('if (controller.phase === "idle") controller.check();', 'controller.check();'),
}


def run(width, height, mutant=None, reopen=False):
    with tempfile.TemporaryDirectory(prefix='emaki-update-ui-') as directory:
        root = Path(directory)
        shutil.copytree(UI, root / 'ui')
        controller = root / 'ui/UpdateController.qml'
        controller.write_text(controller.read_text().replace('@EMAKI_DATADIR@/shell', (ROOT / 'shell').as_uri()))
        view = root / 'ui/UpdateView.qml'
        text = view.read_text().replace('@EMAKI_DATADIR@/shell', (ROOT / 'shell').as_uri())
        if mutant and not reopen:
            before, after = MUTANTS[mutant]
            assert before in text
            text = text.replace(before, after)
        view.write_text(text)
        entry = (root / 'ui/tests/InteractionTest.qml').read_text().replace('".." as UI', '"." as UI')
        if reopen:
            (root / 'ui/UpdateController.qml').write_text(REOPEN_CONTROLLER)
            entry = (UI / 'shell.qml').read_text().replace('import QtQuick', 'import QtTest\nimport QtQuick', 1)
            if mutant:
                before, after = REOPEN_MUTANTS[mutant]
                assert before in entry
                entry = entry.replace(before, after)
            entry = entry.rstrip()[:-1] + REOPEN_TEST + '\n}\n'
        (root / 'ui/test.qml').write_text(entry)
        for name in ('runtime', 'cache', 'config', 'data', 'state'):
            (root / name).mkdir(mode=0o700)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1', TEST_WIDTH=str(width), TEST_HEIGHT=str(height))
        for name in ('runtime', 'cache', 'config', 'data', 'state'):
            env['XDG_' + name.upper() + ('_DIR' if name == 'runtime' else '_HOME')] = str(root / name)
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'DBUS_SESSION_BUS_ADDRESS', 'NIRI_SOCKET'):
            env.pop(name, None)
        result = subprocess.run(['qs', '-p', str(root / 'ui/test.qml'), '--no-color'], env=env, capture_output=True, text=True, timeout=30)
        log = result.stdout + result.stderr
        if mutant:
            assert 'ASSERTION_FAILED' in log, log
        else:
            assert result.returncode == 0 and 'INTERACTION_OK' in log, log
            assert not any(word in log for word in ('ASSERTION_FAILED', 'ReferenceError', 'TypeError', 'Unable to assign', 'Binding loop')), log
        print('PASS', mutant or ('production reopen' if reopen else f'{width}x{height}'))


if __name__ == '__main__':
    for dimensions in ((640, 420), (860, 680), (1100, 800)):
        run(*dimensions)
    for mutation in MUTANTS:
        run(640, 420, mutation)

    run(860, 680, reopen=True)
    for mutation in REOPEN_MUTANTS:
        run(860, 680, mutation, reopen=True)
