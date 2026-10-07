#!/usr/bin/env python3
"""Check the unused BMP artifact; GRUB does not display or install this file."""
import json
from pathlib import Path
import re
import struct
import subprocess
import sys

from PIL import Image
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
for name in ('build-lock-wordmark', 'build-boot-splash'):
    subprocess.run([sys.executable, str(ROOT / 'scripts' / name), '--check'], check=True)
target = ROOT / 'boot/emaki-splash.bmp'
data = target.read_bytes()
assert struct.unpack_from('<2sIHHI', data) == (b'BM', len(data), 0, 0, 54)
assert struct.unpack_from('<IiiHHIIiiII', data, 14) == (40, 600, 168, 1, 24, 0, 302400, 0, 0, 0, 0)
source = (ROOT / 'shell/LockWordmarkData.js').read_text()
pixels = json.loads(re.search(r'var PIXELS = (\[.*\]);', source, re.S).group(1))
colors = [bytes.fromhex(c[1:]) for c in json.loads(re.search(r'var HOT = (.*);', source).group(1))]
expected = bytearray(600 * 168 * 3)
for x, y, _letter, _neighbours, color in pixels:
    for dy in range(3):
        offset = ((12 + y * 3 + dy) * 600 + x * 3) * 3
        expected[offset:offset + 9] = colors[color] * 3
with Image.open(target) as image:
    assert image.format == 'BMP' and image.mode == 'RGB' and image.size == (600, 168)
    assert image.tobytes() == expected, 'BMP differs from the lock pixels, colours or black background'
print('PASS: unused BMP artifact only; GRUB appearance is NOT TESTED; uncompressed 24-bit BMP, 600×168, exact 3× lock wordmark on black')
