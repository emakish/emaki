#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pixel icon for the "Report a problem" launcher entry (2026-10-15).

Emaki's own picture in the family of the installer's icon (art/icons/emaki-install.py): a 16×16
drawing on the same dark rounded tile, here a speech bubble in the mark's "Hot" colours with a
cream "!" — not the logo itself, which stays the brand's (Welcome and the bar use it).
The bubble's cells take the Hot colour of their place along the mark's slant, deep at the bottom
left and light at the top right (mark-large.svg's direction).

  python3 art/icons/emaki-report.py            writes art/icons/emaki-report.svg
  python3 art/icons/emaki-report.py --check    fails if that file is stale
"""
import argparse
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / 'art/icons/emaki-report.svg'
_spec = importlib.util.spec_from_file_location('emaki_install_icon', Path(__file__).with_name('emaki-install.py'))
INSTALL = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(INSTALL)
SIZE = INSTALL.SIZE

# 'h': the bubble (Hot by position), 'c': cream, 'k': the tile, '.': transparent.
DRAWING = [
    '..kkkkkkkkkkkk..',
    '.kkkkkkkkkkkkkk.',
    'kkkhhhhhhhhhhkkk',
    'kkhhhhhcchhhhhkk',
    'kkhhhhhcchhhhhkk',
    'kkhhhhhcchhhhhkk',
    'kkhhhhhcchhhhhkk',
    'kkhhhhhcchhhhhkk',
    'kkhhhhhhhhhhhhkk',
    'kkhhhhhcchhhhhkk',
    'kkkhhhhhhhhhhkkk',
    'kkkhhhkkkkkkkkkk',
    'kkkhhkkkkkkkkkkk',
    'kkkhkkkkkkkkkkkk',
    '.kkkkkkkkkkkkkk.',
    '..kkkkkkkkkkkk..',
]


SLANT = .4   # mostly bottom to top, leaning right like the installer's E


def rows():
    """The drawing in the installer icon's palette: the bubble's cells in four equal Hot bands
    across the bubble's own extent, deep at the bottom left, light at the top right."""
    cells_h = [(x, y) for y, row in enumerate(DRAWING) for x, cell in enumerate(row) if cell == 'h']
    left, top = min(x for x, _ in cells_h), min(y for _, y in cells_h)
    right, bottom = max(x for x, _ in cells_h), max(y for _, y in cells_h)
    span = (bottom - top) + SLANT * (right - left)
    out = []
    for y, row in enumerate(DRAWING):
        cells = []
        for x, cell in enumerate(row):
            if cell == 'h':
                along = ((bottom - y) + SLANT * (x - left)) / span   # 0 … 1
                cell = str(min(len(INSTALL.HOT) - 1, int(along * len(INSTALL.HOT))))
            cells.append(cell)
        out.append(''.join(cells))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--check', action='store_true', help='fail if the shipped icon is stale')
    args = parser.parse_args()
    rendered = ("<!-- Copyright (C) 2026 Artur Yakymenko\n"
                "     SPDX-License-Identifier: GPL-3.0-or-later -->\n" + INSTALL.svg(rows()))
    if args.check:
        if not TARGET.is_file() or TARGET.read_text() != rendered:
            parser.exit(1, 'art/icons/emaki-report.svg is stale; run python3 art/icons/emaki-report.py\n')
        print('PASS: art/icons/emaki-report.svg matches its drawing')
        return
    TARGET.write_text(rendered)
    print(f'Wrote {TARGET.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
