#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Require complete Spanish catalogues and exercise regional fallback in Qt/gettext."""
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
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DOMAINS = ('shell', 'programs', 'installer', 'notes', 'cli')
REGIONS = ('es', 'es_MX.UTF-8', 'es_419', 'es_AR', 'es_CO', 'es_CL.UTF-8')
FIELDS = re.compile(r'%L?(?:[1-9][0-9]*|n)|%\([^)]+\)[#0 +\-]*\d*(?:\.\d+)?[a-zA-Z]|\{[^{}]*\}')
MARKUP = re.compile(r'</?[A-Za-z][^>]*>')


def po_entries(path):
    """Read gettext's quoted fields without requiring an optional editor library."""
    entries, entry, field = [], {}, None
    for line in path.read_text().splitlines() + ['']:
        line = line.strip()
        if not line:
            if entry:
                entries.append(entry)
            entry, field = {}, None
        elif line.startswith('#,'):
            entry['flags'] = line[2:]
        elif line.startswith('#~'):
            raise AssertionError(f'{path}: obsolete entry')
        elif line.startswith('#'):
            continue
        elif line.startswith('"'):
            entry[field] += ast.literal_eval(line)
        else:
            field, value = line.split(' ', 1)
            entry[field] = ast.literal_eval(value)
    return entries


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SpanishCatalogues(unittest.TestCase):
    def setUp(self):
        self.unchanged = json.loads((ROOT / 'i18n/es-unchanged.json').read_text())['messages']

    def check_text(self, source, target, location):
        self.assertTrue(target.strip(), location)
        if target == source:
            self.assertIn(source, self.unchanged, f'{location}: untranslated {source!r}')
        self.assertEqual(Counter(FIELDS.findall(source)), Counter(FIELDS.findall(target)), location)
        self.assertEqual(MARKUP.findall(source), MARKUP.findall(target), location)
        self.assertEqual(source.count('\n'), target.count('\n'), location)
        self.assertEqual(source.startswith('\n'), target.startswith('\n'), location)
        self.assertEqual(source.endswith('\n'), target.endswith('\n'), location)

    def test_qt_complete(self):
        for domain in DOMAINS[:-1]:
            source = ET.parse(ROOT / 'i18n' / domain / 'en.ts').getroot()
            target = ET.parse(ROOT / 'i18n' / domain / 'es.ts').getroot()
            self.assertEqual(target.get('language'), 'es')
            def messages(root):
                return {(context.findtext('name'), message.findtext('source'), message.findtext('comment')): message
                        for context in root.findall('context') for message in context.findall('message')}
            originals, translations = messages(source), messages(target)
            self.assertEqual(originals.keys(), translations.keys(), domain)
            for key, original in originals.items():
                with self.subTest(domain=domain, context=key[0], source=key[1]):
                    message = translations[key]
                    self.assertEqual(original.get('numerus'), message.get('numerus'))
                    translated = message.find('translation')
                    self.assertIsNotNone(translated)
                    self.assertNotIn(translated.get('type'), ('unfinished', 'vanished', 'obsolete'))
                    forms = translated.findall('numerusform')
                    if original.get('numerus') == 'yes':
                        self.assertEqual(len(forms), 2)
                        for form in forms:
                            self.check_text(key[1], form.text or '', str(key))
                    else:
                        self.assertFalse(forms)
                        self.check_text(key[1], translated.text or '', str(key))
            print(f'{domain}: {len(originals)} complete Spanish Qt messages')

    def test_gettext_complete(self):
        for domain in DOMAINS:
            originals = {entry['msgid']: entry for entry in po_entries(ROOT / 'i18n' / domain / (domain + '.pot'))}
            translations = {entry['msgid']: entry for entry in po_entries(ROOT / 'i18n' / domain / 'es.po')}
            self.assertEqual(originals.keys(), translations.keys(), domain)
            header = translations.pop('')['msgstr']
            originals.pop('')
            self.assertIn('Language: es\n', header)
            self.assertIn('nplurals=2;', header)
            self.assertRegex(header, r'plural=\(?n != 1\)?;')
            for source, original in originals.items():
                with self.subTest(domain=domain, source=source):
                    entry = translations[source]
                    self.assertNotIn('fuzzy', entry.get('flags', ''))
                    self.assertEqual(original.get('msgid_plural'), entry.get('msgid_plural'))
                    fields = {key for key in entry if key.startswith('msgstr')}
                    if 'msgid_plural' in original:
                        self.assertEqual(fields, {'msgstr[0]', 'msgstr[1]'})
                        self.check_text(source, entry['msgstr[0]'], domain)
                        self.check_text(original['msgid_plural'], entry['msgstr[1]'], domain)
                    else:
                        self.assertEqual(fields, {'msgstr'})
                        self.check_text(source, entry['msgstr'], domain)
            print(f'{domain}: {len(originals)} complete Spanish gettext messages')

    def test_regional_gettext_fallback_and_installer_choices(self):
        locale_dir = ROOT / 'i18n/locale'
        for region in REGIONS:
            for domain in DOMAINS:
                with self.subTest(region=region, domain=domain):
                    catalogue = gettext.translation(domain, localedir=locale_dir, languages=[region])
                    self.assertEqual(catalogue.info()['language'], 'es')
        with patch.dict(os.environ, {'EMAKI_LOCALE_DIR': str(locale_dir)}, clear=True):
            installer = load_module('installer_i18n_es', ROOT / 'installer/emaki_installer/i18n.py')
            self.assertIn('es_MX.UTF-8', installer.available_locales())
            for region in REGIONS:
                with patch.dict(os.environ, {'LANG': region}):
                    self.assertEqual(installer.system_locale(), 'es_MX.UTF-8')

    def test_compiled_gettext_messages_and_plural_selection(self):
        for domain in DOMAINS:
            catalogue = gettext.translation(domain, localedir=ROOT / 'i18n/locale', languages=['es'])
            for entry in po_entries(ROOT / 'i18n' / domain / 'es.po'):
                if not entry['msgid']:
                    continue
                with self.subTest(domain=domain, source=entry['msgid']):
                    if 'msgid_plural' in entry:
                        for count in (0, 1, 2, 5):
                            self.assertEqual(catalogue.ngettext(entry['msgid'], entry['msgid_plural'], count),
                                             entry['msgstr[0]' if count == 1 else 'msgstr[1]'])
                    else:
                        self.assertEqual(catalogue.gettext(entry['msgid']), entry['msgstr'])

    def test_regional_qt_fallback(self):
        with tempfile.TemporaryDirectory(prefix='emaki-es-runtime-') as temporary:
            base = Path(temporary)
            env = os.environ.copy()
            for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'WAYLAND_SOCKET', 'NIRI_SOCKET',
                        'QT_QPA_PLATFORMTHEME', 'EMAKI_LANGUAGE', 'LANGUAGE', 'LC_ALL', 'LC_MESSAGES'):
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
            fixture = base / 'test.qml'
            entries = []
            for domain in DOMAINS[:-1]:
                for context in ET.parse(ROOT / 'i18n' / domain / 'es.ts').findall('context'):
                    for message in context.findall('message'):
                        source = message.findtext('source')
                        translation = message.find('translation')
                        if message.get('numerus') == 'yes':
                            forms = translation.findall('numerusform')
                            for count in (0, 1, 2, 5):
                                expected = forms[0 if count == 1 else 1].text.replace('%n', str(count))
                                entries.append([context.findtext('name'), source, count, expected, message.findtext('comment', '')])
                        else:
                            entries.append([context.findtext('name'), source, -1, translation.text, message.findtext('comment', '')])
            fixture.write_text('''import QtQuick
import Quickshell
import Emaki.I18n
ShellRoot {
    property var entries: ENTRIES
    Timer { interval: 10; running: true; onTriggered: {
        for (const entry of entries) {
            const translated = qsTranslate(entry[0], entry[1], entry[4], entry[2]);
            if (translated !== entry[3]) {
                console.error("SPANISH_MISMATCH", JSON.stringify(entry), translated);
                Qt.exit(1); return;
            }
        }
        console.log("SPANISH_RESULT", qsTranslate("WelcomeWindow", "Welcome to Emaki"));
        Qt.quit();
    } }
}
'''.replace('ENTRIES', json.dumps(entries, ensure_ascii=False)))
            expected = ET.parse(ROOT / 'i18n/shell/es.ts').find(".//context[name='WelcomeWindow']/message/translation").text
            for region in REGIONS:
                for selection in ({'LANG': region}, {'LANG': 'C', 'EMAKI_LANGUAGE': region}):
                    with self.subTest(selection=selection):
                        result = subprocess.run(['qs', '-p', str(fixture), '--no-color'], env=env | selection,
                                                capture_output=True, text=True, timeout=20)
                        output = result.stdout + result.stderr
                        self.assertEqual(result.returncode, 0, output)
                        self.assertIn('SPANISH_RESULT ' + expected, output)


if __name__ == '__main__':
    unittest.main()
