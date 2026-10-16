# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The map generator on small synthetic shapes: shared borders, enclaves, islands, determinism."""
import importlib.util
import json
from pathlib import Path
import unittest

UI = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('generate_timezones', UI / 'assets/generate-timezones.py')
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def square(west, south, east, north, step=0.01):
    """A closed lon/lat ring, densely sampled like the real boundary data."""
    # Rounded like the source data, so neighbours that share an edge share its exact points.
    count = round((east - west) / step)
    ring = [(west + i * (east - west) / count, south) for i in range(count)]
    ring += [(east, south + i * (north - south) / count) for i in range(count)]
    ring += [(east - i * (east - west) / count, north) for i in range(count)]
    ring += [(west, north - i * (north - south) / count) for i in range(count)]
    ring = [(round(lon, 6), round(lat, 6)) for lon, lat in ring]
    return ring + [ring[0]]


def feature(tzid, *rings):
    return {'properties': {'tzid': tzid}, 'geometry': {'type': 'Polygon', 'coordinates': [list(map(list, r)) for r in rings]}}


# West and East share the meridian 10° E; Enclave sits in a hole of West; Atoll is alone
# in the water; the land layer covers West, East and Enclave but not Atoll.
FEATURES = [
    feature('Test/West', square(0, 40, 10, 50), list(reversed(square(4, 44, 6, 46)))),
    feature('Test/East', square(10, 40, 20, 50)),
    feature('Test/Enclave', square(4, 44, 6, 46)),
    feature('Test/Atoll', square(-30.1, 10.0, -29.9, 10.2)),
]
OFFSETS = {'Test/West': [60, 120], 'Test/East': [0, 60], 'Test/Enclave': [60, 120], 'Test/Atoll': [345, 345]}
POINTS = {'Test/West': [5.0, 48.0, 'Westland', '']}
LAND = [{'geometry': {'type': 'Polygon', 'coordinates': [list(map(list, square(-1, 39, 21, 51, 0.5)))]}}]


class Generator(unittest.TestCase):
    def setUp(self):
        self.data = generator.build_map(FEATURES, OFFSETS, POINTS, LAND)
        self.names = self.data['names']

    def arcs_of(self, name):
        return {ref if ref >= 0 else ~ref for refs in self.data['zones'][self.names.index(name)] for ref in refs}

    def test_deterministic(self):
        again = generator.build_map(json.loads(json.dumps(FEATURES)), OFFSETS, POINTS, LAND)
        self.assertEqual(generator.encode(self.data), generator.encode(again))

    def test_shared_border_is_one_arc(self):
        shared = self.arcs_of('Test/West') & self.arcs_of('Test/East')
        self.assertEqual(len(shared), 1)
        arc = shared.pop()
        self.assertEqual(self.data['kinds'][arc], 2, 'a border between offsets')
        # The densely sampled straight border simplifies to its two ends.
        self.assertEqual(len(self.data['arcs'][arc]), 4)

    def test_enclave_shares_its_ring_with_the_hole(self):
        shared = self.arcs_of('Test/West') & self.arcs_of('Test/Enclave')
        self.assertEqual(len(shared), 1)
        self.assertEqual(self.data['kinds'][shared.pop()], 1, 'a border inside one offset')

    def test_orientation_and_bounds(self):
        arcs = []
        for encoded in self.data['arcs']:
            x = y = 0
            points = []
            for i in range(0, len(encoded), 2):
                x += encoded[i]
                y += encoded[i + 1]
                self.assertTrue(0 <= x <= generator.WIDTH and 0 <= y <= generator.HEIGHT)
                points.append((x, y))
            arcs.append(points)
        west = self.data['zones'][self.names.index('Test/West')]
        signs = []
        for refs in west:
            ring = []
            for ref in refs:
                path = arcs[ref] if ref >= 0 else arcs[~ref][::-1]
                ring.extend(path[1:] if ring else path)
            signs.append(generator.area(ring[:-1] if ring[0] == ring[-1] else ring) > 0)
        self.assertEqual(sorted(signs), [False, True], 'one outer ring, one hole')

    def test_offsets_land_and_dots(self):
        index = self.names.index('Test/Atoll')
        self.assertEqual((self.data['offsets'][index], self.data['summer'][index]), (345, 345))
        self.assertEqual([dot[0] for dot in self.data['dots']], [index], 'only the atoll needs a dot')
        self.assertEqual(len(self.data['land']), 1)
        self.assertEqual(self.data['points'], POINTS)
        self.assertEqual(self.names[0], 'UTC')

    def test_clipping(self):
        north = generator.build_map([feature('Test/North', square(0, 70, 10, 80))], {'Test/North': [0, 0]}, {}, [])
        ys = [north['arcs'][0][1]]
        self.assertGreaterEqual(min(ys), 0)
        far = generator.build_map([feature('Test/Pole', square(0, 80, 10, 85))], {'Test/Pole': [0, 0]}, {}, [])
        self.assertEqual(far['zones'][1], [], 'a zone wholly north of the map has no shape')


if __name__ == '__main__':
    unittest.main()
