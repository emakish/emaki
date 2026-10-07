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
    qml = base / 'shell.qml'
    qml.write_text('''import QtQuick
import Quickshell
import "''' + (ROOT / 'shell').as_uri() + '''"
Scope {
    id: test
    property var labels: ''' + json.dumps(LABELS) + '''
    property var results: []
    Component {
        id: factory
        LauncherItem { width: 100; height: 84; shape: "tile" }
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
        for (const name of ["accessories-character-map", "utilities-system-monitor", "missing-fixture-icon"]) {
            const item = factory.createObject(test, {row: {kind: "app", label: "Fixture", entry: {icon: name}}});
            results.push({name: name, icon: item.icon});
        }
        console.log("LAUNCHER_RESULT " + JSON.stringify(results));
        Qt.callLater(() => Qt.quit());
    }
}
''')
    env = dict(os.environ, HOME=str(base / 'home'), XDG_RUNTIME_DIR=str(runtime_path(base)),
               XDG_CACHE_HOME=str(base / 'cache'), XDG_CONFIG_HOME=str(base / 'config'),
               XDG_DATA_HOME=str(base / 'data'), XDG_DATA_DIRS=str(base / 'data'),
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software')
    env.pop('QT_QPA_PLATFORMTHEME', None)
    run = subprocess.run(['qs', '-p', str(qml), '--no-color'], env=env,
                         text=True, capture_output=True, timeout=20)
    log = run.stdout + run.stderr
    assert run.returncode == 0, log
    result = next((line.split('LAUNCHER_RESULT ', 1)[1] for line in log.splitlines()
                   if 'LAUNCHER_RESULT ' in line), None)
    assert result, log
    results = json.loads(result)
    for item in results:
        if 'label' in item:
            assert not item['truncated'], item
            assert 1 <= item['lines'] <= 2 and item['bottom'] <= 84, item
        elif item['name'] == 'missing-fixture-icon':
            assert item['icon'] == '', item
        else:
            path = 'file:///usr/share/icons/breeze/apps/48/' + item['name'] + '.svg'
            assert item['icon'] == path, item
            assert Path(path.removeprefix('file://')).is_file(), path
print('PASS: complete launcher names, two KDE icons, unknown-icon fallback')
