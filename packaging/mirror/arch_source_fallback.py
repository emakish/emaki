#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reviewed complete Arch source archives for two exact binary recipes."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile

import source_routes

# Raw downloads and their metadata-independent regular-file inventories were
# inspected together. Normalization can change tar headers, never source bytes.
REVIEWED = {
    'jack2': ('1.9.22-2',
              'bcf71099497fd7ab372f2f9504b3e1e520ca17f5b64cf56ad26058f539583695',
              '58127c4f9f2a83c0bda26f8f15a7ece9c29436ce1382c058bff515cd8e547d46',
              '3a3046b37185e68707c534ab8e83085d41d429d2ff10293819f55426ee7cb024',
              'a1598eb10db44e958b4eada76cf40e85b9c3fc5b384f895e158bd83d4e2e202a'),
    'libfakekey': ('0.3-4',
                   '96586d210de223e23f70bb16e54526aeb4ba4affc758437cd9f0f2742e6a6da6',
                   '0f2a098a99c3fe2a692fe7b823f43c21db252077329e63556444b0ce9ac952d5',
                   'c3a1ac8e72e3e54fbe65a87346bc1c30f9ef3415b4ba4d3bad31becb19685c8d',
                   '53885a2bd8b376c4ff29c16c2abd23b16b7d937b9790ed4ce969734997c935e6'),
}
TAG = '80149e552b56d6d57d754dc04d119b8170d27313'
COMMIT = '4f58969432339a250ce87fe855fb962c67d00ddb'
PRIMARY = '62B11043D2F6EB6672D93103CDBAA37ABC74FBA0'
SOURCE = 'git+https://github.com/jackaudio/jack2#tag=' + TAG + '?signed'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def structured_digest(value):
    return digest(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def eligible(record):
    reviewed = REVIEWED.get(record.get('base'))
    return bool(reviewed and (record.get('version'), record.get('recipe_sha256')) == reviewed[:2])


def expected_pins(record):
    return ([{'source': SOURCE, 'kind': 'tag', 'reference': TAG, 'commit': COMMIT}]
            if record['base'] == 'jack2' else [])


def evidence(record):
    if not eligible(record):
        raise ValueError('unreviewed Arch source archive exception')
    version, recipe, raw, metadata, contents = REVIEWED[record['base']]
    result = {'kind': 'arch-source-package', 'base': record['base'], 'version': version,
              'recipe_sha256': recipe,
              'url': 'https://sources.archlinux.org/sources/packages/' + record['base'] + '-' + version + '.src.tar.gz',
              'archive_sha256': raw, 'metadata_sha256': metadata, 'contents_sha256': contents,
              'git_sources': expected_pins(record)}
    if record['base'] == 'jack2':
        result['signed_tag'] = {'object': TAG, 'primary': PRIMARY}
    return result


def allows_tag(record, source):
    return (eligible(record) and source == SOURCE and record['base'] == 'jack2'
            and any(route_identity(route) == evidence(record) for route in record.get('source_routes', [])))


def route_identity(route):
    # The serving URL is observational provenance, not an integrity exception.
    return {key: value for key, value in route.items() if key != 'effective_url'}


def local_run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, **kwargs).stdout


def inspect_archive(path, record, destination, run=local_run):
    """Extract only regular files/directories after validating the complete inventory."""
    reviewed = REVIEWED[record['base']]
    files = {}
    names = set()
    with tarfile.open(path, 'r:*') as archive:
        for member in archive:
            name = member.name.rstrip('/')
            parts = PurePosixPath(name).parts
            if (not parts or parts[0] != record['base'] or name.startswith('/')
                    or '..' in parts or str(PurePosixPath(name)) != name
                    or name in names or not (member.isfile() or member.isdir())):
                raise ValueError('unsafe or duplicate Arch source archive member')
            names.add(name)
            if member.isfile():
                files[name] = archive.extractfile(member).read()
    base = record['base'] + '/'
    if digest(files.get(base + 'PKGBUILD', b'')) != record['recipe_sha256']:
        raise ValueError('Arch source archive recipe differs from binary')
    info = source_routes.fields(files.get(base + '.SRCINFO', b'').decode())
    if structured_digest(info) != reviewed[3]:
        raise ValueError('Arch source archive metadata differs from reviewed recipe')
    content = {name: digest(data) for name, data in files.items()
               if name not in (base + 'PKGBUILD', base + '.SRCINFO')}
    if structured_digest(content) != reviewed[4]:
        raise ValueError('Arch source archive contents differ from reviewed download')
    for name in sorted(names - files.keys()):
        (destination / name).mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for key, sources in info.items():
        if key != 'source' and not key.startswith('source_'):
            continue
        suffix = key[len('source'):]
        for index, source in enumerate(sources):
            git = source.startswith(('git+', 'git://'))
            filename = 'jack2' if git else source_routes.source_name(source)
            target = destination / record['base'] / filename
            if not target.exists():
                raise ValueError('Arch source archive omits a declared source')
            for algorithm in ('b2', 'sha512', 'sha384', 'sha256', 'sha224', 'sha1', 'md5'):
                checksums = info.get(algorithm + 'sums' + suffix)
                if checksums is not None:
                    if len(checksums) != len(sources):
                        raise ValueError('Arch source archive checksum count differs')
                    if checksums[index] != 'SKIP':
                        source_routes.verify_checksum(target, algorithm, checksums[index])
            if git:
                if source != SOURCE or (target / 'objects/info/alternates').exists():
                    raise ValueError('unreviewed or external Git source archive')
                run('git', '--no-replace-objects', '-C', str(target), 'fsck', '--full')
                commit = run('git', '--no-replace-objects', '-C', str(target),
                             'rev-parse', '--verify', TAG + '^{commit}').strip()
                if commit != COMMIT:
                    raise ValueError('Arch source archive Git tag differs')
    return info


def collect(record, original_recipe, recipe_info, root, run, *, recipe_dir=None):
    route = evidence(record)
    if digest(original_recipe) != record['recipe_sha256']:
        raise ValueError('Arch fallback recipe differs from binary')
    if structured_digest(source_routes.fields(recipe_info)) != route['metadata_sha256']:
        raise ValueError('Arch fallback derived metadata differs from reviewed recipe')
    root = Path(root)
    complete = root / 'arch-complete.src.tar.gz'
    route['effective_url'] = source_routes.download(route['url'], complete, run)
    if digest(complete.read_bytes()) != route['archive_sha256']:
        raise ValueError('Arch source archive download differs from reviewed SHA256')
    with tempfile.TemporaryDirectory(dir=root, prefix='arch-verify-') as temporary:
        destination = Path(temporary)
        info = inspect_archive(complete, record, destination, run)
        if record['base'] == 'jack2':
            if recipe_dir is None:
                raise ValueError('signed Arch source tag requires recipe public keys')
            home = destination / 'gnupg'
            home.mkdir(mode=0o700)
            (home / 'gpg.conf').write_text('no-autostart\nno-auto-key-retrieve\n')
            env = dict(os.environ, GNUPGHOME=str(home))
            for key in sorted((Path(recipe_dir) / 'keys/pgp').glob('*.asc')):
                run('gpg', '--batch', '--no-autostart', '--import', str(key), env=env)
            # verify-tag writes machine-readable status to stderr.
            verified = subprocess.run(['git', '--no-replace-objects', '-C',
                                       str(destination / 'jack2/jack2'), 'verify-tag', '--raw', TAG],
                                      env=env, text=True, capture_output=True, check=True)
            statuses = [line.split() for line in verified.stderr.splitlines()
                        if line.startswith('[GNUPG:] VALIDSIG ')]
            if (len(statuses) != 1 or len(statuses[0]) != 12
                    or statuses[0][-1] != PRIMARY or PRIMARY not in info['validpgpkeys']):
                raise ValueError('Arch source tag lacks the recipe allowed primary signature')
    return complete, route, expected_pins(record)


def validate(source_path, record):
    routes = [route for route in record.get('source_routes', [])
              if route.get('kind') == 'arch-source-package']
    if not routes:
        return
    if ([route_identity(route) for route in routes] != [evidence(record)]
            or record.get('git_sources', []) != expected_pins(record)):
        raise ValueError('Arch source archive evidence differs from reviewed exception')
    with tempfile.TemporaryDirectory(prefix='emaki-arch-validate-') as temporary:
        inspect_archive(source_path, record, Path(temporary))
