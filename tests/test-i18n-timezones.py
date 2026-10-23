#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise localized zone labels, searches and stable command IDs in real Qt."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
CASES = {'en_US': 'Europe/Moscow', 'es_MX': 'Moscú', 'pt_BR': 'Moscou',
         'de_DE': 'Moskau', 'fr_FR': 'Moscou', 'ru_RU': 'Москва'}
QML = '''import QtQuick
import Quickshell
import Emaki.I18n
ShellRoot {
    id: root
    QtObject {
        id: backend
        property var commands: []
        property var state: ({search: ["Europe/Moscow"],
            world: [{zone: "Europe/Moscow", time: "12:00", date: "2026-10-23", offset: "+03:00"}]})
        function send(command) { commands.push(command); }
    }
    ClockTools { id: clocks; width: 500; height: 800; page: 3; service: backend }
    SettingsRegionPage { id: region; width: 800 }
    function texts(item) {
        let found = item.text !== undefined ? [item.text] : [];
        for (const child of item.children || []) found = found.concat(texts(child));
        return found;
    }
    function named(item, name) {
        if (item.objectName === name) return item;
        for (const child of item.children || []) {
            const found = named(child, name);
            if (found) return found;
        }
        return null;
    }
    Timer { interval: 300; running: true; onTriggered: {
        region.timeZones = ["Europe/Moscow", "America/New_York"];
        const label = region.choiceLabel("time.zone", "Europe/Moscow");
        clocks.worldQuery = label;
        root.named(clocks, "clock-world-add-Europe/Moscow").clicked();
        root.named(clocks, "clock-world-remove-Europe/Moscow").clicked();
        const options = region.options("time.zone");
        region.timeZones.push("W-SU");
        console.log("TIMEZONES " + JSON.stringify({label: label,
            options: options, duplicateOptions: region.options("time.zone"), texts: root.texts(clocks),
            search: clocks.worldSearch, commands: backend.commands,
            newYork: TimeZones.label("America/New_York", Qt.uiLanguage),
            unknown: TimeZones.label("Unknown/Unknown_City", Qt.uiLanguage),
            emptySearch: TimeZones.search("  ", Qt.uiLanguage),
            utcSearch: TimeZones.search("UTC", "ru"),
            nativeSearch: TimeZones.search(label.toUpperCase(), Qt.uiLanguage)}));
        Qt.quit();
    } }
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix='emaki-zone-labels-') as temporary:
        base = Path(temporary)
        shutil.copytree(ROOT / 'shell', base / 'shell')
        fixture = base / 'shell/TimeZonesTest.qml'
        fixture.write_text(QML)
        binaries = base / 'bin'
        binaries.mkdir()
        backend = binaries / 'emaki-machine-settings'
        backend.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"schema_version":1,"status":"read","settings":[],"locales":[],"time_zones":[]}\'\n')
        backend.chmod(0o755)
        env = os.environ.copy()
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET',
                    'QT_QPA_PLATFORMTHEME', 'LANGUAGE', 'LC_MESSAGES', 'LC_TIME'):
            env.pop(key, None)
        for key in ('HOME', 'XDG_RUNTIME_DIR', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME',
                    'XDG_DATA_HOME', 'XDG_STATE_HOME'):
            directory = base / key
            directory.mkdir(mode=0o700)
            env[key] = str(directory)
        env.update(QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
                   EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'), QT_QPA_PLATFORM='offscreen',
                   QT_QUICK_BACKEND='software', QS_DISABLE_CRASH_HANDLER='1', QML_DISABLE_DISK_CACHE='1',
                   PATH=str(binaries) + os.pathsep + env['PATH'],
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
        for language, city in CASES.items():
            result = subprocess.run(['qs', '-p', str(fixture), '--no-color'],
                                    env=dict(env, EMAKI_LANGUAGE=language, LANG='C.UTF-8', LC_ALL='C.UTF-8'),
                                    capture_output=True, text=True, timeout=25)
            output = result.stdout + result.stderr
            line = next((line for line in output.splitlines() if 'TIMEZONES ' in line), None)
            assert result.returncode == 0 and line, (language, output)
            assert not any(issue in output for issue in ('ReferenceError', 'TypeError', 'Binding loop')), output
            data = json.loads(line.split('TIMEZONES ', 1)[1])
            assert data['label'] == city, (language, data)
            assert city in data['texts'] and '12:00  ' + city in data['texts'], (language, data)
            assert data['options'][0] == {'label': city, 'value': 'Europe/Moscow'}, data
            assert data['commands'] == [{'op': 'world_add', 'zone': 'Europe/Moscow'},
                                        {'op': 'world_remove', 'zone': 'Europe/Moscow'}], data
            assert 'Europe/Moscow' in data['search'], data
            assert data['emptySearch'] == [], data
            assert not any(zone.startswith('UTC+') for zone in data['utcSearch']), data
            expected_labels = ([city + ' (Europe/Moscow)', data['newYork'], city + ' (W-SU)']
                               if language != 'en_US' else [city, data['newYork'], 'W-SU'])
            assert [option['label'] for option in data['duplicateOptions']] == expected_labels, data
            if language != 'en_US':
                assert 'Europe/Moscow' in data['nativeSearch'], data
            else:
                assert data['newYork'] == 'America/New York', data
            assert data['unknown'] == 'Unknown/Unknown City', data
            print(f'{language}: {city}; localized search and stable IDs passed')
    print('PASS: time-zone labels in world clocks and Region use the UI language')


if __name__ == '__main__':
    main()
