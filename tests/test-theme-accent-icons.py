#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Programs & Updates icon follows a non-default accent in the dock, the launcher and the
update reminder notice. The fixture installs the shipped desktop entry and the packaged icon in
the hicolor theme, so the shell resolves the icon by name as on an installed system."""
import importlib.util
from pathlib import Path
import sys
import tomllib

from PIL import Image

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('theme_pictures', ROOT / 'tests/theme-pictures.py')
PICTURES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PICTURES)
# Icon bounds in the 1536 x 960 fixture frame, two pixels around the drawn tile.
REGIONS = {
    'dock': (714, 898, 754, 936),
    'launcher': (354, 137, 388, 172),
    'reminder': (576, 61, 601, 86),
}
ACCENT = 'teal'


def paints(name):
    accent = tomllib.loads((ROOT / 'tokens.toml').read_text())['accents'][name]
    return [tuple(int(accent[role][i:i + 2], 16) for i in (0, 2, 4)) for role in ('base', 'strong', 'soft')]


def near(pixel, colours):
    return any(all(abs(a - b) <= 3 for a, b in zip(pixel, colour)) for colour in colours)


def main():
    PICTURES.WORK = ROOT / '.cache/theme-accent-icons/work'
    PICTURES.WORK.mkdir(parents=True, exist_ok=True)
    output = ROOT / '.cache/theme-accent-icons'
    staged = PICTURES.WORK / 'current'
    PICTURES.stage(ROOT, staged)
    orange, accent = paints('orange'), paints(ACCENT)
    for theme in ('light', 'dark'):
        for shot, region in REGIONS.items():
            target = output / f'{theme}-{ACCENT}-{shot}'
            PICTURES.capture(staged, target, shot, theme, ACCENT)
            icon = Image.open(target.with_suffix('.png')).convert('RGB').crop(region)
            pixels = list(icon.get_flattened_data())
            left = sum(near(pixel, orange) for pixel in pixels)
            drawn = sum(near(pixel, accent) for pixel in pixels)
            assert left == 0, f'{theme} {shot}: {left} pixels of the orange icon remain with the {ACCENT} accent'
            assert drawn >= 100, f'{theme} {shot}: only {drawn} pixels of the {ACCENT} icon'
            print(f'PASS {theme} {shot}: {drawn} {ACCENT} icon pixels, no orange icon pixels')
    installed = (PICTURES.WORK / 'data/applications/emaki-update-manager.desktop').read_text()
    assert installed == (ROOT / 'update-manager/emaki-update-manager.desktop').read_text()
    assert 'Icon=emaki-programs-updates\n' in installed


if __name__ == '__main__':
    main()
