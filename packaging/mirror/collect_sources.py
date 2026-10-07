#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Resume parallel Arch source collection from an extracted image repository."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import iso_sources


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


# Published next to the image for each refused base. The full error, with internal paths,
# stays in the job's collect.log.
PLAIN_REASONS = (
    ('timed out', 'The upstream server was unreachable and the Software Heritage archive '
                  'did not deliver a copy in time.'),
    ('Software Heritage', 'The upstream server was unreachable and the Software Heritage archive '
                          'has no exact copy.'),
    ('source key', 'The source signing key did not match the Arch build recipe.'),
    ('gitlinks', 'A source submodule could not be verified against the Arch build recipe.'),
    ('makepkg', 'The source download failed.'),
)


def plain_reason(log):
    try:
        text = log.read_text(errors='replace')[-8000:]
    except OSError:
        text = ''
    for marker, reason in PLAIN_REASONS:
        if marker in text:
            return reason
    return 'The source could not be fetched yet.'


def collect_one(identity, records, packages, output, repositories, cook_swh_vault=False):
    base, recipe = identity
    # The digest makes arbitrary metadata safe as a directory name.
    key = hashlib.sha256((base + '\0' + recipe).encode()).hexdigest()
    job = output / 'jobs' / key
    job.mkdir(parents=True, exist_ok=True)
    closure = job / 'closure.txt'
    closure.write_text(''.join(name + '\n' for name in sorted(records)))
    manifest = job / 'ARCH-SOURCES.json'
    log = job / 'collect.log'
    start = time.monotonic()
    result = {'base': base, 'recipe_sha256': recipe, 'log': str(log.relative_to(output)),
              'packages': sorted(records), 'status': 'refused', 'bytes': 0}
    try:
        try:
            saved, objects = iso_sources.validate(manifest, closure, packages)
            result['status'] = 'resumed'
        except (OSError, ValueError, KeyError, TypeError):
            command = [sys.executable, str(Path(iso_sources.__file__).resolve()),
                       '--closure', str(closure), '--packages', str(packages),
                       '--output', str(job)]
            if repositories is not None:
                command.extend(['--repositories', str(repositories)])
            if cook_swh_vault:
                command.append('--cook-swh-vault')
            with log.open('a') as stream:
                stream.write(f'\nCollection attempt for {base} ({recipe})\n')
                stream.flush()
                process = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT)
            if process.returncode:
                raise ValueError(f'collector exited {process.returncode}; see {result["log"]}')
            saved, objects = iso_sources.validate(manifest, closure, packages)
            result['status'] = 'collected'
        for name, record in saved.items():
            if any(record.get(field) != value for field, value in records[name].items()):
                raise ValueError('cached source record differs from current image inventory')
        for relative, source in objects.items():
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists() or iso_sources.digest(target) != iso_sources.digest(source):
                temporary = target.with_suffix(target.suffix + '.tmp')
                temporary.unlink(missing_ok=True)
                try:
                    os.link(source, temporary)
                except OSError:
                    shutil.copyfile(source, temporary)
                temporary.replace(target)
        result['bytes'] = sum(path.stat().st_size for path in objects.values())
        result['records'] = saved
    except (OSError, ValueError, KeyError, TypeError) as error:
        result['status'] = 'refused'
        result['reason'] = str(error)
    result['seconds'] = round(time.monotonic() - start, 3)
    return result


def collect_all(closure, packages, output, jobs, repositories=None, cook_swh_vault=False):
    if jobs < 1:
        raise ValueError('jobs must be positive')
    closure, packages, output = (Path(path).resolve() for path in (closure, packages, output))
    repositories = Path(repositories).resolve() if repositories else None
    output.mkdir(parents=True, exist_ok=True)
    with (output / '.collection.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('another collection is using this output directory') from error
        final = output / 'ARCH-SOURCES.json'
        final.unlink(missing_ok=True)
        missing = output / 'MISSING-SOURCES.json'
        missing.unlink(missing_ok=True)
        root = Path(__file__).resolve().parents[1]
        exclude = {path.parent.name for path in root.glob('*/PKGBUILD')}
        selected = iso_sources.inventory(closure, packages, exclude)
        groups = {}
        for filename, record in selected.items():
            groups.setdefault((record['base'], record['recipe_sha256']), {})[filename] = record
        summary = {'closure_sha256': iso_sources.digest(closure), 'complete': False,
                   'jobs': jobs, 'results': []}
        atomic_json(output / 'summary.json', summary)
        records = {}
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            pending = [pool.submit(collect_one, identity, group, packages, output, repositories,
                                   cook_swh_vault)
                       for identity, group in sorted(groups.items())]
            for future in as_completed(pending):
                result = future.result()
                records.update(result.pop('records', {}))
                summary['results'].append(result)
                summary['results'].sort(key=lambda item: (item['base'], item['recipe_sha256']))
                summary['seconds'] = round(time.monotonic() - started, 3)
                atomic_json(output / 'summary.json', summary)
                print(f'{result["base"]}: {result["status"]} '
                      f'({result["bytes"]} bytes, {result["seconds"]} s)', flush=True)
        refused = [item for item in summary['results'] if item['status'] == 'refused']
        missing_candidate = output / 'MISSING-SOURCES.pending.json'
        if refused:
            bases = []
            for item in refused:
                identity = (item['base'], item['recipe_sha256'])
                group = groups[identity]
                versions = {record['version'] for record in group.values()}
                if len(versions) != 1:
                    raise ValueError('refused source base has inconsistent package versions')
                # PKGINFO carries the upstream URL from the binary's build recipe. Split packages
                # of one base can name different pages (lvm2, samba): then the Arch recipe is
                # the only pointer, never a refusal of the whole partial result.
                urls = {url for name in group for url in
                        iso_sources.package_metadata(packages / name).get('url', [])}
                entry = {'base': item['base'], 'version': versions.pop(),
                         'recipe_sha256': item['recipe_sha256'], 'packages': group,
                         'reason': plain_reason(output / item['log']),
                         'upstream_url': urls.pop() if len(urls) == 1 else ''}
                key = hashlib.sha256(('\0'.join(identity)).encode()).hexdigest()
                evidence = output / 'jobs' / key / 'RECIPE.json'
                if evidence.exists():
                    recipe = json.loads(evidence.read_text())
                    if (recipe.get('base'), recipe.get('recipe_sha256')) != identity:
                        raise ValueError('saved recipe metadata differs from refused source base')
                    if not entry['upstream_url']:
                        entry['upstream_url'] = recipe.get('upstream_url') or ''
                    elif recipe.get('upstream_url') not in ('', None, entry['upstream_url']):
                        raise ValueError('saved recipe upstream URL differs from image binary')
                    entry['revision'] = recipe['revision']
                bases.append(entry)
            atomic_json(missing_candidate, {'schema': 1,
                        'closure_sha256': iso_sources.digest(closure), 'bases': bases})
        candidate = output / 'ARCH-SOURCES.pending.json'
        atomic_json(candidate, {'schema': 1, 'closure_sha256': iso_sources.digest(closure),
                                'packages': records})
        # Read the actual binaries again; a completed job is not sufficient evidence.
        iso_sources.validate(candidate, closure, packages, exclude,
                             missing_sources=missing_candidate if refused else None)
        if refused:
            missing_candidate.replace(missing)
        candidate.replace(final)
        summary['complete'] = not refused
        summary['seconds'] = round(time.monotonic() - started, 3)
        atomic_json(output / 'summary.json', summary)
        return summary['complete']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--closure', type=Path, required=True)
    parser.add_argument('--packages', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=2)
    parser.add_argument('--repositories', type=Path)
    parser.add_argument('--cook-swh-vault', action='store_true',
                        help='allow remote Software Heritage vault cooking requests (not read-only)')
    args = parser.parse_args()
    try:
        complete = collect_all(args.closure, args.packages, args.output, args.jobs, args.repositories,
                               args.cook_swh_vault)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f'{error}\n')
    if not complete:
        parser.exit(1, f'Incomplete collection; see {args.output / "summary.json"}\n')


if __name__ == '__main__':
    main()
