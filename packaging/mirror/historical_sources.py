#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exact binary exceptions backed by pre-build Software Heritage Git snapshots."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile

# Reviewed visits are full Git snapshots. Annotated release objects resolve to
# the recorded revision; checkout-only directory snapshots are not evidence.
REVIEWED = [{'base': 'efivar',
  'version': '39-2',
  'recipe_sha256': '6cab4eabc3d5526c40f7434ad7f86bce475067a6dd41b232f9b6d2a8515faf82',
  'builddate': 1778329899,
  'origin': 'https://github.com/rhboot/efivar',
  'visit': 96,
  'visit_date': '2024-02-20T02:25:29.710000+00:00',
  'snapshot': 'd6171d5a223fc6d72391a9afb19a198703f305f4',
  'tag': '39',
  'tag_object': 'a77a4ffec000ad5dfc5d6394d208784672acda82',
  'commit': 'c47820c37ac26286559ec004de07d48d05f3308c',
  'visit_url': 'https://archive.softwareheritage.org/api/1/origin/https://github.com/rhboot/efivar/visit/96/',
  'snapshot_url': 'https://archive.softwareheritage.org/api/1/snapshot/d6171d5a223fc6d72391a9afb19a198703f305f4/',
  'release_url': 'https://archive.softwareheritage.org/api/1/release/a77a4ffec000ad5dfc5d6394d208784672acda82/',
  'binary_sha256': '9bfa2152203237d61f0e41819572e2fe19e9cf5613442b24f5f02573ab030e74',
  'kind': 'software-heritage-tag',
  'source': 'git+https://github.com/rhboot/efivar?signed#tag=39'},
 {'base': 'efibootmgr',
  'version': '18-4',
  'recipe_sha256': '11d8062bee2432de10af1185e06d3717f6a7d251e23e63a9116cbf14977c8dec',
  'builddate': 1778329978,
  'origin': 'https://github.com/rhboot/efibootmgr',
  'visit': 99,
  'visit_date': '2024-02-29T05:04:45.852000+00:00',
  'snapshot': '09dda5756e933e6c6dfc3a097cb994167de8ecec',
  'tag': '18',
  'tag_object': '712aeb81311de28a3fcfea7465dcb93743f07a53',
  'commit': 'c3f9f0534e32158f62c43564036878b93b9e0fd6',
  'visit_url': 'https://archive.softwareheritage.org/api/1/origin/https://github.com/rhboot/efibootmgr/visit/99/',
  'snapshot_url': 'https://archive.softwareheritage.org/api/1/snapshot/09dda5756e933e6c6dfc3a097cb994167de8ecec/',
  'release_url': 'https://archive.softwareheritage.org/api/1/release/712aeb81311de28a3fcfea7465dcb93743f07a53/',
  'binary_sha256': '1e6166d0024f3f0de37919a51f5c00981e42e6e087937858cf82f30edf71f17e',
  'kind': 'software-heritage-tag',
  'source': 'efibootmgr::git+https://github.com/rhboot/efibootmgr?signed#tag=18'},
 {'base': 'sysfsutils',
  'version': '2.1.1-2',
  'recipe_sha256': 'e3c6bbc3444b87ae33d81ce2945bae0903b287d37cc4565f62fbe4c2e786767c',
  'builddate': 1719817428,
  'origin': 'https://github.com/linux-ras/sysfsutils',
  'visit': 4,
  'visit_date': '2022-09-07T07:44:57.937000+00:00',
  'snapshot': 'a8be60d2dbed568fb92c084bc81560664322699b',
  'tag': 'v2.1.1',
  'tag_object': 'da2f1f8500c0af6663a56ce2bff07f67e60a92e0',
  'commit': 'da2f1f8500c0af6663a56ce2bff07f67e60a92e0',
  'visit_url': 'https://archive.softwareheritage.org/api/1/origin/https://github.com/linux-ras/sysfsutils/visit/4/',
  'snapshot_url': 'https://archive.softwareheritage.org/api/1/snapshot/a8be60d2dbed568fb92c084bc81560664322699b/',
  'release_url': None,
  'binary_sha256': 'f5a8f077128cc71c02fbfe8c555b479fed17dcc1e612378dba0fcef9464743c0',
  'kind': 'software-heritage-tag',
  'source': 'git+https://github.com/linux-ras/sysfsutils.git#tag=v2.1.1'}]

# A full Git visit before the binary build binds this unsigned release tag.
REVIEWED.append({
    'base': 'screen', 'version': '5.0.2-2',
    'recipe_sha256': '6f50333e7fec57c807a8fcb6015dd153b3998c6ca2c02b20fc3e0e472dbd3a5e',
    'binary_sha256': '29549ef3b8e3ad472a526f6ebd112ea1c4e7289642902cd182c3bdcb83c278c1',
    'builddate': 1786331766,
    'origin': 'https://git.savannah.gnu.org/git/screen.git',
    'visit': 524, 'visit_date': '2026-07-21T02:28:49.997000+00:00',
    'snapshot': 'b5644c61ab93f6f1858a872b45aa23d59b9f4a0f',
    'tag': 'v.5.0.2', 'tag_object': 'b4d92ea827325c17969e8de0ca341d5f55de55ab',
    'commit': 'e9206eff9c9c97bdae4809c97c801e221fcf641c',
    'visit_url': 'https://archive.softwareheritage.org/api/1/origin/https://git.savannah.gnu.org/git/screen.git/visit/524/',
    'snapshot_url': 'https://archive.softwareheritage.org/api/1/snapshot/b5644c61ab93f6f1858a872b45aa23d59b9f4a0f/',
    'release_url': 'https://archive.softwareheritage.org/api/1/release/b4d92ea827325c17969e8de0ca341d5f55de55ab/',
    'kind': 'software-heritage-tag',
    'source': 'git+https://git.savannah.gnu.org/git/screen.git#tag=v.5.0.2',
})



def exception(record):
    """A binary hash binds the independently read BUILDDATE without schema changes."""
    keys = ('base', 'version', 'recipe_sha256', 'binary_sha256')
    for reviewed in REVIEWED:
        if all(record.get(key) == reviewed[key] for key in keys):
            visit = datetime.fromisoformat(reviewed['visit_date'])
            if visit.tzinfo is None or visit.timestamp() >= reviewed['builddate']:
                raise ValueError('historical Git visit does not predate the binary build')
            return deepcopy(reviewed)
    return None


def allows_tag(record, source):
    reviewed = exception(record)
    return reviewed is not None and reviewed['source'] == source


def prepare(text, record, downloads, run):
    """Compare today's exact tag object and commit before makepkg can use it."""
    reviewed = exception(record)
    if reviewed is None:
        return []
    sources = [line.split(' = ', 1)[1].strip() for line in text.splitlines()
               if ' = ' in line and line.split(' = ', 1)[0].strip().startswith('source')]
    if reviewed['source'] not in sources:
        raise ValueError('historical Git source differs from the reviewed recipe')
    source = reviewed['source'].split('::', 1)[-1]
    url = source.removeprefix('git+').split('#', 1)[0].split('?', 1)[0]
    name = (reviewed['source'].split('::', 1)[0] if '::' in reviewed['source'] else
            url.rstrip('/').rsplit('/', 1)[-1].removesuffix('.git'))
    destination = downloads / name
    if not destination.exists():
        run('git', 'init', '--bare', str(destination))
    run('git', '-C', str(destination), 'config', 'remote.origin.url', url)
    ref = 'refs/tags/' + reviewed['tag']
    run('git', '-C', str(destination), 'fetch', '--force', '--no-tags', url, ref + ':' + ref)
    for suffix, expected in (('', reviewed['tag_object']), ('^{commit}', reviewed['commit'])):
        actual = run('git', '-C', str(destination), 'rev-parse', '--verify', ref + suffix).strip()
        if actual != expected:
            raise ValueError('current Git tag differs from the pre-build historical snapshot')
    run('git', '-C', str(destination), 'update-ref', 'refs/heads/emaki-source', reviewed['commit'])
    run('git', '-C', str(destination), 'symbolic-ref', 'HEAD', 'refs/heads/emaki-source')
    run('git', '-C', str(destination), 'fsck', '--full', '--no-reflogs')
    return [reviewed]


def validate_git_archive(source_path, prefix, tag, commit, tag_object=None):
    """Validate self-contained archived Git objects without trusting Git config."""
    with tempfile.TemporaryDirectory(prefix='emaki-historical-git-') as temporary:
        root = Path(temporary)
        seen = set()
        with tarfile.open(source_path, 'r:*') as bundle:
            for member in bundle.getmembers():
                path = PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts:
                    raise ValueError('unsafe historical Git archive member')
                normalized = str(path)
                if not normalized.startswith(prefix.rstrip('/') + '/'):
                    continue
                relative = normalized[len(prefix.rstrip('/')) + 1:]
                if relative in seen or not (member.isdir() or member.isfile()):
                    raise ValueError('unsafe or duplicate historical Git member')
                seen.add(relative)
                target = root / relative
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(bundle.extractfile(member).read())
        if ('objects/info/alternates' in seen or 'objects/info/http-alternates' in seen or
                'shallow' in seen or 'info/grafts' in seen or
                any(name.startswith('refs/replace/') or name.endswith('.promisor') for name in seen) or
                not (root / 'objects').is_dir()):
            raise ValueError('historical Git archive is not self-contained')
        (root / 'config').write_text('[core]\n\tbare = true\n\trepositoryformatversion = 0\n')
        def git(*args):
            result = subprocess.run(['git', '--no-replace-objects', '--git-dir', str(root), *args],
                                    text=True, capture_output=True)
            if result.returncode:
                raise ValueError('archived historical Git objects failed verification')
            return result.stdout.strip()
        ref = 'refs/tags/' + tag
        if git('rev-parse', '--verify', ref + '^{commit}') != commit:
            raise ValueError('archived Git tag differs from the historical commit')
        if tag_object is not None and git('rev-parse', '--verify', ref) != tag_object:
            raise ValueError('archived Git tag object differs from the historical snapshot')
        git('fsck', '--full', '--no-reflogs')


def validate(source_path, record):
    reviewed = exception(record)
    routes = [route for route in record.get('source_routes', [])
              if route.get('kind') == 'software-heritage-tag']
    if reviewed is None:
        if routes:
            raise ValueError('historical Git evidence has no reviewed binary exception')
        return
    if routes != [reviewed]:
        raise ValueError('historical Git evidence differs from the reviewed pre-build visit')
    pin = {'source': reviewed['source'], 'kind': 'tag', 'reference': reviewed['tag'],
           'commit': reviewed['commit']}
    if record.get('git_sources') != [pin]:
        raise ValueError('historical Git pin differs from the reviewed snapshot')
    validate_git_archive(source_path, record['base'] + '/' + record['base'],
                         reviewed['tag'], reviewed['commit'], reviewed['tag_object'])
