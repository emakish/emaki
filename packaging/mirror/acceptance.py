#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Functional upgrade coverage for the systems that really exist (machines.toml).

This validates named run coverage, not screenshot contents. The upgrade-check
frames require human review; a complete run list or publication stamp does not
establish visual acceptance. Review the live desktop, upgrade prompts, terminal
results and rebooted desktop in each run's evidence before release signoff.
"""
import datetime
import json
from pathlib import Path
import re
import tomllib

# These images predate mirror release records; a machine may still name them.
PRE_RECORD_IMAGES = ('0.1.0', '0.1.1')
REQUIRED_RUNS = ('T1', 'T2')
REQUIRED_SIZES = ('1920x1080', '2560x1600')
PREFIX = 'released/iso/'
MACHINES = Path(__file__).resolve().parent / 'machines.toml'
VERSION_RE = r'\d+\.\d+\.\d+'


def version_key(version):
    return tuple(map(int, version.split('.')))


def released_records(backend):
    """{version: record} of every image recorded under released/iso/. A record may carry
    `"launch": true`: `iso` writes it once, for the image the machines file names as the launch."""
    records = {}
    for key in backend.list(PREFIX):
        if not isinstance(key, str) or not re.fullmatch(re.escape(PREFIX) + VERSION_RE, key):
            raise ValueError(f'invalid published image record key: {key!r}')
        data, _ = backend.get(key)
        try:
            record = json.loads(data)
        except (TypeError, ValueError, UnicodeError) as error:
            raise ValueError(f'invalid published image record: {key}') from error
        digest = record.get('manifest') if isinstance(record, dict) else None
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError(f'published image record has no valid MANIFEST digest: {key}')
        if record.get('launch', True) is not True:
            raise ValueError(f'published image record has an invalid launch mark: {key}')
        records[key[len(PREFIX):]] = record
    return records


def released_images(backend):
    """{version: MANIFEST digest} of every image recorded under released/iso/."""
    return {version: record['manifest'] for version, record in released_records(backend).items()}


def launched(records, launch, path):
    """The launch image the records name, or None while it is not published.

    The records are written once, so they, not the checked-in file, say whether the launch
    happened: while a record carries the mark, a `launch` that is empty, earlier, later or
    invented refuses; so does a `launch` naming an image that was published without the mark."""
    marked = sorted(version for version, record in records.items() if record.get('launch') is True)
    if len(marked) > 1:
        raise ValueError('more than one released/iso/ record carries the launch mark: ' + ', '.join(marked))
    if marked:
        if launch != marked[0]:
            raise ValueError(f'{path}: launch is {launch or ""!r}, but released/iso/{marked[0]} records the '
                             'public launch; the launch marker is never removed or moved')
        return marked[0]
    if launch in records:
        raise ValueError(f'{path}: launch {launch} names an image published without the launch mark; the '
                         'launch is recorded only when its image is published with launch already set')
    return None


def load_machines(path):
    """(launch version or None, [installed_from, ...]) from the machines file; any doubt refuses."""
    try:
        data = tomllib.loads(Path(path).read_text())
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(f'cannot read the machines file {path}: {error}') from None
    if set(data) - {'launch', 'machine'} or not isinstance(data.get('launch'), str):
        raise ValueError(f'{path}: expected `launch = ""` or a version, and [[machine]] tables only')
    launch = data['launch'] or None
    if launch is not None and not re.fullmatch(VERSION_RE, launch):
        raise ValueError(f'{path}: launch {launch!r} is not a version')
    machines = data.get('machine', [])
    if not isinstance(machines, list):
        raise ValueError(f'{path}: machine must be a [[machine]] table list')
    versions = []
    for machine in machines:
        if (not isinstance(machine, dict) or set(machine) != {'name', 'installed_from', 'installed'}
                or not isinstance(machine['name'], str) or not machine['name'].strip()
                or not isinstance(machine['installed_from'], str)
                or not re.fullmatch(VERSION_RE, machine['installed_from'])
                or type(machine['installed']) is not datetime.date):
            raise ValueError(f'{path}: every [[machine]] has exactly a name, installed_from = "X.Y.Z" '
                             f'and installed = YYYY-MM-DD; got {machine!r}')
        versions.append(machine['installed_from'])
    return launch, versions


def launch_mark(backend, version, machines=MACHINES):
    """Whether `iso` marks the record of the image it publishes as the launch image: the machines
    file names this image as the launch and the records do not contradict it."""
    launch, _ = load_machines(machines)
    return version == (launched(released_records(backend), launch, machines) or launch)


def required_starts(backend, manifest_sha, machines=MACHINES):
    """Before the launch, the current released image and the oldest image a real machine was
    installed from; after it, every image published since the launch (DECISIONS 2026-10-10).
    Never the candidate's own image.

    The exclusion keeps first image publication and retries from requiring an upgrade
    from the image being published. A different candidate must cover that image; for the
    same reason the launch image's own candidate keeps the rule from before the launch, but
    only while no later image is published: re-testing it after that needs every later image.
    Read records on each check: a stamp written before another image was released
    must not allow a later promotion to skip its upgrade coverage.
    """
    if not isinstance(manifest_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', manifest_sha):
        raise ValueError('the acceptance candidate has no valid MANIFEST digest')
    records = released_records(backend)
    images = {version: record['manifest'] for version, record in records.items()}
    launch, installed = load_machines(machines)
    for version in installed:
        if version not in images and version not in PRE_RECORD_IMAGES:
            raise ValueError(f'{machines} names {version}, an image that was never released')
    since = launched(records, launch, machines)
    if (since is not None and images[since] == manifest_sha
            and all(version_key(version) <= version_key(since) for version in images)):
        since = None
    others = [version for version, digest in images.items() if digest != manifest_sha]
    if since is not None:
        starts = {version for version in others if version_key(version) >= version_key(since)}
    else:
        installed = [version for version in installed if images.get(version) != manifest_sha]
        starts = set()
        if others:
            starts.add(max(others, key=version_key))
        if installed:
            starts.add(min(installed, key=version_key))
    if not starts:
        raise ValueError('no upgrade start is required: no released image and no machine in '
                         f'{machines}; acceptance would prove nothing')
    return tuple(sorted(starts, key=version_key))


def plan(starts):
    """Every required (run, start, size), in the order the stamp names them."""
    return [(run, start, size) for run in REQUIRED_RUNS for start in starts for size in REQUIRED_SIZES]


def validate_runs(stamp_runs, starts):
    """Validate the existing stamp's run strings against the current required starts.

    Runs beyond the required ones are allowed; a missing required one refuses."""
    if not isinstance(stamp_runs, (list, tuple)) or any(not isinstance(run, str) for run in stamp_runs):
        raise ValueError('the acceptance stamp has no valid run list')
    green = set(stamp_runs)
    missing = [f'{run} from {start} at {size}' for run, start, size in plan(starts)
               if f'{run} {start} {size}' not in green]
    if missing:
        raise ValueError('the acceptance is not green for: ' + ', '.join(missing))
