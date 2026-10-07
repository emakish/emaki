#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The unlock artwork preserves the menu's wave below its text field."""

from pathlib import Path
import unittest

from PIL import Image, ImageChops


ROOT = Path(__file__).resolve().parents[1]


class MenuPictureIdentity(unittest.TestCase):
    def test_uncovered_wave_matches_menu_picture(self):
        with Image.open(ROOT / 'art/grub/background.png') as source:
            menu = source.convert('RGB').resize((2560, 1600), Image.Resampling.LANCZOS)
        # The bottom eighth is outside the text, its feathered band and the
        # cursor strip. Downsampling compares picture structure and color,
        # rather than mistaking an arbitrary colorful image for the menu wave.
        region = (0, 1400, 2560, 1600)
        expected = menu.crop(region).resize((256, 20), Image.Resampling.BOX)
        for state in ('unlock', 'wrong', 'checking'):
            with self.subTest(state=state):
                path = ROOT / 'installer/emaki_installer/grub_artwork/C' / f'{state}-2560x1600.png'
                with Image.open(path) as source:
                    actual = source.convert('RGB').crop(region).resize(
                        expected.size, Image.Resampling.BOX)
                self.assertIsNone(ImageChops.difference(expected, actual).getbbox(),
                                  f'{state} must preserve the menu wave exactly')


if __name__ == '__main__':
    unittest.main()
