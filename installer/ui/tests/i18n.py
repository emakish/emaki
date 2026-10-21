#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render production installer labels with the pseudo catalogue in isolation."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
UI = ROOT / 'installer' / 'ui'
QML = '''import QtQuick
import Quickshell
import Emaki.I18n
import "." as UI
ShellRoot {
    UI.InstallerController { id: backend }
    FloatingWindow {
        id: window
        implicitWidth: 1024; implicitHeight: 700
        UI.InstallerView { id: view; anchors.fill: parent; controller: backend }
    }
    function walk(item, labels) {
        if (!item || item.visible === false) return;
        if (typeof item.text === "string" && item.text) labels.push(item.text);
        for (const child of item.children || []) walk(child, labels);
    }
    Timer {
        interval: 1000; running: true
        onTriggered: {
            const labels = [];
            walk(view, labels);
            const english = labels.filter(text => ["Welcome to Emaki", "Install Emaki", "Try Emaki first", "Hide", "Welcome", "Installer", "System language"].includes(text));
            const translated = labels.filter(text => text.startsWith("[") && text.endsWith("]"));
            if (english.length || translated.length < 8) {
                console.error("ASSERTION_FAILED pseudo labels: " + JSON.stringify(labels));
                Qt.exit(1); return;
            }
            view.grabToImage(result => {
                if (!result.saveToFile(Quickshell.env("TEST_PSEUDO_IMAGE"))) { Qt.exit(1); return; }
                console.log("INSTALLER_I18N_OK labels=" + translated.length);
                Qt.quit();
            });
        }
    }
}
'''


def main():
    output = ROOT / '.cache/evidence/i18n/installer-en_XA.png'
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='installer-i18n-') as directory:
        root = Path(directory)
        shutil.copytree(UI, root / 'ui', ignore=shutil.ignore_patterns('__pycache__', 'artifacts'))
        for path in (root / 'ui').glob('*.qml'):
            path.write_text(path.read_text().replace('file:///usr/share/emaki/shell', 'file://' + str(ROOT / 'shell')))
        entry = root / 'ui/PseudoTest.qml'
        entry.write_text(QML)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1', QS_DISABLE_CRASH_HANDLER='1',
                   EMAKI_LANGUAGE='en_XA', EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'),
                   QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
                   QML2_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
                   PYTHONPATH=str(ROOT / 'installer'), TEST_PSEUDO_IMAGE=str(output),
                   EMAKI_INSTALLER_SOCKET=str(root / 'missing.sock'))
        for name, variable in [('home', 'HOME'), ('runtime', 'XDG_RUNTIME_DIR'), ('config', 'XDG_CONFIG_HOME'),
                               ('cache', 'XDG_CACHE_HOME'), ('state', 'XDG_STATE_HOME'), ('data', 'XDG_DATA_HOME')]:
            target = root / name
            target.mkdir(mode=0o700)
            env[variable] = str(target)
        for variable in ['WAYLAND_DISPLAY', 'DISPLAY', 'NIRI_SOCKET', 'DBUS_SESSION_BUS_ADDRESS', 'QT_QPA_PLATFORMTHEME']:
            env.pop(variable, None)
        try:
            result = subprocess.run(['qs', '-p', str(entry), '--no-color'], env=env,
                                    capture_output=True, text=True, timeout=20)
        except subprocess.TimeoutExpired as error:
            output.with_suffix('.log').write_bytes((error.stdout or b'') + (error.stderr or b''))
            raise
        log = result.stdout + result.stderr
        output.with_suffix('.log').write_text(log.replace(str(ROOT), '<checkout>'))
        assert result.returncode == 0 and 'INSTALLER_I18N_OK' in log, log
        assert not any(value in log for value in ('ASSERTION_FAILED', 'ReferenceError', 'TypeError')), log
        print(next(line for line in log.splitlines() if 'INSTALLER_I18N_OK' in line))
        print(output)


if __name__ == '__main__':
    main()
