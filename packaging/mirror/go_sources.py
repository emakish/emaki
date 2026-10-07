#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Retain checksum-pinned Go modules for exact reviewed image recipes."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

RECIPES = {
    '8b40fbffa704abfb05a711d4351ce7248840816db8fe1cc473c8ed0b59e9f005':
        ('cliphist', 'cliphist-0.7.0'),
    '22be54850508ce1c8a707f73fbe0659b977063927b19b77084d352f1d0b7b5f3':
        ('kitty', 'kitty-0.49.2'),
}
RESTORE = """Copyright (C) 2026 Artur Yakymenko
SPDX-License-Identifier: GPL-3.0-or-later

Retained Go module sources

Restore the primary source from the same archive. Its go.mod and go.sum must
match this supplement exactly. Copy vendor into that source workspace root.
Preserve the recipe's build flags, replacing -mod=readonly with -mod=vendor.
Set GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local GOWORK=off and use the Go version
required by go.mod. Keep build caches in a writable temporary directory.
No module downloads are needed for the vendored build. vendor/modules.txt lists
the selected module versions; the collection manifest lists their go.sum
identities and SHA-256 hashes of every retained vendor file.
"""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def required_tree(record):
    match = RECIPES.get(record['recipe_sha256'])
    if match:
        if record['base'] != match[0]:
            raise ValueError('Go recipe base differs from its reviewed identity')
        return match[1]
    if record['base'] in {item[0] for item in RECIPES.values()}:
        raise ValueError('Go dependency inputs require a reviewed recipe')
    return None


def inspect_vendor(mod, sums, read, names):
    """Bind every retained module to the pinned sums and inventory every file."""
    checksums = {}
    for line in sums.decode().splitlines():
        parts = line.split()
        if len(parts) != 3 or not re.fullmatch(r'h1:[A-Za-z0-9+/]{43}=', parts[2]):
            raise ValueError('invalid pinned Go module checksum')
        key = tuple(parts[:2])
        if key in checksums and checksums[key] != parts[2]:
            raise ValueError('conflicting pinned Go module checksums')
        checksums[key] = parts[2]
    # These reviewed recipes have neither replacements nor exclusions. Refuse
    # new directives rather than silently downloading an unpinned replacement.
    required = {}
    block = False
    for line in mod.decode().splitlines():
        line = line.split('//', 1)[0].strip()
        if re.match(r'(replace|exclude)\b', line):
            raise ValueError('unreviewed Go replacement or exclusion')
        if line == 'require (':
            block = True
            continue
        if line == ')':
            block = False
            continue
        if line.startswith('require '):
            line = line[len('require '):]
        elif not block:
            continue
        if line:
            parts = line.split()
            if len(parts) != 2:
                raise ValueError('unsupported Go module requirement')
            required[parts[0]] = parts[1]
    files = {name: sha256(read(name)) for name in sorted(names) if name.startswith('vendor/')}
    modules = []
    selected = {}
    for line in read('vendor/modules.txt').decode().splitlines():
        if line.startswith('# ') and not line.startswith('##'):
            parts = line[2:].split()
            if len(parts) != 2 or not parts[1].startswith('v'):
                raise ValueError('unreviewed vendored Go module identity')
            name, version = parts
            if name in selected:
                raise ValueError('duplicate vendored Go module')
            selected[name] = version
            identity = {'name': name, 'version': version}
            for suffix, key in (('', 'sum'), ('/go.mod', 'go_mod_sum')):
                value = checksums.get((name, version + suffix))
                if value:
                    identity[key] = value
            if 'go_mod_sum' not in identity:
                raise ValueError('vendored Go module has no pinned go.mod checksum')
            if any(path.startswith('vendor/' + name + '/') for path in files):
                if 'sum' not in identity:
                    raise ValueError('vendored Go module has no pinned source checksum')
            modules.append(identity)
    if not modules or any(selected.get(name) != version for name, version in required.items()):
        raise ValueError('Go supplement is missing required modules')
    for path in files:
        if path != 'vendor/modules.txt' and not any(
                path.startswith('vendor/' + module['name'] + '/') for module in modules):
            raise ValueError('vendored Go file has no module identity')
    return sorted(modules, key=lambda item: item['name']), files


def create_supplement(record, source_tree, destination, env=None):
    if required_tree(record) is None:
        raise ValueError('Go supplement requires a reviewed recipe')
    source_tree, destination = Path(source_tree).resolve(), Path(destination).resolve()
    inputs = {name: (source_tree / name).read_bytes() for name in ('go.mod', 'go.sum')}
    destination.mkdir(parents=True)
    cache = destination.parent / (destination.name + '-go-cache')
    vendor_env = dict(os.environ if env is None else env,
                      GOPATH=str(cache), GOMODCACHE=str(cache / 'pkg/mod'),
                      GOCACHE=str(cache / 'build'), GOWORK='off', GOENV='off',
                      GOFLAGS='-mod=readonly', GOTOOLCHAIN='local',
                      GOPROXY='https://proxy.golang.org', GOSUMDB='sum.golang.org',
                      GOPRIVATE='', GONOPROXY='', GONOSUMDB='')
    subprocess.run(['go', 'mod', 'vendor', '-o', str(destination / 'vendor')],
                   cwd=source_tree, env=vendor_env, check=True, capture_output=True, text=True)
    for name, original in inputs.items():
        if (source_tree / name).read_bytes() != original:
            raise ValueError('go mod vendor changed the pinned module inputs')
        (destination / name).write_bytes(original)
    (destination / 'README.sources').write_text(RESTORE)
    names = []
    for path in destination.rglob('*'):
        if path.is_symlink():
            raise ValueError('Go supplement contains a symbolic link')
        if path.is_file():
            names.append(path.relative_to(destination).as_posix())
    modules, files = inspect_vendor(inputs['go.mod'], inputs['go.sum'],
                                   lambda name: (destination / name).read_bytes(), names)
    return {'path': record['base'] + '/_go',
            'go_mod_sha256': sha256(inputs['go.mod']), 'go_sum_sha256': sha256(inputs['go.sum']),
            'readme_sha256': sha256(RESTORE.encode()), 'modules': modules, 'files': files,
            'method': 'go mod vendor'}


def add_supplement(source_archive, destination, target_archive, record):
    prefix = record['base'] + '/_go'
    with tarfile.open(source_archive, 'r:*') as source, tarfile.open(target_archive, 'w') as target:
        for member in source:
            if member.name == prefix or member.name.startswith(prefix + '/'):
                raise ValueError('source archive already contains a Go supplement')
            target.addfile(member, source.extractfile(member) if member.isfile() else None)
        target.add(destination, arcname=prefix, recursive=True)


def primary_module_inputs(archive, record):
    """Read the reviewed workspace inputs inside the retained upstream tarball.

    Read members without extracting paths or links. Require a unique workspace,
    including when the same input appears in multiple source archives.
    """
    tree = required_tree(record)
    inputs = {}
    for member in archive.getmembers():
        if (not member.isfile() or not member.name.startswith(record['base'] + '/')
                or '/_go/' in member.name):
            continue
        if not re.search(r'\.(?:tar(?:\.(?:gz|bz2|xz|zst))?|tgz|tbz2|txz)$', member.name):
            continue
        try:
            with tarfile.open(fileobj=archive.extractfile(member), mode='r:*') as upstream:
                for source in upstream:
                    name = source.name.removeprefix('./')
                    for filename in ('go.mod', 'go.sum'):
                        if name != tree + '/' + filename:
                            continue
                        if not source.isfile() or filename in inputs:
                            raise ValueError('ambiguous Go primary source module inputs')
                        inputs[filename] = upstream.extractfile(source).read()
        except tarfile.TarError as error:
            raise ValueError('invalid Go primary source archive') from error
    if set(inputs) != {'go.mod', 'go.sum'}:
        raise ValueError('missing Go primary source module inputs')
    return inputs


def validate_supplement(archive, record):
    required = required_tree(record)
    evidence = record.get('go_supplement')
    if not required:
        if evidence:
            raise ValueError('unreviewed Go supplement')
        return
    prefix = record['base'] + '/_go'
    if not isinstance(evidence, dict) or evidence.get('path') != prefix:
        raise ValueError('Go dependencies are not archived and listed')
    members = {}
    for member in archive.getmembers():
        if member.name.startswith(prefix + '/'):
            name = member.name[len(prefix) + 1:]
            if '..' in PurePosixPath(name).parts or name.startswith('/'):
                raise ValueError('unsafe Go supplement member')
            if member.isdir():
                continue
            if not member.isfile() or name in members:
                raise ValueError('Go supplement contains links or duplicate files')
            members[name] = member
    def read(name):
        if name not in members:
            raise ValueError('Go supplement is incomplete')
        return archive.extractfile(members[name]).read()
    for name, key in (('go.mod', 'go_mod_sha256'), ('go.sum', 'go_sum_sha256'),
                      ('README.sources', 'readme_sha256')):
        if sha256(read(name)) != evidence.get(key):
            raise ValueError('Go supplement input checksum mismatch')
    if read('README.sources') != RESTORE.encode():
        raise ValueError('Go supplement restoration instructions differ')
    if set(members) - {'go.mod', 'go.sum', 'README.sources'} != {
            name for name in members if name.startswith('vendor/')}:
        raise ValueError('unexpected Go supplement member')
    modules, files = inspect_vendor(read('go.mod'), read('go.sum'), read, members)
    if modules != evidence.get('modules') or files != evidence.get('files'):
        raise ValueError('Go supplement differs from manifest evidence')
    upstream = primary_module_inputs(archive, record)
    if any(read(name) != data for name, data in upstream.items()):
        raise ValueError('Go supplement module inputs differ from primary source')
