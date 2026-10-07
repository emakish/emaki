#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regenerate committed unlock artwork with Pillow/FreeType and Adwaita Sans."""

import argparse
from pathlib import Path
import struct
import sys
import tomllib
import zlib

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'installer'))
from emaki_installer import grub_screen as screen  # noqa: E402


def png(image):
    """Opaque RGB with PNG Up filters, reducing the embedded photographic payload."""
    width, height = image.size
    pixels = image.tobytes()
    stride = width * 3
    rows = [b'\0' + pixels[:stride]]
    for y in range(1, height):
        row = pixels[y * stride:(y + 1) * stride]
        previous = pixels[(y - 1) * stride:y * stride]
        rows.append(b'\2' + bytes((a - b) & 255 for a, b in zip(row, previous)))
    raw = b''.join(rows)

    def chunk(name, body):
        return struct.pack('>I', len(body)) + name + body + struct.pack('>I', zlib.crc32(name + body))

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'tEXt', b'Copyright\0Copyright (C) 2026 Artur Yakymenko')
            + chunk(b'tEXt', b'License\0SPDX-License-Identifier: GPL-3.0-or-later')
            + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def backdrop(colors, width, height, variant):
    """Keep the menu wave visible, with a soft dark field under the text."""
    if variant == 'C':
        with Image.open(ROOT / 'art/grub/background.png') as source:
            image = source.convert('RGB').resize((width, height), Image.Resampling.LANCZOS)
    else:
        image = Image.new('RGB', (width, height), colors['background'])
    mask = Image.new('L', image.size)
    draw = ImageDraw.Draw(mask)
    for y in range(height):
        # At 480px high, 128 black master rows still conceal the 32px terminal.
        fade = min(1, max(0, (y - 128) / (272 if variant == 'C' else 112)))
        top = 1 - (fade * fade * (3 - 2 * fade) if variant == 'C' else fade)
        band = 0
        if variant == 'C':
            # Only the orange status line needs shade; the cream instructions
            # already clear 4.5:1 against the unmodified wave.
            rise = min(1, max(0, (y - 1036) / 80))
            fall = min(1, max(0, (1285 - y) / 80))
            amount = min(rise, fall)
            band = .48 * amount * amount * (3 - 2 * amount)
        draw.line((0, y, width - 1, y), fill=round(255 * max(top, band)))
    return Image.composite(Image.new('RGB', image.size), image, mask)


def luminance(color):
    channels = [v / 255 for v in color]
    return sum(weight * (v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4)
               for weight, v in zip((.2126, .7152, .0722), channels))


def picture(font_path, colors, width, height, variant, state):
    size = round(width * 36 / 1024)
    font = ImageFont.truetype(str(font_path), size)
    if font.getname()[0] != 'Adwaita Sans':
        raise ValueError('the artwork requires Adwaita Sans')
    margin = round(width * 0.12)
    while max(font.getlength(line) for line in (*screen.TEXT, screen.WRONG, screen.CHECKING)) > width - 2 * margin:
        size -= 1
        font = ImageFont.truetype(str(font_path), size)
    image = backdrop(colors, width, height, variant)
    draw = ImageDraw.Draw(image)
    line_height = round(size * (1.75 if variant == 'A' else 2.0))
    middle = height // 2
    status = screen.CHECKING if state == 'checking' else screen.WRONG
    contrasts = []
    for index, line in enumerate((*screen.TEXT, status)):
        if index == len(screen.TEXT) and state == 'unlock':
            continue
        color = colors['accent'] if index == len(screen.TEXT) and variant != 'A' else colors['text']
        left, top, right, bottom = draw.textbbox((0, 0), line, font=font)
        x = (width - (right - left)) // 2 - left
        y = middle + (index - 2) * line_height - (bottom - top) // 2 - top
        # Check the entire glyph rectangle before painting; antialiased edges
        # naturally blend, while solid interiors retain this contrast or better.
        brightest = max(luminance(pixel) for pixel in image.crop(
            (x + left, y + top, x + right, y + bottom)).get_flattened_data())
        foreground = luminance(tuple(bytes.fromhex(color[1:])))
        contrast = (foreground + .05) / (brightest + .05)
        if contrast < 4.5:
            raise ValueError(f'{variant}/{state}: text contrast {contrast:.2f}:1 is too low')
        contrasts.append(contrast)
        draw.text((x, y), line, font=font, fill=color)
    if variant == 'C':
        y = middle - 3 * line_height
        half = round(width * 0.032)
        draw.rectangle((width // 2 - half, y, width // 2 + half, y + 4), fill=colors['accent'])
    print(f'{variant}/{state}: {size}px font; line contrasts '
          + ', '.join(f'{value:.2f}:1' for value in contrasts))
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font', type=Path, default=Path('/usr/share/fonts/Adwaita/AdwaitaSans-Regular.ttf'))
    parser.add_argument('--output', type=Path, default=screen.ARTWORK)
    parser.add_argument('--check', action='store_true', help='compare regenerated bytes without writing')
    args = parser.parse_args()
    tokens = tomllib.loads((ROOT / 'tokens.toml').read_text())
    colors = {name: '#' + value for name, value in tokens['color'].items()}
    for variant in ('A', 'B', 'C'):
        for width, height in screen.SIZES:
            for state in ('unlock', 'wrong', 'checking'):
                data = png(picture(args.font, colors, width, height, variant, state))
                path = args.output / variant / f'{state}-{width}x{height}.png'
                if args.check:
                    if path.read_bytes() != data:
                        raise SystemExit(f'artwork needs regeneration: {path}')
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
    print('Unlock artwork is current.' if args.check else 'Wrote all three unlock variants.')


if __name__ == '__main__':
    main()
