#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Play a short clock chime through the session's existing audio provider."""

import math
import struct
import subprocess


def samples():
    """Three soft, bounded notes with short fades to avoid clicks."""
    rate = 24000
    output = bytearray()
    for frequency in (660, 880, 660):
        for index in range(rate // 3):
            seconds = index / rate
            fade = min(1, seconds / .015, (1 / 3 - seconds) / .04)
            value = int(7000 * max(0, fade) * math.sin(2 * math.pi * frequency * seconds))
            output.extend(struct.pack('<h', value))
        output.extend(bytes(rate // 12 * 2))
    return bytes(output)


def main():
    try:
        return subprocess.run(
            ['pw-play', '--raw', '--format=s16', '--rate=24000', '--channels=1',
             '--media-role=Notification', '-'], input=samples(), timeout=8,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
