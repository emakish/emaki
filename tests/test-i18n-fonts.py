#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prove shipped text faces cover all added languages without another font."""
import gzip
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('font_renderer', ROOT / 'tests/render-shell.py')
RENDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RENDER)
CATALOGUE_SPEC = importlib.util.spec_from_file_location('font_catalogues', ROOT / 'i18n/check_catalogues.py')
CATALOGUES = importlib.util.module_from_spec(CATALOGUE_SPEC)
CATALOGUE_SPEC.loader.exec_module(CATALOGUES)
CYRILLIC = 'Ёё' + ''.join(chr(n) for n in range(0x410, 0x450))


def translated_characters():
    characters = set(CYRILLIC + '№‹›œ«»')
    for language in ('de', 'fr', 'ru'):
        for domain in CATALOGUES.DOMAINS:
            directory = ROOT / 'i18n' / domain
            if domain != 'cli':
                _, messages = CATALOGUES.qt_messages(directory / (language + '.ts'))
                for message in messages.values():
                    translation = message.find('translation')
                    for form in translation.findall('numerusform') or [translation]:
                        characters.update(form.text or '')
            for entry in CATALOGUES.po_entries(directory / (language + '.po')):
                if entry.get('msgid'):
                    for key, value in entry.items():
                        if key == 'msgstr' or key.startswith('msgstr['):
                            characters.update(value)
    return ''.join(sorted(character for character in characters if ord(character) > 127))


CHARACTERS = translated_characters()


def console_glyphs(path):
    """Read the PSF2 Unicode table and retain single-character glyph mappings."""
    data = gzip.decompress(path.read_bytes())
    magic, version, header, flags, count, size, height, width = struct.unpack('<8I', data[:32])
    if magic != 0x864AB572 or version != 0 or not flags & 1:
        raise ValueError(f'{path}: expected PSF2 with a Unicode table')
    if header < 32 or size != height * ((width + 7) // 8):
        raise ValueError(f'{path}: invalid PSF2 bitmap dimensions')
    table_start = header + count * size
    entries = data[table_start:].split(b'\xff')
    if len(entries) != count + 1 or entries[-1]:
        raise ValueError(f'{path}: incomplete PSF2 Unicode table')
    glyphs = {}
    for index, entry in enumerate(entries[:-1]):
        # A sequence after 0xfe is not an independent character mapping.
        for character in entry.split(b'\xfe', 1)[0].decode('utf-8'):
            glyphs[character] = data[header + index * size:header + (index + 1) * size]
    return glyphs



def charset(value):
    result = set()
    for span in value.split():
        bounds = [int(n, 16) for n in span.split('-')]
        result.update(range(bounds[0], bounds[-1] + 1))
    return result


class LanguageFonts(unittest.TestCase):
    def test_console_fonts_cover_russian_and_french(self):
        required = CYRILLIC + '№œ«»'
        for name in ('ter-v24b', 'ter-v32b'):
            with self.subTest(font=name):
                path = Path('/usr/share/kbd/consolefonts') / (name + '.psf.gz')
                self.assertTrue(path.is_file(), f'Install the required terminus-font package: {path}')
                glyphs = console_glyphs(path)
                missing = ''.join(character for character in required
                                  if not any(glyphs.get(character, b'')))
                self.assertEqual(missing, '', (name, missing))

    def test_terminal_font_covers_the_translated_cli(self):
        inventory = subprocess.check_output(['fc-list', '--format', '%{file}\t%{family}\n'], text=True)
        paths = {Path(line.split('\t')[0]) for line in inventory.splitlines()
                 if 'Iosevka Nerd Font Mono' in line.split('\t')[1].split(',')}
        expected = (ROOT / 'packaging/emaki-font-iosevka/expected-files.list').read_text().splitlines()
        names = {Path(name).name for name in expected if name.endswith('.ttf')}
        faces = {path.name: path for path in paths if str(path) in expected}
        self.assertEqual(faces.keys(), names)
        evidence = []
        for name, path in faces.items():
            with self.subTest(face=name):
                coverage = subprocess.check_output(['fc-query', '--format', '%{charset}', str(path)], text=True)
                self.assertEqual(''.join(c for c in CHARACTERS if ord(c) not in charset(coverage)), '')
                evidence.append(dict(file=str(path.resolve()), missing=''))
        destination = ROOT / '.cache/evidence/i18n/terminal-fonts.json'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(evidence, indent=2) + '\n')

    def test_shipped_faces_cover_latin_and_cyrillic(self):
        evidence = []
        with tempfile.TemporaryDirectory(prefix='i18n-fonts-') as directory:
            profile = Path(directory)
            env = dict(os.environ, FONTCONFIG_FILE=str(RENDER.shipped_font_config(profile)))
            for family in ('Noto Sans', 'Noto Sans Mono'):
                for style in ('Regular', 'Bold', 'Italic', 'Bold Italic'):
                    with self.subTest(family=family, style=style):
                        result = subprocess.check_output(['fc-match', '--format', '%{family}\n%{style}\n%{file}\n%{charset}',
                                                          family + ':style=' + style], env=env, text=True)
                        resolved, resolved_style, filename, coverage = result.split('\n', 3)
                        self.assertIn(family, resolved.split(','))
                        missing = ''.join(c for c in CHARACTERS if ord(c) not in charset(coverage))
                        self.assertEqual(missing, '', (family, style, filename, missing))
                        evidence.append(dict(family=family, requested_style=style, resolved_style=resolved_style,
                                             file=str(Path(filename).resolve()), missing=missing))
        destination = ROOT / '.cache/evidence/i18n/fonts.json'
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(dict(characters=CHARACTERS, faces=evidence), ensure_ascii=False, indent=2) + '\n')

    def test_ui_and_fallback_fonts_are_packaged(self):
        recipe = (ROOT / 'packaging/emaki-config/PKGBUILD').read_text()
        self.assertIn("'noto-fonts'", recipe)
        self.assertIn("'terminus-font'", recipe)
        self.assertIn("'emaki-font-iosevka'", recipe)
        self.assertIn('ui   = "Noto Sans"', (ROOT / 'tokens.toml').read_text())
        self.assertIn('uiFont: "Noto Sans"', (ROOT / 'shell/ShellPalette.qml').read_text())
        self.assertIn('ShellPalette.uiFont', (ROOT / 'shell/settings/SettingsTheme.qml').read_text())
        self.assertIn('Shell.ShellPalette.uiFont', (ROOT / 'installer/ui/InstallerView.qml').read_text())
        self.assertIn('font.family: "Noto Sans"', (ROOT / 'shell/NotesView.qml').read_text())
        self.assertIn('font_family = Noto Sans', (ROOT / 'hypr/hyprlock.conf').read_text())
        for name in ('LockSurface.qml', 'GreeterUsers.qml'):
            source = (ROOT / 'shell' / name).read_text()
            self.assertEqual(source.count('Text {'), source.count('font.family: ShellPalette.uiFont'), name)
        self.assertIn('font.family: "Noto Sans Mono"', (ROOT / 'installer/ui/InstallerView.qml').read_text())


if __name__ == '__main__':
    unittest.main()
