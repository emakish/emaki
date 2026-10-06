#!/usr/bin/env python3
"""Rebuild the small offline map from pinned, checksum-verified upstream data.

Needs Python and Pillow; downloads are cached only in the supplied directory.
The raster index and its source table remain ODbL-1.0 data (see README.md).
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile
from urllib.request import urlopen
import zipfile
import zlib

from PIL import Image, ImageDraw

SOURCES = {
    'ne_110m_land-5.1.2.geojson': (
        'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_110m_land.geojson',
        '9e0729ee253ca7d7a5c4ae9395fb1902264c5377c52e224d13dd85010e2835d9'),
    'timezones-2026b.zip': (
        'https://github.com/evansiroky/timezone-boundary-builder/releases/download/2026b/timezones.geojson.zip',
        'f892b57ce8c7d9633a03ce9e6775d54544c05d9b8d62029bc6543091cac213c4'),
    'tzdata2026b.tar.gz': (
        'https://data.iana.org/time-zones/releases/tzdata2026b.tar.gz',
        '114543d9f19a6bfeb5bca43686aea173d38755a3db1f2eec112647ae92c6f544'),
}
WIDTH, HEIGHT = 960, 480


def source(cache, name):
    url, checksum = SOURCES[name]
    path = cache / name
    if not path.exists():
        with urlopen(url, timeout=90) as response:
            path.write_bytes(response.read())
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError('Source checksum mismatch: ' + name)
    return data


def xy(lon, lat):
    return ((lon + 180) * WIDTH / 360, (90 - lat) * HEIGHT / 180)


def coordinate(part):
    sign = -1 if part[0] == '-' else 1
    digits = part[1:]
    degrees = len(digits) % 2 + 2
    return sign * (int(digits[:degrees]) + int(digits[degrees:degrees + 2]) / 60
                   + (int(digits[degrees + 2:]) / 3600 if len(digits) > degrees + 2 else 0))


def build(cache):
    with zipfile.ZipFile(io.BytesIO(source(cache, 'timezones-2026b.zip'))) as archive:
        features = json.loads(archive.read('combined.json'))['features']
    with tarfile.open(fileobj=io.BytesIO(source(cache, 'tzdata2026b.tar.gz'))) as archive:
        countries = {}
        for line in archive.extractfile('iso3166.tab').read().decode().splitlines():
            if line and not line.startswith('#'):
                code, label = line.split('\t')
                countries[code] = label
        points = {}
        for table in ('zone1970.tab', 'zone.tab'):
            for line in archive.extractfile(table).read().decode().splitlines():
                if not line or line.startswith('#'):
                    continue
                country, position, name, *comments = line.split('\t')
                lat, lon = [coordinate(p) for p in re.findall(r'[+-]\d+', position)]
                points[name] = [round(lon, 4), round(lat, 4),
                                ', '.join(countries[c] for c in country.split(',')),
                                ' '.join(comments)]
    names = ['UTC'] + sorted({f['properties']['tzid'] for f in features})
    # Each feature is rendered into a private mask, so interior rings never erase
    # a previously rendered neighbouring zone. Sorted order resolves overlaps.
    index = Image.new('I', (WIDTH, HEIGHT), 0)
    for feature in sorted(features, key=lambda f: f['properties']['tzid']):
        name = feature['properties']['tzid']
        geometry = feature['geometry']
        polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
        mask = Image.new('1', index.size)
        draw = ImageDraw.Draw(mask)
        for polygon in polygons:
            for ring_index, ring in enumerate(polygon):
                draw.polygon([xy(*p) for p in ring], fill=1 if ring_index == 0 else 0)
        index.paste(names.index(name), mask=mask)
    # Antarctic time zones include rectangular territorial sectors over the ocean.
    # Clip only south of 60 degrees to the pinned public-domain land coastline;
    # the same index still drives both the picture and selection/hit testing.
    land = Image.new('1', index.size)
    coast = ImageDraw.Draw(land)
    for feature in json.loads(source(cache, 'ne_110m_land-5.1.2.geojson'))['features']:
        geometry = feature['geometry']
        polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
        for polygon in polygons:
            for ring_index, ring in enumerate(polygon):
                coast.polygon([xy(*p) for p in ring], fill=1 if ring_index == 0 else 0)
    for y in range(int(xy(0, -60)[1]), HEIGHT):
        for x in range(WIDTH):
            if not land.getpixel((x, y)):
                index.putpixel((x, y), 0)
    pixels = list(index.get_flattened_data())
    rows = []
    palette = ['#cfb9aa', '#e5c2a6', '#c5bbb0', '#d7b9b1', '#d9c9b4', '#e2c7ba']
    colors = ['#f5e8de'] + [palette[zlib.crc32(name.encode()) % len(palette)] for name in names[1:]]
    picture = Image.new('RGB', index.size)
    paint = ImageDraw.Draw(picture)
    for y in range(HEIGHT):
        row, start = [], 0
        while start < WIDTH:
            ident = pixels[y * WIDTH + start]
            end = start + 1
            while end < WIDTH and pixels[y * WIDTH + end] == ident:
                end += 1
            row.extend([end, ident])
            paint.line((start, y, end - 1, y), fill=colors[ident])
            start = end
        rows.append(row)
    # Polar sectors describe station timekeeping, not geographic subdivisions.
    # Draw Antarctic land in one colour instead of outlining rectangular claims.
    # The clipped zone index remains available for clicks and selection highlights.
    for y in range(int(xy(0, -60)[1]), HEIGHT):
        for x in range(WIDTH):
            if land.getpixel((x, y)):
                picture.putpixel((x, y), (217, 201, 180))
    # Subtle, one-pixel zone boundaries and ocean meridians.
    for y in range(HEIGHT - 1):
        for x in range(WIDTH - 1):
            pos = y * WIDTH + x
            ident = pixels[pos]
            if y >= int(xy(0, -60)[1]) and land.getpixel((x, y)):
                if not land.getpixel((x + 1, y)) or not land.getpixel((x, y + 1)):
                    picture.putpixel((x, y), (173, 143, 124))
            elif ident and (pixels[pos + 1] != ident or pixels[pos + WIDTH] != ident):
                picture.putpixel((x, y), (173, 143, 124))
            elif not ident and x % 40 == 0:
                picture.putpixel((x, y), (233, 216, 204))
    png = io.BytesIO()
    picture.save(png, format='PNG', optimize=True)
    # RLE rows are both the hit map and the highlight source: no second geometry.
    data = dict(width=WIDTH, height=HEIGHT, names=names, rows=rows, points=points)
    js = ('// Generated by generate-timezones.py. Map data: ODbL-1.0; see README.md.\n'
          'var data = ' + json.dumps(data, separators=(',', ':'), ensure_ascii=True) + ';\n')
    return {'timezone-map.png': png.getvalue(), 'ZoneData.js': js.encode()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    for name, data in build(args.cache).items():
        path = Path(__file__).parent / name
        if args.check:
            assert path.read_bytes() == data, name + ' needs regeneration'
        else:
            path.write_bytes(data)
        print(('PASS ' if args.check else 'Wrote ') + name + ': ' + str(len(data)) + ' bytes')


if __name__ == '__main__':
    main()
