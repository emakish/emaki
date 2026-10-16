#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rebuild the offline time zone map from pinned, checksum-verified upstream data.

Needs only Python and the `zic` compiler from tzcode; downloads are cached only in
the supplied directory. The map and its zone table remain ODbL-1.0 data (README.md).

Steps: clip every zone polygon to the map's latitudes, project it with Mercator,
split the rings into arcs at the points where neighbouring zones
meet (each shared border becomes one arc), simplify every arc once with
Douglas-Peucker, round to the map grid and write the arcs and the zones' arc lists.
Neighbouring zones keep exactly the same simplified border, so the map has no gaps or
overlaps along borders, and the shapes the picker draws are the shapes it hit-tests.
"""
import argparse
import datetime
import hashlib
import io
import json
import math
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
from urllib.request import urlopen
import zipfile
import zoneinfo

LAND = 'ne_50m_land-5.1.2.geojson'
SOURCES = {
    LAND: (
        'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_50m_land.geojson',
        'e874b27a51d146452be360cafb3cc50c86001074a67d534113e6534682f9826b'),
    'timezones-2026b.zip': (
        'https://github.com/evansiroky/timezone-boundary-builder/releases/download/2026b/timezones.geojson.zip',
        'f892b57ce8c7d9633a03ce9e6775d54544c05d9b8d62029bc6543091cac213c4'),
    'tzdata2026b.tar.gz': (
        'https://data.iana.org/time-zones/releases/tzdata2026b.tar.gz',
        '114543d9f19a6bfeb5bca43686aea173d38755a3db1f2eec112647ae92c6f544'),
}
# The map shows 75° N to 60° S. Further north Mercator inflates the Arctic; further
# south there is only Antarctica, whose zones are research-station sectors.
NORTH, SOUTH = 75.0, -60.0
WIDTH = 3600            # map units across 360° of longitude: 0.1° each
TOLERANCE = 1.0         # Douglas-Peucker tolerance, in map units
MIN_AREA = 2.0          # smaller rings (map units²) are dropped, except a zone's largest
# Standard and summer offsets: the smaller and the larger of the offsets a zone has on
# these days. Daylight saving time is in force on at most one of them in either hemisphere.
REFERENCE_DAYS = (datetime.datetime(2026, 1, 15, 12, tzinfo=datetime.UTC),
                  datetime.datetime(2026, 7, 15, 12, tzinfo=datetime.UTC))
TZDATA_FILES = ['africa', 'antarctica', 'asia', 'australasia', 'europe', 'northamerica',
                'southamerica', 'etcetera', 'backward']


def source(cache, name):
    url, checksum = SOURCES[name]
    path = cache / name
    if not path.exists():
        with urlopen(url, timeout=300) as response:
            path.write_bytes(response.read())
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError('Source checksum mismatch: ' + name)
    return data


def mercator(lat):
    return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))


TOP = mercator(NORTH)
HEIGHT = round(WIDTH * (TOP - mercator(SOUTH)) / (2 * math.pi))


def project(lon, lat):
    """Longitude and latitude to map units (y grows southwards). Exact floats: the same
    source point always gives the same projected point, which the topology relies on."""
    x = (lon + 180) / 360 * WIDTH
    y = (TOP - mercator(lat)) / (2 * math.pi) * WIDTH
    return (min(WIDTH, max(0.0, x)), min(HEIGHT, max(0.0, y)))


def clip(ring, limit, keep_above):
    """Sutherland-Hodgman against one parallel; the ring is open (no repeated end)."""
    inside = (lambda p: p[1] >= limit) if keep_above else (lambda p: p[1] <= limit)
    result = []
    for index, current in enumerate(ring):
        previous = ring[index - 1]
        if inside(current) != inside(previous):
            # Interpolate from the smaller end, so the two zones that share this edge
            # (and walk it in opposite directions) get the very same crossing point.
            a, b = sorted((previous, current))
            t = (limit - a[1]) / (b[1] - a[1])
            result.append((a[0] + t * (b[0] - a[0]), limit))
        if inside(current):
            result.append(current)
    return result


def dedupe(points):
    """Drop repeated neighbours of a closed ring."""
    return [p for i, p in enumerate(points) if p != points[i - 1]] if len(points) > 1 else list(points)


def area(points):
    return sum(points[i - 1][0] * p[1] - p[0] * points[i - 1][1] for i, p in enumerate(points)) / 2


def douglas_peucker(points, tolerance):
    """Simplify an open polyline; both ends stay."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    limit = tolerance * tolerance
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        ax, ay = points[first]
        dx, dy = points[last][0] - ax, points[last][1] - ay
        length = dx * dx + dy * dy
        farthest, index = -1.0, -1
        for i in range(first + 1, last):
            px, py = points[i][0] - ax, points[i][1] - ay
            t = 0.0 if length == 0 else min(1.0, max(0.0, (px * dx + py * dy) / length))
            ex, ey = px - t * dx, py - t * dy
            distance = ex * ex + ey * ey
            if distance > farthest:
                farthest, index = distance, i
        if farthest > limit:
            keep[index] = True
            stack.extend(((first, index), (index, last)))
    return [p for p, kept in zip(points, keep) if kept]


def simplify_closed(points, tolerance):
    """Simplify a closed ring that has no fixed start; at least a triangle stays."""
    start = points[0]
    far = max(range(len(points)), key=lambda i: (points[i][0] - start[0]) ** 2 + (points[i][1] - start[1]) ** 2)
    if far == 0:
        return [start]
    result = douglas_peucker(points[:far + 1], tolerance)[:-1] + douglas_peucker(points[far:] + [start], tolerance)[:-1]
    if len(result) < 3 and len(points) >= 3:
        line = points[far]
        third = max(range(len(points)), key=lambda i: abs(
            (line[0] - start[0]) * (points[i][1] - start[1]) - (line[1] - start[1]) * (points[i][0] - start[0])))
        result = sorted({0, far, third})
        result = [points[i] for i in result]
    return result


def to_map(points):
    """Round to whole map units (half up), without repeated neighbours."""
    result = []
    for x, y in points:
        point = (math.floor(x + .5), math.floor(y + .5))
        if not result or result[-1] != point:
            result.append(point)
    return result


def build_map(features, offsets, points, land_features):
    """features: GeoJSON zone features with a tzid; offsets: tzid -> [standard, summer] offsets
    in minutes (equal without daylight saving time);
    points: zone.tab entries by tzid; land_features: GeoJSON land polygons.

    Returns the data written to ZoneData.js. Pure: the same input gives the same output.
    """
    names = ['UTC'] + sorted({f['properties']['tzid'] for f in features})
    zone_index = {name: i for i, name in enumerate(names)}
    # 1. Clip, project and quantize every ring. A ring is (zone, is_hole, points).
    rings = []
    for feature in sorted(features, key=lambda f: f['properties']['tzid']):
        geometry = feature['geometry']
        polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
        for polygon in polygons:
            for ring_number, ring in enumerate(polygon):
                ring = [tuple(p[:2]) for p in ring]
                if ring and ring[0] == ring[-1]:
                    ring = ring[:-1]
                ring = clip(clip(ring, NORTH, False), SOUTH, True)
                ring = dedupe([project(lon, lat) for lon, lat in ring])
                if len(ring) >= 3 and area(ring):
                    rings.append((zone_index[feature['properties']['tzid']], ring_number > 0, ring))
    # 2. Junctions: points where the neighbouring points differ between the rings
    # that pass through them, i.e. where a shared border starts or ends.
    seen, junctions = {}, set()
    for _, _, ring in rings:
        for i, point in enumerate(ring):
            before, after = ring[i - 1], ring[(i + 1) % len(ring)]
            pair = (before, after) if before < after else (after, before)
            if seen.setdefault(point, pair) != pair:
                junctions.add(point)
    # 3. Cut rings into arcs at junctions; identical arcs (either direction) are stored once.
    arcs, arc_index, ring_arcs = [], {}, []

    def reference(path):
        forward = tuple(path)
        found = arc_index.get(forward)
        if found is not None:
            return found
        found = arc_index.get(forward[::-1])
        if found is not None:
            return ~found
        arc_index[forward] = len(arcs)
        arcs.append(path)
        return len(arcs) - 1

    for zone, hole, ring in rings:
        cuts = [i for i, point in enumerate(ring) if point in junctions]
        if not cuts:
            # A ring without junctions is one closed arc; start it at its smallest point
            # so the same ring met as an island and as a hole matches either way round.
            start = ring.index(min(ring))
            ring = ring[start:] + ring[:start]
            forward = tuple(ring)
            backward = (ring[0],) + tuple(ring[:0:-1])
            if forward in arc_index:
                refs = [arc_index[forward]]
            elif backward in arc_index:
                refs = [~arc_index[backward]]
            else:
                arc_index[forward] = len(arcs)
                arcs.append(list(ring) + [None])  # None marks a closed arc
                refs = [len(arcs) - 1]
        else:
            ring = ring[cuts[0]:] + ring[:cuts[0]]
            cuts = [i - cuts[0] for i in cuts] + [len(ring)]
            ring = ring + [ring[0]]
            refs = [reference(ring[a:b + 1]) for a, b in zip(cuts, cuts[1:])]
        ring_arcs.append((zone, hole, refs))
    # 4. Simplify every arc once and move it onto the map grid.
    simple = []
    for arc in arcs:
        if arc[-1] is None:
            points_ = to_map(simplify_closed(arc[:-1], TOLERANCE))
            if len(points_) > 1 and points_[0] == points_[-1]:
                points_.pop()
            simple.append(points_ + [points_[0]] if points_ else points_)
        else:
            simple.append(to_map(douglas_peucker(arc, TOLERANCE)))

    def ring_points(refs):
        result = []
        for ref in refs:
            path = simple[ref] if ref >= 0 else simple[~ref][::-1]
            result.extend(path[1:] if result else path)
        if len(result) > 1 and result[0] == result[-1]:
            result.pop()
        return result

    # 5. Drop rings too small to see, but never a zone's largest ring; holes go
    # together with the islands that fill them.
    measured = [(zone, hole, refs, area(ring_points(refs))) for zone, hole, refs in ring_arcs]
    largest = {}
    for i, (zone, hole, refs, size) in enumerate(measured):
        if not hole and (zone not in largest or abs(size) > abs(measured[largest[zone]][3])):
            largest[zone] = i
    dropped_arcs = {refs[0] if refs[0] >= 0 else ~refs[0]
                    for i, (zone, hole, refs, size) in enumerate(measured)
                    if not hole and len(refs) == 1 and abs(size) < MIN_AREA and largest.get(zone) != i}
    zones = [[] for _ in names]
    for i, (zone, hole, refs, size) in enumerate(measured):
        single = len(refs) == 1 and (refs[0] if refs[0] >= 0 else ~refs[0]) in dropped_arcs
        if largest.get(zone) != i and (abs(size) < MIN_AREA or single):
            continue
        if size == 0:
            continue
        # Outer rings run clockwise on screen (positive area with y down), holes the other way.
        if (size > 0) == hole:
            refs = [~ref for ref in reversed(refs)]
        zones[zone].append(refs)
    # 6. Keep only arcs still in use, renumbered in first-use order; classify each arc
    # by the zones on its two sides: a coast, a border inside one UTC offset, or a
    # border between offsets.
    renumber, used, sides = {}, [], {}
    for zone, zone_rings in enumerate(zones):
        for refs in zone_rings:
            for k, ref in enumerate(refs):
                arc = ref if ref >= 0 else ~ref
                if arc not in renumber:
                    renumber[arc] = len(used)
                    used.append(simple[arc])
                sides.setdefault(renumber[arc], set()).add(zone)
                refs[k] = renumber[arc] if ref >= 0 else ~renumber[arc]
    kinds = []
    for i in range(len(used)):
        zones_ = sorted(sides[i])
        kinds.append(0 if len(zones_) == 1 else 1 if len({offsets[names[z]][0] for z in zones_}) == 1 else 2)
    # 7. Land. Zone shapes include territorial waters, so the map draws water over
    # everything outside these rings; the zones stay whole for hit testing.
    land = []
    for feature in land_features:
        geometry = feature['geometry']
        polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
        for polygon in polygons:
            for ring in polygon:
                ring = [tuple(p[:2]) for p in ring]
                if ring and ring[0] == ring[-1]:
                    ring = ring[:-1]
                ring = dedupe([project(lon, lat) for lon, lat in clip(clip(ring, NORTH, False), SOUTH, True)])
                if len(ring) < 3:
                    continue
                ring = dedupe(to_map(simplify_closed(ring, TOLERANCE)))
                if len(ring) >= 3 and abs(area(ring)) >= MIN_AREA:
                    land.append(ring)
    # 8. Zones whose land the land layer does not show at all (atolls, small archipelagos):
    # a dot at the centre of each of their rings keeps them visible on the water.
    boxes = [box(ring) for ring in land]
    dots = []
    for zone, zone_rings in enumerate(zones):
        outer = []
        for refs in zone_rings:
            ring = []
            for ref in refs:
                path = used[ref] if ref >= 0 else used[~ref][::-1]
                ring.extend(path[1:] if ring else path)
            if area(ring) > 0:  # holes have negative area
                outer.append(ring)

        def on_land(ring):
            near = [land_ring for land_ring, land_box in zip(land, boxes) if overlaps(box(ring), land_box)]
            return any(inside(p, land_ring) for land_ring in near for p in ring[::4]) or \
                any(inside(p, ring) for land_ring in near for p in land_ring[::4])

        if any(on_land(ring) for ring in outer):
            continue
        for ring in outer:
            size = area(ring)
            cx = sum((ring[i - 1][0] + p[0]) * (ring[i - 1][0] * p[1] - p[0] * ring[i - 1][1]) for i, p in enumerate(ring))
            cy = sum((ring[i - 1][1] + p[1]) * (ring[i - 1][0] * p[1] - p[0] * ring[i - 1][1]) for i, p in enumerate(ring))
            dot = [zone, math.floor(cx / (6 * size) + .5), math.floor(cy / (6 * size) + .5)]
            if dot not in dots:
                dots.append(dot)
    return dict(version=2, width=WIDTH, height=HEIGHT, north=NORTH, south=SOUTH, names=names,
                offsets=[0] + [offsets[name][0] for name in names[1:]],
                summer=[0] + [offsets[name][1] for name in names[1:]], arcs=[delta(path) for path in used],
                kinds=kinds, zones=zones, land=[delta(ring) for ring in land], dots=dots, points=points)


def delta(path):
    """[x0, y0, dx1, dy1, …]: small numbers keep the asset small."""
    flat, last = [], (0, 0)
    for point in path:
        flat.extend((point[0] - last[0], point[1] - last[1]))
        last = point
    return flat


def box(ring):
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def overlaps(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def inside(point, ring):
    """Even-odd point-in-polygon, the same rule the picker uses."""
    x, y = point
    result = False
    for i, (bx, by) in enumerate(ring):
        ax, ay = ring[i - 1]
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            result = not result
    return result


def coordinate(part):
    sign = -1 if part[0] == '-' else 1
    digits = part[1:]
    degrees = len(digits) % 2 + 2
    return sign * (int(digits[:degrees]) + int(digits[degrees:degrees + 2]) / 60
                   + (int(digits[degrees + 2:]) / 3600 if len(digits) > degrees + 2 else 0))


def tzdata_tables(archive):
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
    return points


def standard_offsets(archive, names):
    """Compile the pinned tzdata with zic; each zone's [standard, summer] offsets in minutes."""
    with tempfile.TemporaryDirectory(prefix='emaki-tzdata-') as directory:
        root = Path(directory)
        for name in TZDATA_FILES:
            (root / name).write_bytes(archive.extractfile(name).read())
        subprocess.run(['zic', '-d', str(root / 'out'), *[str(root / name) for name in TZDATA_FILES]], check=True)
        offsets = {}
        for name in names:
            with open(root / 'out' / name, 'rb') as compiled:
                zone = zoneinfo.ZoneInfo.from_file(compiled, key=name)
            found = sorted(int(day.astimezone(zone).utcoffset().total_seconds()) // 60 for day in REFERENCE_DAYS)
            offsets[name] = [found[0], found[-1]]
        return offsets


def build(cache):
    with zipfile.ZipFile(io.BytesIO(source(cache, 'timezones-2026b.zip'))) as archive:
        features = json.loads(archive.read('combined.json'))['features']
    with tarfile.open(fileobj=io.BytesIO(source(cache, 'tzdata2026b.tar.gz'))) as archive:
        points = tzdata_tables(archive)
        offsets = standard_offsets(archive, sorted({f['properties']['tzid'] for f in features}))
    land = json.loads(source(cache, LAND))['features']
    return encode(build_map(features, offsets, points, land))


def encode(data):
    js = ('// Generated by generate-timezones.py. Map data: ODbL-1.0; see README.md.\n'
          '.pragma library\n'
          'var data = ' + json.dumps(data, separators=(',', ':'), ensure_ascii=True) + ';\n')
    return {'ZoneData.js': js.encode()}


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
