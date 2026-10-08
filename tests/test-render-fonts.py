#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rendering must use the fonts available on installed targets."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('render_shell', ROOT / 'tests/render-shell.py')
RENDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RENDER)


class RenderFonts(unittest.TestCase):
    def test_isolated_fonts_and_default_families(self):
        cache = ROOT / '.cache' / 'render-shell'
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache) as directory:
            profile = Path(directory)
            (profile / 'cache').mkdir()
            config = RENDER.shipped_font_config(profile)
            env = dict(os.environ, FONTCONFIG_FILE=str(config))
            for requested, expected in [('sans-serif', 'Noto Sans'),
                                        ('monospace', 'Noto Sans Mono'),
                                        ('Iosevka Nerd Font Mono', 'Noto Sans Mono')]:
                family = subprocess.check_output(
                    ['fc-match', '--format', '%{family}', requested], env=env, text=True)
                self.assertEqual(family.split(',')[0], expected)
            families = subprocess.check_output(
                ['fc-list', '--format', '%{family}\n'], env=env, text=True)
            self.assertNotIn('DejaVu', families)
            self.assertTrue(all(name.startswith('Noto ')
                                for name in families.splitlines()))


if __name__ == '__main__':
    unittest.main()
