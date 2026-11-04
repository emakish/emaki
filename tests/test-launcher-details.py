#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise launcher labels and icon resolution with the real QML component."""
from runtime_fixture import runtime_path
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / '.cache/evidence/launcher'
CACHE.mkdir(parents=True, exist_ok=True)
LABELS = ['LibreOffice Base', 'LibreOffice Calc', 'LibreOffice Draw', 'LibreOffice Impress',
          'LibreOffice Math', 'LibreOffice Writer', 'KDE Connect SMS', 'KDE Partition Manager',
          'ISO Image Writer', 'System Monitor', 'Manage Printing', 'mpv Media Player',
          'Welcome to Emaki']
with tempfile.TemporaryDirectory(prefix='ld-') as temporary:
    base = Path(temporary)
    for name in ('home', 'runtime', 'cache', 'config', 'data'):
        (base / name).mkdir(mode=0o700)
    icons = base / 'icons-data/icons/breeze/apps/48'
    icons.mkdir(parents=True)
    for name in ('accessories-character-map', 'utilities-system-monitor'):
        (icons / (name + '.svg')).write_text('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="red"/></svg>')
    expected_icons = {name: icons / (name + '.svg') for name in ('accessories-character-map', 'utilities-system-monitor')}
    for name, directory in [('system-software-update', 'apps'), ('printer', 'devices')]:
        path = base / ('icons-data/icons/hicolor/scalable/' + directory) / (name + '.svg')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16"><rect width="16" height="16" fill="red"/></svg>')
        expected_icons[name] = path
    qml = base / 'shell.qml'
    qml.write_text('''import QtQuick
import Quickshell
import "''' + (ROOT / 'shell').as_uri() + '''"
Scope {
    id: test
    property var labels: ''' + json.dumps(LABELS) + '''
    property var results: []
    property var iconItems: []
    property var commandRows: []
    Component {
        id: factory
        LauncherItem { width: 100; height: 84; shape: "tile" }
    }
    Component {
        id: rowFactory
        LauncherItem { height: 44; shape: "row" }
    }
    function texts(node) {
        let out = [];
        for (const child of node.children || []) {
            if (child.text !== undefined && child.text !== "")
                out.push(child);
            out = out.concat(texts(child));
        }
        return out;
    }
    Component.onCompleted: {
        for (const label of labels) {
            const item = factory.createObject(test, {row: {kind: "app", label: label}});
            const text = texts(item).find(t => t.text === label);
            results.push({label: label, truncated: text.truncated,
                          lines: text.lineCount, bottom: text.y + text.height});
        }
        for (const name of ["accessories-character-map", "utilities-system-monitor", "system-software-update", "printer", "missing-fixture-icon"]) {
            const item = factory.createObject(test, {row: {kind: "app", label: "Fixture", entry: {icon: name}}});
            iconItems.push({name: name, item: item});
        }
        // A translation-sized description must reserve its displayed width, even when
        // its unelided text is wider than the entire launcher.
        for (const width of [280, 436, 676]) {
            const label = "Change display and colour preferences";
            const detail = "Choose the screen arrangement, scaling, refresh rate, and colour preferences for each connected display. Review every screen before applying the new arrangement and colour preferences.";
            const item = rowFactory.createObject(test, {width: width, row: {
                kind: "command", label: label, detail: detail, icon: "preferences-system-symbolic"
            }});
            commandRows.push({item: item, label: label, detail: detail});
        }
        report.start();
    }
    Timer {
        id: report
        interval: 500
        onTriggered: {
            for (const row of test.iconItems) test.results.push({name: row.name, icon: row.item.icon});
            for (const row of test.commandRows) {
                const parts = test.texts(row.item);
                const title = parts.find(text => text.text === row.label && text.parent.cy !== undefined);
                const detail = parts.find(text => text.text === row.detail && text.horizontalAlignment === Text.AlignRight);
                test.results.push({commandWidth: row.item.width,
                    titleX: title.x, titleWidth: title.width,
                    detailX: detail.x, detailWidth: detail.width, detailNaturalWidth: detail.implicitWidth,
                    detailVisible: detail.visible, detailElided: detail.truncated});
            }
            console.log("LAUNCHER_RESULT " + JSON.stringify(test.results));
            Qt.quit();
        }
    }
}
''')
    env = dict(os.environ, HOME=str(base / 'home'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               XDG_CACHE_HOME=str(base / 'cache'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_DATA_HOME=str(base / 'data'), XDG_DATA_DIRS=str(base / 'data') + ':' + str(base / 'icons-data'),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software')
    env.pop('QT_QPA_PLATFORMTHEME', None)
    run = subprocess.run(['qs', '-p', str(qml), '--no-color'], env=env,
                         text=True, capture_output=True, timeout=20)
    log = run.stdout + run.stderr
    assert run.returncode == 0, log
    assert 'Binding loop' not in log, log
    result = next((line.split('LAUNCHER_RESULT ', 1)[1] for line in log.splitlines()
                   if 'LAUNCHER_RESULT ' in line), None)
    assert result, log
    results = json.loads(result)
    for item in results:
        if 'commandWidth' in item:
            assert item['detailVisible'] and item['detailElided'], item
            assert item['detailNaturalWidth'] > item['commandWidth'], item
            assert item['titleX'] >= 0 and item['titleWidth'] > 0, item
            assert item['titleX'] + item['titleWidth'] < item['detailX'], item
            assert item['detailX'] >= 0 and item['detailWidth'] > 0, item
            assert item['detailX'] + item['detailWidth'] <= item['commandWidth'], item
        elif 'label' in item:
            assert not item['truncated'], item
            assert 1 <= item['lines'] <= 2 and item['bottom'] <= 84, item
        elif item['name'] == 'missing-fixture-icon':
            assert item['icon'] == '', item
        else:
            path = expected_icons[item['name']].as_uri()
            assert item['icon'] == path, item
            assert Path(path.removeprefix('file://')).is_file(), path
    expected_icons['utilities-system-monitor'].unlink()
    run = subprocess.run(['qs', '-p', str(qml), '--no-color'], env=env,
                         text=True, capture_output=True, timeout=20)
    log = run.stdout + run.stderr
    assert run.returncode == 0 and 'Binding loop' not in log, log
    result = next(line.split('LAUNCHER_RESULT ', 1)[1] for line in log.splitlines() if 'LAUNCHER_RESULT ' in line)
    monitor = next(item for item in json.loads(result) if item.get('name') == 'utilities-system-monitor')
    assert monitor['icon'] == '', monitor
print('PASS: complete launcher names, Breeze and hicolor fallbacks, exhausted candidates; no icon binding loops')
print('PASS: command titles retain positive space and never overlap long descriptions at 280, 436 and 676 px')
