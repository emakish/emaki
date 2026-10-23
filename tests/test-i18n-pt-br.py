#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify complete Brazilian Portuguese catalogues and their compiled fallbacks."""
import ast
import gettext
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DOMAINS = ('shell', 'programs', 'installer', 'notes', 'cli')

def po_entries(path):
    entries, entry, key = [], {}, None
    for line in path.read_text().splitlines() + ['']:
        if not line:
            if entry:
                entries.append(entry)
            entry, key = {}, None
        elif line.startswith('#,'):
            entry['flags'] = line
        elif line.startswith('#'):
            continue
        elif line.startswith('"'):
            entry[key] += ast.literal_eval(line)
        else:
            key, value = line.split(' ', 1)
            entry[key] = ast.literal_eval(value)
    return entries


def qt_entries(path):
    return {(context.findtext('name'), message.findtext('source'), message.findtext('comment', '')): message
            for context in ET.parse(path).findall('context') for message in context.findall('message')}


def environment(base, language):
    env = os.environ.copy()
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET', 'QT_QPA_PLATFORMTHEME'):
        env.pop(key, None)
    for key in ('HOME', 'XDG_RUNTIME_DIR', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME', 'XDG_STATE_HOME'):
        directory = base / ('run' if key == 'XDG_RUNTIME_DIR' else key)
        directory.mkdir(mode=0o700, exist_ok=True)
        env[key] = str(directory)
    env.update(QML_IMPORT_PATH=str(ROOT / '.cache/i18n-native/qml'),
               EMAKI_LOCALE_DIR=str(ROOT / 'i18n/locale'), EMAKI_LANGUAGE=language,
               QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
               QS_DISABLE_CRASH_HANDLER='1', QML_DISABLE_DISK_CACHE='1',
               DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(base / 'no-bus'),
               DBUS_SYSTEM_BUS_ADDRESS='unix:path=' + str(base / 'no-system-bus'))
    return env


class Portuguese(unittest.TestCase):
    def test_shared_completeness_contract(self):
        spec = importlib.util.spec_from_file_location('catalogue_contract', ROOT / 'i18n/check_catalogues.py')
        check = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(check)
        unchanged = json.loads((ROOT / 'i18n/pt_BR-unchanged.json').read_text())['messages']
        for domain in DOMAINS:
            base = ROOT / 'i18n' / domain
            if domain != 'cli':
                check.check_qt(base / 'en.ts', base / 'pt_BR.ts', 'pt_BR', unchanged)
            check.check_po(base / (domain + '.pot'), base / 'pt_BR.po', 'pt_BR', unchanged)

    def test_plain_portuguese_aliases(self):
        base = ROOT / 'i18n/locale'
        for domain in DOMAINS:
            self.assertEqual((base / 'pt/LC_MESSAGES' / (domain + '.mo')).read_bytes(),
                             (base / 'pt_BR/LC_MESSAGES' / (domain + '.mo')).read_bytes())
            if domain != 'cli':
                self.assertEqual((base / (domain + '_pt.qm')).read_bytes(),
                                 (base / (domain + '_pt_BR.qm')).read_bytes())

    def test_qt_runtime_every_message_and_plural(self):
        entries = []
        for domain in DOMAINS[:-1]:
            for (context, source, comment), message in qt_entries(ROOT / 'i18n' / domain / 'pt_BR.ts').items():
                forms = message.findall('translation/numerusform')
                for n in (0, 1, 2, 21) if forms else (-1,):
                    translated = forms[int(n > 1)].text if forms else message.findtext('translation')
                    entries.append([context, source, comment, n, translated.replace('%n', str(n))])
        qml = '''import QtQuick
import Quickshell
import Emaki.I18n
ShellRoot {
 property var entries: ENTRIES
 Timer { interval: 10; running: true; onTriggered: {
  for (const entry of entries) {
   const actual = qsTranslate(entry[0], entry[1], entry[2], entry[3]);
   if (actual !== entry[4]) { console.error("PORTUGUESE_MISMATCH", JSON.stringify(entry), actual); Qt.exit(1); return; }
  }
  console.log("PORTUGUESE_QT_OK", entries.length); Qt.quit();
 } }
}
'''.replace('ENTRIES', json.dumps(entries, ensure_ascii=False))
        with tempfile.TemporaryDirectory(prefix='pt-br-runtime-') as directory:
            base = Path(directory)
            fixture = base / 'test.qml'
            fixture.write_text(qml)
            for language in ('pt_BR', 'pt_BR.UTF-8', 'pt'):
                result = subprocess.run(['qs', '-p', str(fixture), '--no-color'],
                                        env=environment(base, language), text=True, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('PORTUGUESE_QT_OK', result.stdout + result.stderr)

    def test_installer_offers_packaged_portuguese(self):
        spec = importlib.util.spec_from_file_location('installer_i18n', ROOT / 'installer/emaki_installer/i18n.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertIn('pt_BR.UTF-8', module.available_locales())
        with tempfile.TemporaryDirectory(prefix='pt-br-picker-') as directory:
            from unittest.mock import patch
            with patch.dict(os.environ, {'EMAKI_LOCALE_DIR': directory}):
                self.assertEqual(module.available_locales(), ['en_US.UTF-8'])


if __name__ == '__main__':
    unittest.main()
