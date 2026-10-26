#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Search the real launcher with each compiled Settings display name."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ('en', 'es', 'pt_BR', 'de', 'fr', 'ru')
QML = '''import QtQuick
import Quickshell
import Emaki.I18n
ShellRoot {
    id: root
    property int activations: 0
    NiriService { id: compositor; binary: "" }
    LauncherBody {
        id: launcher
        width: 1000; height: 800
        expansion: 0; opened: false; niri: compositor
        onSettingsRequested: root.activations++
    }
    Timer { interval: 300; running: true; onTriggered: {
        const label = qsTranslate("LauncherBody", "Settings");
        const queries = [label, label.substring(0, 3), label.toUpperCase(),
            label.substring(0, 2), "settings", "set", "preferences", "control", "panel",
            "control panel", "settings nonsense", label + " nonsense", "zzzz"];
        const rows = [];
        for (const query of queries) {
            launcher.setQuery(query);
            rows.push(launcher.buildResults().filter(row => row.kind === "settings")
                .map(row => ({label: row.label, score: row.score})));
        }
        launcher.setQuery(label);
        launcher.opened = true;
        launcher.selectAt(launcher.results.findIndex(row => row.kind === "settings"));
        launcher.activate();
        launcher.assignFor = "unmatched-app";
        const assigning = launcher.buildResults().some(row => row.kind === "settings");
        console.log("SETTINGS_SEARCH " + JSON.stringify({label: label, rows: rows,
            activations: root.activations, assigning: assigning}));
        Qt.quit();
    } }
}
'''


class LauncherSettingsSearch(unittest.TestCase):
    def test_compiled_languages(self):
        with tempfile.TemporaryDirectory(prefix='emaki-settings-search-') as temporary:
            base = Path(temporary)
            shutil.copytree(ROOT / 'shell', base / 'shell')
            fixture = base / 'shell/SettingsSearchTest.qml'
            fixture.write_text(QML)
            env = os.environ.copy()
            for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET',
                        'QT_QPA_PLATFORMTHEME', 'LANGUAGE', 'LC_ALL', 'LC_MESSAGES'):
                env.pop(key, None)
            for key in ('HOME', 'XDG_RUNTIME_DIR', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME',
                        'XDG_DATA_HOME', 'XDG_STATE_HOME'):
                directory = base / key
                directory.mkdir(mode=0o700)
                env[key] = str(directory)
            env.update(QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
                       EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'), QT_QPA_PLATFORM='offscreen',
                       QT_QUICK_BACKEND='software', QS_DISABLE_CRASH_HANDLER='1',
                       QML_DISABLE_DISK_CACHE='1', EMAKI_BIN='', LANG='en_US.UTF-8',
                       XDG_CONFIG_DIRS=env['XDG_CONFIG_HOME'], XDG_DATA_DIRS=env['XDG_DATA_HOME'],
                       DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                       DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
            for language in LANGUAGES:
                with self.subTest(language=language):
                    result = subprocess.run(['qs', '-p', str(fixture), '--no-color'],
                                            env=dict(env, EMAKI_LANGUAGE=language),
                                            capture_output=True, text=True, timeout=25)
                    output = result.stdout + result.stderr
                    line = next((line for line in output.splitlines() if 'SETTINGS_SEARCH ' in line), None)
                    self.assertEqual(result.returncode, 0, output)
                    self.assertIsNotNone(line, output)
                    for issue in ('ReferenceError', 'TypeError', 'Binding loop', 'Unable to assign'):
                        self.assertNotIn(issue, output)
                    actual = json.loads(line.split('SETTINGS_SEARCH ', 1)[1])
                    label = 'Settings' if language == 'en' else ET.parse(
                        ROOT / f'i18n/shell/{language}.ts').find(
                            ".//context[name='LauncherBody']/message[source='Settings']/translation").text
                    self.assertEqual(actual['label'], label)
                    scores = (34, 33, 34, None, 34, 33, 14, 14, 14, 28, None, None, None)
                    expected = [[{'label': label, 'score': score}] if score else [] for score in scores]
                    self.assertEqual(actual['rows'], expected)
                    self.assertEqual(actual['activations'], 1)
                    self.assertFalse(actual['assigning'])
                    print(f'PASS: {language}: 13 searches, translated label, activation and assignment exclusion')


if __name__ == '__main__':
    unittest.main()
