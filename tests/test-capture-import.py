#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify implicit DMA-BUF import attributes and bounded invalid-input failures."""
import ctypes
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('capture_dmabuf', ROOT / 'capture/dmabuf.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
XR24 = int.from_bytes(b'XR24', 'little')


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.file = tempfile.TemporaryFile()
        self.addCleanup(self.file.close)
        self.plane = [(self.file.fileno(), 128, 256)]

    def attributes(self, modifier):
        return MODULE.image_attributes(64, 48, XR24, modifier, self.plane)

    def test_implicit_layout_omits_both_modifier_attributes(self):
        attributes = self.attributes(MODULE.DRM_FORMAT_MOD_INVALID)
        self.assertEqual(attributes[-1], MODULE.EGL_NONE)
        fields = dict(zip(attributes[:-1:2], attributes[1:-1:2]))
        self.assertNotIn(MODULE.EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT, fields)
        self.assertNotIn(MODULE.EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT, fields)
        self.assertEqual(fields[MODULE.EGL_DMA_BUF_PLANE0_FD_EXT], self.file.fileno())
        self.assertEqual(fields[MODULE.EGL_DMA_BUF_PLANE0_OFFSET_EXT], 128)
        self.assertEqual(fields[MODULE.EGL_DMA_BUF_PLANE0_PITCH_EXT], 256)

    def test_explicit_modifier_is_preserved_including_high_bits(self):
        modifier = 0xFEDCBA9876543210
        attributes = self.attributes(modifier)
        fields = dict(zip(attributes[:-1:2], attributes[1:-1:2]))
        self.assertEqual(fields[MODULE.EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT], 0x76543210)
        self.assertEqual(fields[MODULE.EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT], 0xFEDCBA98)
        signed = MODULE._ints(attributes)
        self.assertEqual(ctypes.c_uint(signed[-2]).value, 0xFEDCBA98)

    def test_explicit_linear_is_not_confused_with_implicit(self):
        attributes = self.attributes(0)
        fields = dict(zip(attributes[:-1:2], attributes[1:-1:2]))
        self.assertEqual(fields[MODULE.EGL_DMA_BUF_PLANE0_MODIFIER_LO_EXT], 0)
        self.assertEqual(fields[MODULE.EGL_DMA_BUF_PLANE0_MODIFIER_HI_EXT], 0)

    def test_invalid_metadata_fails_before_loading_graphics(self):
        invalid = [(0, 48, XR24, 0, self.plane), (64, 0, XR24, 0, self.plane),
                   (16385, 48, XR24, 0, self.plane), (64, 48, 0, 0, self.plane),
                   (64, 48, XR24, -1, self.plane), (64, 48, XR24, 1 << 64, self.plane),
                   (64, 48, XR24, 0, []), (64, 48, XR24, 0, self.plane * 5),
                   (64, 48, XR24, 0, [(self.file.fileno(), 0, 255)]),
                   (64, 48, XR24, 0, [(-1, 0, 256)]),
                   (64, 48, XR24, 0, [(self.file.fileno(), -1, 256)])]
        with patch.object(MODULE, '_Api') as api:
            for args in invalid:
                with self.subTest(args=args), self.assertRaises(MODULE.CaptureImportError):
                    MODULE.Importer().read_frame(*args)
            api.assert_not_called()

    def test_auxiliary_planes_keep_offsets_and_modifiers(self):
        planes = [(self.file.fileno(), 128, 256), (self.file.fileno(), 8192, 64),
                  (self.file.fileno(), 16384, 64), (self.file.fileno(), 24576, 64)]
        attributes = MODULE.image_attributes(64, 48, XR24, 0x100000001, planes)
        fields = dict(zip(attributes[:-1:2], attributes[1:-1:2]))
        self.assertEqual(fields[0x3440], self.file.fileno())
        self.assertEqual(fields[0x3441], 24576)
        self.assertEqual(fields[0x3442], 64)
        self.assertEqual(fields[0x3449], 1)
        self.assertEqual(fields[0x344A], 1)
        implicit = MODULE.image_attributes(64, 48, XR24, MODULE.DRM_FORMAT_MOD_INVALID, planes)
        implicit_fields = dict(zip(implicit[:-1:2], implicit[1:-1:2]))
        self.assertTrue(all(key not in implicit_fields for key in range(0x3443, 0x344B)))

    def test_closed_descriptor_rejected(self):
        fd = os.dup(self.file.fileno())
        os.close(fd)
        with self.assertRaisesRegex(MODULE.CaptureImportError, 'Closed'):
            MODULE.image_attributes(64, 48, XR24, 0, [(fd, 0, 256)])

    def test_close_is_idempotent_and_prevents_initialization(self):
        importer = MODULE.Importer()
        importer.close()
        importer.close()
        with patch.object(MODULE, '_Api') as api:
            with self.assertRaisesRegex(MODULE.CaptureImportError, 'closed'):
                importer.read_frame(64, 48, XR24, 0, self.plane)
            api.assert_not_called()

    def test_offscreen_readback_preserves_rgba_and_row_order(self):
        result = subprocess.run([sys.executable, __file__, '--offscreen'],
                                capture_output=True, text=True, timeout=15)
        if result.returncode == 77:
            self.skipTest(result.stdout.strip())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'RGBA rows preserved')


def offscreen_probe():
    """Exercise actual EGL/GLES readback with a synthetic texture, not a cast."""
    importer = MODULE.Importer()
    texture = ctypes.c_uint()
    image = None
    try:
        try:
            importer._initialize()
        except MODULE.CaptureImportError as error:
            print('Offscreen EGL unavailable: ' + str(error))
            return 77
        api = importer._api
        if not api.eglMakeCurrent(importer._display, importer._surface,
                                  importer._surface, importer._context):
            raise RuntimeError('Cannot activate synthetic context')
        api.bind('glTexImage2D', None, [ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                 ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint,
                 ctypes.c_uint, ctypes.c_void_p], gl=True)
        expected = bytes([255, 0, 0, 255, 0, 255, 0, 255,
                          0, 0, 255, 255, 255, 255, 255, 255])
        pixels = (ctypes.c_ubyte * len(expected)).from_buffer_copy(expected)
        api.glGenTextures(1, ctypes.byref(texture))
        api.glBindTexture(MODULE.GL_TEXTURE_2D, texture)
        api.glTexImage2D(MODULE.GL_TEXTURE_2D, 0, MODULE.GL_RGBA, 2, 2, 0,
                         MODULE.GL_RGBA, MODULE.GL_UNSIGNED_BYTE, pixels)
        image = importer._create_image(importer._display, importer._context, 0x30B1,
                                        ctypes.c_void_p(texture.value),
                                        MODULE._ints([0x30D2, 1, MODULE.EGL_NONE]))
        if not image:
            raise RuntimeError('Cannot create synthetic EGL image')
        api.eglMakeCurrent(importer._display, None, None, None)
        # The production readback and cleanup receive a real EGLImage. Replacing
        # only its creation isolates DMA-BUF negotiation from RGBA row handling.
        with tempfile.TemporaryFile() as storage, patch.object(importer, '_create_image', return_value=image):
            actual = importer.read_frame(2, 2, XR24, MODULE.DRM_FORMAT_MOD_INVALID,
                                         [(storage.fileno(), 0, 8)])
            image = None  # read_frame destroyed the transferred image.
        if actual != expected:
            raise AssertionError(f'Unexpected RGBA row order: {actual!r}')
        print('RGBA rows preserved')
        return 0
    finally:
        if importer._context:
            api = importer._api
            api.eglMakeCurrent(importer._display, importer._surface, importer._surface, importer._context)
            if image:
                importer._destroy_image(importer._display, image)
            if texture.value:
                api.glDeleteTextures(1, ctypes.byref(texture))
            api.eglMakeCurrent(importer._display, None, None, None)
        importer.close()


if __name__ == '__main__':
    if sys.argv[1:] == ['--offscreen']:
        raise SystemExit(offscreen_probe())
    unittest.main()
