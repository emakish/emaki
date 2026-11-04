#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reduce a PNG from an inherited descriptor without allocating its full raster.

Run only as an isolated worker: libpng errors terminate this process, and the caller
owns the deadline and bounded temporary output. No source path is opened here.
"""
import ctypes
import ctypes.util
import math
import os
import resource
import signal
import stat
import struct
import sys

MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_SOURCE_PIXELS = 256_000_000
MAX_OUTPUT_PIXELS = 48_000_000


def source_geometry(fd):
    """Bound the PNG container before loading its metadata into the decoder."""
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or not 33 <= info.st_size <= MAX_SOURCE_BYTES:
        raise ValueError('source_limit')
    header = os.pread(fd, 33, 0)
    if header[:16] != b'\x89PNG\r\n\x1a\n\0\0\0\rIHDR':
        raise ValueError('png_header')
    width, height, _, _, _, _, interlace = struct.unpack('!IIBBBBB', header[16:29])
    if not (0 < width <= 65536 and 0 < height <= 16384
            and width * height <= MAX_SOURCE_PIXELS) or interlace:
        raise ValueError('source_geometry')
    offset = 8
    for _ in range(65536):
        chunk = os.pread(fd, 8, offset)
        if len(chunk) != 8:
            raise ValueError('png_truncated')
        length, kind = struct.unpack('!I4s', chunk)
        offset += length + 12
        if offset > info.st_size or kind in (b'acTL', b'fcTL', b'fdAT'):
            raise ValueError('png_container')
        if kind == b'IEND':
            if length or offset != info.st_size:
                raise ValueError('png_end')
            return width, height
    raise ValueError('png_chunks')


def reduce_png(fd, width, height, crop=None):
    from PIL import Image

    source_width, source_height = source_geometry(fd)
    if not (0 < width <= 65536 and 0 < height <= 8192
            and width * height <= MAX_OUTPUT_PIXELS):
        raise ValueError('output_limit')
    left, top, right, bottom = crop or (0, 0, source_width, source_height)
    if not (all(math.isfinite(x) for x in (left, top, right, bottom))
            and 0 <= left < right <= source_width and 0 <= top < bottom <= source_height
            and width <= right - left and height <= bottom - top):
        raise ValueError('crop_limit')

    library = ctypes.util.find_library('png16') or ctypes.util.find_library('png')
    if not library:
        raise ValueError('png_decoder_missing')
    pnglib = ctypes.CDLL(library)
    libc = ctypes.CDLL(None)
    pointer = ctypes.c_void_p
    uint = ctypes.c_uint

    def function(name, result, arguments):
        call = getattr(pnglib, name)
        call.restype = result
        call.argtypes = arguments
        return call

    error_type = ctypes.CFUNCTYPE(None, pointer, ctypes.c_char_p)

    @error_type
    def fail(_png, _message):
        # A Python exception cannot unwind libpng's C stack. Exit this isolated
        # worker instead of invoking libpng's default abort without a C setjmp.
        os._exit(2)

    png = function('png_create_read_struct', pointer,
                   [ctypes.c_char_p, pointer, error_type, error_type])(
                       b'1.6.0', None, fail, fail)
    if not png:
        raise ValueError('png_allocation')
    info = function('png_create_info_struct', pointer, [pointer])(png)
    if not info:
        raise ValueError('png_allocation')
    libc.fdopen.restype = pointer
    libc.fdopen.argtypes = [ctypes.c_int, ctypes.c_char_p]
    os.lseek(fd, 0, os.SEEK_SET)
    stream = libc.fdopen(fd, b'rb')
    if not stream:
        raise ValueError('png_descriptor')
    function('png_set_user_limits', None, [pointer, uint, uint])(png, 65536, 16384)
    function('png_set_chunk_malloc_max', None, [pointer, ctypes.c_size_t])(png, 1024 * 1024)
    function('png_set_chunk_cache_max', None, [pointer, uint])(png, 16)
    function('png_init_io', None, [pointer, pointer])(png, stream)
    function('png_read_info', None, [pointer, pointer])(png, info)
    actual_width = function('png_get_image_width', uint, [pointer, pointer])(png, info)
    actual_height = function('png_get_image_height', uint, [pointer, pointer])(png, info)
    interlace = function('png_get_interlace_type', ctypes.c_ubyte,
                         [pointer, pointer])(png, info)
    if (actual_width, actual_height) != (source_width, source_height) or interlace:
        raise ValueError('png_changed')
    for name in ('png_set_strip_16', 'png_set_palette_to_rgb',
                 'png_set_expand_gray_1_2_4_to_8', 'png_set_tRNS_to_alpha',
                 'png_set_gray_to_rgb', 'png_set_strip_alpha'):
        function(name, None, [pointer])(png)
    function('png_read_update_info', None, [pointer, pointer])(png, info)
    row_bytes = function('png_get_rowbytes', ctypes.c_size_t, [pointer, pointer])(png, info)
    if row_bytes != source_width * 3:
        raise ValueError('png_channels')
    buffer = ctypes.create_string_buffer(row_bytes)
    read_row = function('png_read_row', None, [pointer, pointer, pointer])
    output = Image.new('RGB', (width, height))
    # EXTENT samples pixel centres. Keep at most the two rows surrounding the
    # next output centre; every other source row is discarded after decoding.
    targets = []
    for y in range(height):
        position = max(0, min(source_height - 1, top + (y + .5) * (bottom - top) / height - .5))
        lower = math.floor(position)
        targets.append((lower, min(lower + 1, source_height - 1), position - lower))
    needed = {row for lower, upper, _ in targets for row in (lower, upper)}
    rows = {}
    destination = 0
    for y in range(source_height):
        read_row(png, buffer, None)
        if y in needed:
            rows[y] = Image.frombytes('RGB', (source_width, 1), buffer.raw).transform(
                (width, 1), Image.Transform.EXTENT, (left, 0, right, 1),
                Image.Resampling.BILINEAR)
        while destination < height and targets[destination][1] <= y:
            lower, upper, weight = targets[destination]
            row = rows[lower] if lower == upper else Image.blend(rows[lower], rows[upper], weight)
            output.paste(row, (0, destination))
            destination += 1
            keep_from = targets[destination][0] if destination < height else source_height
            rows = {index: value for index, value in rows.items() if index >= keep_from}
    function('png_read_end', None, [pointer, pointer])(png, info)
    # Nothing is emitted until the entire source, including its final CRC, has
    # been checked. The parent atomically accepts only a successful worker.
    output.save(sys.stdout.buffer, format='PNG')


def main():
    parent = os.getppid()
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                           ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    if parent <= 1 or libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
        return 2
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (6, 6))
    if len(sys.argv) not in (4, 8):
        return 2
    try:
        reduce_png(*(int(value) for value in sys.argv[1:4]),
                   crop=tuple(float(value) for value in sys.argv[4:]) or None)
    except (OSError, ValueError, OverflowError, AttributeError):
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
