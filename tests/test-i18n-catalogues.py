#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Test the shared release gate, including deliberately damaged catalogues."""
import copy
import configparser
import gettext
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECK = load('catalogue_contract', ROOT / 'i18n/check_catalogues.py')
RUNTIME = load('catalogue_environment', ROOT / 'tests/test-i18n-pt-br.py')
REGIONS = {'es': ('es', 'es_MX.UTF-8'), 'pt_BR': ('pt_BR', 'pt'),
           'de': ('de', 'de_DE.UTF-8', 'de_AT'), 'fr': ('fr', 'fr_FR.UTF-8', 'fr_CA'),
           'ru': ('ru', 'ru_RU.UTF-8')}
COUNTS = (0, 1, 2, 4, 5, 11, 12, 14, 21, 22, 25, 101, 111)


def desktop_translations(path):
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read(path)
    entry = parser['Desktop Entry']
    if not any('[' in key for key in entry):
        return
    for language in CHECK.LANGUAGES:
        for field in ('Name', 'Comment', 'GenericName', 'Keywords'):
            # GenericName is optional in desktop entries; require translations
            # whenever the field exists in English or any translated variant.
            if field not in entry and not any(key.startswith(field + '[') for key in entry):
                continue
            key = f'{field}[{language}]'
            CHECK.require(bool(entry.get(key, '').strip()), f'{path}: missing {key}')
            if language == 'fr' and field != 'Keywords':
                CHECK.french_typography(entry[key], str(path) + ': ' + key)


def desktop_files(root):
    if (root / '.git').exists():
        tracked = subprocess.check_output(['git', 'ls-files', '-z', '--', '*.desktop'],
                                          cwd=root).decode().split('\0')
        return [root / filename for filename in filter(None, tracked)]
    # Package sources are exported without repository metadata. Hidden build and
    # evidence directories are not part of that source inventory.
    return sorted(path for path in root.rglob('*.desktop')
                  if not any(part.startswith('.') for part in path.relative_to(root).parts))


class ShippedCatalogues(unittest.TestCase):
    def test_complete(self):
        CHECK.check_all()

    def test_desktop_entries_have_every_shipped_language(self):
        for path in desktop_files(ROOT):
            with self.subTest(filename=path):
                desktop_translations(path)

    def test_desktop_inventory_in_exported_package_sources(self):
        with tempfile.TemporaryDirectory(prefix='desktop-export-') as directory:
            root = Path(directory)
            source = root / 'shell/translated.desktop'
            scratch = root / '.cache/scratch.desktop'
            for path in (source, scratch):
                path.parent.mkdir(parents=True)
                path.write_text('[Desktop Entry]\nName=Example\n')
            self.assertEqual(desktop_files(root), [source])

    def test_missing_desktop_translation_fails(self):
        rows = ['[Desktop Entry]', 'Name=Example']
        rows += [f'{field}[{language}]=Example' for language in CHECK.LANGUAGES
                 for field in ('Name', 'Comment', 'GenericName', 'Keywords')]
        with tempfile.TemporaryDirectory(prefix='desktop-translation-') as directory:
            path = Path(directory) / 'sample.desktop'
            path.write_text('\n'.join(rows) + '\n')
            desktop_translations(path)
            for row in rows[2:]:
                with self.subTest(key=row.split('=')[0]):
                    path.write_text('\n'.join(value for value in rows if value != row) + '\n')
                    with self.assertRaisesRegex(AssertionError, 'missing'):
                        desktop_translations(path)

    def test_french_typography_rejects_breakable_spaces(self):
        valid = 'État\u00a0: «\u00a0prêt\u00a0»\u202f; continuer\u202f? Oui\u202f! 1\u202f024 octets'
        CHECK.french_typography(valid, 'fixture')
        CHECK.french_typography('hh:mm · https://example.test · saisir :cancel', 'fixture')
        CHECK.french_typography('format: true · PCI {value1:04x}:', 'fixture')
        CHECK.french_typography('Super+; trouve un émoji.', 'fixture')
        with self.assertRaisesRegex(AssertionError, 'non-breaking space'):
            CHECK.french_typography('Super+; trouve un émoji; copiez-le.', 'fixture')
        for fragment in ('\u202f; impossible de terminer la requête.', '\u202f; disque inchangé\u00a0: ', '\u202f!', '\u202f?'):
            CHECK.french_typography(fragment, 'fixture')
        for correct in ('\u00a0:', '«\u00a0', '\u00a0»', '\u202f;', '\u202f?', '\u202f!', '1\u202f024'):
            with self.subTest(value=correct):
                damaged = valid.replace(correct, correct.replace('\u00a0', ' ').replace('\u202f', ' '))
                with self.assertRaisesRegex(AssertionError, 'French'):
                    CHECK.french_typography(damaged, 'fixture')
        for damaged in ('Continuer?', 'Oui!', 'État:', '«prêt»', 'État\u202f:', 'Oui\u00a0!',
                        '; impossible de terminer la requête.', ' ; disque inchangé\u00a0: ', '!', '?'):
            with self.subTest(value=damaged):
                with self.assertRaisesRegex(AssertionError, 'French'):
                    CHECK.french_typography(damaged, 'fixture')

    def test_russian_first_plural_preserves_plural_placeholders(self):
        with tempfile.TemporaryDirectory(prefix='russian-plural-') as directory:
            source = Path(directory) / 'source.pot'
            target = Path(directory) / 'target.po'
            header = CHECK.po_entries(ROOT / 'i18n/shell/ru.po')[0]['msgstr']
            source.write_text('msgid ""\nmsgstr ""\n\nmsgid "One file"\nmsgid_plural "%n files"\nmsgstr[0] ""\nmsgstr[1] ""\n')
            target.write_text('msgid ""\nmsgstr ' + json.dumps(header) + '\n\nmsgid "One file"\nmsgid_plural "%n files"\n'
                              'msgstr[0] "Один файл"\nmsgstr[1] "%n файла"\nmsgstr[2] "%n файлов"\n')
            with self.assertRaisesRegex(AssertionError, 'broken placeholders'):
                CHECK.check_po(source, target, 'ru')

    def test_removed_message_fails_in_every_language_and_component(self):
        with tempfile.TemporaryDirectory(prefix='catalogue-removal-') as directory:
            for language in CHECK.LANGUAGES:
                for domain in CHECK.DOMAINS:
                    base = ROOT / 'i18n' / domain
                    with self.subTest(language=language, domain=domain, format='gettext'):
                        entries = CHECK.po_entries(base / f'{language}.po')
                        entries.pop(next(i for i, entry in enumerate(entries) if entry['msgid']))
                        damaged = Path(directory) / 'removed.po'
                        damaged.write_text('\n\n'.join('\n'.join(key + ' ' + json.dumps(value, ensure_ascii=False)
                                            for key, value in entry.items() if key != 'flags') for entry in entries) + '\n')
                        with self.assertRaisesRegex(AssertionError, 'missing'):
                            CHECK.check_po(base / f'{domain}.pot', damaged, language)
                    if domain != 'cli':
                        with self.subTest(language=language, domain=domain, format='Qt'):
                            tree = ET.parse(base / f'{language}.ts')
                            context = next(c for c in tree.findall('context') if c.find('message') is not None)
                            context.remove(context.find('message'))
                            damaged = Path(directory) / 'removed.ts'
                            tree.write(damaged, encoding='utf-8')
                            with self.assertRaisesRegex(AssertionError, 'missing'):
                                CHECK.check_qt(base / 'en.ts', damaged, language)

    def test_unfinished_obsolete_placeholders_and_plural_damage_fail(self):
        with tempfile.TemporaryDirectory(prefix='catalogue-damage-') as directory:
            for language in CHECK.LANGUAGES:
                base = ROOT / 'i18n/shell'
                original = ET.parse(base / f'{language}.ts')
                for damage in ('unfinished', 'obsolete', 'vanished', 'empty', 'placeholder', 'plural'):
                    with self.subTest(language=language, damage=damage):
                        tree = copy.deepcopy(original)
                        if damage == 'plural':
                            translation = tree.find(".//message[@numerus='yes']/translation")
                            translation.remove(translation.find('numerusform'))
                        elif damage == 'placeholder':
                            message = next(m for m in tree.findall('.//message')
                                           if '%1' in m.findtext('source') and m.get('numerus') != 'yes')
                            translation = message.find('translation')
                            translation.text = translation.text.replace('%1', '%9')
                        else:
                            translation = tree.find('.//message/translation')
                            if damage == 'empty':
                                translation.text = ''
                            else:
                                translation.set('type', damage)
                        target = Path(directory) / 'broken.ts'
                        tree.write(target, encoding='utf-8')
                        with self.assertRaises(AssertionError):
                            CHECK.check_qt(base / 'en.ts', target, language)
                for marker in ('#, fuzzy\n', '#~ msgid "Old message"\n#~ msgstr "Old translation"\n\n'):
                    with self.subTest(language=language, marker=marker):
                        target = Path(directory) / 'broken.po'
                        target.write_text(marker + (base / f'{language}.po').read_text())
                        with self.assertRaises(AssertionError):
                            CHECK.check_po(base / 'shell.pot', target, language)
                for damage in ('empty', 'placeholder'):
                    with self.subTest(language=language, format='gettext', damage=damage):
                        entries = CHECK.po_entries(ROOT / f'i18n/cli/{language}.po')
                        entry = next(entry for entry in entries if '{' in entry['msgid'])
                        entry['msgstr'] = '' if damage == 'empty' else entry['msgstr'].replace('{', '{broken_', 1)
                        target = Path(directory) / 'broken.po'
                        target.write_text('\n\n'.join('\n'.join(key + ' ' + json.dumps(value, ensure_ascii=False)
                                          for key, value in entry.items() if key != 'flags') for entry in entries) + '\n')
                        with self.assertRaisesRegex(AssertionError, 'empty translation|broken placeholders'):
                            CHECK.check_po(ROOT / 'i18n/cli/cli.pot', target, language)
                # Exercise missing gettext plural forms even in domains with no current ngettext calls.
                source = Path(directory) / 'plural.pot'
                source.write_text('msgid ""\nmsgstr ""\n\nmsgid "%n file"\nmsgid_plural "%n files"\nmsgstr[0] ""\nmsgstr[1] ""\n')
                header = CHECK.po_entries(base / f'{language}.po')[0]['msgstr']
                target = Path(directory) / 'plural.po'
                forms = '\n'.join(f'msgstr[{i}] "%n item"' for i in range(CHECK.PLURALS[language][0] - 1))
                target.write_text('msgid ""\nmsgstr ' + json.dumps(header) + '\n\nmsgid "%n file"\nmsgid_plural "%n files"\n' + forms + '\n')
                with self.assertRaisesRegex(AssertionError, 'plural form'):
                    CHECK.check_po(source, target, language)

    def test_compiled_gettext_and_regional_fallback(self):
        for language, regions in REGIONS.items():
            for domain in CHECK.DOMAINS:
                for region in regions:
                    with self.subTest(language=language, domain=domain, region=region):
                        compiled = gettext.translation(domain, ROOT / 'i18n/locale', [region])
                        self.assertEqual(compiled.info()['language'], language)
                        for n in COUNTS:
                            self.assertEqual(compiled.plural(n), CHECK.plural_index(language, n))
                        for entry in CHECK.po_entries(ROOT / f'i18n/{domain}/{language}.po'):
                            if not entry['msgid']:
                                continue
                            if 'msgid_plural' in entry:
                                for n in COUNTS:
                                    self.assertEqual(compiled.ngettext(entry['msgid'], entry['msgid_plural'], n),
                                                     entry[f'msgstr[{CHECK.plural_index(language, n)}]'])
                            else:
                                self.assertEqual(compiled.gettext(entry['msgid']), entry['msgstr'])

    def test_qt_runtime_every_message_and_plural(self):
        for language, regions in REGIONS.items():
            entries = []
            for domain in CHECK.DOMAINS[:-1]:
                _, messages = CHECK.qt_messages(ROOT / f'i18n/{domain}/{language}.ts')
                for (context, source, comment), message in messages.items():
                    forms = message.findall('translation/numerusform')
                    for n in COUNTS if forms else (-1,):
                        translated = forms[CHECK.plural_index(language, n)].text if forms else message.findtext('translation')
                        entries.append([context, source, comment, n, translated.replace('%n', str(n))])
            qml = '''import QtQuick
import Quickshell
import Emaki.I18n
ShellRoot {
 property var entries: ENTRIES
 Timer { interval: 10; running: true; onTriggered: {
  for (const entry of entries) {
   const actual = qsTranslate(entry[0], entry[1], entry[2], entry[3]);
   if (actual !== entry[4]) { console.error("CATALOGUE_MISMATCH", JSON.stringify(entry), actual); Qt.exit(1); return; }
  }
  console.log("CATALOGUE_QT_OK", entries.length); Qt.quit();
 } }
}
'''.replace('ENTRIES', json.dumps(entries, ensure_ascii=False))
            with tempfile.TemporaryDirectory(prefix='catalogue-runtime-') as directory:
                base = Path(directory)
                fixture = base / 'test.qml'
                fixture.write_text(qml)
                for region in regions:
                    with self.subTest(language=language, region=region):
                        result = subprocess.run(['qs', '-p', str(fixture), '--no-color'],
                                                env=RUNTIME.environment(base, region), text=True,
                                                capture_output=True, timeout=30)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertIn('CATALOGUE_QT_OK', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
