#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Large strips cross session publication and lock boundaries with bounded pixels."""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlparse
import zlib

from PIL import Image
import reaper
reaper.guard()

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / 'shell/helpers'))
import wallpaper


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


publisher = load_module('large_publisher', ROOT / 'shell/helpers/publish-wallpaper.py')
fixtures = load_module('reduce_fixtures', ROOT / 'tests/test-wallpaper-reduce.py')


def uncompressed_gray_png(path):
    # 116.7 MB encoded input crosses the old 32 MiB reader bound without
    # allocating the source image. Its row and zlib buffers stay below 128 KiB.
    width, height = 27004, 4320
    with path.open('wb') as stream:
        stream.write(b'\x89PNG\r\n\x1a\n')
        fixtures.chunk(stream, b'IHDR', struct.pack('!IIBBBBB', width, height, 8, 0, 0, 0, 0))
        compressor = zlib.compressobj(0)
        row = b'\0' + bytes((181,)) * width
        for _ in range(height):
            data = compressor.compress(row)
            if data:
                fixtures.chunk(stream, b'IDAT', data)
        fixtures.chunk(stream, b'IDAT', compressor.flush())
        fixtures.chunk(stream, b'IEND', b'')


def texture_image(reply):
    value = reply['texture']
    if value.startswith('data:'):
        return Image.open(io.BytesIO(base64.b64decode(value.split(',', 1)[1])))
    return Image.open(unquote(urlparse(value).path))


class LargeWallpaper(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory(prefix='wallpaper-large-sources-')
        cls.addClassCleanup(cls.fixtures.cleanup)
        base = Path(cls.fixtures.name)
        cls.color = base / 'large.png'
        cls.gray = base / 'large-encoded.png'
        fixtures.streamed_png(cls.color, 27004, 4320)
        uncompressed_gray_png(cls.gray)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='wallpaper-large-')
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.config = self.base / 'config/wpaperd/config.toml'
        self.config.parent.mkdir(parents=True)
        self.destination = self.base / 'published'
        self.destination.mkdir()
        self.destination.chmod(0o2750)
        self.published_config = self.destination / 'wpaperd/config.toml'
        self.old = self.base / 'old.png'
        Image.new('RGB', (16, 8), (230, 10, 20)).save(self.old)
        self.selected = self.old
        self.outputs = {'fixture': {'current_mode': 0,
                                    'modes': [{'width': 1920, 'height': 1080}],
                                    'logical': {'transform': 'normal'}}}
        environment = dict(os.environ)
        for name in ('NIRI_SOCKET', 'WAYLAND_DISPLAY', 'DISPLAY', 'EMAKI_GREETER_WALLPAPER_ROOT'):
            environment.pop(name, None)
        environment.update(HOME=str(self.base), XDG_CONFIG_HOME=str(self.config.parent.parent),
                           XDG_CACHE_HOME=str(self.base / 'cache'), NIRI_SOCKET='fixture-only')
        self.environment = patch.dict(os.environ, environment, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.query = patch.object(wallpaper, 'compositor_query', side_effect=self.compositor)
        self.query.start()
        self.addCleanup(self.query.stop)
        self.assertEqual(publisher.attempt(self.config, self.destination)['state'], 'published')
        self.old_published = self.published_image()

    def compositor(self, endpoint, request):
        self.assertEqual(endpoint, 'fixture-only')
        if request == 'EmakiWallpaper':
            return {'mode': 'strip', 'load_state': 'ready', 'files': {'ring': str(self.selected)}}
        self.assertEqual(request, 'Outputs')
        return self.outputs

    def published_image(self):
        return Path(tomllib.loads(self.published_config.read_text())['default']['path'])

    def assert_new_picture(self, source, expected):
        self.selected = source
        result = publisher.attempt(self.config, self.destination)
        self.assertEqual(result, {'state': 'published', 'images': 1})
        published = self.published_image()
        self.assertNotEqual(published, self.old_published)
        self.assertLessEqual(published.stat().st_size, wallpaper.MAX_IMAGE)
        with Image.open(published) as image:
            self.assertEqual(image.size, (1920, 1080))
            self.assertLessEqual(image.width * image.height, wallpaper.MAX_PIXELS)
            self.assertEqual(image.getpixel((960, 540)), expected)
        result = wallpaper.texture(1920, 1080, 1, 'fixture', 'sharp')
        self.assertEqual(result['state'], 'ready', result)
        with texture_image(result) as image:
            self.assertEqual(image.size, (1920, 1080))
            self.assertEqual(image.getpixel((960, 540)), expected)
        # The login reader consumes only the already-reduced published copy.
        with patch.dict(os.environ, EMAKI_GREETER_WALLPAPER_ROOT=str(self.destination)):
            result = wallpaper.texture(1920, 1080, 1, 'fixture', 'sharp')
        self.assertEqual(result['state'], 'ready', result)
        with texture_image(result) as image:
            self.assertEqual(image.getpixel((960, 540)), expected)

    def test_large_raster_replaces_previous_login_and_lock(self):
        self.assert_new_picture(self.color, (113, 70, 120))

    def test_publication_geometry_uses_active_output_envelope(self):
        def output(width, height, transform='normal', current=0):
            return {'current_mode': current, 'modes': [{'width': width, 'height': height}],
                    'logical': {'transform': transform}}

        for transform in ('90', '270', 'Flipped90', 'Flipped270'):
            with self.subTest(transform=transform):
                self.outputs = {'portrait': output(3840, 2160, transform)}
                self.assertEqual(wallpaper.publication_geometry(), (2160, 3840))
        self.outputs['landscape'] = output(1920, 1080)
        self.assertEqual(wallpaper.publication_geometry(), (6827, 3840))
        self.outputs = {'landscape': output(1920, 1080),
                        'disabled': output(3840, 2160, '90', current=None)}
        self.assertEqual(wallpaper.publication_geometry(), (1920, 1080))
        self.outputs = {'disabled': output(3840, 2160, '90', current=None)}
        self.assertEqual(wallpaper.publication_geometry(), (3840, 2160))
        with patch.object(wallpaper, 'compositor_query', side_effect=OSError('fixture unavailable')):
            self.assertEqual(wallpaper.publication_geometry(), (3840, 2160))
        with patch.dict(os.environ, NIRI_SOCKET=''):
            self.assertEqual(wallpaper.publication_geometry(), (3840, 2160))

    def test_portrait_publication_keeps_only_needed_centre_pixels(self):
        self.selected = self.color
        self.outputs = {'portrait': {'current_mode': 0,
                                    'modes': [{'width': 3840, 'height': 2160}],
                                    'logical': {'transform': '90'}}}
        result = publisher.attempt(self.config, self.destination)
        self.assertEqual(result, {'state': 'published', 'images': 1})
        published = self.published_image()
        self.assertNotEqual(published, self.old_published)
        with Image.open(published) as image:
            self.assertEqual(image.size, (2160, 3840))
            self.assertLessEqual(image.width * image.height, wallpaper.MAX_PIXELS)
            self.assertEqual(image.getpixel((1080, 1920)), (112, 70, 120))
        # Scaling the entire strip to this height would exceed 48 million pixels.
        self.assertGreater(round(27004 * 3840 / 4320) * 3840, wallpaper.MAX_PIXELS)

    def test_watcher_republishes_unchanged_strip_after_output_change(self):
        self.selected = self.color
        self.config.write_text('[default]\npath = ' + json.dumps(str(self.color)) + '\n')
        unchanged = publisher.watch_identity(publisher.watch_paths(self.config))
        observed = []
        test = self

        class StopWatch(Exception):
            pass

        class Notifier:
            closed = False

            def update(self, candidates):
                test.assertIn(test.color, candidates)

            def changed(self, timeout):
                test.assertEqual(timeout, 30)
                test.assertEqual(publisher.watch_identity(publisher.watch_paths(test.config)), unchanged)
                with Image.open(test.published_image()) as image:
                    observed.append(image.size)
                if len(observed) == 1:
                    test.outputs = {'portrait': {'current_mode': 0,
                                                'modes': [{'width': 3840, 'height': 2160}],
                                                'logical': {'transform': '90'}}}
                    return False  # No file event: only the output geometry changed.
                raise StopWatch()

            def close(self):
                self.closed = True

        notifier = Notifier()
        with patch.object(publisher, 'Inotify', return_value=notifier), \
                patch.object(publisher, 'attempt', wraps=publisher.attempt) as publish:
            with self.assertRaises(StopWatch):
                publisher.watch(self.config, self.destination)
        self.assertEqual(publish.call_count, 2)
        self.assertEqual(observed, [(1920, 1080), (2160, 3840)])
        self.assertTrue(notifier.closed)

    def test_encoded_source_over_old_byte_limit(self):
        self.assertGreater(self.gray.stat().st_size, wallpaper.MAX_IMAGE)
        self.assertLessEqual(self.gray.stat().st_size, wallpaper.MAX_STRIP_BYTES)
        self.assert_new_picture(self.gray, (181, 181, 181))

    def assert_old_removed(self):
        self.assertFalse(self.published_config.exists())
        self.assertFalse(self.old_published.exists())
        self.assertEqual(list(self.destination.glob('wallpaper-*.image')), [])

    def test_source_over_new_bound_revokes_previous_publication(self):
        source = self.base / 'too-large.png'
        with source.open('wb') as stream:
            stream.truncate(wallpaper.MAX_STRIP_BYTES + 1)
        self.selected = source
        result = publisher.attempt(self.config, self.destination)
        self.assertEqual(result['state'], 'refused', result)
        self.assert_old_removed()

    def test_worker_timeout_revokes_previous_publication(self):
        self.selected = self.color
        with patch.object(wallpaper.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 6)):
            result = publisher.attempt(self.config, self.destination)
        self.assertEqual(result['state'], 'refused', result)
        self.assert_old_removed()

    def test_worker_refusal_revokes_previous_publication(self):
        self.selected = self.color
        with patch.object(wallpaper.subprocess, 'run', return_value=subprocess.CompletedProcess([], 2)):
            result = publisher.attempt(self.config, self.destination)
        self.assertEqual(result['state'], 'refused', result)
        self.assert_old_removed()

    def test_greeter_cannot_decode_oversized_account_sources(self):
        for source in (self.color, self.gray):
            with self.subTest(source=source.name):
                local = self.destination / source.name
                os.link(source, local)
                self.published_config.write_text('[default]\npath = ' + json.dumps(str(local)) + '\n')
                with patch.dict(os.environ, EMAKI_GREETER_WALLPAPER_ROOT=str(self.destination)), \
                        patch.object(wallpaper, 'reduced_strip') as reduce:
                    result = wallpaper.texture(1920, 1080, 1, 'fixture', 'sharp')
                self.assertNotEqual(result['state'], 'ready', result)
                reduce.assert_not_called()


if __name__ == '__main__':
    unittest.main()
