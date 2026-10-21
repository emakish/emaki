#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify complete Brazilian Portuguese catalogues and their compiled fallbacks."""
import ast
from collections import Counter
import gettext
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DOMAINS = ('shell', 'programs', 'installer', 'notes', 'cli')
# Technical names, units and words whose Portuguese spelling is identical.
UNCHANGED = {
    '%1 %2', '%1 B', '%1 Hz', '%1 h %2 min', '1 min', '15 min', '25 min', '5 min',
    'Bluetooth', 'Command', 'command', 'Coral', 'Dock', 'Firmware', 'Internet', 'Layouts',
    'Login', 'Mouse', 'Normal', 'Pomodoro · 25 / 5', 'Secure Boot', 'Snapshot %1',
    'Snapshots', 'Software', 'Terminal', 'VPN', 'Volume', 'Web', 'Wi-Fi', 'WireGuard',
    '%1 GB', '%1 MB', '%1%', '%1k', '0 MB', '< 0.1 MB', 'Download', 'Flatpak ({scope})',
    '%n min', '%n h', '%n d', '%1 GiB', 'Manual', 'Windows', 'Software: ', 'Wi-Fi: ',
}
FIELDS = re.compile(r'%(?:L?\d+|L?n|\([^)]+\)[#0 +\-]*\d*(?:\.\d+)?[a-zA-Z]|[sdf]|%)|\{[^{}]*\}')
TAGS = re.compile(r'</?[A-Za-z][^>]*>')


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
    def contract(self, source, target):
        self.assertTrue(target.strip(), source)
        self.assertEqual(Counter(FIELDS.findall(source)), Counter(FIELDS.findall(target)), (source, target))
        self.assertEqual(TAGS.findall(source), TAGS.findall(target), (source, target))
        self.assertEqual(source.count('\n'), target.count('\n'), (source, target))
        self.assertEqual(len(source) - len(source.lstrip('\n')), len(target) - len(target.lstrip('\n')))
        self.assertEqual(len(source) - len(source.rstrip('\n')), len(target) - len(target.rstrip('\n')))
        # key={value} status lines keep their English field names.
        if re.search('[A-Za-z]', source) and source not in UNCHANGED and '={' not in source:
            self.assertNotEqual(source, target, 'Untranslated: ' + source)

    def test_qt_complete(self):
        for domain in DOMAINS[:-1]:
            path = ROOT / 'i18n' / domain / 'pt_BR.ts'
            self.assertEqual(ET.parse(path).getroot().get('language'), 'pt_BR')
            source = qt_entries(path.with_name('en.ts'))
            target = qt_entries(path)
            self.assertEqual(source.keys(), target.keys(), domain)
            for key, original in source.items():
                with self.subTest(domain=domain, message=key):
                    message = target[key]
                    self.assertEqual(original.get('numerus'), message.get('numerus'))
                    translated = message.find('translation')
                    self.assertNotIn(translated.get('type'), ('unfinished', 'vanished', 'obsolete'))
                    forms = translated.findall('numerusform')
                    self.assertEqual(len(forms), 2 if original.get('numerus') == 'yes' else 0)
                    for form in forms or [translated]:
                        self.contract(key[1], form.text or '')

    def test_gettext_complete_and_compiled(self):
        for domain in DOMAINS:
            base = ROOT / 'i18n' / domain
            source = {entry['msgid']: entry for entry in po_entries(base / (domain + '.pot')) if entry['msgid']}
            entries = po_entries(base / 'pt_BR.po')
            header = entries[0]['msgstr']
            self.assertIn('Language: pt_BR\n', header)
            self.assertIn('nplurals=2; plural=(n > 1);', header)
            target = {entry['msgid']: entry for entry in entries if entry['msgid']}
            self.assertEqual(source.keys(), target.keys(), domain)
            compiled = gettext.translation(domain, ROOT / 'i18n/locale', ['pt_BR'])
            for message, original in source.items():
                with self.subTest(domain=domain, message=message):
                    entry = target[message]
                    self.assertNotIn('fuzzy', entry.get('flags', ''))
                    self.assertEqual(original.get('msgid_plural'), entry.get('msgid_plural'))
                    if 'msgid_plural' in original:
                        self.assertEqual({key for key in entry if key.startswith('msgstr')}, {'msgstr[0]', 'msgstr[1]'})
                        for n in (0, 1, 2, 21):
                            value = entry['msgstr[' + str(int(n > 1)) + ']']
                            self.contract(message if n <= 1 else original['msgid_plural'], value)
                            self.assertEqual(compiled.ngettext(message, original['msgid_plural'], n), value)
                    else:
                        self.contract(message, entry['msgstr'])
                        self.assertEqual(compiled.gettext(message), entry['msgstr'])

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
