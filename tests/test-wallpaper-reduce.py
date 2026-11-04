#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Isolated streaming PNG reduction, sampling and corrupt-input refusals."""
import io
import os
from pathlib import Path
import resource
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
import reaper
reaper.guard()  # Nothing this test starts outlives it.

from PIL import Image

HELPER = Path(__file__).resolve().parents[1] / 'shell/helpers/wallpaper_reduce.py'


def chunk(stream, kind, data):
    stream.write(struct.pack('!I', len(data)) + kind + data
                 + struct.pack('!I', zlib.crc32(kind + data)))


def streamed_png(path, width, height):
    """A full-size fixture with only one source row resident at a time."""
    with path.open('wb') as stream:
        stream.write(b'\x89PNG\r\n\x1a\n')
        chunk(stream, b'IHDR', struct.pack('!IIBBBBB', width, height, 8, 2, 0, 0, 0))
        compressor = zlib.compressobj(1)
        for y in range(height):
            data = compressor.compress(b'\0' + bytes((y % 256, 70, 120)) * width)
            if data:
                chunk(stream, b'IDAT', data)
        chunk(stream, b'IDAT', compressor.flush())
        chunk(stream, b'IEND', b'')


class Reduction(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='wallpaper-reduce-')
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / 'source.png'
        self.environment = dict(os.environ)
        for name in ('NIRI_SOCKET', 'WAYLAND_DISPLAY', 'DISPLAY'):
            self.environment.pop(name, None)

    def run_worker(self, width=2, height=2, crop=(), timeout=6):
        with self.source.open('rb') as source:
            return subprocess.run([sys.executable, '-I', '-B', str(HELPER),
                                   str(source.fileno()), str(width), str(height),
                                   *(str(value) for value in crop)],
                                  pass_fds=(source.fileno(),), capture_output=True,
                                  timeout=timeout, env=self.environment)

    def pattern(self, mode='RGB'):
        image = Image.new('RGB', (8, 8))
        image.putdata([(x * 24, y * 24, 120) for y in range(8) for x in range(8)])
        image.convert(mode).save(self.source)
        return image

    def assert_refused(self, result):
        self.assertGreater(result.returncode, 0, 'must exit without a fatal signal')
        self.assertEqual(result.stdout, b'')

    def test_pixel_centres_and_crop(self):
        image = self.pattern()
        for crop in ((), (2, 2, 6, 6), (.5, 1.5, 6.5, 7.5)):
            with self.subTest(crop=crop):
                result = self.run_worker(crop=crop)
                self.assertEqual(result.returncode, 0, result.stderr)
                actual = Image.open(io.BytesIO(result.stdout))
                expected = image.transform((2, 2), Image.Transform.EXTENT,
                                           crop or (0, 0, 8, 8), Image.Resampling.BILINEAR)
                self.assertEqual(actual.tobytes(), expected.tobytes())

    def test_palette_gray_alpha(self):
        for mode in ('P', 'L', 'RGBA', 'LA', '1'):
            with self.subTest(mode=mode):
                self.pattern(mode)
                result = self.run_worker()
                self.assertEqual(result.returncode, 0, result.stderr)
                actual = Image.open(io.BytesIO(result.stdout))
                with Image.open(self.source) as source:
                    expected = source.convert('RGB').transform(
                        (2, 2), Image.Transform.EXTENT, (0, 0, 8, 8), Image.Resampling.BILINEAR)
                self.assertEqual(actual.tobytes(), expected.tobytes())

    def test_truncated_and_bad_crc(self):
        self.pattern()
        data = self.source.read_bytes()
        for bad in (b'not a png', data[:-1], data[:-12], data[:-1] + bytes((data[-1] ^ 1,))):
            with self.subTest(size=len(bad)):
                self.source.write_bytes(bad)
                self.assert_refused(self.run_worker())
        # Corrupt the IDAT checksum while retaining a valid container structure.
        bad = bytearray(data)
        start = data.index(b'IDAT')
        length = struct.unpack('!I', data[start - 4:start])[0]
        bad[start + 4 + length] ^= 1
        self.source.write_bytes(bad)
        self.assert_refused(self.run_worker())

    def test_interlace_and_animation(self):
        self.pattern()
        data = bytearray(self.source.read_bytes())
        data[28] = 1
        data[29:33] = struct.pack('!I', zlib.crc32(data[12:29]))
        self.source.write_bytes(data)
        self.assert_refused(self.run_worker())
        image = Image.new('RGB', (8, 8), 'red')
        image.save(self.source, save_all=True, append_images=[Image.new('RGB', (8, 8), 'blue')])
        self.assert_refused(self.run_worker())

    def test_geometry_and_source_bounds(self):
        self.pattern()
        for width, height, crop in ((0, 2, ()), (9, 2, ()), (2, 8193, ()),
                                    (8000, 8000, ()), (2, 2, (-1, 0, 8, 8)),
                                    (2, 2, (0, 0, float('nan'), 8))):
            with self.subTest(width=width, height=height, crop=crop):
                self.assert_refused(self.run_worker(width, height, crop))
        with self.source.open('r+b') as source:
            source.truncate(128 * 1024 * 1024 + 1)
        self.assert_refused(self.run_worker())

    def test_large_strip_full_width_and_centre(self):
        streamed_png(self.source, 27004, 4320)
        for width, height, crop in ((6751, 1080, ()),
                                    (1908, 1178, (10003.5, 0, 17000.5, 4320))):
            with self.subTest(width=width, height=height):
                result = self.run_worker(width, height, crop)
                self.assertEqual(result.returncode, 0, result.stderr)
                with Image.open(io.BytesIO(result.stdout)) as output:
                    self.assertEqual(output.size, (width, height))
                    output.load()
                    self.assertEqual(output.getpixel((width // 2, height // 2))[1:], (70, 120))
        # Linux reports KiB here. A full 116.7-million-pixel decode alone would
        # exceed this budget; streaming must stay below the old 48M RGBA raster.
        self.assertLess(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss, 192 * 1024)


if __name__ == '__main__':
    unittest.main()
