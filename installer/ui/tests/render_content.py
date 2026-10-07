# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Content and geometry assertions for installer frames, independent of byte size."""
import json
import re
from PIL import Image

HEADINGS = {
    'welcome': 'Welcome to Emaki', 'keyboard': 'Make yourself at home',
    'network': 'Get connected', 'timezone': 'Time zone',
    'disk': 'Where should Emaki go?', 'alongside': 'Where should Emaki go?',
    'manual': 'Assign your partitions', 'filesystem': 'A home for your files',
    'encryption': 'Protect your files', 'you': 'About you',
    'software': 'Make it your desktop', 'review': 'One last look',
    'install': 'Making room for you', 'done': 'Welcome home.',
    'error': 'Installation stopped',
}


def heading_for(screen):
    if screen == 'error-details':
        return 'Refresh removable media'
    if screen.endswith(('-caps', '-numlock')):
        return 'Caps Lock is on'
    if screen == 'manual-hibernation':
        return HEADINGS['disk']
    if screen in ('alongside-review', 'plan-errors'):
        return HEADINGS['review']
    if screen == 'live-keyboard-encryption':
        return 'A separate disk password'
    if screen.startswith('live-keyboard'):
        return HEADINGS['encryption' if screen == 'live-keyboard-encryption' else 'you']
    return HEADINGS[screen.split('-')[0]]


def validate_frame(path, log, screen, width, height, version, *, scrolled=False):
    matches = re.findall(r'CONTENT_FRAME ' + re.escape(screen) + r' (\{[^\n]+\})', log)
    assert len(matches) == 1, f'Missing or repeated content evidence [{screen}]'
    evidence = json.loads(matches[0])
    assert (evidence['width'], evidence['height']) == (width, height), evidence
    texts = evidence['texts']
    body = [item for item in texts if item['body'] and not item['clipped'] and not item['truncated']]
    assert body, f'No readable page content [{screen}]'
    for item in body:
        assert item['y'] + item['height'] <= evidence['footerY'] + 1, f'Page overlaps footer [{screen}]'
    expected = ['EMAKI SETUP' if height < 600 else ('Installer' if version is None else 'Install · ' + version)]
    if not scrolled:
        expected.append(heading_for(screen))
        if screen.endswith('-numlock'):
            expected.append('Num Lock is on — the login screen starts with Num Lock off; type digits on the main row.')
    required = []
    for index, sentence in enumerate(expected):
        found = [item for item in texts if item['text'] == sentence and not item['clipped'] and not item['truncated']]
        if index > 0:
            found = [item for item in found if item['body']]
        assert len(found) == 1, f'Expected visible content [{screen}]: {sentence}'
        required.extend(found)
    if scrolled:
        required.append(body[0])
    if not scrolled:
        heading = required[1]
        for item in body:
            if item is heading:
                continue
            overlap_x = min(heading['x'] + heading['width'], item['x'] + item['width']) - max(heading['x'], item['x'])
            overlap_y = min(heading['y'] + heading['height'], item['y'] + item['height']) - max(heading['y'], item['y'])
            assert overlap_x <= 1 or overlap_y <= 1, f'Page heading overlaps text [{screen}]'
    with Image.open(path) as frame:
        frame.load()
        ratio = frame.width / width
        # Each physical edge rounds independently at fractional device scales.
        # Require one common scale within both half-pixel rounding intervals.
        minimum_scale = max(1, (frame.width - 0.5) / width, (frame.height - 0.5) / height)
        maximum_scale = min((frame.width + 0.5) / width, (frame.height + 0.5) / height)
        assert minimum_scale <= maximum_scale, f'Wrong frame dimensions [{screen}]'
        pixels = frame.convert('RGBA')
        for item in required:
            bounds = (round(item['x'] * ratio), round(item['y'] * ratio),
                      round((item['x'] + item['width']) * ratio), round((item['y'] + item['height']) * ratio))
            region = pixels.crop(bounds)
            # Real glyphs contain dark opaque ink on the light pane, even with software antialiasing.
            ink = sum(region.getpixel((x, y))[3] > 128 and max(region.getpixel((x, y))[:3]) < 210
                      for y in range(region.height) for x in range(region.width))
            assert ink >= 10, f'Text has no rendered ink [{screen}]: {item["text"]}'
