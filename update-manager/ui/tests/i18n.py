#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render the real store under the pseudo catalogue with isolated fixture backends."""
import os
from pathlib import Path
import tempfile

os.environ['EMAKI_LANGUAGE'] = 'en_XA'
os.environ['EMAKI_LOCALE_DIR'] = str(Path(__file__).resolve().parents[3] / 'i18n/locale')

import interactions  # noqa: E402

QML = '''import QtQuick
import QtTest
import Quickshell
import Emaki.I18n
import ".." as UI
ShellRoot {
    TestCase { id: frame; when: false }
    property int stage: 0
    Component.onCompleted: updates.data = Object.assign({}, updates.data, {warnings: JSON.parse(Quickshell.env("TEST_UPDATE_WARNINGS"))})
    UI.UpdateController { id: updates }
    UI.ProgramsController { id: programs }
    FloatingWindow {
        id: window
        implicitWidth: 1180; implicitHeight: 780
        UI.ManagerView { id: view; anchors.fill: parent; controller: updates; programs: programs }
    }
    function walk(item, strings) {
        if (!item || item.visible === false) return;
        if (typeof item.text === "string" && item.text) strings.push(item.text);
        for (const child of item.children || []) walk(child, strings);
    }
    Timer {
        id: checkTimer
        interval: 1000; running: true
        onTriggered: {
            const strings = [];
            walk(view, strings);
            const unchanged = strings.filter(text => ["Browse", "Installed", "Updates", "Popular", "Essentials", "Install", "All", "Refresh", "Picked by Emaki", "A good place to start", "Update all", "Check again", "Details", "IF SOMETHING BROKE"].includes(text));
            const translated = strings.filter(text => text.startsWith("[") && text.endsWith("]"));
            if (unchanged.length || translated.length < 8) {
                console.error("ASSERTION_FAILED pseudo labels: " + JSON.stringify(strings));
                Qt.exit(1); return;
            }
            view.resetScroll();
            window.visible = false; frame.wait(30); window.visible = true;
            view.opacity = 0; frame.wait(30); view.opacity = 1; frame.wait(80);
            view.grabToImage(result => {
                if (!result.saveToFile(Quickshell.env("TEST_PSEUDO_IMAGE").replace(".png", stage === 0 ? ".png" : "-updates.png"))) { Qt.exit(1); return; }
                console.log("INTERACTION_OK pseudo labels=" + translated.length);
                if (stage === 0) { stage = 1; view.navigate("updates"); checkTimer.restart(); }
                else Qt.quit();
            });
        }
    }
}
'''


def main():
    import catalog
    import json
    root = interactions.ROOT
    output = root / '.cache/evidence/i18n/programs-en_XA.png'
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='emaki-programs-i18n-') as directory:
        entry = Path(directory) / 'PseudoTest.qml'
        entry.write_text(QML)
        log = interactions.run(entry=entry, extra={
            'EMAKI_LANGUAGE': 'en_XA',
            'EMAKI_LOCALE_DIR': str(root / 'i18n/locale'),
            'QML_IMPORT_PATH': str(root / '.cache/i18n-native/qml') + os.pathsep + os.environ.get('QML_IMPORT_PATH', ''),
            'TEST_PSEUDO_IMAGE': str(output),
            'TEST_UPDATE_WARNINGS': json.dumps(catalog.warnings_for([{'name': 'linux', 'aur': False}])),
        })
        print('\n'.join(line for line in log.splitlines() if 'INTERACTION_OK' in line))
        print(output)


if __name__ == '__main__':
    main()
