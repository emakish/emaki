#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check external-GRUB assets without building or mounting an ISO."""
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('image_check', HERE / 'verify-image.py')
image_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image_check)


class LoopbackAssets(unittest.TestCase):
    def test_missing_background_rejected_and_caller_font_not_required(self):
        for mode in ('', '-m stretch ', '-mstretch ', '--mode stretch ', '--mode=stretch '):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                boot = Path(temporary) / 'boot'
                grub = boot / 'grub'
                grub.mkdir(parents=True)
                text = (HERE / 'profile/grub/loopback.cfg').read_text()
                text = re.sub(r'background_image[^\n]*',
                              f'background_image {mode}/boot/grub/background.png', text)
                (grub / 'loopback.cfg').write_text(text)
                with self.assertRaisesRegex(ValueError, 'loopback background'):
                    image_check.check_loaders(boot, check_assets=True)
                (grub / 'background.png').write_bytes(b'background fixture')
                image_check.check_loaders(boot, check_assets=True)
                self.assertFalse((grub / 'fonts/unicode.pf2').exists())
                (grub / 'background.png').write_bytes(b'')
                with self.assertRaisesRegex(ValueError, 'loopback background'):
                    image_check.check_loaders(boot, check_assets=True)

    def test_caller_font_is_guarded_before_load(self):
        text = (HERE / 'profile/grub/loopback.cfg').read_text()
        if '${prefix}/fonts/unicode.pf2' in text:
            self.assertIn('if [ -f "${prefix}/fonts/unicode.pf2" ]; then\n'
                          'if loadfont "${prefix}/fonts/unicode.pf2" ; then', text)
        else:
            self.assertRegex(text, r'if loadfont /boot/grub/menu\.pf2; then')
        font = re.search(r'if loadfont .*?; then', text)
        self.assertIsNotNone(font)
        self.assertLess(font.start(), text.index('terminal_output gfxterm'))
        self.assertLess(font.start(), text.index('background_image'))

    def test_artwork_staged_after_archiso_builds_tree(self):
        script = (HERE / 'build.sh').read_text()
        first = script.index('mkarchiso -v -w "$work/mk"')
        staging = script.index('install -Dm644 "$ROOT/art/grub/background.png" '
                               '"$work/mk/iso/boot/grub/background.png"')
        second = script.index('mkarchiso -v -w "$work/mk"', first + 1)
        self.assertLess(first, staging)
        self.assertLess(staging, second)


if __name__ == '__main__':
    unittest.main()
