#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check exact image recipes without makepkg or upstream source downloads."""
import argparse
from collections import Counter
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import urllib.parse

import iso_sources

LOCAL_PACKAGES = ('emaki', 'emaki-apps', 'emaki-config', 'emaki-desktop', 'emaki-installer',
                  'emaki-keyring', 'emaki-mirrorlist', 'emaki-nvidia', 'niri-emaki', 'quickshell-emaki',
                  'xdg-desktop-portal-gnome-emaki')


def image_inventory(closure, packages, exclude=LOCAL_PACKAGES):
    selected = iso_sources.inventory(closure, packages, exclude)
    rows = [dict(record, filename=name, **{'class': 'copyleft'})
            for name, record in selected.items()]
    for name in Path(closure).read_text().splitlines():
        if name in selected:
            continue
        info = iso_sources.package_metadata(Path(packages) / name)
        rows.append({'filename': name, 'name': info['pkgname'][0], 'base': info['pkgbase'][0],
                     'version': info['pkgver'][0], 'licenses': info['license'],
                     'class': 'local' if info['pkgname'][0] in exclude else 'noncopyleft'})
    return rows


def classify(text, records, checker=iso_sources, recipe_text=None):
    """A passing recipe is eligibility, not source/signature verification."""
    try:
        for record in records:
            checker.check_recipe(text, record)
            # Historical checker comparisons retain that checker's original policy.
            if recipe_text is not None and checker is iso_sources:
                iso_sources.cargo_sources.required_tree(record, recipe_text)
    except ValueError as error:
        reason = str(error)
        category = ('unpinned_vcs' if 'unpinned VCS' in reason else
                    'unchecked_source' if 'unchecked remote' in reason else
                    'identity_mismatch' if 'does not match' in reason else 'recipe_refused')
        return {'class': category, 'reason': reason}
    info = checker.fields(text)
    secondary = any(re.search(r'(?:^|::)git(?:\+|://)', value)
                    and not ('#tag=' in value or '#commit=' in value)
                    for key, values in info.items()
                    if key == 'source' or key.startswith('source_') for value in values)
    return {'class': 'conditional_vcs' if secondary else 'recipe_pass'}


def git(*args):
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null',
               GIT_TERMINAL_PROMPT='0')
    return subprocess.run(['git', '-c', 'http.userAgent=emaki-publish',
                           '-c', 'credential.helper=', *map(str, args)],
                          env=env, check=True, capture_output=True, text=True,
                          timeout=180).stdout


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    temporary.replace(path)


def retry_delay(url):
    request = urllib.request.Request(url + '/info/refs?service=git-upload-pack',
                                     method='HEAD', headers={'User-Agent': 'emaki-publish'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            value = response.headers.get('Retry-After', '60')
    except urllib.error.HTTPError as error:
        value = error.headers.get('Retry-After', '60')
    except OSError:
        value = '60'
    return max(1, int(value)) if value.isdigit() else 60


def download(url):
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'emaki-publish'})
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == 3:
                raise
            value = error.headers.get('Retry-After', '60')
            time.sleep(max(1, int(value)) if value.isdigit() else 60)


def archive_recipe(record, directory, checker=iso_sources, offline=False):
    """A version tag locates candidates; only the binary recipe hash accepts one."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = record['base'] + '-' + record['recipe_sha256']
    archive_path = directory / (stem + '.tar.gz')
    metadata_path = directory / (stem + '.json')
    if archive_path.exists() and metadata_path.exists():
        metadata = json.loads(metadata_path.read_text())
        data = archive_path.read_bytes()
        if hashlib.sha256(data).hexdigest() != metadata['recipe_archive_sha256']:
            raise ValueError('cached recipe archive checksum mismatch')
    else:
        if offline:
            raise ValueError('exact recipe archive not cached')
        project = checker.project_path(record['base'])
        tag = record['version'].replace(':', '-')
        path = urllib.parse.quote('archlinux/packaging/packages/' + project, safe='')
        tag_url = ('https://gitlab.archlinux.org/api/v4/projects/' + path
                   + '/repository/tags/' + urllib.parse.quote(tag, safe=''))
        revision = json.loads(download(tag_url))['commit']['id']
        if not re.fullmatch('[a-f0-9]{40}', revision):
            raise ValueError('invalid packaging commit')
        url = (f'https://gitlab.archlinux.org/archlinux/packaging/packages/{project}'
               f'/-/archive/{revision}/{project}-{revision}.tar.gz')
        data = download(url)
        metadata = {'revision': revision, 'recipe_archive_url': url,
                    'recipe_archive_sha256': hashlib.sha256(data).hexdigest()}
        archive_path.write_bytes(data)
        write_json(metadata_path, metadata)
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        files = {member.name.split('/', 1)[-1]: member for member in archive.getmembers()
                 if member.isfile() and member.name.count('/') == 1}
        recipe = archive.extractfile(files['PKGBUILD']).read()
        if hashlib.sha256(recipe).hexdigest() != record['recipe_sha256']:
            raise ValueError('tag recipe hash differs from binary; history lookup required')
        text = archive.extractfile(files['.SRCINFO']).read().decode() if '.SRCINFO' in files else None
    return metadata, text, recipe.decode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path,
                        help='JSON list with class, base, name, version, and recipe_sha256')
    parser.add_argument('--closure', type=Path, help='extracted image emaki/repo/closure.txt')
    parser.add_argument('--packages', type=Path, help='extracted image emaki/repo directory')
    parser.add_argument('--exclude', action='append', default=list(LOCAL_PACKAGES),
                        help='additional locally supplied binary package name')
    parser.add_argument('--repositories', type=Path, required=True)
    parser.add_argument('--archives', type=Path,
                        help='use commit-addressed GitLab recipe archives when a repository is absent')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--checker', type=Path, help='saved earlier iso_sources.py for comparison')
    parser.add_argument('--metadata', type=Path,
                        help='reviewed metadata cache, named <recipe SHA256>.SRCINFO')
    parser.add_argument('--offline', action='store_true', help='use cached repositories only')
    args = parser.parse_args()
    if bool(args.inventory) == bool(args.closure or args.packages):
        parser.error('provide --inventory or both --closure and --packages')
    if not args.inventory and not (args.closure and args.packages):
        parser.error('--closure and --packages are required together')
    checker = iso_sources
    if args.checker:
        spec = importlib.util.spec_from_file_location('recipe_checker', args.checker)
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
    groups = {}
    inventory = (json.loads(args.inventory.read_text()) if args.inventory else
                 image_inventory(args.closure, args.packages, args.exclude))
    skips = [row for row in inventory if row['class'] != 'copyleft']
    for record in inventory:
        if record['class'] == 'copyleft':
            groups.setdefault((record['base'], record['recipe_sha256']), []).append(record)
    results = []
    args.repositories.mkdir(parents=True, exist_ok=True)
    for (base, expected), records in groups.items():
        if not re.fullmatch(r'[a-z0-9@._+\-]+', base) or base in ('.', '..'):
            raise ValueError('unsafe package base')
        result = {'base': base, 'recipe_sha256': expected,
                  'members': [record['name'] for record in records]}
        repository = args.repositories / base
        try:
            text = None
            recipe_text = None
            if not repository.exists() and args.archives:
                metadata, text, recipe_text = archive_recipe(records[0], args.archives, checker, args.offline)
                result.update(metadata)
                result['metadata'] = 'archived .SRCINFO'
            elif not repository.exists():
                if args.offline:
                    raise ValueError('exact recipe repository not cached')
                # Each clone uses several requests; respect longer server throttle windows too.
                time.sleep(4)
                url = ('https://gitlab.archlinux.org/archlinux/packaging/packages/'
                       + checker.project_path(base) + '.git')
                for attempt in range(4):
                    try:
                        git('clone', '--quiet', url, repository)
                        break
                    except subprocess.CalledProcessError as error:
                        if '429' not in error.stderr or attempt == 3:
                            raise
                        time.sleep(retry_delay(url))
            if repository.exists():
                revision = checker.exact_revision(repository, expected)
                result['revision'] = revision
                recipe_text = git('-C', repository, 'show', revision + ':PKGBUILD')
                if hashlib.sha256(recipe_text.encode()).hexdigest() != expected:
                    raise ValueError('repository recipe hash differs from binary')
                try:
                    text = git('-C', repository, 'show', revision + ':.SRCINFO')
                    result['metadata'] = 'committed .SRCINFO'
                except subprocess.CalledProcessError:
                    pass
            if text is None:
                metadata = args.metadata / (expected + '.SRCINFO') if args.metadata else None
                if metadata is None or not metadata.is_file():
                    raise ValueError('exact recipe has no .SRCINFO; reviewed metadata required')
                text = metadata.read_text()
                result['metadata'] = 'reviewed metadata cache'
            result.update(classify(text, records, checker, recipe_text))
        except (ValueError, KeyError, OSError, tarfile.TarError, subprocess.SubprocessError) as error:
            result.update({'class': 'recipe_unavailable',
                           'reason': error.stderr.strip() if isinstance(error, subprocess.CalledProcessError)
                           else str(error)})
        results.append(result)
        write_json(args.output, {'counts': dict(Counter(row['class'] for row in results)),
                                'bases': results, 'skipped_binaries': skips})
        print(base, result['class'], flush=True)


if __name__ == '__main__':
    main()
