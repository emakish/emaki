# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Scalable artwork keeps whole sentences readable and terminal output invisible."""

from pathlib import Path
import struct
import unittest
import zlib

from emaki_installer import grub_screen as screen


def decode_png(data):
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    pos, compressed = 8, b''
    while pos < len(data):
        size = struct.unpack_from('>I', data, pos)[0]
        name, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + size]
        assert struct.unpack_from('>I', data, pos + 8 + size)[0] == zlib.crc32(name + body)
        if name == b'IHDR':
            width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', body)
            assert (depth, color, compression, filtering, interlace) == (8, 2, 0, 0, 0)
        elif name == b'IDAT':
            compressed += body
        pos += 12 + size
    raw = zlib.decompress(compressed)
    stride = width * 3 + 1
    assert len(raw) == height * stride
    rows = []
    previous = bytes(width * 3)
    for y in range(height):
        filter_type = raw[y * stride]
        row = raw[y * stride + 1:(y + 1) * stride]
        assert filter_type in (0, 2)
        if filter_type == 2:
            row = bytes((a + b) & 255 for a, b in zip(row, previous))
        rows.append(row)
        previous = row
    return width, height, b''.join(rows)


class UnlockAssets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets = screen.assets()

    def test_approved_words(self):
        self.assertEqual(screen.TEXT, (
            "This computer's disk is encrypted.",
            "Type your disk password and press Enter.",
            "The letters do not appear while you type.",
            "After Enter, wait a few seconds.",
        ))
        self.assertEqual(screen.WRONG, 'Wrong password. Try again.')
        self.assertEqual(screen.CHECKING, 'Checking the password…')
        self.assertEqual(len(self.assets), 4)
        self.assertEqual(screen.SIZES, ((2560, 1600),))
        self.assertLess(sum(map(len, self.assets.values())), 12 * 1024 * 1024)

    def test_hidden_ascii_font(self):
        fields, glyphs = screen._read_font(self.assets['hidden.pf2'])
        self.assertEqual(fields[b'NAME'], b'Emaki Hidden Regular 4\0')
        self.assertEqual(fields[b'MAXH'], b'\0\4')
        self.assertEqual(sorted(glyphs), list(range(32, 127)))
        for width, height, dx, dy, advance, bitmap in glyphs.values():
            self.assertEqual((width, height, advance), (4, 4, 4))
            self.assertFalse(any(bitmap))

    def test_all_variants_are_antialiased_centered_and_keep_the_cursor_hidden(self):
        cream = bytes.fromhex('f2e6d8')
        orange = bytes.fromhex('e2733f')
        for variant in ('A', 'B', 'C'):
            assets = screen.assets(variant)
            for width, height in screen.SIZES:
                with self.subTest(variant=variant, mode=(width, height)):
                    w, h, initial = decode_png(assets[f'unlock-{width}x{height}.png'])
                    _, _, retry = decode_png(assets[f'wrong-{width}x{height}.png'])
                    _, _, checking = decode_png(assets[f'checking-{width}x{height}.png'])
                    self.assertEqual((w, h), (width, height))
                    for state in (initial, retry, checking):
                        self.assertFalse(any(state[:128 * width * 3]))
                    # Cover the tested 400..8192px contract, including every
                    # integer height. The floor is monotonic, so larger heights
                    # retain at least as many black rows.
                    # Stock GOP auto has no lower bound: modes below 400 need
                    # a future runtime size guard, not an unsupported guarantee.
                    for target_height in range(400, 8193):
                        self.assertGreaterEqual(128 * target_height // height, 32,
                                                f'{target_height}px cursor strip')
                    colors = {retry[i:i + 3] for i in range(0, len(retry), 3)}
                    self.assertGreater(len(colors), 100)  # antialiased, not one-bit glyphs
                    bands = []
                    for y in range(240, height):
                        row = retry[y * width * 3:(y + 1) * width * 3]
                        if row.count(cream) + row.count(orange) < 3:
                            continue
                        xs = [x for x in range(width) if row[x * 3:x * 3 + 3] in (cream, orange)]
                        if xs:
                            self.assertGreaterEqual(min(xs), width * .12)
                            self.assertLessEqual(max(xs), width * .88)
                            if not bands or y > bands[-1][-1] + 1:
                                bands.append([])
                            bands[-1].append(y)
                    self.assertEqual(len(bands), 6 if variant == 'C' else 5)
                    text_bands = bands[-5:]
                    centers = [(b[0] + b[-1]) / 2 for b in text_bands]
                    gaps = [b - a for a, b in zip(centers, centers[1:])]
                    self.assertLessEqual(max(gaps) - min(gaps), 1)
                    self.assertAlmostEqual((centers[0] + centers[-1]) / 2, height / 2, delta=1)
                    for band in text_bands:
                        xs = [x for y in band for x in range(width)
                              if retry[(y * width + x) * 3:(y * width + x + 1) * 3] in (cream, orange)]
                        self.assertAlmostEqual((min(xs) + max(xs)) / 2, width / 2, delta=width * .002)
                    wrong_start, wrong_end = (text_bands[-1][0] - 3) * width * 3, (text_bands[-1][-1] + 4) * width * 3
                    self.assertEqual(initial[:wrong_start], retry[:wrong_start])
                    self.assertEqual(initial[wrong_end:], retry[wrong_end:])
                    self.assertNotIn(orange, initial[wrong_start:wrong_end])
                    self.assertNotIn(cream, initial[wrong_start:wrong_end])
                    # Checking keeps the instructions fixed and replaces only the status line.
                    status_top = int((centers[-2] + centers[-1]) / 2) * width * 3
                    self.assertEqual(checking[:status_top], initial[:status_top])
                    self.assertNotEqual(checking[status_top:], initial[status_top:])
                    self.assertNotEqual(checking[status_top:], retry[status_top:])
                    self.assertFalse(any(checking[:128 * width * 3]))
                    points = [(x, y) for y in range(status_top // (width * 3), height)
                              for x in range(width)
                              if checking[(y * width + x) * 3:(y * width + x + 1) * 3] in (cream, orange)]
                    xs, ys = zip(*points)
                    self.assertGreaterEqual(min(xs), width * .12)
                    self.assertLessEqual(max(xs), width * .88)
                    self.assertAlmostEqual((min(xs) + max(xs)) / 2, width / 2, delta=width * .002)
                    self.assertAlmostEqual((min(ys) + max(ys)) / 2, centers[-1], delta=1)
                    expected_color = bytes.fromhex('f2e6d8' if variant == 'A' else 'e2733f')
                    self.assertIn(expected_color, {retry[i:i + 3] for i in range(wrong_start, wrong_end, 3)})
                    self.assertIn(expected_color, {checking[i:i + 3] for i in range(status_top, len(checking), 3)})

    def test_unknown_variant_and_missing_artwork_fail_closed(self):
        from unittest.mock import patch
        with self.assertRaises(ValueError):
            screen.assets('../outside')
        with patch.object(screen, 'ARTWORK', Path('/nonexistent/emaki-unlock-artwork')):
            with self.assertRaisesRegex(ValueError, 'artwork is unavailable'):
                screen.assets()

    def test_invalid_hidden_font_is_rejected(self):
        font = self.assets['hidden.pf2']
        for data in (b'', b'not a font', font[:100], font[:-10]):
            with self.subTest(length=len(data)):
                with self.assertRaises(ValueError):
                    screen._read_font(data)


if __name__ == '__main__':
    unittest.main()
