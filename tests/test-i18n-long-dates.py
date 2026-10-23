#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Long dates and the calendar month title follow each language's catalogue pattern («21 de octubre»)."""
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SHORT_DATES = {
    "es_MX.UTF-8": ("mié 21 de oct", "21 de oct de 2026"),
    "pt_BR.UTF-8": ("qua., 21 out.", "21 de out. de 2026"),
    "de_DE.UTF-8": ("Mi. 21. Okt.", "21. Okt. 2026"),
    "fr_FR.UTF-8": ("mer. 21 oct.", "21 oct. 2026"),
    "ru_RU.UTF-8": ("ср, 21 окт.", "21 окт. 2026 г."),
    "en_US.UTF-8": ("Wed 21 Oct", "Oct 21, 2026"),
}

CASES = {
    'es_MX.UTF-8': ('miércoles, 21 de octubre', 'octubre de 2026'),
    'pt_BR.UTF-8': ('quarta-feira, 21 de outubro', 'outubro de 2026'),
    'de_DE.UTF-8': ('Mittwoch, 21. Oktober', 'Oktober 2026'),
    'fr_FR.UTF-8': ('mercredi 21 octobre', 'octobre 2026'),
    'ru_RU.UTF-8': ('среда, 21 октября', 'октябрь 2026'),
    'en_US.UTF-8': ('Wednesday, 21 October', 'October 2026'),
}


def binding(filename, marker):
    """The expression of a one-line property binding, as the shell has it."""
    line = next(line.strip() for line in (ROOT / 'shell' / filename).read_text().splitlines()
                if line.strip().startswith(marker))
    return line.split(':', 1)[1].strip()


QML = '''import QtQuick
import Quickshell
import Emaki.I18n
import "NumberLocale.js" as Numbers
ShellRoot {
    id: root
    property date dateTime: new Date(2026, 9, 21, 14, 30)
    property string headingDate: "2026-10-21"
    property var mode: ({width: 1920, height: 1080, refresh_rate: 59940})
    WIRE
    readonly property string refreshRate: RATE
    readonly property string clockShortDate: SHORT
    readonly property string noteDate: NOTE
    readonly property string clockLongDate: CLOCK
    readonly property string dateHeading: HEADING
    CalendarMonth { id: calendar; year: 2026; month: 9 }
    function texts(item) {
        let found = item.text !== undefined ? [item.text] : [];
        for (const child of item.children)
            found = found.concat(texts(child));
        return found;
    }
    Timer { interval: 300; running: true; onTriggered: {
        console.log("LONG_DATES " + JSON.stringify([root.clockLongDate, root.dateHeading,
                                                   root.texts(calendar).filter(text => /2026/.test(text)), root.clockShortDate, root.noteDate, root.refreshRate, root.wireMode(root.mode)]));
        Qt.quit();
    } }
}
'''.replace('CLOCK', binding('StaticBar.qml', 'readonly property string clockLongDate:')) \
   .replace('WIRE', re.search(r'    function wireMode\(.*?^    }', (ROOT / 'shell/DisplaysSettingsPage.qml').read_text(), re.M | re.S).group(0)) \
   .replace('RATE', next(line.strip().removeprefix('label: ') for line in (ROOT / 'shell/DisplaysSettingsPage.qml').read_text().splitlines() if 'label: qsTranslate("DisplaysSettingsPage", "%1 Hz")' in line)) \
   .replace('SHORT', binding('StaticBar.qml', 'readonly property string clockDate:')) \
   .replace('NOTE', 'dateTime' + (ROOT / 'shell/NotesView.qml').read_text().split('text: new Date(entry.modelData.updated * 1000)', 1)[1].split('; color:', 1)[0]) \
   .replace('HEADING', binding('CalendarEvents.qml', 'readonly property string dateHeading:'))


def main():
    with tempfile.TemporaryDirectory(prefix='emaki-long-dates-') as temporary:
        base = Path(temporary)
        shutil.copytree(ROOT / 'shell', base / 'shell')
        fixture = base / 'shell/LongDatesTest.qml'
        fixture.write_text(QML)
        env = os.environ.copy()
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME', 'LC_ALL',
                    'EMAKI_LANGUAGE', 'LANGUAGE', 'LC_MESSAGES', 'LC_TIME'):
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
        for region, (long_date, month_title) in CASES.items():
            result = subprocess.run(['qs', '-p', str(fixture), '--no-color'], env=dict(env, LANG=region, LC_MESSAGES=region, LC_NUMERIC='de_DE.UTF-8' if region.startswith('en_') else 'en_US.UTF-8'),
                                    capture_output=True, text=True, timeout=25)
            output = result.stdout + result.stderr
            line = next((line for line in output.splitlines() if 'LONG_DATES ' in line), None)
            assert result.returncode == 0 and line, (region, output)
            for issue in ('ReferenceError', 'TypeError', 'Binding loop'):
                assert issue not in output, (region, output)
            clock, heading, titles, short, note, rate, wire = json.loads(line.split('LONG_DATES ', 1)[1])
            assert (clock, heading, titles) == (long_date, long_date[0].upper() + long_date[1:] if region.startswith('ru_') else long_date, [month_title[0].upper() + month_title[1:] if region.startswith('ru_') else month_title]), (region, clock, heading, titles)
            assert (short, note) == SHORT_DATES[region], (region, short, note)
            assert wire == '1920x1080@59.940', (region, wire)
            assert rate.startswith('59.94' if region.startswith('en_') else '59,94'), (region, rate)
            print(f'{region}: {clock} · {month_title} · {short} · {note} · {rate}')
        result = subprocess.run(['qs', '-p', str(fixture), '--no-color'],
                                env=dict(env, LANG='en_US.UTF-8', LC_MESSAGES='en_US.UTF-8',
                                         LC_TIME='ru_RU.UTF-8'),
                                capture_output=True, text=True, timeout=25)
        output = result.stdout + result.stderr
        line = next((line for line in output.splitlines() if 'LONG_DATES ' in line), None)
        assert result.returncode == 0 and line, output
        assert not any(issue in output for issue in ('ReferenceError', 'TypeError', 'Binding loop')), output
        clock, heading, titles, short, note, rate, wire = json.loads(line.split('LONG_DATES ', 1)[1])
        assert clock.startswith('Wednesday') and heading.startswith('Среда'), (clock, heading)
        assert titles == ['Октябрь 2026'], titles
        print('en_US.UTF-8 with ru_RU.UTF-8 time formats: Russian headings capitalized')
    print('PASS: long dates and month titles follow the catalogue pattern')


if __name__ == '__main__':
    main()
