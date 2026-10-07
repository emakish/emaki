# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate recorded menu modes against retained bootloader configuration."""
import hashlib
from pathlib import Path
import re


class MissingMode(ValueError):
    pass


def configured_size(base, item, advertised):
    mode = item.get('menu_mode')
    if not mode:
        raise MissingMode('no recorded menu mode and bootloader configuration')
    name = mode.get('source', '')
    source = (base / name).resolve()
    if not name or Path(name).is_absolute() or not source.is_relative_to(base.resolve()):
        raise ValueError('menu configuration must be inside the evidence directory')
    if not source.is_file():
        raise MissingMode('recorded menu configuration is missing')
    content = source.read_bytes()
    if hashlib.sha256(content).hexdigest() != mode.get('sha256'):
        raise ValueError('menu configuration checksum mismatch')
    size = mode.get('size', '')
    if not isinstance(size, str) or not re.fullmatch(r'[1-9][0-9]*x[1-9][0-9]*', size):
        raise ValueError('invalid configured menu size')
    # Read retained configuration, never infer the desired mode from screenshot pixels.
    values = re.findall(r'^\s*(?:set\s+gfxmode\s*=|GRUB_GFXMODE\s*=)\s*[\"\']?([^\s\"\'#]+)',
                        content.decode('utf-8'), re.MULTILINE)
    allowed = {token for value in values for token in value.split(',')}
    native = f'{advertised[0]}x{advertised[1]}'
    if size not in allowed and not (size == native and 'auto' in allowed):
        raise ValueError('recorded menu size is not allowed by retained configuration')
    return tuple(map(int, size.split('x')))
