#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture the production storefront with isolated deterministic local fixtures."""
import argparse
import json
import os
import struct
from pathlib import Path
import interactions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shots', type=Path, default=Path('/tmp/emaki-storefront'))
    parser.add_argument('--match', action='append', default=[], help='Render cases containing this name fragment')
    parser.add_argument('--quick', action='store_true', help='Render one populated case')
    args = parser.parse_args()
    os.nice(19)
    args.shots.mkdir(parents=True, exist_ok=True)
    cases = [(scale, theme, availability, installed, 1180, 780)
             for scale in (1, 2) for theme in ('light', 'dark')
             for availability in ('cached-artwork', 'cached-offline', 'no-appstream')
             for installed in ('empty', 'typical')]
    cases += [(1, theme, 'cached-artwork', 'typical', width, height)
              for width, height in ((960, 600), (206, 342)) for theme in ('light', 'dark')]
    if args.quick:
        cases = [(1, 'light', 'cached-artwork', 'typical', 1180, 780)]
    # Keep an installed inventory available when the AppStream catalog is absent.
    interactions.PROGRAMS = interactions.PROGRAMS.replace(
        'allRows.filter(row => row.kind !== "aur")',
        'allRows.filter(row => row.kind !== "aur" && !row.storefrontInventoryOnly)')
    records = []
    for scale, theme, availability, installed, width, height in cases:
        name = f'{theme}-scale{scale}-{availability}-{installed}-{width}'
        if args.match and not any(fragment in name for fragment in args.match):
            continue
        print('Rendering', name, flush=True)
        interactions.run(width, height, entry='StorefrontTest.qml', timeout=45,
                         extra={'TEST_SHOTS': str(args.shots.resolve()), 'TEST_STORE_CASE': name,
                                'TEST_STORE_AVAILABILITY': availability, 'TEST_STORE_INSTALLED': installed,
                                'TEST_STORE_THEME': theme, 'QT_SCALE_FACTOR': str(scale)},
                         allow_restore_binding_warning=True)
        for suffix in ('top', 'lower', 'restored'):
            png = (args.shots / f'{name}-{suffix}.png').read_bytes()
            assert png[:8] == b'\x89PNG\r\n\x1a\n'
            assert struct.unpack('>II', png[16:24]) == (width * scale, height * scale)
        records.append({'case': name, 'scale': scale, 'theme': theme, 'availability': availability,
                        'installed': installed, 'logicalSize': [width, height],
                        'images': [name + '-' + suffix + '.png' for suffix in ('top', 'lower', 'restored')]})
    if args.match and (args.shots / 'manifest.json').exists():
        prior = json.loads((args.shots / 'manifest.json').read_text())['cases']
        replaced = {record['case'] for record in records}
        records = [record for record in prior if record['case'] not in replaced] + records
    (args.shots / 'manifest.json').write_text(json.dumps({
        'notice': 'Isolated production QML renders; catalog and artwork are synthetic test fixtures, '
                  'not live Flathub content. No network requests are made. Cached-artwork supplies a '
                  'local illustration; cached-offline supplies missing local artwork; no-appstream '
                  'supplies no catalog. These represent availability states, not a network integration test.',
        'date': '2026-11-01', 'renderer': 'Qt offscreen software', 'cases': records}, indent=2) + '\n')


if __name__ == '__main__':
    main()
