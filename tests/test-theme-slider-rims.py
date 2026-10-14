#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check the rendered sound slider for chromatic fringes over dark glass."""
import importlib.util
from pathlib import Path
import sys

from PIL import Image

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('theme_pictures', ROOT / 'tests/theme-pictures.py')
PICTURES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PICTURES)


def main():
    PICTURES.WORK = ROOT / '.cache/theme-slider-rims/work'
    PICTURES.WORK.mkdir(parents=True, exist_ok=True)
    output = ROOT / '.cache/theme-slider-rims'
    staged = PICTURES.WORK / 'current'
    PICTURES.stage(ROOT, staged)
    for theme in ('light', 'dark'):
        target = output / f'{theme}-system-sound'
        PICTURES.capture(staged, target, 'system-sound', theme)
        # The real fixture's 64% output-volume knob, clear of the green input
        # level meter. Neutral highlights must not turn into green/cyan ink.
        knob = Image.open(target.with_suffix('.png')).convert('RGB').crop((1310, 105, 1345, 140))
        green = sum(g > r + 20 and g > b + 10 and g > 180 for r, g, b in knob.get_flattened_data())
        assert green == 0, f'{theme} slider has {green} green/cyan fringe pixels'
        print(f'PASS {theme} sound knob: {green} green/cyan fringe pixels')


if __name__ == '__main__':
    main()
