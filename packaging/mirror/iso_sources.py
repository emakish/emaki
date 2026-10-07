#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Collect exact Arch source archives in a disposable, unprivileged Arch build environment."""
import argparse
import hashlib
import gzip
import json
import io
import os
import posixpath
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tarfile
from urllib.parse import quote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cargo_sources
import go_sources
import arch_source_fallback
import historical_sources
import source_routes
import unused_sources
import source_keys
import savannah_git


def run(*args, **kwargs):
    if args and args[0] == 'git':
        args = ('git', '-c', 'http.userAgent=emaki-publish', *args[1:])
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs).stdout
    except subprocess.CalledProcessError as error:
        print(f'{args[0]} failed ({error.returncode}):\n'
              f'{error.stdout or ""}{error.stderr or ""}', file=sys.stderr)
        raise


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fields(text):
    result = {}
    for line in text.splitlines():
        if ' = ' in line and not line.startswith('#'):
            key, value = line.split(' = ', 1)
            result.setdefault(key.strip(), []).append(value.strip())
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
    if Path(packages).is_file():
        saved = json.loads(Path(packages).read_text())
        if (saved.get('schema') != 1 or saved.get('closure_sha256') != digest(closure)
                or set(saved.get('binaries', {})) != set(names)):
            raise ValueError('image inventory does not describe the full image package list')
        for name, sha in saved['binaries'].items():
            if (Path(name).name != name or not name.endswith('.pkg.tar.zst')
                    or not re.fullmatch('[0-9a-f]{64}', sha)):
                raise ValueError('invalid image binary inventory')
        selected = saved['sources']
        for name, record in selected.items():
            if name not in saved['binaries'] or record['binary_sha256'] != saved['binaries'][name]:
                raise ValueError('source identity differs from the image binary inventory')
        return {name: record for name, record in selected.items() if record['name'] not in exclude}
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


def validate_live_closure(closure, pkglist, target_closure, image_inventory=None):
    """Cover exactly the installed live packages and the shipped target archives."""
    versions = None
    if image_inventory is not None:
        versions = json.loads(Path(image_inventory).read_text()).get('versions')
        if not isinstance(versions, dict) or set(versions) != set(Path(closure).read_text().splitlines()):
            raise ValueError('image inventory lacks exact package versions')

    def archives(path):
        result = set()
        lines = Path(path).read_text().splitlines()
        if not lines or len(lines) != len(set(lines)):
            raise ValueError('empty or duplicate image package list')
        for filename in lines:
            match = re.fullmatch(r'(.+)-([^-]+)-([^-]+)-(any|x86_64)\.pkg\.tar\.zst', filename)
            if not match or Path(filename).name != filename:
                raise ValueError('invalid image package filename')
            name, version, release, _ = match.groups()
            identity = (name, version + '-' + release)
            if versions is not None:
                exact = versions.get(filename)
                if not isinstance(exact, str) or exact != identity[1]:
                    raise ValueError('image inventory version differs from archive filename')
                identity = (name, exact)
            if identity in result:
                raise ValueError('duplicate image package identity')
            result.add(identity)
        return result

    installed = set()
    for line in Path(pkglist).read_text().splitlines():
        fields_ = line.split()
        if len(fields_) != 2:
            raise ValueError('invalid live package list')
        name, version = fields_
        # Older callers have filenames only; publication checks the exact metadata epoch.
        identity = (name, version if versions is not None else version.split(':', 1)[-1])
        if identity in installed:
            raise ValueError('duplicate live package identity')
        installed.add(identity)
    if not installed:
        raise ValueError('empty live package list')
    expected = installed | archives(target_closure)
    actual = archives(closure)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f'live closure differs from image package set: missing={missing}, extra={extra}')


def write_image_inventory(closure, packages, output, exclude=()):
    """Retain source identities and binary hashes without shipping live-only archives."""
    selected = inventory(closure, packages, exclude)
    binaries = {name: digest(Path(packages) / name)
                for name in Path(closure).read_text().splitlines()}
    versions = {name: package_metadata(Path(packages) / name)['pkgver'][0] for name in binaries}
    Path(output).write_text(json.dumps({'schema': 1, 'closure_sha256': digest(closure),
                                      'binaries': binaries, 'sources': selected, 'versions': versions},
                                     sort_keys=True, indent=2) + '\n')


def exact_revision(repository, expected):
    """Match the recipe used by the binary; version tags are not a trust anchor."""
    revisions = run('git', '-C', str(repository), 'log', '--all', '--format=%H', '--', 'PKGBUILD')
    for revision in revisions.splitlines():
        recipe = subprocess.run(['git', '-C', str(repository), 'show', f'{revision}:PKGBUILD'],
                                check=True, capture_output=True).stdout
        if hashlib.sha256(recipe).hexdigest() == expected:
            return revision
    raise ValueError(f'{repository.name}: exact binary build recipe is absent from packaging history')


def project_path(base):
    """Port devtools src/lib/api/gitlab.sh:gitlab_project_name_to_path exactly.

    https://gitlab.archlinux.org/archlinux/devtools/-/blob/master/src/lib/api/gitlab.sh
    """
    name = re.sub(r'([a-zA-Z0-9]+)\+([a-zA-Z]+)', r'\1-\2', base)
    name = name.replace('+', 'plus')
    name = re.sub(r'[^a-zA-Z0-9_.\-]', '-', name)
    name = re.sub(r'[_\-]{2,}', '-', name)
    return 'unix-tree' if name == 'tree' else name


# These mappings are reviewed against the entire binary-matched PKGBUILD, including
# prepare(): each redirects the named submodule to the local secondary repository
# and uses submodule update without --remote. A changed recipe needs a new review.
GNU_GIT = 'git+https://git.savannah.gnu.org/git/'
GNOME_GIT = 'git+https://gitlab.gnome.org/GNOME/'
VIDEOLAN_GIT = 'git+https://code.videolan.org/videolan/'
SUBMODULE_RECIPES = {
    '16b4384c87031c8294d9e61c918cd05d3e50698f5cef3d1c6950b2211c7f0efb':
        ('m4', GNU_GIT + 'm4.git', ((GNU_GIT + 'gnulib.git', 'gnulib'),)),
    '4e24185da5742e5766773843b380fa96c750700716594b8dff86f362d5021915':
        ('malcontent', 'git+https://gitlab.freedesktop.org/pwithnall/malcontent.git',
         ((GNOME_GIT + 'gvdb.git', 'subprojects/gvdb'),)),
    '0d48c704bf448a706df715476d1632813f81756cd14ce03447f36dcd98ce08b9':
        ('ostree', 'git+https://github.com/ostreedev/ostree',
         (('git+https://github.com/mendsley/bsdiff', 'bsdiff'),
          (GNOME_GIT + 'libglnx.git', 'libglnx'))),
    '64fe32d8242a063f428dfb7e1ed8cb0268ee798f6fb2a8a84551141fb8ea180e':
        ('obs-studio', 'obs-studio::git+https://github.com/obsproject/obs-studio',
         (('obs-studio-libdshowcapture::git+https://github.com/obsproject/libdshowcapture.git',
           'deps/libdshowcapture/src'),
          ('obs-studio-obs-browser::git+https://github.com/obsproject/obs-browser.git',
           'plugins/obs-browser'),
          ('obs-studio-obs-websocket::git+https://github.com/obsproject/obs-websocket.git',
           'plugins/obs-websocket'))),
    '69b18787e996921d0b7c06a4566f197e87a6076af5f279812eb072bf68ea626a':
        ('grub', 'git+https://gitlab.freedesktop.org/gnu-grub/grub.git',
         ((GNU_GIT + 'gnulib.git', 'bootstrap.conf'),)),
    'cc8cea0fa0e6128ee3435860fa90c48e7b2eb9d33a132f2a8325c18d7ad9a10f':
        ('gobject-introspection', GNOME_GIT + 'gobject-introspection.git',
         ((GNOME_GIT + 'gobject-introspection-tests.git', 'gobject-introspection-tests'),)),
    '14e16ee87225a0e34f37397cec175d2574b344110122c00aa00767f007771f4d':
        ('libshout', 'git+https://gitlab.xiph.org/xiph/icecast-libshout.git',
         (('git+https://gitlab.xiph.org/xiph/icecast-common.git', 'src/common'),
          ('git+https://gitlab.xiph.org/xiph/icecast-m4.git', 'm4'))),
    # _chromium is empty in this exact recipe, so its optional checkout is disabled.
    'e4395367f03fd6cad9d12c8e5548fa5214668fcde4a6a513b3d535955674dc92':
        ('qt6-webengine', 'git+https://code.qt.io/qt/qtwebengine',
         (('git+https://code.qt.io/qt/qtwebengine-chromium', 'src/3rdparty'),)),
    'ef4bd0dc08ca7a48e2f767a5a4d756f742455a790f2e330203d25de9588abf83':
        ('coreutils', GNU_GIT + 'coreutils.git', ((GNU_GIT + 'gnulib.git', 'gnulib'),)),
    'fadd10516e159f5e74be240971ad79a51cc14a58357dfb9f17df86d3f2a22f01':
        ('findutils', GNU_GIT + 'findutils.git', ((GNU_GIT + 'gnulib.git', 'gnulib'),)),
    '9ec5c0b395d3af2eccc651b5dd2a1d6b366e909138f220b34506dd12a38cf5fc':
        ('gzip', GNU_GIT + 'gzip.git', ((GNU_GIT + 'gnulib.git', 'gnulib'),)),
    'fc18cbacb0e3e98e8c50ee7ca8b187c8c930dfdde6a7afa04b4a80d082bdc9c5':
        ('diffutils', GNU_GIT + 'diffutils.git', ((GNU_GIT + 'gnulib.git', 'gnulib'),)),
    '5e808dc9bba06fa1b2e922318679908c25b3f964436510b5fa8f5d73230427c4':
        ('glib2', GNOME_GIT + 'glib.git', ((GNOME_GIT + 'gvdb.git', 'subprojects/gvdb'),)),
    'abcebc1d3dc2c5b57fde1bd69ed472997a1d1dd231d6dee49d3d1f52d5fc98c3':
        ('grep', GNU_GIT + 'grep.git', ((GNU_GIT + 'gnulib.git', 'gnulib'),)),
    '26658daada16d1cc5991cd1e73e101105ac80f6df2f683bfbb17fe9182f3cd10':
        ('tar', GNU_GIT + 'tar.git', ((GNU_GIT + 'gnulib.git', 'gnulib'),
                                    (GNU_GIT + 'paxutils.git', 'paxutils'))),
    '228ba86432c4f92f9d93990c3b921b5641c2d683ae32f409c1b3aa9748821902':
        ('libtool', GNU_GIT + 'libtool.git', ((GNU_GIT + 'gnulib.git', 'gnulib'),
            ('gnulib-bootstrap::git+https://github.com/gnulib-modules/bootstrap.git',
             'gl-mod/bootstrap'))),
    '5f157fb66b7d8d4363e93c13d339549cecba4d58f2602fac641fef4155fdebfe':
        ('glycin', GNOME_GIT + 'glycin.git',
         (('git+https://gitlab.gnome.org/sophie-h/test-images.git', 'tests/test-images'),)),
    'a26cb78c5ee812f83a679e6eb3798c708a9efcfea9c19c593fdb8ecc5d1ed2e7':
        ('libbluray', VIDEOLAN_GIT + 'libbluray.git',
         ((VIDEOLAN_GIT + 'libudfread.git', 'contrib/libudfread'),)),
}
BOOTSTRAP_RECIPES = {
    '69b18787e996921d0b7c06a4566f197e87a6076af5f279812eb072bf68ea626a': {
        'commit': '9f48fb992a3d7e96610c4ce8be969cff2d61a01b',
        'files': {
            'bootstrap': '3092d83b85f5250c2a0a23a48481c434d605ec255e61cf8c729e2697ddf6a863',
            'bootstrap.conf': 'd6ba5ef6d801f60f5516b4e2f54aa9509d2b9901b3732a9cee03a4bf4838ceb0',
        },
    },
}
RECIPE_COMMITS = {
    '5f157fb66b7d8d4363e93c13d339549cecba4d58f2602fac641fef4155fdebfe':
        ('476720131410e80436e65b9e24fcab52467e1b0f',
         '590c6d215afb3ed055909d5f208d2db200386409'),
    'abcebc1d3dc2c5b57fde1bd69ed472997a1d1dd231d6dee49d3d1f52d5fc98c3':
        ('2e19d07ef1c08c3ce4771bb1bfee1ae6541f1c0d',),
}
GITLINK_UPDATES = {
    'abcebc1d3dc2c5b57fde1bd69ed472997a1d1dd231d6dee49d3d1f52d5fc98c3': {
        'gnulib': {'primary_commit': '2e19d07ef1c08c3ce4771bb1bfee1ae6541f1c0d',
                   'before': '3773db653242ab7165cd300295c27405e4f9cc79',
                   'after': 'ff657b72826c90b7a0247156c302d3600f9f2594'},
    },
}
OMITTED_GITLINKS = {
    '5f157fb66b7d8d4363e93c13d339549cecba4d58f2602fac641fef4155fdebfe': {
        'gitmodules_sha256': 'b14bbb67fb41bf6198ece2e86673d55a8b3c0c76c99e940a445aadcbb8ad67cb',
        'paths': {'libglycin-rebind/gir': 'c93f8f31b6a5dcad139b2aff7ba29ce9f07d8e99',
                  'libglycin-rebind/gir-files': '01ea5f1a07cd53c1f070f4b7ce6110fbbe70049b'},
        'reason': 'Both submodules have update=none and serve manual binding generation; '
                  'the reviewed recipe builds the committed bindings without these generators.',
    },
}


def recipe_submodules(text, record):
    """Return only reviewed gitlink mappings bound to the image's recipe hash."""
    reviewed = SUBMODULE_RECIPES.get(record.get('recipe_sha256'))
    if reviewed is None:
        return {}
    base, primary_url, secondaries = reviewed
    if record.get('base') != base:
        raise ValueError('submodule evidence does not match package base')
    sources = [source for key, values in fields(text).items()
               if key == 'source' or key.startswith('source_') for source in values]
    primaries = [source for source in sources
                 if source.split('#', 1)[0].split('?', 1)[0] == primary_url]
    if any(sources.count(url) != 1 for url, _ in secondaries) or len(primaries) != 1:
        raise ValueError('reviewed submodule source mapping differs from recipe')
    git_source(primaries[0])
    mappings = {url: {'primary_source': primaries[0], 'path': path,
                  'recipe_sha256': record['recipe_sha256'],
                  'additional_primary_commits': list(RECIPE_COMMITS.get(record['recipe_sha256'], ())),
                  'selection': 'prepare() redirects this submodule to the local source '
                               'and runs git submodule update without --remote'}
            for url, path in secondaries}
    if record['recipe_sha256'] in BOOTSTRAP_RECIPES:
        for evidence in mappings.values():
            evidence['bootstrap'] = BOOTSTRAP_RECIPES[record['recipe_sha256']]
            evidence['selection'] = ('prepare() calls bootstrap --gnulib-srcdir; the reviewed '
                                     'bootstrap checks out GNULIB_REVISION from bootstrap.conf')
    if base == 'obs-studio':
        for evidence in mappings.values():
            evidence['recursive'] = True
    for evidence in mappings.values():
        update = GITLINK_UPDATES.get(record['recipe_sha256'], {}).get(evidence['path'])
        if update:
            evidence['gitlink_update'] = update
        if record['recipe_sha256'] in OMITTED_GITLINKS:
            evidence['omitted_gitlinks'] = OMITTED_GITLINKS[record['recipe_sha256']]
    return mappings


def fetch_git_reference(source, downloads, *, commit=None, env=None):
    """Fetch only a selected ref, never a secondary repository's moving HEAD."""
    parsed = git_source(source if commit is None else source + '#commit=' + commit)
    name, kind, reference = parsed
    repository = downloads / name
    selected_source = source if commit is None else source + '#commit=' + commit
    if savannah_git.parse(selected_source):
        if repository.exists():
            retained = json.loads((repository / 'emaki-source-route.json').read_text())
            if retained.get('source') != selected_source:
                raise ValueError('retained Savannah source differs from selected source')
            ref = git_reference(kind, reference)
            actual = run('git', '-C', str(repository), 'rev-parse', ref + '^{commit}').strip()
            if actual != retained.get('commit'):
                raise ValueError('retained Savannah source commit changed')
            return actual
        return savannah_git.fetch(selected_source, downloads, env, [], run)['commit']
    url = source.split('::', 1)[-1].removeprefix('git+').split('#', 1)[0].split('?', 1)[0]
    run('git', 'init', '--quiet', '--bare', str(repository))
    run('git', '-C', str(repository), 'remote', 'add', 'origin', url)
    ref = git_reference(kind, reference)
    mirror = source_routes.git_mirror(source, reference if kind == 'commit' else commit)
    route = None
    try:
        options = {'timeout': 45} if mirror else {}
        run('git', '-C', str(repository), 'fetch', '--quiet', '--no-tags', 'origin', ref, **options)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        if not mirror:
            raise
        run('git', '-C', str(repository), 'fetch', '--quiet', '--no-tags', mirror['mirror'], ref)
        route = mirror
    resolved = run('git', '-C', str(repository), 'rev-parse', '--verify',
                   'FETCH_HEAD^{commit}').strip()
    if not re.fullmatch('[0-9a-f]{40,64}', resolved) or (kind == 'commit' and resolved != reference):
        raise ValueError('fetched Git commit differs from exact source evidence')
    if kind == 'tag':
        # Preserve the tag object as well as the peeled commit for normal signature checks.
        tag = run('git', '-C', str(repository), 'rev-parse', 'FETCH_HEAD').strip()
        if re.fullmatch('[0-9a-f]{40,64}', reference):
            if tag != reference:
                raise ValueError('fetched Git object differs from exact tag selector')
            retained_ref = 'refs/emaki/objects/' + reference
        else:
            retained_ref = ref
        run('git', '-C', str(repository), 'update-ref', retained_ref, tag)
    run('git', '-C', str(repository), 'update-ref', 'refs/heads/emaki-source', resolved)
    run('git', '-C', str(repository), 'symbolic-ref', 'HEAD', 'refs/heads/emaki-source')
    if route:
        (repository / 'emaki-source-route.json').write_text(json.dumps(route, sort_keys=True) + '\n')
    return resolved


def prepare_nested_sources(source, commit, evidence, downloads, env=None, *, covered=()):
    """Archive the reviewed nested source; unknown recursive dependencies stay refused."""
    name = git_source(source.split('#', 1)[0] + '#commit=' + commit)[0]
    repository = downloads / name
    tree = run('git', '-C', str(repository), 'ls-tree', '-r', commit)
    links = {line.split('\t', 1)[1]: line.split()[2] for line in tree.splitlines()
             if line.startswith('160000 ')}
    reviewed = {}
    if (evidence['recipe_sha256'] == '64fe32d8242a063f428dfb7e1ed8cb0268ee798f6fb2a8a84551141fb8ea180e'
            and name == 'obs-studio-libdshowcapture'):
        reviewed['external/capture-device-support'] = 'https://github.com/elgatosf/capture-device-support'
    if (evidence['recipe_sha256'] == '16b4384c87031c8294d9e61c918cd05d3e50698f5cef3d1c6950b2211c7f0efb'
            and source == GNU_GIT + 'm4.git#tag=v1.4.21?signed'):
        reviewed['gl-mod/bootstrap'] = 'https://github.com/gnulib-modules/bootstrap.git'
    if links.keys() - reviewed.keys() - set(covered):
        raise ValueError('recursive secondary gitlinks need exact-source collection: ' + source)
    pins = []
    for path, nested_commit in links.items():
        if path in covered:
            continue
        url = run('git', '-C', str(repository), 'config', '--blob=' + commit + ':.gitmodules',
                  '--get', 'submodule.' + path + '.url').strip()
        if url != reviewed[path]:
            raise ValueError('nested submodule URL differs from reviewed source')
        supplements = repository / 'source-supplements'
        supplements.mkdir(exist_ok=True)
        nested_source = 'git+' + url
        nested_name = git_source(nested_source + '#commit=' + nested_commit)[0]
        fetch_git_reference(nested_source, supplements, commit=nested_commit, env=env)
        nested_tree = run('git', '-C', str(supplements / nested_name), 'ls-tree', '-r', nested_commit)
        if any(line.startswith('160000 ') for line in nested_tree.splitlines()):
            raise ValueError('nested source contains further unreviewed gitlinks')
        pins.append({'source': nested_source, 'commit': nested_commit, 'kind': 'gitlink',
                     'parent_source': source, 'parent_commit': commit, 'path': path,
                     'archive_directory': name + '/source-supplements/' + nested_name})
        (supplements / 'README.sources').write_text(
            'Copyright (C) 2026 Artur Yakymenko\nSPDX-License-Identifier: GPL-3.0-or-later\n\n'
            'This directory retains a nested source mirror at the recorded gitlink commit.\n'
            'The original PKGBUILD is unchanged. makepkg does not automatically wire this mirror.\n'
            'For a local rebuild, set the nested submodule URL in the extracted parent working\n'
            'copy to the absolute path of this mirror before recursive submodule update:\n'
            f'  git config submodule.{path}.url /absolute/path/to/{name}/source-supplements/{nested_name}\n'
            'Run submodule update with protocol.file.allow=always and use makepkg --holdver.\n')
    return pins


def prepare_submodules(text, record, downloads, env=None):
    """Seed all VCS mirrors when a reviewed secondary needs --holdver protection."""
    mappings = recipe_submodules(text, record)
    if not mappings:
        return {}
    for key, values in fields(text).items():
        if key == 'source' or key.startswith('source_'):
            for source in values:
                if source not in mappings and git_source(source) is not None:
                    fetch_git_reference(source, downloads, env=env)
    for evidence in mappings.values():
        name = git_source(evidence['primary_source'])[0]
        for commit in evidence.get('additional_primary_commits', []):
            run('git', '-c', 'http.userAgent=emaki-publish', '-C', str(downloads / name),
                'fetch', '--quiet', '--no-tags', 'origin', commit)
            actual = run('git', '-C', str(downloads / name), 'rev-parse',
                         '--verify', 'FETCH_HEAD^{commit}').strip()
            if actual != commit:
                raise ValueError('supplementary primary commit differs from recipe')
            changed = run('git', '-C', str(downloads / name), 'diff-tree',
                          '--no-commit-id', '--name-only', '--root', '-m', '-r', commit, '--', evidence['path'])
            if changed.strip():
                update = evidence.get('gitlink_update', {})
                if commit != update.get('primary_commit'):
                    raise ValueError('recipe cherry-pick changes the reviewed primary gitlink')
                for revision, expected in ((commit + '^', update['before']), (commit, update['after'])):
                    entry = run('git', '-C', str(downloads / name), 'ls-tree', revision,
                                '--', evidence['path']).strip()
                    if entry != '160000 commit ' + expected + '\t' + evidence['path']:
                        raise ValueError('recipe cherry-pick gitlink differs from reviewed transition')
            run('git', '-C', str(downloads / name), 'update-ref',
                'refs/heads/emaki-supplement-' + commit, commit)
    result = {}
    for source, evidence in mappings.items():
        primary = evidence['primary_source']
        name, kind, reference = git_source(primary)
        ref = git_reference(kind, reference)
        primary_commit = run('git', '-C', str(downloads / name), 'rev-parse',
                             '--verify', ref + '^{commit}').strip()
        if 'bootstrap' not in evidence:
            tree = run('git', '-C', str(downloads / name), 'ls-tree', '-r', primary_commit)
            paths = {line.split('\t', 1)[1] for line in tree.splitlines()
                     if line.startswith('160000 ')}
            covered = {item['path'] for item in mappings.values()
                       if item['primary_source'] == primary}
            omitted = evidence.get('omitted_gitlinks')
            if omitted:
                content = run('git', '-C', str(downloads / name), 'show', primary_commit + ':.gitmodules')
                if hashlib.sha256(content.encode()).hexdigest() != omitted['gitmodules_sha256']:
                    raise ValueError('omitted gitlink configuration differs from reviewed source')
                for path, expected in omitted['paths'].items():
                    entry = run('git', '-C', str(downloads / name), 'ls-tree', primary_commit,
                                '--', path).strip()
                    if entry != '160000 commit ' + expected + '\t' + path:
                        raise ValueError('omitted gitlink differs from reviewed source')
                covered.update(omitted['paths'])
            if record.get('base') == 'm4' and evidence['recipe_sha256'] == \
                    '16b4384c87031c8294d9e61c918cd05d3e50698f5cef3d1c6950b2211c7f0efb':
                nested = prepare_nested_sources(primary, primary_commit, evidence, downloads,
                                                env, covered=covered)
                result[primary] = {'source': primary, 'kind': kind, 'reference': reference,
                                   'commit': primary_commit, 'nested_sources': nested}
                covered.update(pin['path'] for pin in nested)
            if paths - covered:
                raise ValueError('primary gitlinks are absent from reviewed sources')
        if 'bootstrap' in evidence:
            for path, expected in evidence['bootstrap']['files'].items():
                content = run('git', '-C', str(downloads / name), 'show',
                              primary_commit + ':' + path)
                if hashlib.sha256(content.encode()).hexdigest() != expected:
                    raise ValueError('pinned bootstrap evidence differs from reviewed source')
            commit = evidence['bootstrap']['commit']
            kind = 'bootstrap-revision'
        else:
            entry = run('git', '-C', str(downloads / name), 'ls-tree', primary_commit,
                        '--', evidence['path']).strip()
            match = re.fullmatch(r'160000 commit ([0-9a-f]{40,64})\t' + re.escape(evidence['path']), entry)
            if not match:
                raise ValueError('reviewed secondary source has no exact primary gitlink')
            commit = match[1]
            kind = 'gitlink'
            update = evidence.get('gitlink_update')
            if update:
                if commit != update['before']:
                    raise ValueError('primary gitlink differs from reviewed transition')
                commit = update['after']
        fetch_git_reference(source, downloads, commit=commit, env=env)
        if evidence.get('gitlink_update'):
            secondary = git_source(source + '#commit=' + commit)[0]
            # prepare() initializes the original gitlink before applying the reviewed update.
            run('git', '-C', str(downloads / secondary), 'cat-file', '-e',
                evidence['gitlink_update']['before'] + '^{commit}')
        nested = (prepare_nested_sources(source, commit, evidence, downloads, env)
                  if evidence.get('recursive') else [])
        result[source] = {'source': source, 'kind': kind, 'reference': evidence['path'],
                          'commit': commit, 'evidence': dict(evidence, primary_commit=primary_commit)}
        if nested:
            result[source]['nested_sources'] = nested
    return result


def git_source(source):
    """Return makepkg's download directory and exact Git reference, if applicable."""
    url = source.split('::', 1)[-1]
    if not (url.startswith('git+') or url.startswith('git://')):
        return None
    fragment = url.partition('#')[2].split('?', 1)[0]
    kind, _, reference = fragment.partition('=')
    if (kind == 'commit' and re.fullmatch('[0-9a-f]{40,64}', reference)):
        pass
    elif kind == 'tag' and reference:
        # Git validates the complete ref; never interpret the tag as an option or revision.
        run('git', 'check-ref-format', 'refs/tags/' + reference)
    else:
        raise ValueError('unpinned VCS source requires a reviewed exact-source recipe')
    name = (source.split('::', 1)[0] if '::' in source else
            url.split('#', 1)[0].split('?', 1)[0].rstrip('/').rsplit('/', 1)[-1].split('.git', 1)[0])
    if not name or Path(name).name != name or name in ('.', '..'):
        raise ValueError('unsafe Git source directory')
    return name, kind, reference


def git_reference(kind, reference):
    """makepkg accepts an object ID in #tag= as well as a named tag."""
    if kind == 'tag' and not re.fullmatch('[0-9a-f]{40,64}', reference):
        return 'refs/tags/' + reference
    return reference


def prepare_exact_git(text, record, downloads, env=None):
    """Avoid broken full-repository advertisement for reviewed immutable sources."""
    reviewed = {
        ('rtmpdump', '32be15a8f00c4231f18d3e11d90cb44b210b9aa824855a9bad86140e8cf94aa0'):
            'git+https://git.ffmpeg.org/rtmpdump#tag=138fdb258d9fc26f1843fd1b891180416c9dc575',
        ('libasyncns', '4956bbe70e57ab2c53d9cd329e871d0181f05ebc56d9411e49365f12b2b14c6c'):
            'git+https://git.0pointer.net/clone/libasyncns.git#commit=68cd5aff1467638c086f1bedcc750e34917168e4',
        ('libcanberra', '51974ca0e4f08d09fcf4f6a61816ee1fbcf07313dc0f55b5c2d8ce3f6426eb90'):
            'git+https://git.0pointer.net/clone/libcanberra.git#commit=c0620e432650e81062c1967cc669829dbd29b310',
    }
    source = reviewed.get((record.get('base'), record.get('recipe_sha256')))
    if source is None:
        return False
    sources = [value for key, values in fields(text).items()
               if key == 'source' or key.startswith('source_') for value in values]
    if sources.count(source) != 1:
        raise ValueError('reviewed exact Git source differs from recipe')
    fetch_git_reference(source, downloads, env=env)
    return True


def source_pins(text, downloads, submodules=None):
    """Resolve refs from the very mirrors makepkg verified and put in its archive."""
    pins = []
    for key, values in fields(text).items():
        if key != 'source' and not key.startswith('source_'):
            continue
        for source in values:
            if source in (submodules or {}):
                pin = submodules[source]
                name = git_source(source.split('#', 1)[0] + '#commit=' + pin['commit'])[0]
                actual = run('git', '-C', str(downloads / name), 'rev-parse',
                             '--verify', 'HEAD^{commit}').strip()
                if actual != pin['commit']:
                    raise ValueError('secondary source changed after gitlink resolution')
                for nested in pin.get('nested_sources', []):
                    actual = run('git', '-C', str(downloads / nested['archive_directory']),
                                 'rev-parse', '--verify', 'HEAD^{commit}').strip()
                    if actual != nested['commit']:
                        raise ValueError('nested source changed after gitlink resolution')
                pins.append(pin)
                continue
            parsed = git_source(source)
            if parsed is None:
                continue
            name, kind, reference = parsed
            ref = git_reference(kind, reference)
            commit = run('git', '-C', str(downloads / name), 'rev-parse',
                         '--verify', ref + '^{commit}').strip()
            if not re.fullmatch('[0-9a-f]{40,64}', commit):
                raise ValueError('invalid resolved Git commit')
            pins.append({'source': source, 'kind': kind, 'reference': reference, 'commit': commit})
    return pins


def pin_identity(pins):
    """Compare immutable selections, independently of explanatory evidence text."""
    return sorted((
        pin['source'], pin['kind'], pin.get('reference', ''), pin['commit'],
        pin_identity(pin.get('nested_sources', []))) for pin in pins)


def atomic_json(path, data):
    """Keep the previous complete record if writing the replacement is interrupted."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json.dumps(data, sort_keys=True, indent=2) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def remember_pins(output, record, pins):
    """Keep the first resolution even if a later recipe fails in the same collection."""
    path = output / 'git-pins' / (record['base'] + '-' + record['recipe_sha256'] + '.json')
    data = {'recipe_sha256': record['recipe_sha256'], 'sources': pins}
    manifest = output / 'ARCH-SOURCES.json'
    if manifest.exists():
        try:
            previous = json.loads(manifest.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError):
            # A killed older writer may have truncated this derived job index.
            # The independently retained git-pins record remains authoritative.
            previous = {'packages': {}}
        for old in previous['packages'].values():
            if (old['base'], old['recipe_sha256']) == (record['base'], record['recipe_sha256']):
                if pin_identity(old.get('git_sources', [])) != pin_identity(pins):
                    raise ValueError(f"{record['base']}: Git source tag resolved differently from the previous collection")
    if path.exists():
        previous = json.loads(path.read_text())
        if (previous.get('recipe_sha256') != record['recipe_sha256'] or
                pin_identity(previous['sources']) != pin_identity(pins)):
            raise ValueError(f"{record['base']}: Git source tag resolved differently from the previous collection")
    else:
        atomic_json(path, data)


def check_recipe(text, record):
    text, _ = unused_sources.filtered_srcinfo(record, text)
    if 'recipe_sha256' in record and 'base' in record:
        cargo_sources.required_tree(record)
    info = fields(text)
    version = f"{info['pkgver'][0]}-{info['pkgrel'][0]}"
    if info.get('epoch', ['0'])[0] != '0':
        version = info['epoch'][0] + ':' + version
    if version != record['version'] or record['name'] not in info.get('pkgname', []):
        raise ValueError('packaging revision does not match the binary name and version')
    submodules = recipe_submodules(text, record)
    # A recipe hash alone cannot bind a moving branch or an unchecked download.
    for key, values in info.items():
        if key != 'source' and not key.startswith('source_'):
            continue
        suffix = key[len('source'):]
        sums = [info.get(algorithm + 'sums' + suffix, [])
                for algorithm in ('sha256', 'sha384', 'sha512', 'b2', 'sha224', 'sha1', 'md5')]
        for index, source in enumerate(values):
            if source in submodules:
                continue
            parsed = git_source(source)
            if parsed is not None:
                if parsed[1] == 'tag' and not any(
                        index < len(values_) and values_[index] != 'SKIP' for values_ in sums) and not (
                        arch_source_fallback.allows_tag(record, source) or
                        historical_sources.allows_tag(record, source)):
                    raise ValueError('unpinned Git tag requires a non-SKIP recipe checksum')
                continue
            elif re.search(r'(?:^|::)(hg|svn|bzr|fossil)(?:\+|://)', source):
                raise ValueError('unpinned VCS source requires a reviewed exact-source recipe')
            elif '://' in source and not source.split('::', 1)[0].endswith(('.sig', '.asc', '.sign')):
                if not any(index < len(values_) and values_[index] != 'SKIP' for values_ in sums):
                    raise ValueError('unchecked remote source requires a reviewed exact-source recipe')


def normalize(source, target, replacements=None):
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
                    content = incoming.extractfile(member) if member.isfile() else None
                    if replacements and normalized in replacements:
                        if not member.isfile():
                            raise ValueError('recipe metadata must be a regular archive member')
                        data = replacements[normalized]
                        member.size = len(data)
                        content = io.BytesIO(data)
                    if member.isfile() and posixpath.basename(normalized) == '.SRCINFO':
                        data = content.read()
                        lines = data.splitlines(keepends=True)
                        # makepkg adds its current date after the generator comment.
                        if (len(lines) > 1 and lines[0].startswith(b'# Generated by makepkg ')
                                and lines[1].startswith(b'# ')):
                            data = lines[0] + b''.join(lines[2:])
                        member.size = len(data)
                        content = io.BytesIO(data)
                    outgoing.addfile(member, content)


def collect(closure, packages, output, exclude=(), repositories=None, *, cook_swh_vault=False):
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
        # Public keyrings are small; keep their socket paths short even when source
        # workspaces live in a deep disk-backed TMPDIR.
        with tempfile.TemporaryDirectory(prefix='emaki-iso-source-') as temp, \
                tempfile.TemporaryDirectory(prefix='es-key-', dir='/tmp') as keytemp:
            root = Path(temp)
            repo = root / 'recipe'
            origin = (str(Path(repositories).resolve() / base) if repositories else
                      f'https://gitlab.archlinux.org/archlinux/packaging/packages/{project_path(base)}.git')
            run('git', 'clone', '--quiet', origin, str(repo))
            revision = exact_revision(repo, record['recipe_sha256'])
            run('git', '-C', str(repo), 'checkout', '--quiet', '--detach', revision)
            recipe_info = run('makepkg', '--printsrcinfo', cwd=repo)
            recipe_urls = fields(recipe_info).get('url', [])
            atomic_json(output / 'RECIPE.json', {
                'base': base, 'recipe_sha256': record['recipe_sha256'], 'revision': revision,
                'upstream_url': recipe_urls[0] if len(recipe_urls) == 1 else ''})
            original_recipe = (repo / 'PKGBUILD').read_bytes()
            cargo_tree = cargo_sources.required_tree(record, original_recipe.decode(),
                (path.relative_to(repo).as_posix() for path in repo.rglob('*.wrap')))
            go_tree = go_sources.required_tree(record)
            execution_recipe, execution_info, omissions = unused_sources.effective_recipe(
                record, original_recipe, recipe_info)
            (repo / 'PKGBUILD').write_bytes(execution_recipe)
            # Verification uses only this recipe's public keys.
            env = dict(os.environ, SRCDEST=str(root / 'downloads'), SRCPKGDEST=str(root / 'archives'),
                       GNUPGHOME=keytemp, EMAKI_SWH_VAULT_COOK='1' if cook_swh_vault else '0')
            keyring = Path(env['GNUPGHOME'])
            (keyring / 'gpg.conf').write_text('no-autostart\nno-auto-key-retrieve\n')
            for key in sorted((repo / 'keys' / 'pgp').glob('*.asc')):
                run('gpg', '--batch', '--no-autostart', '--import', str(key), env=env)
            verification_keys = source_keys.prepare(
                fields(execution_info).get('validpgpkeys', []), repo, env, run)
            config = root / 'makepkg.conf'
            config.write_text(
                'source /etc/makepkg.conf\n'
                'DLAGENTS=(\n'
                "'file::/usr/bin/curl -qgC - -o %o %u'\n"
                "'ftp::/usr/bin/curl -qgfLC - --retry 3 --user-agent emaki-publish -o %o %u'\n"
                "'http::/usr/bin/curl -qgfLC - --retry 3 --user-agent emaki-publish -o %o %u'\n"
                "'https::/usr/bin/curl -qgfLC - --retry 3 --user-agent emaki-publish -o %o %u'\n)\n")
            env.update(MAKEPKG_CONF=str(config), GIT_CONFIG_COUNT='1',
                       GIT_CONFIG_KEY_0='http.userAgent', GIT_CONFIG_VALUE_0='emaki-publish')
            Path(env['SRCDEST']).mkdir()
            Path(env['SRCPKGDEST']).mkdir()
            def refresh_verification_keys():
                nonlocal verification_keys
                refreshed = source_keys.prepare(fields(execution_info).get('validpgpkeys', []),
                                                repo, env, run, refresh=True)
                verification_keys = list({item['fingerprint']: item for item in
                                          [*verification_keys, *refreshed]}.values())
                return bool(refreshed)
            try:
                check_recipe(recipe_info, record)
                routes = source_routes.prepare_routes(execution_info, record, Path(env['SRCDEST']), env, run,
                                                      rejected_dir=output / 'rejected' / base,
                                                      refresh_keys=refresh_verification_keys)
                savannah = savannah_git.prepare(execution_info, Path(env['SRCDEST']), env, run,
                                                record=record)
                exact_git = prepare_exact_git(execution_info, record, Path(env['SRCDEST']), env)
                submodules = prepare_submodules(execution_info, record, Path(env['SRCDEST']), env)
                historical = historical_sources.prepare(execution_info, record, Path(env['SRCDEST']), run)
                routes['evidence'].extend(historical)
                source_flags = [*routes['makepkg_flags'],
                                *(['--holdver'] if submodules or historical or savannah or exact_git else [])]
                try:
                    run('makepkg', '--allsource', '--force', *source_flags, cwd=repo, env=env)
                except subprocess.CalledProcessError as error:
                    # A bundled primary can lack a later signing subkey. Refresh only
                    # recipe-authorized full primaries, never the reported short ID.
                    diagnostic = (error.stdout or '') + (error.stderr or '')
                    if 'unknown public key' not in diagnostic.lower():
                        raise
                    if not refresh_verification_keys():
                        raise
                    run('makepkg', '--allsource', '--force', '--holdver', *source_flags, cwd=repo, env=env)
                pins = source_pins(execution_info, Path(env['SRCDEST']), submodules)
                for route_file in sorted(Path(env['SRCDEST']).rglob('emaki-source-route.json')):
                    routes['evidence'].append(json.loads(route_file.read_text()))
                archives = list(Path(env['SRCPKGDEST']).glob('*.src.tar.*'))
                if len(archives) != 1:
                    raise ValueError(f'{base}: expected one complete source archive')
                complete = archives[0]
            except (ValueError, subprocess.CalledProcessError):
                if not arch_source_fallback.eligible(record):
                    raise
                print(f'{base}: normal source route refused; checking the exact Arch source package',
                      file=sys.stderr)
                complete, evidence, pins = arch_source_fallback.collect(
                    record, original_recipe, recipe_info, root, run, recipe_dir=repo)
                routes = {'makepkg_flags': [], 'evidence': [evidence]}
            remember_pins(output, record, pins)
            supplement = None
            go_supplement = None
            if cargo_tree or go_tree:
                run('makepkg', '--nobuild', '--noprepare', '--nodeps', '--holdver',
                    *routes['makepkg_flags'], cwd=repo, env=env)
            if cargo_tree:
                supplement_dir = root / 'cargo-supplement'
                prepared = cargo_sources.prepare_tree(
                    dict(record, git_sources=pins), repo / 'src' / cargo_tree,
                    Path(env['SRCDEST']), run)
                supplement = cargo_sources.create_supplement(
                    record, repo / 'src' / cargo_tree, supplement_dir, env, prepared)
                complete = root / 'complete.tar'
                cargo_sources.add_supplement(archives[0], supplement_dir, complete, record)
            if go_tree:
                supplement_dir = root / 'go-supplement'
                go_supplement = go_sources.create_supplement(
                    record, repo / 'src' / go_tree, supplement_dir, env)
                with_go = root / 'complete-go.tar'
                go_sources.add_supplement(complete, supplement_dir, with_go, record)
                complete = with_go
            if verification_keys:
                with_keys = root / 'complete-keys.tar'
                source_keys.add_keys(complete, with_keys, repo, base, verification_keys)
                complete = with_keys
            source = root / (base + '.src.tar.gz')
            normalize(complete, source, {base + '/PKGBUILD': original_recipe,
                                        base + '/.SRCINFO': recipe_info.encode()})
            sha = digest(source)
            target = output / 'sources' / 'sha256' / sha / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            archive = {'archive': source.name, 'sha256': sha, 'size': source.stat().st_size,
                       'revision': revision, 'git_sources': pins}
            if routes['evidence']:
                archive['source_routes'] = routes['evidence']
            if verification_keys:
                archive['verification_keys'] = verification_keys
            if omissions:
                archive['omitted_sources'] = omissions
            if supplement:
                archive['cargo_supplement'] = supplement
            if go_supplement:
                archive['go_supplement'] = go_supplement
            validate_archive(target, dict(record, **archive))
            built[identity] = archive
            record.update(archive)
    manifest = {'schema': 1, 'closure_sha256': digest(closure), 'packages': selected}
    atomic_json(output / 'ARCH-SOURCES.json', manifest)
    return manifest


def validate_archive(source, record):
    """Inspect original recipe bytes and package identity without extracting or executing."""
    try:
        with tarfile.open(source, 'r:*') as archive:
            contents = {}
            for name in ('PKGBUILD', '.SRCINFO'):
                path = record['base'] + '/' + name
                members = [member for member in archive.getmembers()
                           if posixpath.normpath(member.name) == path]
                if len(members) != 1 or not members[0].isfile():
                    raise ValueError('source archive requires one regular ' + path)
                contents[name] = archive.extractfile(members[0]).read()
            if hashlib.sha256(contents['PKGBUILD']).hexdigest() != record['recipe_sha256']:
                raise ValueError('archived PKGBUILD differs from the binary recipe hash')
            info = fields(contents['.SRCINFO'].decode())
            if any(len(info.get(key, [])) != 1 for key in ('pkgver', 'pkgrel')):
                raise ValueError('archived .SRCINFO has ambiguous package version')
            if len(info.get('epoch', [])) > 1:
                raise ValueError('archived .SRCINFO has ambiguous package epoch')
            version = info['pkgver'][0] + '-' + info['pkgrel'][0]
            if info.get('epoch', ['0'])[0] != '0':
                version = info['epoch'][0] + ':' + version
            if version != record['version'] or record['name'] not in info.get('pkgname', []):
                raise ValueError('archived .SRCINFO differs from the binary name and version')
            source_routes.validate_routes(source, record)
            source_keys.validate(archive, record, info)
            check_recipe(contents['.SRCINFO'].decode(), record)
            cargo_sources.required_tree(record, contents['PKGBUILD'].decode(), archive.getnames())
            cargo_sources.validate_supplement(archive, record)
            go_sources.validate_supplement(archive, record)
            unused_sources.validate_evidence(record, record.get('omitted_sources', []))
    except (tarfile.TarError, UnicodeError, EOFError) as error:
        raise ValueError('invalid source archive: ' + str(error)) from error


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate source manifest field: ' + key)
        result[key] = value
    return result


def validate_missing(path, closure, expected_inventory):
    """Validate openly missing sources against the same exact binary inventory."""
    missing = json.loads(Path(path).read_text(), object_pairs_hook=_unique_object)
    if (not isinstance(missing, dict) or type(missing.get('schema')) is not int
            or missing.get('schema') != 1
            or missing.get('closure_sha256') != digest(closure)):
        raise ValueError('missing source manifest does not describe this image package list')
    bases = missing.get('bases')
    if not isinstance(bases, list):
        raise ValueError('missing source manifest requires a bases list')
    records = {}
    seen = set()
    for group in bases:
        if not isinstance(group, dict):
            raise ValueError('invalid missing source base')
        base = group.get('base', '')
        version = group.get('version', '')
        recipe = group.get('recipe_sha256', '')
        if (not isinstance(base, str) or not re.fullmatch(r'[A-Za-z0-9@_+][A-Za-z0-9@._+\-]*', base)
                or not isinstance(version, str) or not version
                or not isinstance(recipe, str) or not re.fullmatch('[0-9a-f]{64}', recipe)):
            raise ValueError('invalid missing source base identity')
        identity = (base, version, recipe)
        if identity in seen:
            raise ValueError('duplicate missing source base')
        seen.add(identity)
        reason = group.get('reason')
        url = group.get('upstream_url')
        if not isinstance(reason, str) or not reason.strip() or any(ord(c) < 32 or ord(c) == 127 for c in reason):
            raise ValueError('invalid missing source refusal reason')
        if not isinstance(url, str) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
            raise ValueError('invalid missing source upstream URL')
        # Empty when the base's packages name different upstream pages: the Arch recipe points there.
        parsed = urlsplit(url)
        if url and (parsed.scheme not in ('http', 'https') or not parsed.hostname
                    or parsed.username or parsed.password):
            raise ValueError('invalid missing source upstream URL')
        revision = group.get('revision')
        if revision is not None and (not isinstance(revision, str)
                or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9._/+\-]*', revision)
                or '..' in revision or '//' in revision or revision.endswith('/')):
            raise ValueError('invalid missing source recipe revision')
        packages = group.get('packages')
        if not isinstance(packages, dict) or not packages:
            raise ValueError('missing source base requires package identities')
        for filename, record in packages.items():
            if filename in records:
                raise ValueError('duplicate missing source package')
            if (filename not in expected_inventory or record != expected_inventory[filename]
                    or any(record.get(key) != group[key] for key in ('base', 'version', 'recipe_sha256'))):
                raise ValueError(f'{filename}: missing source record differs from the image binary')
            records[filename] = dict(record, reason=reason, upstream_url=url)
            if revision is not None:
                records[filename]['revision'] = revision
    return records


def validate(manifest_path, closure, packages, exclude=(), *, expected_inventory=None,
             missing_sources=None):
    """Bind sources to the actual image, with no network or recipe execution at publish time."""
    path = Path(manifest_path)
    manifest = json.loads(path.read_text())
    if manifest.get('schema') != 1 or manifest.get('closure_sha256') != digest(closure):
        raise ValueError('Arch source manifest does not describe this image package list')
    expected = (inventory(closure, packages, exclude) if expected_inventory is None
                else expected_inventory)
    records = manifest.get('packages', {})
    missing = validate_missing(missing_sources, closure, expected) if missing_sources is not None else {}
    if records.keys() & missing.keys():
        raise ValueError('Arch source manifest and missing sources overlap')
    if records.keys() | missing.keys() != expected.keys():
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
        validate_archive(source, record)
        objects[key] = source
    return records, objects


def missing_directions(records):
    lines = ['Sources not yet copied by Emaki', '',
             'The following package sources are not yet in Emaki\'s source archive.',
             'Find the Arch build recipe and upstream sources at the addresses below.',
             "Emaki's own copy will be added.", '']
    for filename, record in sorted(records.items()):
        recipe_url = ('https://gitlab.archlinux.org/archlinux/packaging/packages/' +
                      project_path(record['base']))
        if record.get('revision'):
            recipe_url += '/-/tree/' + quote(record['revision'], safe='')
        upstream = record['upstream_url'] or 'see the Arch recipe'
        lines.extend([f"{record['name']} {record['version']}", f'  Package: {filename}',
                      f'  Arch recipe: {recipe_url}', f'  Upstream: {upstream}',
                      f"  Reason: {record['reason']}", ''])
    return '\n'.join(lines) + '\n'


def directions(records, base_url, *, partial=False):
    lines = ['Arch package sources in this image', '',
             'These archives contain the build recipes and upstream source files for the',
             ('collected GPL and LGPL packages supplied by Arch Linux in this image.' if partial else
              'GPL and LGPL packages supplied by Arch Linux in this image.'),
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
    parser.add_argument('--inventory-only', action='store_true',
                        help='write image metadata to --output without collecting sources')
    parser.add_argument('--pkglist', type=Path, help='installed live package list to verify')
    parser.add_argument('--target-closure', type=Path, help='shipped target archive list to verify')
    parser.add_argument('--repositories', type=Path, help='local Arch packaging repository clones')
    parser.add_argument('--cook-swh-vault', action='store_true',
                        help='allow remote Software Heritage vault cooking requests (not read-only)')
    args = parser.parse_args()
    if bool(args.pkglist) != bool(args.target_closure) or (args.pkglist and not args.inventory_only):
        parser.error('--pkglist and --target-closure must be used together with --inventory-only')
    # Keep this list aligned with the packages published in the Emaki repository.
    root = Path(__file__).resolve().parents[1]
    exclude = {path.parent.name for path in root.glob('*/PKGBUILD')}
    if args.inventory_only:
        write_image_inventory(args.closure, args.packages, args.output, exclude)
        if args.pkglist:
            validate_live_closure(args.closure, args.pkglist, args.target_closure, args.output)
    else:
        collect(args.closure, args.packages, args.output, exclude, args.repositories,
                cook_swh_vault=args.cook_swh_vault)


if __name__ == '__main__':
    main()
