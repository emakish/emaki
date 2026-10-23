#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render translated Settings buttons at the smallest widths used by their rows."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('renderer', ROOT / 'tests/render-shell.py')
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)
QML = '''import QtQuick
import QtQuick.Controls
import Emaki.I18n
import "settings"
Rectangle {
    id: root
    width: 380; height: 280; color: "#fffaf8"
    Column {
        anchors.centerIn: parent; spacing: 14
        SettingsButton { id: record; width: RECORD_WIDTH; text: qsTranslate("KeyboardSettingsPage", "Record") }
        SettingsButton { id: restore; width: 150; text: qsTranslate("SettingsMaintenancePage", "Review snapshot") }
        SettingsButton { id: reset; width: 150; text: qsTranslate("SettingsMaintenancePage", "Reset…") }
        SettingsButton { id: report; width: 160; text: qsTranslate("SettingsAboutPage", "Save diagnostics…") }
        SettingsComboBox { id: silent; width: 180; model: [{label: qsTranslate("NotificationsSettingsPage", "Silent"), value: "silent"}] }
        SettingsButton { id: password; width: 150; PASSWORD_PROPERTIES }
    }
    function inspect(button) {
        const label = button.contentItem;
        return {text: button.displayText !== undefined ? button.displayText : button.text, width: button.width, labelWidth: label.width,
                contentWidth: label.contentWidth, truncated: label.truncated,
                accessible: button.Accessible.description || button.Accessible.name, tooltip: button.ToolTip.text};
    }
    readonly property var renderStats: ({silentDescription: silent.Accessible.description, silentText: silent.displayText, passwordName: qsTranslate("SettingsMaintenancePage", "Change password"), buttons: [record, restore, reset, report, silent, password].map(inspect)})
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--button-revision', help='render the button implementation from this revision')
    args = parser.parse_args()
    output = ROOT / '.cache/evidence/i18n/buttons' / ('before' if args.button_revision else 'after')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='emaki-i18n-buttons-') as temporary:
        base = Path(temporary)
        shutil.copytree(ROOT / 'shell', base / 'shell')
        if args.button_revision:
            source = subprocess.check_output(['git', '-C', str(ROOT), 'show',
                                              args.button_revision + ':shell/settings/SettingsButton.qml'])
            (base / 'shell/settings/SettingsButton.qml').write_bytes(source)
            keyboard = subprocess.check_output(['git', '-C', str(ROOT), 'show',
                                                args.button_revision + ':shell/KeyboardSettingsPage.qml'], text=True)
        else:
            keyboard = (ROOT / 'shell/KeyboardSettingsPage.qml').read_text()
        record = re.search(r'SettingsButton\s*\{([^{}]*?)text: editor.recording', keyboard, re.S).group(1)
        record_width = re.search(r'\bwidth: ([^\n]+)', record).group(1)
        fixture = base / 'shell/Buttons.qml'
        maintenance = (ROOT / 'shell/SettingsMaintenancePage.qml').read_text()
        password = re.search(r'SettingsButton\s*\{\s*(text: qsTranslate\("SettingsMaintenancePage", "Change…"\).*?)\s*onClicked:', maintenance, re.S).group(1)
        fixture.write_text(QML.replace('RECORD_WIDTH', record_width).replace('PASSWORD_PROPERTIES', password))
        failures = []
        for language, region in [('de', 'de_DE'), ('fr', 'fr_FR'), ('ru', 'ru_RU')]:
            stats = output / (language + '.json')
            log = renderer.render(fixture, png=output / (language + '.png'), stats=stats,
                                  width=380, height=280, milliseconds=1, warmup=800, extra_env={
                                      'LANG': region + '.UTF-8', 'LC_ALL': region + '.UTF-8',
                                      'EMAKI_LANGUAGE': language, 'EMAKI_LOCALE_DIR': str(ROOT / 'i18n/locale'),
                                      'QML_IMPORT_PATH': str(renderer.build_plugin()) + os.pathsep + str(ROOT / '.cache/i18n-native/qml'),
                                      'QT_QUICK_CONTROLS_STYLE': 'Basic'})
            (output / (language + '.log')).write_text(log)
            assert not any(error in log for error in ('ReferenceError:', 'TypeError:', 'Binding loop')), log
            measured = json.loads(stats.read_text())['fixture_end']
            assert measured['silentDescription'] == measured['silentText'], (language, measured)
            buttons = measured['buttons']
            assert buttons[-1]['accessible'] == buttons[-1]['tooltip'] == measured['passwordName'], (language, measured)
            assert buttons[-1]['text'] != measured['passwordName'], (language, measured)
            for button in buttons:
                if button['contentWidth'] > button['labelWidth'] + 1:
                    failures.append((language, 'text exceeds content bounds', button))
                if button['truncated'] and not button['text'] == button['accessible'] == button['tooltip']:
                    failures.append((language, 'shortened label lacks full text', button))
            print(f'{language}: rendered and measured {len(buttons)} buttons', flush=True)
        assert not failures, failures
    print('PASS: button labels fit or expose their full text')


if __name__ == '__main__':
    main()
