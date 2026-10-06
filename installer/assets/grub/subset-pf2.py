#!/usr/bin/env python3
"""Cut a GRUB PFF2 font down to printable ASCII and name it after its family.

Text GRUB prints before normal mode goes through grub_xputs_dumb, which turns every
code point above 0x7f into '?', so the disk-unlock screen needs no glyph outside
U+0020..U+007E. The metric sections (PTSZ, MAXW, MAXH, ASCE, DESC) are copied as they
are: gfxterm takes the line height from MAXH and the cell width from the ASCII device
widths, so the subset renders exactly like the full font it was cut from.

    grub-mkfont -s 32 -o full-32.pf2 /usr/share/fonts/TTF/DejaVuSansMono.ttf
    python installer/assets/grub/subset-pf2.py --family 'DejaVu Sans Mono' full-32.pf2 unlock-32.pf2
"""
import argparse
from pathlib import Path
import struct

FIRST, LAST = 0x20, 0x7E
MAGIC = b'FILE\x00\x00\x00\x04PFF2'
ENTRY = struct.Struct('>IBI')  # code point, storage flags, absolute file offset
GLYPH = struct.Struct('>HHhhh')  # width, height, x offset, y offset, device width


def read_sections(data):
    """Every section up to DATA as (name, body); DATA's body is the rest of the file."""
    sections, pos = [], 0
    while True:
        name = data[pos:pos + 4].decode('ascii')
        length = struct.unpack('>I', data[pos + 4:pos + 8])[0]
        pos += 8
        if name == 'DATA':
            sections.append((name, data[pos:]))
            return sections
        sections.append((name, data[pos:pos + length]))
        pos += length


def glyphs(data, chix):
    """Code point -> complete glyph record (header and bitmap) of a font file."""
    result = {}
    for i in range(0, len(chix), ENTRY.size):
        code, flags, offset = ENTRY.unpack_from(chix, i)
        if flags:
            raise ValueError(f'U+{code:04X}: compressed glyph storage is not supported')
        width, height = struct.unpack_from('>HH', data, offset)
        result[code] = data[offset:offset + GLYPH.size + (width * height + 7) // 8]
    return result


def subset(data, family):
    if data[:len(MAGIC)] != MAGIC:
        raise ValueError('not a PFF2 font')
    sections = read_sections(data)
    names = [name for name, _ in sections]
    if names.count('CHIX') != 1 or names[-1] != 'DATA':
        raise ValueError('unexpected section layout: ' + ' '.join(names))
    fields = dict(sections)
    size = struct.unpack('>H', fields['PTSZ'])[0]
    records = glyphs(data, fields['CHIX'])
    missing = [code for code in range(FIRST, LAST + 1) if code not in records]
    if missing:
        raise ValueError('glyphs missing: ' + ' '.join(f'U+{code:04X}' for code in missing))
    kept = [(code, records[code]) for code in range(FIRST, LAST + 1)]
    header = b''
    for name, body in sections[:-1]:
        if name == 'NAME':
            body = f'{family} Regular {size}'.encode() + b'\0'
        elif name == 'FAMI':
            body = family.encode() + b'\0'
        elif name == 'CHIX':
            continue
        header += name.encode() + struct.pack('>I', len(body)) + body
    # Offsets are absolute: header, CHIX header and entries, DATA header, then the records.
    offset = len(header) + 8 + len(kept) * ENTRY.size + 8
    chix = b''
    for code, record in kept:
        chix += ENTRY.pack(code, 0, offset)
        offset += len(record)
    return (header + b'CHIX' + struct.pack('>I', len(chix)) + chix
            + b'DATA' + b'\xff\xff\xff\xff' + b''.join(record for _, record in kept))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--family', required=True, help='family name written into NAME and FAMI')
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    data = args.source.read_bytes()
    result = subset(data, args.family)
    # Read the result back the way GRUB does and compare every kept glyph with the source.
    original = glyphs(data, dict(read_sections(data))['CHIX'])
    check = dict(read_sections(result))
    kept = glyphs(result, check['CHIX'])
    if sorted(kept) != list(range(FIRST, LAST + 1)) or any(kept[c] != original[c] for c in kept):
        raise SystemExit('self-check failed: the subset does not reproduce the source glyphs')
    args.output.write_bytes(result)
    print(f'{args.output}: {len(kept)} glyphs, {len(result)} bytes, {check["NAME"][:-1].decode()}')


if __name__ == '__main__':
    main()
