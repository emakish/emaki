# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Load scalable unlock artwork and build the invisible terminal font."""

import struct
from pathlib import Path

VARIANT = 'D'  # The chosen look; A, B and C stay available as one-line alternatives.
ARTWORK = Path(__file__).with_name('grub_artwork')
# The boot menu theme (grub/theme/generate-theme.py) and variant D of the disk-password
# pictures share one layout, so the two screens read as one: the same picture, a calm
# panel at the same place, the same face. Percentages of the screen.
PANEL = (22, 28, 56, 46)  # left, top, width, height
PANEL_ALPHA = 0.82
HINT_TOP = 78
TIMEOUT_TOP = 83
FONT_FAMILY = 'Noto Sans Regular'  # tokens.toml [font] ui
# Approved 0.7.0 texts (2026-10-12).
HINT = '↑ ↓ choose · Enter to start'
TIMEOUT = 'Starting in %d s'
# Pixel sizes per menu scale (percent); the generator picks by GRUB's video mode height.
SCALES = {
    100: {'item': 22, 'hint': 16, 'row': 40, 'inset': 14, 'gap': 4, 'hint_row': 24,
          'panel_radius': 16, 'select_radius': 6},
    150: {'item': 33, 'hint': 24, 'row': 60, 'inset': 21, 'gap': 6, 'hint_row': 36,
          'panel_radius': 24, 'select_radius': 9},
    200: {'item': 44, 'hint': 32, 'row': 80, 'inset': 28, 'gap': 8, 'hint_row': 48,
          'panel_radius': 32, 'select_radius': 12},
    300: {'item': 66, 'hint': 48, 'row': 120, 'inset': 42, 'gap': 12, 'hint_row': 72,
          'panel_radius': 48, 'select_radius': 18},
}
# The largest mode height each scale is drawn for; taller modes take the next scale.
SCALE_HEIGHTS = ((1000, 100), (1400, 150), (1900, 200))
ARTWORK_SIZE = (2560, 1600)
SIZES = (ARTWORK_SIZE,)
TEXT = (
    "This computer's disk is encrypted.",
    "Type your disk password and press Enter.",
    "The letters do not appear while you type.",
    "After Enter, wait a few seconds.",
)
WRONG = "Wrong password. Try again."
CHECKING = "Checking the password…"
_ENTRY = struct.Struct('>IBI')
_GLYPH = struct.Struct('>HHhhh')


def _section(name, body):
    return name + struct.pack('>I', len(body)) + body


def _read_font(data):
    """Read uncompressed PF2 records and reject truncated or inconsistent inputs."""
    fields = {}
    pos = 0
    while pos + 8 <= len(data):
        name, length = data[pos:pos + 4], struct.unpack_from('>I', data, pos + 4)[0]
        pos += 8
        if name == b'DATA':
            break
        if name in fields or length > len(data) - pos:
            raise ValueError('invalid PF2 section')
        fields[name] = data[pos:pos + length]
        pos += length
    else:
        raise ValueError('missing PF2 data')
    if fields.get(b'FILE') != b'PFF2' or len(fields.get(b'PTSZ', b'')) != 2:
        raise ValueError('invalid PF2 font')
    index = fields.get(b'CHIX', b'')
    if not index or len(index) % _ENTRY.size:
        raise ValueError('invalid PF2 character index')
    records = {}
    for i in range(0, len(index), _ENTRY.size):
        code, flags, offset = _ENTRY.unpack_from(index, i)
        if flags or code in records or offset < pos or offset + _GLYPH.size > len(data):
            raise ValueError('invalid PF2 glyph offset or storage')
        width, height, x, y, advance = _GLYPH.unpack_from(data, offset)
        length = (width * height + 7) // 8
        if offset + _GLYPH.size + length > len(data) or advance <= 0:
            raise ValueError('invalid PF2 glyph metrics or bitmap')
        records[code] = (width, height, x, y, advance,
                         data[offset + _GLYPH.size:offset + _GLYPH.size + length])
    return fields, records


def _hidden_font():
    """All printable ASCII is blank; small cells keep hidden output in the top band."""
    header = b''.join(_section(name, value) for name, value in (
        (b'FILE', b'PFF2'), (b'NAME', b'Emaki Hidden Regular 4\0'),
        (b'FAMI', b'Emaki Hidden\0'), (b'WEIG', b'normal\0'), (b'SLAN', b'normal\0'),
        (b'PTSZ', b'\0\4'), (b'MAXW', b'\0\4'), (b'MAXH', b'\0\4'),
        (b'ASCE', b'\0\2'), (b'DESC', b'\0\2'),
    ))
    record = _GLYPH.pack(4, 4, 0, 0, 4) + b'\0\0'
    start = len(header) + 8 + 95 * _ENTRY.size + 8
    index = b''.join(_ENTRY.pack(code, 0, start + (code - 32) * len(record))
                     for code in range(32, 127))
    return header + _section(b'CHIX', index) + b'DATA\xff\xff\xff\xff' + record * 95



def assets(variant=None) -> dict[str, bytes]:
    """Return the hidden terminal font and three pre-rendered RGB master pictures."""
    variant = VARIANT if variant is None else variant
    if variant not in ('A', 'B', 'C', 'D'):
        raise ValueError('unknown unlock artwork variant')
    result = {'hidden.pf2': _hidden_font()}
    for width, height in SIZES:
        for prefix in ('unlock', 'wrong', 'checking'):
            name = f'{prefix}-{width}x{height}.png'
            try:
                data = (ARTWORK / variant / name).read_bytes()
            except OSError as error:
                raise ValueError(f'unlock artwork is unavailable: {variant}/{name}') from error
            header = (b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR'
                      + struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            if not data.startswith(header):
                raise ValueError(f'unlock artwork has the wrong format: {variant}/{name}')
            result[name] = data
    return result
