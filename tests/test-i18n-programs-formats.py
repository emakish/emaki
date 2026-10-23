#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise program counts, sizes, ratings, dates and refusals in the Qt runtime."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REGIONS = {'en': 'en_US.UTF-8', 'de': 'de_DE.UTF-8', 'fr': 'fr_FR.UTF-8',
           'es': 'es_MX.UTF-8', 'pt_BR': 'pt_BR.UTF-8', 'ru': 'ru_RU.UTF-8'}


def function(source, name):
    start = source.index('    function ' + name + '(')
    end = source.index('\n    }', start) + len('\n    }')
    return source[start:end]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=ROOT)
    args = parser.parse_args()
    source = args.source_root / 'update-manager/ui'
    with tempfile.TemporaryDirectory(prefix='emaki-program-formats-') as temporary:
        base = Path(temporary)
        (base / 'ManagerLogic.js').write_text((source / 'ManagerLogic.js').read_text().replace('@EMAKI_DATADIR@', str(ROOT)))
        (base / 'Stars.qml').write_text((source / 'Stars.qml').read_text().replace(
            'file:@EMAKI_DATADIR@/shell', (ROOT / 'shell').as_uri()))
        updates = (source / 'ManagerUpdates.qml').read_text()
        fixture = base / 'Formats.qml'
        fixture.write_text('''import QtQuick
import Quickshell
import Emaki.I18n
import "ManagerLogic.js" as Logic
ShellRoot {
    property var progress: ({step: 1, steps: 4, total: 0, label: "Große Vorbereitung"})
    Stars { id: stars; rating: ({average: 4.7, count: 14532}); showCount: true }
    DATE_FUNCTION
    STEP_FUNCTION
    Timer { interval: 100; running: true; onTriggered: {
        const stamp = new Date(2026, 9, 12, 14, 30);
        const display = Logic.decimal(60, 2);
        const originalLanguage = Qt.uiLanguage;
        Qt.uiLanguage = originalLanguage.startsWith("de") ? "en" : "de";
        const switched = Logic.decimal(60, 2);
        Qt.uiLanguage = originalLanguage;
        console.log("PROGRAM_FORMATS " + JSON.stringify({
            display: display, switched: switched,
            count: Logic.groupedCount(14532), size: Logic.downloadText(150200000),
            large: Logic.downloadText(1234500000000), average: stars.averageText,
            ratings: stars.fullText, compact: stars.compact(1200), exact: stars.compact(1000),
            huge: stars.compact(12345678), day: Logic.day(stamp), restore: date(stamp),
            progress: stepText(), refusal: Logic.undoRefusal({code: "cannot-undo", reason: "explicit"}),
            singular: Logic.undoRefusal({code: "cannot-undo", requiredBy: ["one"]}),
            plural: Logic.undoRefusal({code: "cannot-undo", requiredBy: ["one", "two"]})
        })); Qt.quit();
    } }
}
'''.replace('DATE_FUNCTION', function(updates, 'date')).replace('STEP_FUNCTION', function(updates, 'stepText')))
        env = os.environ.copy()
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME', 'LANGUAGE'):
            env.pop(key, None)
        for key in ('HOME', 'XDG_RUNTIME_DIR', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME'):
            directory = base / key
            directory.mkdir(mode=0o700)
            env[key] = str(directory)
        env.update(QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
                   EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'), QT_QPA_PLATFORM='offscreen',
                   QT_QUICK_BACKEND='software', QS_DISABLE_CRASH_HANDLER='1', QML_DISABLE_DISK_CACHE='1',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
        for language, region in REGIONS.items():
            result = subprocess.run(['qs', '-p', str(fixture), '--no-color'],
                                    env=dict(env, LANG=region, LC_ALL=region, EMAKI_LANGUAGE=region),
                                    capture_output=True, text=True, timeout=25)
            output = result.stdout + result.stderr
            line = next((line for line in output.splitlines() if 'PROGRAM_FORMATS ' in line), None)
            assert result.returncode == 0 and line, (language, output)
            assert not any(issue in output for issue in ('ReferenceError', 'TypeError', 'Binding loop')), output
            row = json.loads(line.split('PROGRAM_FORMATS ', 1)[1])
            decimal = '.' if language == 'en' else ','
            assert row['display'] == '60' + decimal + '00', (language, row)
            assert row['switched'] == ('60.00' if language == 'de' else '60,00'), (language, row)
            assert row['average'] == '4' + decimal + '7', (language, row)
            assert row['size'] == ('150,2\u00a0Mo' if language == 'fr' else '150' + decimal + '2 MB'), (language, row)
            assert row['large'] == ('1234,5\u00a0Go' if language == 'fr' else '1234' + decimal + '5 GB'), (language, row)
            suffix = {'de': ' Tsd.', 'pt_BR': ' mil', 'ru': ' тыс.'}.get(language, 'k')
            assert row['compact'] == '1' + decimal + '2' + suffix and row['exact'] == '1' + suffix and row['huge'] == '12346' + suffix, (language, row)
            assert 'Große Vorbereitung' in row['progress'], (language, row)
            assert row['count'] in row['ratings'], (language, row)
            if language == 'en':
                assert row['count'] == '14,532' and row['day'] == '12 Oct 2026, 14:30', row
                assert row['restore'] == 'Mon 12 Oct 2026, 14:30', row
                assert row['refusal'] == 'This install can no longer be taken back as it was. You installed it yourself since.', row
                assert row['singular'] == 'This install can no longer be taken back as it was. one needs what it added.', row
                assert row['plural'] == 'This install can no longer be taken back as it was. one, two need what it added.', row
            elif language == 'de':
                assert row['count'] == '14.532' and '12. Okt.' in row['day'], row
                assert row['restore'].startswith('Mo., 12. Okt.'), row
                assert row['refusal'].startswith('Diese Installation'), row
            elif language == 'es':
                assert row['count'] == '14.532', row
            elif language == 'ru':
                assert row['count'] == '14\u00a0532', row
                assert row['singular'] == 'Эту установку больше нельзя отменить в прежнем виде. Добавленные компоненты нужны для one.', row
                assert row['plural'] == 'Эту установку больше нельзя отменить в прежнем виде. Добавленные компоненты нужны для one, two.', row
            elif language == 'fr':
                assert row['count'] == '14\u202f532', row
                assert row['refusal'].startswith('Cette installation'), row
            print(language + ': ' + json.dumps(row, ensure_ascii=False))
    print('PASS: program display formats in six languages')


if __name__ == '__main__':
    main()
