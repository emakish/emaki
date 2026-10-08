#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline checks of visible boot evidence, without a VM."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('frame_assessment', ROOT / 'tests/vm/frame_assessment.py')
CHECK = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CHECK)


class Frames(unittest.TestCase):
    def fixture(self, height=24, text=None):
        image = Image.new('RGB', (640, 480), '#101010')
        draw = ImageDraw.Draw(image)
        words = []
        lines = text or ['disk is encrypted', 'type your disk password and press enter',
                         'letters do not appear while you type']
        for row, line in enumerate(lines):
            x, y = 70, 110 + row * 80
            for word in line.split():
                width = len(word) * 8
                words.append(dict(text=word, left=x, top=y, width=width, height=height))
                # Synthetic glyph strokes with the same 8-pixel cell pitch as
                # the historical tiny console; independently controlled height.
                for offset in range(0, width, 8):
                    draw.rectangle((x + offset, y, x + offset + 3, y + height - 1), fill='white')
                x += width + 8
        return image, words

    def test_blank_rejected_even_with_prompt_words(self):
        _, words = self.fixture()
        with self.assertRaisesRegex(RuntimeError, 'Blank'):
            CHECK.assess_image(Image.new('RGB', (640, 480)), 'prompt', words)

    def test_tiny_8_by_16_prompt_rejected(self):
        image, words = self.fixture(height=16)
        with self.assertRaisesRegex(RuntimeError, 'too small'):
            CHECK.assess_image(image, 'prompt', words)

    def test_readable_prompt_pixels_and_text(self):
        image, words = self.fixture()
        result = CHECK.assess_image(image, 'prompt', words)
        self.assertEqual(result['glyph_height'], 24)
        self.assertGreater(result['lit_pixels'], result['minimum_lit_pixels'])

    def test_bright_frame_without_semantic_prompt_fails(self):
        image, _ = self.fixture()
        with self.assertRaisesRegex(RuntimeError, 'missing'):
            CHECK.assess_image(image, 'prompt', [])

    def test_readable_wrong_words_are_not_a_password_prompt(self):
        image, words = self.fixture(text=['This is an unrelated readable screen'])
        with self.assertRaisesRegex(RuntimeError, 'missing'):
            CHECK.assess_image(image, 'prompt', words)

    def test_committed_prompt_artwork_with_host_ocr(self):
        # The visible prompt is baked into this raster; the hidden terminal
        # font is 4px and unlock-24.pf2 is only the menu fallback.
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'prompt.png'
            with Image.open(ROOT / 'installer/emaki_installer/grub_artwork/C/unlock-2560x1600.png') as artwork:
                artwork.resize((640, 480)).save(path)
            self.assertEqual(CHECK.assess_frame(path, 'prompt')['status'], 'PASS')

    def test_prompt_at_firmware_top_edge_fails(self):
        image, words = self.fixture()
        words[0]['top'] = 0
        with self.assertRaisesRegex(RuntimeError, 'outside'):
            CHECK.assess_image(image, 'prompt', words)

    def test_text_alone_does_not_make_a_menu(self):
        image, words = self.fixture(text=['Emaki snapshots'])
        with self.assertRaisesRegex(RuntimeError, 'menu border'):
            CHECK.assess_image(image, 'menu', words, ('Emaki snapshots',))

    def test_menu_selection_is_independent_of_highlighted_text(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'menu.png'
            path.write_bytes((ROOT / 'tests/fixtures/vm-frames/G-eyes57-snaprow-enc.png').read_bytes())
            record = CHECK.assess_frame(path, 'menu', ('snapshot',), selected_index=0)
            self.assertEqual(record['selection']['index'], 0)
            with self.assertRaisesRegex(RuntimeError, 'Unexpected GRUB selection'):
                CHECK.assess_frame(path, 'menu', selected_index=1)
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                CHECK.assess_frame(path, 'menu', ('unrelated menu entry',))
            words = [dict(word, text='unrelated') for word in CHECK.read_words(path)]
            with self.assertRaisesRegex(RuntimeError, 'menu labels'):
                CHECK.assess_image(Image.open(path), 'menu', words)

    def test_real_vm_frames_with_host_ocr(self):
        fixtures = ROOT / 'tests/fixtures/vm-frames'
        stages = {
            'G-clean017-prompt': ('prompt',),
            'G-clean019-menu-enc': ('menu',),
            'G-eyes57-snaprow-enc': ('menu',),
            'G-eyes58-kernel-enc': ('menu',),
            'G-clean021-greeter': ('after-unlock',),
            'G-key11-greeter2560': ('after-unlock',),
            'G-audit-f01-cream': ('after-unlock',),
            'G-hw37-cream': ('after-unlock',),
            'B-audit-g19-lock-wrongpw': ('after-unlock',),
            'B-audit-i07-greeter-wrongpw': ('after-unlock',),
            'F1-g18-lock-crop640': ('after-unlock',),
            'B-current-greeter-wrongpw': ('after-unlock',),
            'B-n2full-installed-prompt': ('prompt', 'menu', 'after-unlock'),
            'B-clean018-wrongpw': ('prompt',),
            'B-key12-display-inactive': ('after-unlock',),
        }
        self.assertEqual({p.stem for p in fixtures.glob('*.png')}, set(stages))
        with tempfile.TemporaryDirectory() as temp:
            for label, checks in stages.items():
                path = Path(temp) / (label + '.png')
                path.write_bytes((fixtures / path.name).read_bytes())
                for stage in checks:
                    with self.subTest(frame=label, stage=stage):
                        if label.startswith('G-'):
                            self.assertEqual(CHECK.assess_frame(path, stage)['status'], 'PASS')
                        else:
                            reason = ''
                            if label.startswith('B-audit-') or label in ('F1-g18-lock-crop640', 'B-current-greeter-wrongpw'):
                                reason = 'Authentication error feedback visible:.*dark-red pixels'
                            with self.assertRaisesRegex(RuntimeError, reason):
                                CHECK.assess_frame(path, stage)
                            self.assertEqual(json.loads(path.with_suffix('.assessment.json').read_text())['status'], 'FAIL')

    def test_field_requires_bottom_border(self):
        image = Image.new('RGB', (640, 360), '#fff8f3')
        draw = ImageDraw.Draw(image)
        draw.rectangle((160, 152, 480, 359), fill='#d0c0b0')
        self.assertIsNone(CHECK.field_top(image))
        draw.rectangle((160, 208, 480, 359), fill='#fff8f3')
        self.assertEqual(CHECK.field_top(image), 152)

    def test_feedback_ink_threshold_without_ocr(self):
        for count in (8, 99, 100):
            with self.subTest(count=count):
                image, words = self.fixture(height=11, text=['Password'])
                ImageDraw.Draw(image).rectangle((160, 212, 480, 268), outline='#fff8f3')
                ImageDraw.Draw(image).rectangle((100, 283, 100 + count - 1, 283), fill='#a01b45')
                self.assertEqual(CHECK.feedback_ink(image), count)
                if count >= 100:
                    with self.assertRaisesRegex(RuntimeError, 'Authentication error feedback visible'):
                        CHECK.assess_image(image, 'after-unlock', words)
                else:
                    CHECK.assess_image(image, 'after-unlock', words)

    def test_account_locked_feedback_without_ocr(self):
        for plate in (False, True):
            with self.subTest(plate=plate):
                image, words = self.fixture(height=11, text=['Password'])
                draw = ImageDraw.Draw(image)
                draw.rectangle((160, 212, 480, 268), outline='#fff8f3')
                if plate:
                    draw.rectangle((90, 279, 550, 310), fill='#fff8f3')
                draw.text((320, 283), 'Account locked. Try again later.', anchor='mt',
                          font=ImageFont.truetype('DejaVuSans.ttf', 13), fill='#a01b45')
                with self.assertRaisesRegex(RuntimeError, 'Authentication error feedback visible'):
                    CHECK.assess_image(image, 'after-unlock', words)

    def test_dark_on_light_greeter(self):
        from PIL import ImageOps
        image, words = self.fixture(height=11, text=['Password'])
        result = CHECK.assess_image(ImageOps.invert(image), 'after-unlock', words)
        self.assertEqual(result['glyph_height'], 11)

    def test_too_small_greeter_rejected(self):
        image, words = self.fixture(height=8, text=['Password'])
        with self.assertRaisesRegex(RuntimeError, 'too small'):
            CHECK.assess_image(image, 'after-unlock', words)

    def test_firmware_password_error_rejected(self):
        image, words = self.fixture(text=['Password', 'UEFI interactive shell'])
        with self.assertRaisesRegex(RuntimeError, 'error visible'):
            CHECK.assess_image(image, 'after-unlock', words)

    def test_after_unlock_positive_content(self):
        image, words = self.fixture(text=['Password'])
        CHECK.assess_image(image, 'after-unlock', words)
        image, words = self.fixture(text=['UEFI interactive shell'])
        with self.assertRaisesRegex(RuntimeError, 'error visible'):
            CHECK.assess_image(image, 'after-unlock', words)

    def test_error_alongside_good_marker_rejected(self):
        image, words = self.fixture(text=['Password', 'error invalid passphrase'])
        with self.assertRaisesRegex(RuntimeError, 'error visible'):
            CHECK.assess_image(image, 'after-unlock', words)

    def test_tsv_ignores_unconfident_text(self):
        tsv = ('level\tleft\ttop\twidth\theight\tconf\ttext\n'
               '5\t10\t20\t30\t24\t95.0\tPassword\n'
               '5\t0\t0\t100\t10\t10\tNoise\n')
        self.assertEqual(CHECK.parse_words(tsv), [dict(text='Password', left=10, top=20,
                                                      width=30, height=24)])

    def test_unavailable_ocr_leaves_red_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'frame.png'
            self.fixture()[0].save(path)
            with patch.object(CHECK.shutil, 'which', return_value=None):
                with self.assertRaisesRegex(RuntimeError, 'tesseract'):
                    CHECK.assess_frame(path, 'prompt')
            self.assertEqual(json.loads(path.with_suffix('.assessment.json').read_text())['status'], 'FAIL')


if __name__ == '__main__':
    unittest.main()
