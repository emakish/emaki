#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Upgrade acceptance coverage for the images already released to users."""
import json
import re

# These images predate mirror release records, but their users still need upgrades.
LEGACY_STARTS = ('0.1.0', '0.1.1')
REQUIRED_RUNS = ('T1', 'T2')
REQUIRED_SIZES = ('1920x1080', '2560x1600')
PREFIX = 'released/iso/'


def required_starts(backend, manifest_sha):
    """Require every released image except the candidate's own immutable snapshot.

    The exclusion keeps first image publication and retries from requiring an upgrade
    from the image being published. A different candidate must cover that image.
    Read records on each check: a stamp written before another image was released
    must not allow a later promotion to skip its upgrade coverage.
    """
    if not isinstance(manifest_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', manifest_sha):
        raise ValueError('the acceptance candidate has no valid MANIFEST digest')
    starts = set(LEGACY_STARTS)
    for key in backend.list(PREFIX):
        if not isinstance(key, str) or not re.fullmatch(r'released/iso/\d+\.\d+\.\d+', key):
            raise ValueError(f'invalid published image record key: {key!r}')
        data, _ = backend.get(key)
        try:
            record = json.loads(data)
        except (TypeError, ValueError, UnicodeError) as error:
            raise ValueError(f'invalid published image record: {key}') from error
        digest = record.get('manifest') if isinstance(record, dict) else None
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError(f'published image record has no valid MANIFEST digest: {key}')
        if digest != manifest_sha:
            starts.add(key[len(PREFIX):])
    return tuple(sorted(starts, key=lambda version: tuple(map(int, version.split('.')))))


def validate_runs(stamp_runs, starts):
    """Validate the existing stamp's run strings against the current required starts."""
    if not isinstance(stamp_runs, (list, tuple)) or any(not isinstance(run, str) for run in stamp_runs):
        raise ValueError('the acceptance stamp has no valid run list')
    green = set(stamp_runs)
    missing = [f'{run} from {start} at {size}'
               for run in REQUIRED_RUNS for start in starts for size in REQUIRED_SIZES
               if f'{run} {start} {size}' not in green]
    if missing:
        raise ValueError('the acceptance is not green for: ' + ', '.join(missing))
