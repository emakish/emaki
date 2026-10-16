#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep first-party sources identifiable when copied outside the source archive."""
from pathlib import Path
import os
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SUFFIXES = {'.rs', '.py', '.sh', '.qml', '.js', '.c', '.cpp', '.h', '.hpp',
                   '.frag', '.vert', '.css'}
EXCEPTIONS = {
    # Unmodified archiso releng input, verified against RELENG-SHA256SUMS.
    'iso/profile/profiledef.sh',
    # Derived OpenStreetMap boundary database; its ODbL notice must remain intact.
    'installer/ui/assets/ZoneData.js',
}


def sources():
    for directory, subdirs, filenames in os.walk(ROOT):
        subdirs[:] = sorted(name for name in subdirs
                            if not name.startswith('.')
                            and name not in {'target', 'vendor', '__pycache__', 'node_modules'}
                            and not (Path(directory) / name).is_symlink())
        for name in sorted(filenames):
            path = Path(directory) / name
            if path.is_symlink() or path.relative_to(ROOT).as_posix() in EXCEPTIONS:
                continue
            with path.open('rb') as source:
                header = source.read(1024)
            if path.suffix in SOURCE_SUFFIXES or header.startswith(b'#!'):
                yield path, header


class LicenceHeaders(unittest.TestCase):
    def test_first_party_source_headers(self):
        checked = 0
        missing = []
        for path, header in sources():
            checked += 1
            if (b'Copyright (C) 2026 Artur Yakymenko' not in header
                    or b'SPDX-License-Identifier: GPL-3.0-or-later' not in header):
                missing.append(str(path.relative_to(ROOT)))
        self.assertGreater(checked, 100, 'source inventory is unexpectedly empty')
        self.assertEqual(missing, [], 'missing first-party headers:\n' + '\n'.join(missing))


if __name__ == '__main__':
    unittest.main()
