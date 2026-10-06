#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Collect exact Arch source archives in a disposable, unprivileged Arch build environment."""
import argparse
import hashlib
import gzip
import json
import os
import posixpath
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tarfile


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs).stdout


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fields(text):
    result = {}
    for line in text.splitlines():
        if ' = ' in line and not line.startswith('#'):
            key, value = line.strip().split(' = ', 1)
            result.setdefault(key, []).append(value)
    return result


def package_metadata(path):
    info = fields(run('bsdtar', '-xOf', str(path), '.PKGINFO'))
    for key in ('pkgname', 'pkgbase', 'pkgver'):
        if key == 'pkgbase' and key not in info:
            info[key] = info.get('pkgname', [])
        if len(info.get(key, [])) != 1:
            raise ValueError(f'{path.name}: missing or ambiguous {key}')
    if not info.get('license'):
        raise ValueError(f'{path.name}: missing licence metadata; inspect before publishing')
    return info


def copyleft(info):
    return any(re.search(r'(?:^|[^A-Za-z])(?:A?GPL|LGPL)', item, re.I)
               for item in info['license'])


def inventory(closure, packages, exclude=()):
    names = Path(closure).read_text().splitlines()
    if not names or len(names) != len(set(names)):
        raise ValueError('empty or duplicate image package list')
    selected = {}
    for name in names:
        if Path(name).name != name or not name.endswith('.pkg.tar.zst'):
            raise ValueError('invalid image package filename')
        path = Path(packages) / name
        if name.rsplit('-', 3)[0] in exclude:
            continue
        info = package_metadata(path)
        if info['pkgname'][0] not in exclude and copyleft(info):
            build = fields(run('bsdtar', '-xOf', str(path), '.BUILDINFO'))
            recipe = build.get('pkgbuild_sha256sum', [])
            if len(recipe) != 1 or not re.fullmatch('[0-9a-f]{64}', recipe[0]):
                raise ValueError(f'{name}: no exact build recipe hash')
            selected[name] = {'name': info['pkgname'][0], 'base': info['pkgbase'][0],
                              'version': info['pkgver'][0], 'binary_sha256': digest(path),
                              'recipe_sha256': recipe[0]}
    return selected


def exact_revision(repository, expected):
    """Match the recipe used by the binary; version tags are not a trust anchor."""
    revisions = run('git', '-C', str(repository), 'log', '--all', '--format=%H', '--', 'PKGBUILD')
    for revision in revisions.splitlines():
        recipe = subprocess.run(['git', '-C', str(repository), 'show', f'{revision}:PKGBUILD'],
                                check=True, capture_output=True).stdout
        if hashlib.sha256(recipe).hexdigest() == expected:
            return revision
    raise ValueError(f'{repository.name}: exact binary build recipe is absent from packaging history')


def check_recipe(text, record):
    info = fields(text)
    version = f"{info['pkgver'][0]}-{info['pkgrel'][0]}"
    if info.get('epoch', ['0'])[0] != '0':
        version = info['epoch'][0] + ':' + version
    if version != record['version'] or record['name'] not in info.get('pkgname', []):
        raise ValueError('packaging revision does not match the binary name and version')
    # A recipe hash alone cannot bind a moving branch or an unchecked download.
    for key, values in info.items():
        if key != 'source' and not key.startswith('source_'):
            continue
        suffix = key[len('source'):]
        sums = [info.get(algorithm + 'sums' + suffix, [])
                for algorithm in ('sha256', 'sha384', 'sha512', 'b2', 'sha224', 'sha1', 'md5')]
        for index, source in enumerate(values):
            if re.search(r'(?:^|::)(git|hg|svn|bzr)\+', source):
                if not re.search(r'#commit=[0-9a-f]{40,64}(?:$|&)', source):
                    raise ValueError('unpinned VCS source requires a reviewed exact-source recipe')
            elif '://' in source and not source.endswith(('.sig', '.asc', '.sign')):
                if not any(index < len(values_) and values_[index] != 'SKIP' for values_ in sums):
                    raise ValueError('unchecked remote source requires a reviewed exact-source recipe')


def normalize(source, target):
    """Discard creation times and host ownership so unchanged sources share one object."""
    with tarfile.open(source, 'r:*') as incoming, target.open('wb') as stream:
        with gzip.GzipFile(filename='', mode='wb', fileobj=stream, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w') as outgoing:
                for member in sorted(incoming.getmembers(), key=lambda item: item.name):
                    normalized = posixpath.normpath(member.name)
                    if normalized.startswith('/') or normalized == '..' or normalized.startswith('../'):
                        raise ValueError('source archive contains an unsafe member path')
                    if member.issym() or member.islnk():
                        link = member.linkname
                        target = posixpath.normpath(posixpath.join(
                            posixpath.dirname(normalized) if member.issym() else '', link))
                        if target.startswith('/') or target == '..' or target.startswith('../'):
                            raise ValueError('source archive links outside its own tree')
                    if member.name.endswith('/objects/info/alternates') and member.size:
                        raise ValueError('source archive depends on an external Git object store')
                    member.uid = member.gid = member.mtime = 0
                    member.uname = member.gname = ''
                    member.pax_headers = {}
                    outgoing.addfile(member, incoming.extractfile(member) if member.isfile() else None)


def collect(closure, packages, output, exclude=(), repositories=None):
    """Network/build work is explicit; publication only consumes the resulting manifest."""
    selected = inventory(closure, packages, exclude)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    built = {}
    for record in selected.values():
        identity = (record['base'], record['recipe_sha256'])
        if identity in built:
            record.update(built[identity])
            continue
        base = record['base']
        if not re.fullmatch(r'[a-z0-9@._+\-]+', base):
            raise ValueError('unsafe Arch package base')
        with tempfile.TemporaryDirectory(prefix='emaki-iso-source-') as temp:
            root = Path(temp)
            repo = root / 'recipe'
            origin = (str(Path(repositories).resolve() / base) if repositories else
                      f'https://gitlab.archlinux.org/archlinux/packaging/packages/{base}.git')
            run('git', 'clone', '--quiet', origin, str(repo))
            revision = exact_revision(repo, record['recipe_sha256'])
            run('git', '-C', str(repo), 'checkout', '--quiet', '--detach', revision)
            check_recipe(run('makepkg', '--printsrcinfo', cwd=repo), record)
            # No key or checksum bypass: failures require fixing the build environment.
            env = dict(os.environ, SRCDEST=str(root / 'downloads'), SRCPKGDEST=str(root / 'archives'))
            Path(env['SRCDEST']).mkdir()
            Path(env['SRCPKGDEST']).mkdir()
            run('makepkg', '--allsource', '--force', cwd=repo, env=env)
            archives = list(Path(env['SRCPKGDEST']).glob('*.src.tar.*'))
            if len(archives) != 1:
                raise ValueError(f'{base}: expected one complete source archive')
            source = root / (base + '.src.tar.gz')
            normalize(archives[0], source)
            sha = digest(source)
            target = output / 'sources' / 'sha256' / sha / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            archive = {'archive': source.name, 'sha256': sha, 'size': source.stat().st_size,
                       'revision': revision}
            built[identity] = archive
            record.update(archive)
    manifest = {'schema': 1, 'closure_sha256': digest(closure), 'packages': selected}
    (output / 'ARCH-SOURCES.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
    return manifest


def validate(manifest_path, closure, packages, exclude=()):
    """Bind sources to the actual image, with no network or recipe execution at publish time."""
    path = Path(manifest_path)
    manifest = json.loads(path.read_text())
    if manifest.get('schema') != 1 or manifest.get('closure_sha256') != digest(closure):
        raise ValueError('Arch source manifest does not describe this image package list')
    expected = inventory(closure, packages, exclude)
    records = manifest.get('packages', {})
    if records.keys() != expected.keys():
        raise ValueError('Arch source manifest is missing required packages or includes other packages')
    objects = {}
    for filename, record in records.items():
        if any(record.get(key) != value for key, value in expected[filename].items()):
            raise ValueError(f'{filename}: Arch source manifest differs from the image binary')
        archive = record.get('archive', '')
        sha = record.get('sha256', '')
        if (not re.fullmatch('[0-9a-f]{64}', sha) or Path(archive).name != archive
                or not re.fullmatch(r'[A-Za-z0-9@._+\-]+\.src\.tar\.[a-z0-9]+', archive)):
            raise ValueError('invalid Arch source archive path')
        key = f'sources/sha256/{sha}/{archive}'
        source = path.parent / key
        if source.stat().st_size != record.get('size') or digest(source) != sha:
            raise ValueError(f'{archive}: source archive bytes differ from manifest')
        objects[key] = source
    return records, objects


def directions(records, base_url):
    lines = ['Arch package sources in this image', '',
             'These archives contain the build recipes and upstream source files for the',
             'GPL and LGPL packages supplied by Arch Linux in this image.',
             'Download the archive listed for the package and version you need.', '']
    for record in sorted(records.values(), key=lambda item: item['name']):
        lines.extend([f"{record['name']} {record['version']}",
                      f"  {base_url.rstrip('/')}/sources/sha256/{record['sha256']}/{record['archive']}",
                      f"  SHA-256: {record['sha256']}", ''])
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--closure', required=True, type=Path)
    parser.add_argument('--packages', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--repositories', type=Path, help='local Arch packaging repository clones')
    args = parser.parse_args()
    # Keep this list aligned with the packages published in the Emaki repository.
    root = Path(__file__).resolve().parents[1]
    exclude = {path.parent.name for path in root.glob('*/PKGBUILD')}
    collect(args.closure, args.packages, args.output, exclude, args.repositories)


if __name__ == '__main__':
    main()
