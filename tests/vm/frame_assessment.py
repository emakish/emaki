# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail-closed semantic and pixel checks of host display captures.

Pillow and tesseract are already used by grub-unlock-check.py. No guest OCR
or guest framebuffer substitutes for the host scan-out. These checks establish
visible content and minimum raster size, not physical readability on hardware.
"""
import csv
import io
import json
from pathlib import Path
import re
import shutil
import statistics
import subprocess


def normalized(text):
    return ' '.join(re.findall(r"[a-z0-9]+", text.lower()))


def require_tools():
    from PIL import Image  # noqa: F401 - fail before starting a disposable VM
    if not shutil.which('tesseract'):
        raise RuntimeError('Frame assessment requires the existing tesseract host tool')


def read_words(path, psm=11, minimum_confidence=40):
    require_tools()
    result = subprocess.run(['tesseract', str(path), 'stdout', '--psm', str(psm), 'tsv'],
                            capture_output=True, text=True, check=True, timeout=60)
    return parse_words(result.stdout, minimum_confidence)


def parse_words(tsv, minimum_confidence=40):
    words = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter='\t'):
        if row['level'] == '5' and row['text'].strip() and float(row['conf']) >= minimum_confidence:
            words.append({'text': row['text'], **{key: int(row[key])
                         for key in ('left', 'top', 'width', 'height')}})
    return words


def field_top(image):
    """Recognize both horizontal borders of the centered 320 by 56px field.

    LockGeometry.qml fixes the settled field at the screen center. Contrast
    across its borders survives both the glass and cream fallback backgrounds.
    """
    width, height = image.size
    if width < 340 or height < 120:
        return None
    pixels = image.convert('RGB').load()
    def edge(y):
        differences = [max(abs(pixels[x, y][k] - pixels[x, y - 1][k])
                           for k in range(3))
                       for x in range(width // 2 - 120, width // 2 + 120)]
        return (sum(value >= 6 for value in differences), sum(differences), y)
    top = max(edge(y) for y in range(height // 2 - 32, height // 2 - 23))
    bottom = max(edge(y) for y in range(height // 2 + 24, height // 2 + 33))
    if min(top[0], bottom[0]) < 200:
        return None
    return top[2]


def crop_words(image, box, scale, psm=7, minimum_confidence=20):
    """Read a separate text line and map its boxes back to original pixels."""
    import tempfile
    left, upper, _, _ = box
    crop = image.crop(box)
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / 'line.png'
        crop.resize((crop.width * scale, crop.height * scale)).save(path)
        words = read_words(path, psm=psm, minimum_confidence=minimum_confidence)
    for word in words:
        word['left'] = left + word['left'] // scale
        word['top'] = upper + word['top'] // scale
        word['width'] = (word['width'] + scale - 1) // scale
        word['height'] = (word['height'] + scale - 1) // scale
    return words


def feedback_words(image):
    """Read LockSurface.qml's 13px feedback at center + 43, below the field."""
    width, height = image.size
    return crop_words(image, (max(0, width // 2 - 220), height // 2 + 40,
                             min(width, width // 2 + 220), min(height, height // 2 + 84)),
                      4, psm=6)


def feedback_ink(image):
    """Count dangerOnLight ink in LockSurface.qml's scale-1 feedback band."""
    width, height = image.size
    # Feedback starts at center + 43 in a 440px-wide box, with 13px text.
    # Include wrapped feedback and the opaque cream plate's padding. The same
    # #a01b45 ink is used without a plate for other authentication errors.
    band = image.convert('RGB').crop((max(0, width // 2 - 220), height // 2 + 34,
                                     min(width, width // 2 + 220), min(height, height // 2 + 84)))
    # Allow antialiased strokes; neutral cream and ordinary dark ink do not
    # qualify. Recorded good frames have at most 8 pixels, refusals at least 160.
    return sum(r > g * 1.7 and r > b * 1.2 and 100 < r < 200
               for r, g, b in band.getdata())


def field_words(image):
    """Locate the field's borders, then OCR its centered placeholder."""
    width, _ = image.size
    rgb = image.convert('RGB')
    top = field_top(rgb)
    if top is None:
        return []
    # The shipped field is 320px wide, with a 15px centered placeholder.
    # Exclude the eye button and layout indicator; preserve the original pixels.
    center = width // 2
    left, upper = center - 55, top + 15
    # Older host captures have a baked-in pointer across the lower glyphs.
    # Try the tight text line, then the complete label with more OCR resolution.
    # Require the exact word even at reduced confidence; never substitute a label.
    for right, bottom, scale in ((center + 55, top + 34, 2),
                                 (center + 50, top + 41, 4)):
        words = crop_words(rgb, (left, upper, right, bottom), scale)
        if any(normalized(word['text']) == 'password' for word in words):
            return words
    # Raw-line recognition of the upper glyphs avoids the pointer's broad base.
    words = crop_words(rgb, (center - 50, top + 18, center + 50, top + 33), 2, psm=13)
    if any(normalized(word['text']) == 'password' for word in words):
        return words
    return []


def menu_selection(image, words):
    """Find the gfxterm menu border and its full-width neutral selection bar."""
    width, height = image.size
    pixels = image.load()
    rows = []
    for y in range(int(height * .04), int(height * .85)):
        samples = [pixels[x, y] for x in range(int(width * .08), int(width * .92), 8)]
        if sum(max(p) - min(p) < 12 and min(p) > 120 for p in samples) > len(samples) * .85:
            rows.append(y)
    bands = []
    for y in rows:
        if bands and bands[-1][1] == y:
            bands[-1][1] = y + 1
        else:
            bands.append([y, y + 1])
    bars = [b for b in bands if 8 <= b[1] - b[0] <= 40]
    borders = [b for b in bands if b[1] - b[0] <= 3]
    if len(bars) != 1 or len(borders) < 2:
        raise RuntimeError('Missing GRUB menu border or highlight bar')
    top, bottom = bars[0]
    upper = next((b[0] for b in borders if b[1] <= top), None)
    lower = next((b[0] for b in borders if b[0] > bottom), None)
    if upper is None or lower is None or lower - upper < height * .4:
        raise RuntimeError('Incomplete GRUB menu layout')
    row_height = bottom - top
    visible_rows = {round((w['top'] - upper - 8) / row_height) for w in words
                    if upper < w['top'] < lower and w['height'] >= 9}
    if len(visible_rows) < 2:
        raise RuntimeError('GRUB menu needs several readable rows')
    index = round((top - upper - 8) / row_height)
    if index < 0 or abs(top - (upper + 8 + index * row_height)) > 3:
        raise RuntimeError('Highlight does not align with GRUB menu rows')
    return dict(index=index, top=top, bottom=bottom, row_height=row_height)


def assess_image(image, stage, words, expected=(), selected_index=None):
    """Assess decoded pixels and OCR boxes; separated for tiny offline fixtures."""
    from PIL import ImageStat
    image = image.convert('RGB')
    width, height = image.size
    if max(ImageStat.Stat(image).stddev) < 4:
        raise RuntimeError('Blank or nearly uniform host frame')
    text = normalized(' '.join(word['text'] for word in words))
    errors = ('grub rescue', 'error', 'failed to boot', 'no bootable',
              'uefi interactive shell', 'press esc to skip startup', 'invalid passphrase',
              'wrong password', 'emergency mode', 'attempting to decrypt')
    if stage == 'after-unlock' and field_top(image) is not None:
        # A boot menu or desktop can contain red pixels at these coordinates;
        # identify the shell field before calling them authentication feedback.
        ink = feedback_ink(image)
        if ink >= 100:
            raise RuntimeError('Authentication error feedback visible: '
                               f'{ink} dark-red pixels below password field')
    if any(normalized(error) in text for error in errors):
        raise RuntimeError('Firmware, boot or password error visible: ' + text)
    if stage == 'prompt':
        required = ('disk is encrypted', 'type your disk password and press enter',
                    'letters do not appear while you type')
        minimum_height, minimum_area = max(18, height * .025), width * height * .003
        region = (.1 * width, .15 * height, .9 * width, .8 * height)
    elif stage == 'menu':
        selection = menu_selection(image, words)
        if selected_index is not None and selection['index'] != selected_index:
            raise RuntimeError('Unexpected GRUB selection: ' + str(selection))
        # Read stable labels from the other rows: selected titles can be clipped
        # or hidden by the highlight, and kernel submenus have no Emaki heading.
        required = tuple(expected) or tuple(word for word in
                    ('emaki', 'snapshot', 'initramfs', 'linux') if word in text)
        if not required:
            raise RuntimeError('Missing readable GRUB menu labels')
        minimum_height, minimum_area = 10, 80
        region = (0, 0, width, height)
    elif stage == 'after-unlock':
        required = tuple(expected) or ('password',)
        # The shipped greeter placeholder uses a 15px font (LockSurface.qml).
        minimum_height, minimum_area = 10, 80
        region = (0, 0, width, height)
        if 'disk is encrypted' in text or 'grub' in text:
            raise RuntimeError('Unlock screen or boot loader still visible')
    else:
        raise ValueError('Unknown frame stage: ' + stage)
    for phrase in required:
        if normalized(phrase) not in text:
            raise RuntimeError('Expected visible text missing: ' + phrase)
    relevant = set(normalized(' '.join(required)).split())
    if stage == 'menu' and not required:
        relevant = set(text.split())
    glyph_heights, lit = [], 0
    for word in words:
        if not relevant.intersection(normalized(word['text']).split()):
            continue
        x, y, w, h = (word[key] for key in ('left', 'top', 'width', 'height'))
        if not (region[0] <= x < x + w <= region[2] and
                region[1] <= y < y + h <= region[3]):
            raise RuntimeError('Expected text is outside the prompt region')
        # Count actual contrasting glyph pixels, not the OCR rectangle.
        # Menu selections and the greeter can have dark text on a light surface.
        crop = image.crop((x, y, x + w, y + h)).convert('L')
        # Sample just outside the OCR box so strokes touching its corners
        # cannot turn the estimated background into a fictitious middle gray.
        gray = image.convert('L')
        border_y = max(0, y - 1)
        background = statistics.median([gray.getpixel((column, border_y))
                                        for column in range(x, x + w)])
        mask = crop.point(lambda value: 255 if abs(value - background) >= 40 else 0)
        box = mask.getbbox()
        if box:
            glyph_heights.append(box[3] - box[1])
            lit += mask.histogram()[255]
    measured_height = statistics.median(glyph_heights) if glyph_heights else 0
    metrics = {'size': [width, height], 'text': text, 'glyph_height': measured_height,
               'minimum_glyph_height': minimum_height, 'lit_pixels': lit,
               'minimum_lit_pixels': minimum_area, 'stage': stage}
    if measured_height < minimum_height or lit < minimum_area:
        raise RuntimeError('Visible text is too small or dim: ' + json.dumps(metrics))
    if stage == 'menu':
        metrics['selection'] = selection
    return metrics


def assess_frame(path, stage, expected=(), selected_index=None):
    """Write FAIL before decoding/OCR; only a completed assessment writes PASS."""
    from PIL import Image
    path = Path(path)
    evidence = path.with_suffix('.assessment.json')
    record = {'status': 'FAIL', 'stage': stage, 'frame': path.name,
              'scope': 'Raster content and size; hardware appearance requires human assessment.'}
    evidence.write_text(json.dumps(record, indent=2) + '\n')
    try:
        words = read_words(path)
        with Image.open(path) as frame:
            if stage == 'after-unlock':
                words += field_words(frame)
                words += feedback_words(frame)
            record.update(assess_image(frame, stage, words, expected, selected_index))
        record['status'] = 'PASS'
    except Exception as error:
        record['error'] = str(error)
        raise
    finally:
        evidence.write_text(json.dumps(record, indent=2) + '\n')
    return record
