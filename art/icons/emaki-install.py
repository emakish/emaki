#!/usr/bin/env python3
"""Pixel icon for the "Install Emaki" launcher entry (2026-10-03).

Each variant is a 16×16 drawing in the Emaki palette: tokens.toml colours plus the mark's
"Hot" colours (art/logo/mark.py, the lock/greeter/boot wordmark). 16 is the grid on purpose:
the shell rasterises launcher icons at 80 and 48 px and the dock at 88 px, so 16 cells land on
whole device pixels at 48, 80 and 128 (×3, ×5, ×8). Every horizontal run of one colour becomes
one whole-pixel <rect>; shape-rendering="crispEdges" keeps the edges hard at any size.

  python3 art/icons/emaki-install.py            writes installer/ui/emaki-install.svg (DEFAULT)
  python3 art/icons/emaki-install.py --check    fails if that file is stale
  python3 art/icons/emaki-install.py --variant drive --output FILE    writes another variant
"""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / 'installer/ui/emaki-install.svg'
SIZE = 16
HOT = ['#b01e78', '#ec2a55', '#ff6a2a', '#ffb62e']   # art/logo/mark.py, deep → light
PALETTE = {
    'k': '#14100d',   # tokens color.background
    'r': '#2a211a',   # tokens color.surface_high
    'g': '#a39483',   # tokens color.text_muted
    'c': '#f2e6d8',   # tokens color.text
    **{str(i): colour for i, colour in enumerate(HOT)},
}

VARIANTS = {
    # The slanted E of the wordmark (its 12-px cut: 2-px bars with 45° ends, a 16° stem that
    # steps one pixel every three to four rows) on a dark rounded tile, Hot from the bottom left
    # to the top right as in mark-large.svg; a cream arrow sits in the E's lower right notch.
    'mark': [
        '..kkkkkkkkkkkk..',
        '.kkkkkkkkkkkkkk.',
        'kkkkk2233333kkkk',
        'kkkk2222233kkkkk',
        'kkkkk22kkkkkkkkk',
        'kkkkk22kkkkkkkkk',
        'kkkk22kkkkkkkkkk',
        'kkkk1222222kkkkk',
        'kkk1111222kccckk',
        'kkkk11kkkkkccckk',
        'kkk11kkkkkccccck',
        'kkk11kkkkkkccckk',
        'kk0011111kkkckkk',
        'k0000011kkkkkkkk',
        '.kkkkkkkkkkkkkk.',
        '..kkkkkkkkkkkk..',
    ],
    # A Hot arrow going down into a disk drive: the generic install picture, in Emaki colours.
    'drive': [
        '......3333......',
        '......3333......',
        '......3333......',
        '......2222......',
        '......2222......',
        '....22222222....',
        '.....111111.....',
        '......0000......',
        '.......00.......',
        '................',
        '.gggggggggggggg.',
        'gkkkkkkkkkkkkkkg',
        'gkrrrrrrrkkk3kkg',
        'gkkkkkkkkkkkkkkg',
        'gkkkkkkkkkkkkkkg',
        '.gggggggggggggg.',
    ],
}
DEFAULT = 'mark'


def svg(rows):
    assert len(rows) == SIZE and all(len(row) == SIZE for row in rows), 'a drawing is 16×16 cells'
    rects = []
    for y, row in enumerate(rows):
        x = 0
        while x < SIZE:
            cell = row[x]
            end = x
            while end < SIZE and row[end] == cell:
                end += 1
            if cell != '.':
                rects.append(f'<rect x="{x}" y="{y}" width="{end - x}" height="1" fill="{PALETTE[cell]}"/>')
            x = end
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{SIZE}" height="{SIZE}" '
            f'viewBox="0 0 {SIZE} {SIZE}" shape-rendering="crispEdges">\n'
            + ''.join(f'  {rect}\n' for rect in rects) + '</svg>\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--variant', choices=sorted(VARIANTS), default=DEFAULT)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check', action='store_true', help='fail if the shipped icon is stale')
    args = parser.parse_args()
    if args.check:
        if not TARGET.is_file() or TARGET.read_text() != svg(VARIANTS[DEFAULT]):
            parser.exit(1, 'installer/ui/emaki-install.svg is stale; run python3 art/icons/emaki-install.py\n')
        print(f'PASS: installer/ui/emaki-install.svg matches the "{DEFAULT}" drawing')
        return
    output = args.output or (TARGET if args.variant == DEFAULT else None)
    if output is None:
        parser.error('--output is required for a variant that is not the default')
    output.write_text(svg(VARIANTS[args.variant]))
    print(f'Wrote {output} ({args.variant})')


if __name__ == '__main__':
    main()
