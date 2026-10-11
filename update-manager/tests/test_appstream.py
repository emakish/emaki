# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise the standard AppStream reader against actual library parsers."""
import gzip
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import programs_appstream as reader

XML = '''<?xml version="1.0"?>
<components version="1.0" origin="arch-extra">
 <component type="desktop-application">
  <id>org.example.Painter</id><pkgname>painter</pkgname>
  <name>Painter</name><name xml:lang="de">Maler</name>
  <summary>Paint images</summary><summary xml:lang="de">Bilder malen</summary>
  <description><p>Draw &amp; paint <em>pictures</em>.</p><ul><li>Layers</li></ul></description>
  <project_license>GPL-3.0-or-later</project_license>
  <categories><category>Graphics</category></categories>
  <keywords><keyword>drawing</keyword></keywords>
  <icon type="stock">painter</icon>
  <icon type="remote" width="64" height="64">https://example.org/icon.png</icon>
  <icon type="remote">file:///tmp/private.png</icon>
  <icon type="remote">javascript:alert(1)</icon>
  <screenshots><screenshot type="default">
   <image type="source" width="800" height="600">https://example.org/screenshot.png</image>
   <image type="thumbnail" width="200" height="150">https://example.org/thumb.png</image>
  </screenshot><screenshot><image type="source">file:///tmp/private.png</image></screenshot></screenshots>
  <bundle type="flatpak">app/org.example.Painter/x86_64/stable</bundle>
 </component>
 <component type="console-application"><id>org.example.Cli</id><name>Cli</name></component>
 <component type="runtime"><id>org.example.Runtime</id><name>Runtime</name></component>
</components>'''

YAML = '''---
File: DEP-11
Version: '0.16'
Origin: arch-extra
---
Type: desktop-application
ID: org.example.Reader
Package: reader
Name:
  C: Reader
  de: Leser
Summary:
  C: Read documents
Description:
  C: '<p>Read books.</p>'
ProjectLicense: MIT
Categories:
  - Office
Keywords:
  C:
    - books
Icon:
  stock: reader
Screenshots:
  - default: true
    source-image:
      url: https://example.org/reader.png
      width: 800
      height: 600
'''


class AppStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        _, _, glib = reader._library()
        self.enterContext(patch.object(glib, 'get_language_names', return_value=['C']))

    def file(self, name, data):
        path = self.root / name
        if name.endswith('.gz'):
            path.write_bytes(gzip.compress(data.encode()))
        else:
            path.write_text(data)
        return path

    def test_xml_metadata_and_default_locale(self):
        records = reader.read_file(self.file('catalog.xml', XML))
        self.assertEqual(len(records), 2)
        app = records[0]
        self.assertEqual(app['name'], 'Painter')
        self.assertEqual(app['summary'], 'Paint images')
        self.assertEqual(app['packages'], ['painter'])
        self.assertEqual(app['origin'], 'arch-extra')
        self.assertEqual(app['licence'], 'GPL-3.0-or-later')
        self.assertEqual(app['categories'], ['Graphics'])
        self.assertEqual(app['keywords'], ['drawing'])
        self.assertIn('Draw & paint pictures.', app['description'])
        self.assertNotIn('<', app['description'])
        self.assertEqual(app['bundles'], [{'kind': 'flatpak', 'id': 'app/org.example.Painter/x86_64/stable'}])
        self.assertEqual(app['icons'], [
            {'kind': 'stock', 'value': 'painter', 'width': 0, 'height': 0},
            {'kind': 'remote', 'value': 'https://example.org/icon.png', 'width': 64, 'height': 64}])
        self.assertEqual(app['screenshots'], ['https://example.org/screenshot.png', 'https://example.org/thumb.png'])
        self.assertIsNone(app['downloadSize'])
        self.assertIsNone(app['installedSize'])

    def test_metainfo_uses_one_session_language_with_english_fallback(self):
        xml = '<component type="desktop-application"><id>org.example.Locale</id>' \
              '<name>English name</name><name xml:lang="de">German name</name>' \
              '<summary>English summary</summary><summary xml:lang="de">German summary</summary>' \
              '<description><p>English paragraph.</p><p xml:lang="de">German paragraph.</p>' \
              '<p xml:lang="fr">French paragraph.</p><ul><li>English item</li></ul>' \
              '<ul xml:lang="de"><li>German item</li></ul></description></component>'
        path = self.file('localized.metainfo.xml', xml)
        _, _, glib = reader._library()
        for locale, name, paragraph, item in (
                ('de_DE.UTF-8', 'German name', 'German paragraph.', 'German item'),
                ('zz_ZZ.UTF-8', 'English name', 'English paragraph.', 'English item'),
                ('C', 'English name', 'English paragraph.', 'English item')):
            with self.subTest(locale=locale), patch.object(glib, 'get_language_names', return_value=[locale, 'C']):
                app = reader.read_file(path)[0]
                self.assertEqual(app['name'], name)
                self.assertIn(paragraph, app['description'])
                self.assertIn(item, app['description'])
                self.assertNotIn('French', app['description'])
                self.assertNotIn('English' if locale.startswith('de') else 'German', app['description'])

    def test_yaml_and_gzip(self):
        for name in ('catalog.yml', 'catalog.yml.gz', 'catalog.yaml.gz'):
            with self.subTest(name=name):
                app = reader.read_file(self.file(name, YAML))[0]
                self.assertEqual(app['name'], 'Reader')
                self.assertEqual(app['summary'], 'Read documents')
                self.assertEqual(app['description'].strip(), 'Read books.')
                self.assertEqual(app['packages'], ['reader'])
                self.assertEqual(app['categories'], ['Office'])
                self.assertEqual(app['screenshots'], ['https://example.org/reader.png'])
        app = reader.read_file(self.file('appstream.xml.gz', XML), origin='flathub')[0]
        self.assertEqual(app['origin'], 'flathub')

    def test_metainfo_missing_optional_fields(self):
        path = self.file('empty.metainfo.xml', '<component type="console-application"><id>org.example.Empty</id></component>')
        app = reader.read_file(path)[0]
        for key in ('name', 'summary', 'description', 'licence', 'origin'):
            self.assertEqual(app[key], '')
        for key in ('packages', 'icons', 'categories', 'screenshots', 'bundles', 'keywords'):
            self.assertEqual(app[key], [])
        self.assertEqual(app['sourcePath'], str(path))

    def test_bidi_and_format_characters_cannot_reach_any_display_field(self):
        import json
        import unicodedata
        controls = '\u202e\u2066\u2069\u200e\u200f\u200b\u061c\ufeff'
        data = XML.replace('<name>Painter</name>', '<name>Paint' + controls + 'er</name>')
        data = data.replace('Paint images', 'Paint' + controls + ' images')
        data = data.replace('Draw &amp;', 'Draw' + controls + ' &amp;')
        data = data.replace('Graphics', 'Graph' + controls + 'ics')
        data = data.replace('drawing', 'draw' + controls + 'ing')
        data = data.replace('GPL-3.0-or-later', 'GPL' + controls + '-3.0-or-later')
        app = reader.read_file(self.file('display.xml', data))[0]
        self.assertEqual(app['name'], 'Painter')
        self.assertEqual(app['summary'], 'Paint images')
        self.assertIn('Draw & paint', app['description'])
        self.assertFalse(any(unicodedata.category(char) == 'Cf'
                             for char in json.dumps(app, ensure_ascii=False)))

    def test_joiners_preserve_persian_spelling_and_emoji_families(self):
        text = 'می\u200cخواهم 👨\u200d👩\u200d👧'
        data = XML.replace('Painter</name>', text + '</name>')
        data = data.replace('Paint images', text).replace('Draw &amp;', text)
        app = reader.read_file(self.file('joiners.xml', data))[0]
        self.assertEqual(app['name'], text)
        self.assertEqual(app['summary'], text)
        self.assertIn(text, app['description'])

    def test_xml_component_limits_reset_but_file_limit_still_applies(self):
        data = b'<components><component><id>A</id></component><component><id>B</id></component></components>'
        with patch.object(reader, 'MAX_NODES', 2):
            self.assertEqual(reader._validate_xml(data), 'components')
            with self.assertRaisesRegex(ValueError, 'structural limits'):
                reader._validate_xml(b'<component><id>A</id><name>B</name></component>')
        with patch.object(reader, 'MAX_FILE_NODES', 4), self.assertRaisesRegex(ValueError, 'structural limits'):
            reader._validate_xml(data)
        with patch.object(reader, 'MAX_COMPONENTS', 1), self.assertRaisesRegex(ValueError, 'structural limits'):
            reader._validate_xml(data)

    def test_yaml_component_event_limits_reset_but_file_limit_still_applies(self):
        data = b'---\nID: first\n---\nID: second\n'
        with patch.object(reader, 'MAX_NODES', 7):
            reader._validate_yaml(data)
            with self.assertRaisesRegex(ValueError, 'structural limits'):
                reader._validate_yaml(b'---\nID: first\nName: one\nSummary: two\n')
        with patch.object(reader, 'MAX_FILE_NODES', 10), self.assertRaisesRegex(ValueError, 'structural limits'):
            reader._validate_yaml(data)

    def test_catalog_does_not_build_a_second_xml_tree(self):
        path = self.file('catalog.xml', XML)
        with patch.object(reader.ElementTree, 'fromstring', side_effect=AssertionError('catalog tree')):
            self.assertEqual(len(reader.read_file(path)), 2)

    def test_unsafe_identities_and_media_are_rejected_without_retargeting(self):
        for text in ('org.example.Painter', 'app/org.example.Painter/x86_64/stable', 'arch-extra'):
            data = XML.replace(text, text + '\u202e')
            self.assertFalse(any(row['id'].startswith('org.example.Painter')
                                 for row in reader.read_file(self.file('identity.xml', data))))
        data = XML.replace('https://example.org/', 'https://example.org/\u200b')
        app = reader.read_file(self.file('media.xml', data))[0]
        self.assertEqual(app['screenshots'], [])
        self.assertFalse(any(icon['kind'] == 'remote' for icon in app['icons']))

    def test_file_and_gzip_expansion_are_bounded_before_native_parser(self):
        for name in ('large.xml', 'large.xml.gz'):
            with self.subTest(name=name):
                path = self.file(name, '<components>' + ' ' * 4096 + '</components>')
                with patch.object(reader, 'MAX_FILE_BYTES', 1024):
                    with self.assertRaisesRegex(ValueError, 'file limit'):
                        reader.read_file(path)

    def test_xml_rejects_depth_dtd_and_entities(self):
        hostile = [
            '<component>' + '<p>' * 40 + 'nested' + '</p>' * 40 + '</component>',
            '<!DOCTYPE component [<!ENTITY x "expanded">]><component>&x;</component>',
            '<!DOCTYPE component SYSTEM "file:///tmp/secret"><component/>',
            '<!DOCTYPE component [<!ENTITY x SYSTEM "https://example.org/secret">]><component>&x;</component>',
        ]
        for index, data in enumerate(hostile):
            with self.subTest(index=index), self.assertRaises(ValueError):
                reader.read_file(self.file(f'hostile-{index}.xml', data))
        with patch.object(reader, 'MAX_NODES', 3), self.assertRaises(ValueError):
            reader.read_file(self.file('nodes.xml', XML))
        with patch.object(reader, 'MAX_SCALAR_BYTES', 8), self.assertRaises(ValueError):
            reader.read_file(self.file('scalar.xml', XML))

    def test_yaml_rejects_aliases_depth_scalars_and_document_count(self):
        cases = [
            ('anchor.yml', YAML + '\nEvil: &evil [one, two]\nAgain: *evil'),
            ('deep.yml', YAML + '\nEvil: ' + '[' * 40 + 'one' + ']' * 40),
        ]
        for name, data in cases:
            with self.subTest(name=name), self.assertRaises(ValueError):
                reader.read_file(self.file(name, data))
        with patch.object(reader, 'MAX_SCALAR_BYTES', 8), self.assertRaises(ValueError):
            reader.read_file(self.file('scalar.yml', YAML))
        with patch.object(reader, 'MAX_COMPONENTS', 1), self.assertRaises(ValueError):
            reader.read_file(self.file('documents.yml', YAML + '\n---\nID: another'))
        with patch.dict(sys.modules, {'yaml': None}), self.assertRaisesRegex(ImportError, 'parser unavailable'):
            reader.read_file(self.file('optional.yml', YAML))

    def test_output_fields_and_lists_are_bounded(self):
        text = 'Long description. ' * 2000
        icons = ''.join(f'<icon type="stock">icon-{i}</icon>' for i in range(100))
        keywords = ''.join(f'<keyword>word-{i}</keyword>' for i in range(100))
        data = '<component type="desktop-application"><id>org.example.Large</id>' \
               + '<name>' + 'N' * 4000 + '</name><description><p>' + text \
               + '</p></description>' + icons + '<keywords>' + keywords + '</keywords></component>'
        app = reader.read_file(self.file('large-record.xml', data))[0]
        self.assertEqual(len(app['name'].encode()), reader.MAX_TEXT_BYTES)
        self.assertEqual(len(app['description'].encode()), reader.MAX_DESCRIPTION_BYTES)
        self.assertEqual(len(app['icons']), reader.MAX_LIST_ITEMS)
        self.assertEqual(len(app['keywords']), reader.MAX_LIST_ITEMS)
        oversized_id = data.replace('org.example.Large', 'org.example.' + 'x' * reader.MAX_TEXT_BYTES)
        self.assertEqual(reader.read_file(self.file('large-identity.xml', oversized_id)), [])

    def test_screenshot_collection_stops_at_limit_and_deduplicates(self):
        images = ''.join('<screenshot><image type="source">https://example.org/'
                         + str(index // 2) + '.png</image></screenshot>' for index in range(160))
        data = '<component type="desktop-application"><id>org.example.Images</id>' \
               + '<screenshots>' + images + '</screenshots></component>'
        app = reader.read_file(self.file('images.xml', data))[0]
        self.assertEqual(len(app['screenshots']), reader.MAX_LIST_ITEMS)
        self.assertEqual(len(set(app['screenshots'])), reader.MAX_LIST_ITEMS)

    def test_local_icons_stay_inside_metadata_directory_and_reject_remote_origin(self):
        icon = self.root / 'icon.png'
        icon.write_bytes(b'local image reference')
        escape = self.root / 'escape.png'
        escape.symlink_to('/tmp/outside-icon.png')
        xml = '<component type="desktop-application"><id>org.example.Icons</id>' \
              + f'<icon type="local">{icon}</icon>' \
              + '<icon type="local">/tmp/outside-icon.png</icon>' \
              + f'<icon type="local">{escape}</icon></component>'
        path = self.file('icons.metainfo.xml', xml)
        app = reader.read_file(path)[0]
        self.assertEqual([item['value'] for item in app['icons']], [str(icon)])
        self.assertEqual(reader.read_file(path, origin='flathub')[0]['icons'], [])

    def test_failure_does_not_discard_other_files(self):
        paths = [self.file('broken.xml', '<components><component'),
                 self.root / 'absent.xml', self.file('good.xml', XML)]
        records, warnings = reader.components(paths)
        self.assertEqual(len(records), 2)
        self.assertEqual(len(warnings), 2)
        self.assertIn('invalid', warnings[0])
        self.assertIn('unreadable', warnings[1])
        self.assertTrue(all(warning.startswith('[PLACEHOLDER]') for warning in warnings))
        self.assertEqual(reader.components([]), ([], []))

    def test_missing_dependency_is_reported(self):
        with patch.object(reader, '_library', side_effect=ImportError('missing library')):
            records, warnings = reader.components()
        self.assertEqual(records, [])
        self.assertIn('missing library', warnings[0])

    def test_url_validation(self):
        for value in ('javascript:alert(1)', 'file:///tmp/a', 'data:image/png,a',
                      '//example.org/a', 'https:///a', 'https://user:password@example.org/a',
                      'https://example.org:bad/a', 'https://bad host.org/a', 'https://example.org/\\evil', 'https://example.org/\na', 'https://[invalid/a'):
            with self.subTest(value=value):
                self.assertFalse(reader._web_url(value))
        self.assertTrue(reader._web_url('https://example.org/a.png'))
        self.assertTrue(reader._web_url('http://example.org/a.png'))

    def test_discovery_only_standard_native_paths(self):
        native = self.root / 'share'
        xml_dir = native / 'swcatalog' / 'xml'
        xml_dir.mkdir(parents=True)
        catalog = xml_dir / 'arch.xml'
        catalog.write_text(XML)
        (native / 'app-info').symlink_to(native / 'swcatalog', target_is_directory=True)
        metainfo = native / 'metainfo'
        metainfo.mkdir()
        installed = metainfo / 'installed.metainfo.xml'
        installed.write_text(XML)
        flatpak = self.root / 'flatpak' / 'exports' / 'share'
        (flatpak / 'metainfo').mkdir(parents=True)
        (flatpak / 'metainfo' / 'ignore.xml').write_text(XML)
        _, _, glib = reader._library()
        with patch.object(glib, 'get_system_data_dirs', return_value=[str(native), str(flatpak)]):
            paths = reader._standard_paths()
        self.assertEqual(sum(path.resolve() == catalog for path in paths), 1)
        self.assertIn(installed, paths)
        self.assertFalse(any('flatpak' in path.parts for path in paths))


class InstalledCatalogTests(unittest.TestCase):
    """Read host metadata without refreshing catalogs or changing installations."""

    def setUp(self):
        try:
            reader._library()
        except (ImportError, ValueError) as exc:
            self.skipTest(f'Installed AppStream parser unavailable: {exc}')

    def assert_catalogs_parse(self, paths, reason):
        paths = list(paths)
        if not paths:
            self.skipTest(reason)
        records = []
        for path in paths:
            with self.subTest(path=str(path)):
                records.extend(reader.read_file(path))
        # Individual repositories can be empty, including Arch's core catalog.
        self.assertTrue(records, 'Installed catalogs produced no application metadata')
        self.assertTrue(any(row['name'] for row in records))

    def test_installed_native_catalogs(self):
        self.assert_catalogs_parse(
            (path for path in reader._standard_paths()
             if 'swcatalog' in path.parts or 'app-info' in path.parts),
            'No installed native AppStream catalogs found in standard data/cache locations')

    def test_installed_system_flatpak_catalogs(self):
        self.assert_catalogs_parse(
            Path('/var/lib/flatpak/appstream').glob('*/*/active/appstream.xml.gz'),
            'No installed system Flatpak AppStream catalogs found')


if __name__ == '__main__':
    unittest.main()
