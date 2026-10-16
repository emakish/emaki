#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The terminal's look (0.9.0): kitty loads the packaged theme with a see-through background
that can be changed per window, and the Iosevka Nerd Font Mono that the packages install."""
import hashlib
import json
import os
import runpy
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parent.parent
TOKENS = tomllib.loads((ROOT / 'tokens.toml').read_text())
PROBE = '''
import json, sys
from kitty.config import load_config
bad = []
o = load_config(sys.argv[-1], accumulate_bad_lines=bad)  # as kitty itself loads it
print(json.dumps({'font_family': o.font_family.created_from_string, 'font_size': o.font_size,
                  'background_opacity': o.background_opacity,
                  'dynamic_background_opacity': o.dynamic_background_opacity,
                  'background': bytes((o.background.red, o.background.green, o.background.blue)).hex(),
                  'bad_lines': [b.line for b in bad]}))
'''
FONT_PACKAGE = 'emaki-font-iosevka'


def depends(recipe):
    result = subprocess.run(['bash', '-c', 'source "$1"; printf "%s\\n" "${depends[@]}"', '_', str(recipe)],
                            capture_output=True, text=True, check=True)
    return {re.split(r'[<>=]', line, maxsplit=1)[0] for line in result.stdout.splitlines()}


class KittyLook(unittest.TestCase):
    def load(self, personal_lines=''):
        with tempfile.TemporaryDirectory(prefix='kitty-look-', dir=ROOT / '.cache') as directory:
            base = Path(directory)
            for name in ('home', 'config', 'runtime'):
                (base / name).mkdir(mode=0o700)
            conf = base / 'kitty.conf'
            # /etc/skel's kitty.conf, pointed at the repository's theme, plus a person's lines.
            skel = (ROOT / 'etc-skel/.config/kitty/kitty.conf').read_text()
            self.assertIn('include /usr/share/emaki/kitty/theme.conf', skel)
            conf.write_text(skel.replace('/usr/share/emaki/kitty/theme.conf', str(ROOT / 'kitty/theme.conf'))
                            + personal_lines)
            env = {'HOME': str(base / 'home'), 'XDG_CONFIG_HOME': str(base / 'config'),
                   'KITTY_CONFIG_DIRECTORY': str(base / 'config'), 'XDG_RUNTIME_DIR': str(base / 'runtime'),
                   'PATH': '/usr/local/bin:/usr/bin:/bin', 'LANG': 'C.UTF-8'}
            result = subprocess.run([shutil.which('kitty') or 'kitty', '+runpy', PROBE, str(conf)], env=env,
                                    text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sorted(p.name for p in base.rglob('*') if p.parent.name in ('home', 'config')), [],
                             'loading the config wrote into the home')
            return json.loads(result.stdout)

    def test_packaged_look(self):
        look = self.load()
        self.assertEqual(look['bad_lines'], [])
        self.assertEqual(look['font_family'], 'Iosevka Nerd Font Mono')
        self.assertEqual(look['font_family'], TOKENS['font']['mono'])
        self.assertEqual(look['font_size'], 12.0)
        self.assertEqual(look['font_size'], float(TOKENS['font']['size']))
        self.assertEqual(look['background_opacity'], 0.75)
        self.assertEqual(look['background_opacity'], TOKENS['opacity']['terminal'])
        self.assertIs(look['dynamic_background_opacity'], True)
        # The colour theme stays: the opacity applies to Emaki's own background.
        self.assertEqual(look['background'], TOKENS['color']['background'])

    def test_personal_lines_after_the_include_win(self):
        look = self.load('background_opacity 1\nfont_size 14\n')
        self.assertEqual((look['background_opacity'], look['font_size']), (1.0, 14.0))

    def test_font_is_installed_with_the_terminal(self):
        # Installed systems (upgrades included) get it with the packages, the live image and the
        # offline repository name it explicitly like every emaki-desktop dependency.
        for name in ('emaki-config', 'emaki-desktop'):
            with self.subTest(recipe=name):
                names = depends(ROOT / 'packaging' / name / 'PKGBUILD')
                self.assertIn(FONT_PACKAGE, names)
                self.assertNotIn('ttf-iosevka-nerd', names)
                self.assertIn('kitty', names)
        for relative in ('iso/packages-extra.txt', 'iso/profile/packages.x86_64', 'iso/emaki-packages.txt'):
            with self.subTest(list=relative):
                self.assertIn(FONT_PACKAGE, (ROOT / relative).read_text().split())
                self.assertNotIn('ttf-iosevka-nerd', (ROOT / relative).read_text().split())


class FontPackage(unittest.TestCase):
    styles = ('Regular', 'Bold', 'Italic', 'BoldItalic')
    recipe = ROOT / 'packaging' / FONT_PACKAGE / 'PKGBUILD'

    def expected_files(self):
        return {line.lstrip('/') for line in self.recipe.with_name('expected-files.list')
                .read_text().splitlines() if line and not line.startswith('#')}

    def stage(self, source, destination):
        subprocess.run(['bash', '-eu', '-c',
                        'source "$1"; srcdir=$2; pkgdir=$3; package',
                        '_', str(self.recipe), str(source), str(destination)], check=True)

    def test_only_four_faces_and_upstream_notices_are_packaged(self):
        with tempfile.TemporaryDirectory(prefix='font-package-') as directory:
            base = Path(directory)
            source, destination = base / 'source', base / 'package'
            source.mkdir()
            wanted = {f'IosevkaNerdFontMono-{style}.ttf' for style in self.styles}
            for name in wanted | {'IosevkaNerdFontMono-Light.ttf', 'IosevkaNerdFont-Regular.ttf',
                                  'IosevkaNerdFontPropo-Regular.ttf'}:
                (source / name).write_text(name)
            notices = {Path(path).name for path in self.expected_files()
                       if path.startswith('usr/share/licenses/')}
            self.assertEqual(len(notices), 19)
            for name in notices:
                (source / name).write_text('upstream ' + name)
            self.stage(source, destination)
            fonts = list(destination.rglob('*.ttf'))
            self.assertEqual({p.name for p in fonts}, wanted)
            self.assertEqual(len(fonts), 4)
            for path in fonts:
                self.assertEqual(path.read_bytes(), (source / path.name).read_bytes())
            for name in notices:
                installed = destination / 'usr/share/licenses' / FONT_PACKAGE / name
                self.assertEqual(installed.read_bytes(), (source / name).read_bytes())
            self.assertEqual({str(p.relative_to(destination)) for p in destination.rglob('*')
                              if p.is_file()}, self.expected_files())

    def test_font_is_in_release_build_and_source_inventory(self):
        script = (ROOT / 'packaging/build.sh').read_text()
        order = re.search(r'ORDER=\((.*?)\)', script, re.S).group(1).split()
        self.assertIn(FONT_PACKAGE, order)
        self.assertLess(order.index(FONT_PACKAGE), order.index('emaki-config'))
        import sys
        sys.path.insert(0, str(ROOT / 'packaging'))
        try:
            self.assertIn(FONT_PACKAGE, runpy.run_path(str(ROOT / 'packaging/source_archive.py'))['PACKAGES'])
        finally:
            sys.path.pop(0)

    def test_source_is_pinned_and_full_font_can_coexist(self):
        result = subprocess.run(['bash', '-eu', '-c',
                                 'source "$1"; printf "%s\\n" "${source[@]}"; '
                                 'printf "\\0"; printf "%s\\n" "${sha256sums[@]}"',
                                 '_', str(self.recipe)], check=True, capture_output=True, text=True)
        sources, checksums = [part.splitlines() for part in result.stdout.split('\0')]
        self.assertEqual(len(sources), 18)
        self.assertEqual(len(checksums), len(sources))
        self.assertEqual(sources[0],
            'Iosevka-3.5.1.tar.xz::https://github.com/ryanoasis/nerd-fonts/releases/download/v3.5.1/Iosevka.tar.xz')
        self.assertEqual(checksums[0],
            '3b94ea1dc3955756762f977b7677bca671947dd56bc755a6f8465a8e83b5f257')
        names = [source.split('::', 1)[0] for source in sources[1:]]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names) | {'LICENSE.md', 'README.md'},
                         {Path(path).name for path in self.expected_files()
                          if path.startswith('usr/share/licenses/')})
        for source, checksum in zip(sources, checksums):
            self.assertRegex(checksum, r'^[0-9a-f]{64}$')
            if '::' not in source:
                self.assertEqual(hashlib.sha256(self.recipe.with_name(source).read_bytes()).hexdigest(),
                                 checksum)
            elif 'raw.githubusercontent.com' in source:
                self.assertRegex(source, r'/[0-9a-f]{40}/')
        extension = names.index('font-awesome-extension-LICENCE') + 1
        self.assertEqual(sources[extension], 'font-awesome-extension-LICENCE::'
            'https://raw.githubusercontent.com/AndreLZGava/font-awesome-extension/'
            '09d80249058ee8018a45da30add4339289dbb466/LICENCE')
        self.assertEqual(checksums[extension],
                         'b47b15d771c4507a75ff65beca3a78168d6af4cca9b45177a1aa817139c64ba5')
        self.assertNotIn("conflicts=", self.recipe.read_text())
        self.assertNotIn("replaces=", self.recipe.read_text())

    @unittest.skipUnless(os.environ.get('EMAKI_IOSEVKA_SOURCE'), 'set EMAKI_IOSEVKA_SOURCE to extracted upstream archive')
    def test_real_faces_resolve_in_fontconfig_and_kitty(self):
        with tempfile.TemporaryDirectory(prefix='font-resolution-') as directory:
            base = Path(directory)
            destination = base / 'package'
            self.stage(Path(os.environ['EMAKI_IOSEVKA_SOURCE']).resolve(), destination)
            fonts = destination / 'usr/share/fonts/emaki-iosevka'
            config = base / 'fonts.conf'
            config.write_text(f'<fontconfig><dir>{fonts}</dir><cachedir>{base / "cache"}</cachedir></fontconfig>')
            env = {**os.environ, 'FONTCONFIG_FILE': str(config), 'FONTCONFIG_PATH': str(base),
                   'HOME': str(base), 'XDG_CACHE_HOME': str(base / 'cache')}
            for style in self.styles:
                match = subprocess.check_output(['fc-match', '-f', '%{file}',
                    'Iosevka Nerd Font Mono:style=' + style.replace('BoldItalic', 'Bold Italic')],
                    env=env, text=True)
                self.assertEqual(Path(match), fonts / f'IosevkaNerdFontMono-{style}.ttf')
            probe = """
import json
from kitty.config import defaults
from kitty.fonts.common import get_font_files
from kitty.options.utils import parse_font_spec
opts = defaults._replace(font_family=parse_font_spec('Iosevka Nerd Font Mono'))
print(json.dumps({k: v['path'] for k, v in get_font_files(opts).items()}))
"""
            result = subprocess.check_output(['kitty', '+runpy', probe], text=True, env=env)
            faces = json.loads(result)
            self.assertEqual(faces, {key: str(fonts / f'IosevkaNerdFontMono-{style}.ttf')
                                    for key, style in zip(('medium', 'bold', 'italic', 'bi'), self.styles)})
            self.assertLess(sum(p.stat().st_size for p in destination.rglob('*') if p.is_file()), 64 * 1024**2)


if __name__ == '__main__':
    unittest.main()
