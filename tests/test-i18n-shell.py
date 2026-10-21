#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render every Welcome page with the real shell components and pseudo catalogue."""
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
QML = '''import QtQuick
import Quickshell
import Emaki.I18n
ShellRoot {
    FloatingWindow {
        id: window
        implicitWidth: 900; implicitHeight: 760
        Welcome { id: view; anchors.fill: parent }
    }
    NotificationStore {
        id: notices
        catalog: ({values: [{key: "notifications.rules", value: '{"name:Programs & Updates":"off"}'}]})
    }
    function checkNotificationIdentity() {
        const legacy = {id: 1, appName: "Programs & Updates", desktopEntry: "", appIcon: "fixture",
                        summary: "Fixture", body: "", actions: [], expireTimeout: 0};
        const hinted = Object.assign({}, legacy, {desktopEntry: "emaki-update-manager"});
        const key = "name:Programs & Updates";
        const before = notices.snapshot(legacy, Date.now());
        const after = notices.snapshot(hinted, Date.now());
        const count = notices.entries.length;
        notices.local("Programs & Updates", "Fixture", "");
        const display = notices.displayApp(legacy.appName);
        return notices.applicationId(legacy) === key && notices.applicationId(hinted) === key
            && before.appId === key && after.appId === key
            && notices.ruleFor(before.appId) === "off" && notices.ruleFor(after.appId) === "off"
            && !notices.peekAllowed(before) && !notices.peekAllowed(after)
            && notices.entries.length === count && display !== legacy.appName
            && display.startsWith("[") && display.endsWith("]")
            && notices.applicationId({appName: "Other", desktopEntry: "other.app"}) === "desktop:other.app";
    }
    property int checked: 0
    property bool capturing: false
    function inspect(item, strings) {
        if (!item || item.visible === false || item.opacity === 0) return;
        if (typeof item.text === "string" && item.text) strings.push(item.text);
        for (const child of item.children || []) inspect(child, strings);
    }
    Timer {
        interval: 700; running: true; repeat: true
        onTriggered: {
            if (capturing) return;
            if (!checkNotificationIdentity()) {
                console.error("I18N_SHELL_FAILED notification identity"); Qt.exit(1); return;
            }
            const strings = [];
            inspect(view, strings);
            const keys = ["Emaki", "Super", "Enter", "E", "D", "Q", "F", "R", "L", "O"];
            const unchanged = strings.filter(text => /[A-Za-z]/.test(text)
                && !keys.includes(text) && !(text.startsWith("[") && text.endsWith("]")));
            const translated = strings.filter(text => text.startsWith("[") && text.endsWith("]"));
            if (unchanged.length || translated.length < 4) {
                console.error("I18N_SHELL_FAILED page=" + view.page + " unchanged=" + JSON.stringify(unchanged) + " texts=" + JSON.stringify(strings));
                Qt.exit(1); return;
            }
            capturing = true;
            view.grabToImage(result => {
                if (!result.saveToFile(Quickshell.env("I18N_SHELL_SHOTS") + "/shell-welcome-en_XA-" + view.page + ".png")) {
                    console.error("I18N_SHELL_FAILED capture"); Qt.exit(1); return;
                }
                console.log("I18N_SHELL_PAGE " + view.page + " labels=" + translated.length);
                checked++;
                if (view.last) {
                    console.log("I18N_SHELL_OK pages=" + checked);
                    Qt.quit();
                } else { view.go(view.page + 1); capturing = false; }
            });
        }
    }
}
'''



# Source fragments recorded with git show release/0.10:<path>, revision
# 42c9a9eaa319fc041bdca16566b8b58ba6556128. Keys below name the original
# StaticBar properties, settings explanations, NotesView text and storage/update
# functions (shell/SettingsStoragePage.qml and update-manager/ui/ManagerUpdates.qml
# and ManagerView.qml), plus shell/settings/SettingsRow.qml and shell/PolkitText.js.
# They run in Qt beside current bindings; no historical objects are
# required in a one-commit source export.
RELEASE_010 = {'resetName': '"Reset " + root.title + " to default: " + root.formatValue(root.defaultValue)',
 'runProgram': 'function runProgram(name, user) {\n'
               '    return "Enter an administrator password to run " + (name || "a program")\n'
               '        + " as " + (user || "administrator") + ".";\n'
               '}',
 'clockTime': 'Qt.formatDateTime(dateTime, (SettingsBridge.values.find(row => row.key === '
              '"bar.clock_24_hour")?.value ?? true) ? "HH:mm" : "h:mm AP")',
 'clockDate': 'dayNames[dateTime.getDay()].slice(0, 3) + " " + dateTime.getDate() + " " + '
              'monthNames[dateTime.getMonth()].slice(0, 3)',
 'clockLongDate': 'dayNames[dateTime.getDay()] + ", " + dateTime.getDate() + " " + '
                  'monthNames[dateTime.getMonth()]',
 'dayNames': '["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]',
 'monthNames': '["January", "February", "March", "April", "May", "June", "July", "August", "September", '
               '"October", "November", "December"]',
 'temperature': 'value + " K. Choose how warm Night Light makes the display."',
 'repeatDelay': 'value + " ms before a held key starts repeating."',
 'notes': '"Local notes · " + root.filtered.length',
 'bytes': '    function bytes(value): string {\n'
          '        return value === null || value === undefined ? "Unknown" : '
          'value.toLocaleString(Qt.locale("en_US"), "f", 0) + " B";\n'
          '    }',
 'date': '    function date(value: var): string {\n'
         '        if (!value) return "";\n'
         '        const parsed = new Date(value);\n'
         '        return isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString(Qt.locale("en_GB"), '
         '"ddd d MMM yyyy, HH:mm");\n'
         '    }',
 'planDownload': '    function planDownload(): string {\n'
                 '        const rows = programs.plan?.transaction?.packages || [];\n'
                 '        if (!rows.length || rows.some(row => row.downloadSize === null || row.downloadSize '
                 '=== undefined)) return "Download size unknown";\n'
                 '        return Math.ceil(rows.reduce((total, row) => total + row.downloadSize, 0) / '
                 '1048576) + " MB to download in total";\n'
                 '    }'}


def source_function(source, name):
    return re.search(r'    function ' + name + r'\([^\n]*\n.*?^    }', source, re.M | re.S).group(0)


def english_template():
    """Render current bindings and recorded release/0.10 source in the same Qt engine."""
    bindings = []
    for filename, marker, key in (
        ('StaticBar.qml', 'readonly property string clockTime:', 'clockTime'),
        ('StaticBar.qml', 'readonly property string clockDate:', 'clockDate'),
        ('StaticBar.qml', 'readonly property string clockLongDate:', 'clockLongDate'),
        ('DisplaysSettingsPage.qml', 'explanation: qsTranslate("DisplaysSettingsPage", "%1 K.', 'temperature'),
        ('KeyboardSettingsPage.qml', 'explanation: qsTranslate("KeyboardSettingsPage", "%1 ms', 'repeatDelay'),
    ):
        line = next(line.strip() for line in (ROOT / 'shell' / filename).read_text().splitlines()
                    if line.strip().startswith(marker))
        expression = line.split(':', 1)[1].strip()
        expected = RELEASE_010[key]
        for name in ('dateTime', 'dayNames', 'monthNames'):
            expression = expression.replace(name, 'window.' + name)
            expected = expected.replace(name, 'window.' + name)
        expected = expected.replace('SettingsBridge.values', '[]')
        value = 4000 if key == 'temperature' else 1000
        bindings.append('Text { property int value: %s; text: %s; property string expected: %s }'
                        % (value, expression, expected))
    objects = []
    current = []
    for name, filename in (('bytes', 'shell/SettingsStoragePage.qml'),
                           ('date', 'update-manager/ui/ManagerUpdates.qml'),
                           ('planDownload', 'update-manager/ui/ManagerView.qml')):
        current.append(source_function((ROOT / filename).read_text(), name))
    for identity, functions in [('candidate', current), ('baseline', [RELEASE_010[name] for name in ('bytes', 'date', 'planDownload', 'runProgram')])]:
        objects.append('QtObject { id: ' + identity + '; property var programs: window.programs\n'
                       + '\n'.join(functions) + '\n}')
    for call in ('bytes(null)', 'bytes(0)', 'bytes(1234567)', 'bytes(1234567.6)',
                 'date("")', 'date("invalid")', 'date("2026-09-21T14:30:00")',
                 'planDownload()'):
        bindings.append('Text { text: candidate.' + call + '; property string expected: baseline.' + call + ' }')
    notes = next(line for line in (ROOT / 'shell/NotesView.qml').read_text().splitlines() if 'Local notes' in line)
    expression = notes.split('text: ', 1)[1].split('; color:', 1)[0]
    bindings.append('Text { property var root: ({filtered: new Array(1000)}); text: ' + expression
                    + '; property string expected: ' + RELEASE_010['notes'] + ' }')
    return '''import QtQuick
import Quickshell
import Emaki.I18n
import "PolkitText.js" as PolkitText
import "settings"
ShellRoot {
    FloatingWindow {
        id: window
        implicitWidth: 900; implicitHeight: 400
        property date dateTime: new Date(2026, @MONTH@, 21, 14, 30)
        property var dayNames: ''' + RELEASE_010['dayNames'] + '''
        property var monthNames: ''' + RELEASE_010['monthNames'] + '''
        property var programs: ({plan: {transaction: {packages: [{downloadSize: 2000000000000}]}}})
''' + '\n'.join(objects) + '''
        Column { id: labels; anchors.fill: parent
''' + '\n'.join(bindings) + '''
        }
        SettingsRow { id: resetRow; title: "title%2"; defaultValue: "value%1"; visible: false }
        CalendarMonth { id: calendar; year: 2026; month: 9; y: 140 }
        ClockCompactRow { id: clock; time: "09:11"; date: "Wed 21 Oct"; y: 330 }
    }
    Timer {
        interval: 600; running: true
        onTriggered: {
            for (const label of labels.children) {
                if (label.text !== label.expected) {
                    console.error("I18N_ENGLISH_FAILED " + label.text + " != " + label.expected);
                    Qt.exit(1); return;
                }
            }
            if (resetRow.resetItem.Accessible.name !== (''' + RELEASE_010['resetName'].replace('root.', 'resetRow.') + ''') || calendar.start !== 3 || PolkitText.runProgram("name%2", "user%1") !== baseline.runProgram("name%2", "user%1")) {
                console.error("I18N_ENGLISH_FAILED calendar or placeholders"); Qt.exit(1); return;
            }
            for (const amount of [0, 1048576, 1048576000000, 2000000000000]) {
                window.programs = {plan: {transaction: {packages: [{downloadSize: amount}]}}};
                if (candidate.planDownload() !== baseline.planDownload()) {
                    console.error("I18N_ENGLISH_FAILED download " + amount);
                    Qt.exit(1); return;
                }
            }
            const width = clock.naturalWidth;
            clock.time = "23:59";
            if (clock.naturalWidth !== width) {
                console.error("I18N_ENGLISH_FAILED clock width"); Qt.exit(1); return;
            }
            labels.grabToImage(result => {
                if (!result.saveToFile(Quickshell.env("I18N_SHELL_SHOTS") + "/shell-en_US-formats.png")) {
                    Qt.exit(1); return;
                }
                console.log("I18N_ENGLISH_OK"); Qt.quit();
            });
        }
    }
}
'''


def english_qml(month):
    """The comparison fixture on the 21st of a month (0-based, as JavaScript's Date)."""
    return english_template().replace('@MONTH@', str(month))


def main():
    output = ROOT / '.cache/evidence/i18n'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='emaki-i18n-shell-', dir='/tmp') as directory:
        base = Path(directory)
        shutil.copytree(ROOT / 'shell', base / 'shell')
        entry = base / 'shell/PseudoTest.qml'
        entry.write_text(QML)
        for name in ('home', 'config', 'data', 'cache', 'state', 'runtime'):
            (base / name).mkdir(mode=0o700)
        env = dict(os.environ, HOME=str(base / 'home'), XDG_CONFIG_HOME=str(base / 'config'),
                   XDG_CONFIG_DIRS=str(base / 'config'), XDG_DATA_HOME=str(base / 'data'),
                   XDG_DATA_DIRS=str(base / 'data'), XDG_CACHE_HOME=str(base / 'cache'),
                   XDG_STATE_HOME=str(base / 'state'), XDG_RUNTIME_DIR=str(base / 'runtime'),
                   QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QT_QUICK_CONTROLS_STYLE='Basic', QML_DISABLE_DISK_CACHE='1',
                   QS_DISABLE_CRASH_HANDLER='1', EMAKI_LANGUAGE='en_XA',
                   EMAKI_BIN='false', EMAKI_LIVE_SESSION='0',
                   EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'),
                   QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml') + os.pathsep + os.environ.get('QML_IMPORT_PATH', ''),
                   I18N_SHELL_SHOTS=str(output),
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
                   DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
        for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
            env.pop(key, None)
        english = base / 'shell/EnglishFormats.qml'
        # en_GB in September: Qt names that month "Sept" there, 0.10 said "Sep" in every English locale.
        # The en_GB run has no EMAKI_LANGUAGE, as a real session: Qt then takes its locale from LC_ALL.
        for locale, month, language in (('en_US.UTF-8', 9, 'en'), ('en_GB.UTF-8', 8, None)):
            english.write_text(english_qml(month))
            english_env = dict(env, LC_ALL=locale)
            english_env.pop('EMAKI_LANGUAGE', None)
            if language:
                english_env['EMAKI_LANGUAGE'] = language
            english_result = subprocess.run(['qs', '-p', str(english)], env=english_env, text=True,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=25)
            assert english_result.returncode == 0 and 'I18N_ENGLISH_OK' in english_result.stdout, (locale, english_result.stdout)
            for issue in ('ReferenceError', 'TypeError', 'Binding loop'):
                assert issue not in english_result.stdout, (locale, english_result.stdout)
        print('I18N_ENGLISH_OK')
        result = subprocess.run(['qs', '-p', str(entry)], env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=25)
        assert result.returncode == 0 and 'I18N_SHELL_OK pages=5' in result.stdout, result.stdout
        for issue in ('ReferenceError', 'TypeError', 'I18N_SHELL_FAILED', 'Binding loop'):
            assert issue not in result.stdout, result.stdout
        print('\n'.join(line for line in result.stdout.splitlines() if 'I18N_SHELL_' in line))
        print(output)


if __name__ == '__main__':
    main()
