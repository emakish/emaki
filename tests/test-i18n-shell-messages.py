#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise complete About summaries and notification application display names."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = {'en': 'en_US', 'es': 'es_MX', 'pt_BR': 'pt_BR', 'de': 'de_DE', 'fr': 'fr_FR', 'ru': 'ru_RU'}
NAMES = {'en': 'Emaki Calendar', 'es': 'Calendario de Emaki', 'pt_BR': 'Calendário do Emaki', 'de': 'Emaki-Kalender', 'fr': 'Calendrier Emaki', 'ru': 'Календарь Emaki'}
UNKNOWN = {'en': 'Version and channel unavailable', 'es': 'Versión y canal no disponibles', 'pt_BR': 'Versão e canal indisponíveis', 'de': 'Version und Kanal nicht verfügbar', 'fr': 'Version et canal indisponibles', 'ru': 'Версия и канал неизвестны'}

def main():
    about = (ROOT / 'shell/SettingsAboutPage.qml').read_text()
    expression = re.search(r'objectName: "settings-about-version"\s+(?://[^\n]+\s+)*text: ([^\n]+)', about)[1]
    channel = re.search(r'readonly property string channelLabel: ([^\n]+)', about)[1]
    display = re.search(r'    function displayApp\(.*?^    }', (ROOT / 'shell/NotificationStore.qml').read_text(), re.S | re.M)[0]
    qml = '''import QtQuick
import Quickshell
import Emaki.I18n
ShellRoot {
    DISPLAY
    function about(details) {
        const channelLabel = CHANNEL;
        return SUMMARY;
    }
    Timer { interval: 100; running: true; onTriggered: {
        console.log("MESSAGES " + JSON.stringify([displayApp("Emaki Calendar"), displayApp("Third-party name"), about({}), about({version: "0.12", channel: "stable"}), about({version: "0.12"}), about({channel: "testing"})]));
        Qt.quit();
    } }
}'''.replace('DISPLAY', display).replace('CHANNEL', channel).replace('SUMMARY', expression.replace('page.details', 'details').replace('page.channelLabel', 'channelLabel'))
    failures = []
    with tempfile.TemporaryDirectory(prefix='emaki-shell-messages-') as temporary:
        base = Path(temporary)
        fixture = base / 'Messages.qml'
        fixture.write_text(qml)
        env = os.environ.copy()
        for key in ('EMAKI_LANGUAGE', 'LANGUAGE', 'LC_MESSAGES', 'DISPLAY', 'WAYLAND_DISPLAY'):
            env.pop(key, None)
        env.update(QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'), EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'), QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software', QS_DISABLE_CRASH_HANDLER='1')
        for language, region in LANGUAGES.items():
            result = subprocess.run(['qs', '-p', str(fixture), '--no-color'], env=dict(env, LANG=region+'.UTF-8', LC_ALL=region+'.UTF-8'), capture_output=True, text=True, timeout=20)
            output = result.stdout + result.stderr
            line = next((line for line in output.splitlines() if 'MESSAGES ' in line), None)
            assert result.returncode == 0 and line, output
            values = json.loads(line.split('MESSAGES ', 1)[1])
            checks = [values[0] == NAMES[language], values[1] == 'Third-party name', values[2] == UNKNOWN[language], '0.12' in values[3], '0.12' in values[4], values[5] != values[2]]
            if language == 'en':
                checks.append(values[3:] == ['Version 0.12 · Stable channel', 'Version 0.12 · channel unavailable', 'Version unavailable · Testing channel'])
            failures.extend((language, index, values) for index, passed in enumerate(checks) if not passed)
            print(language, values)
    assert not failures, failures
    print('PASS: six languages preserve names and use complete summaries for all four availability states')

if __name__ == '__main__':
    main()
