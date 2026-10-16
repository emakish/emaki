#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pictures of "Welcome to Emaki" for review: every page in light and dark, and one page in
every accent, on the real wallpaper glass (production QML, llvmpipe, the shipped shaders).

  python3 tests/welcome-pictures.py [--output DIR]     default .cache/welcome-pictures

Not part of `make check`: these are pictures for a person to look at. tests/test-welcome.py
checks the same pages.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location('theme_pictures', ROOT / 'tests/theme-pictures.py')
PICTURES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PICTURES)


def pages():
    script = (ROOT / 'shell/WelcomeContent.js').read_text().replace('.pragma library', '')
    return json.loads(subprocess.check_output(
        ['node', '-e', script + '\nconsole.log(JSON.stringify(pages.map(page => page.id)));'], text=True))


def accents():
    text = (ROOT / 'shell/ShellPalette.qml').read_text()
    start = text.index('readonly property var accents: ')
    listing = text[start + len('readonly property var accents: '):text.index('readonly property var chosenAccent')]
    return [row['id'] for row in json.loads(listing)]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, default=ROOT / '.cache/welcome-pictures')
    parser.add_argument('--accent-page', type=int, default=3, help='the page shown in every accent (0-based)')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    PICTURES.WORK = ROOT / '.cache/welcome-render'
    PICTURES.CACHE = PICTURES.WORK / 'frames'
    PICTURES.CACHE.mkdir(parents=True, exist_ok=True)
    staged = PICTURES.WORK / 'shell'
    PICTURES.stage(ROOT, staged)
    shots = []

    def capture(name, page, theme, accent):
        os.environ['EMAKI_PICTURE_DATA'] = json.dumps({'welcomePage': page})
        target = PICTURES.CACHE / name
        PICTURES.capture(staged, target, 'welcome', theme, accent)
        frame = Image.open(target.with_suffix('.png')).convert('RGB')
        # The window and a margin of the desktop around it (the fixture centres it at 800 x 660).
        left, top = (frame.width - 800) // 2 - 60, (frame.height - 660) // 2 - 50
        picture = frame.crop((left, top, left + 920, top + 760))
        picture.save(output / (name + '.png'))
        shots.append(output / (name + '.png'))
        return picture

    ids = pages()
    rose = 'rose'
    for index, page in enumerate(ids):
        for theme in ('light', 'dark'):
            capture(f'{index + 1}-{page}-{theme}', index, theme, rose)
    for theme in ('dark', 'light'):
        frames = [(accent, capture(f'accent-{theme}-{accent}', args.accent_page, theme, accent)) for accent in accents()]
        columns = 4
        sheet = Image.new('RGB', (columns * 460, ((len(frames) + columns - 1) // columns) * 410), 'white')
        draw = ImageDraw.Draw(sheet)
        for position, (accent, frame) in enumerate(frames):
            x, y = (position % columns) * 460, (position // columns) * 410
            sheet.paste(frame.resize((460, 380)), (x, y + 30))
            draw.text((x + 10, y + 8), f'{theme.title()} / {accent.title()}', fill='black')
        sheet.save(output / f'accents-{theme}.png')
    print('Welcome pictures:', output)
    for path in shots:
        print(' ', path.name)


if __name__ == '__main__':
    main()
