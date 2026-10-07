#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Retain dependencies omitted by makepkg --allsource for reviewed Cargo recipes."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
import tomllib


# Full PKGBUILD review establishes the build workspace and dependency fetches in every recipe function.
RECIPES = {
    'f805a80e6086a363d8495c88009f8c7e211239153c74d20c4eb2b7fbddc4d871':
        ('bcachefs-tools', 'bcachefs-tools'),
    '4fe6ac610f7bffeb0a6021c11d0a54757edc15f73be74fdb67ed131142bd6986':
        ('libimagequant', 'libimagequant-4.4.1'),
    '5f157fb66b7d8d4363e93c13d339549cecba4d58f2602fac641fef4155fdebfe':
        ('glycin', 'glycin'),
    'c91bb52f1245f8af4a411f53d0088847314f6d65b98b5a9eb33a4b0afe0a1319':
        ('librsvg', 'librsvg'),
    '4e4215ac5f1a16dee1700a8bb668fff115a585a2fd4b9856c6fd0761930e7826':
        ('niri', 'niri-26.04'),
    '9cac571e77ab2af60eee0a76967be03b7f6b4a7508824711a93ab3bdf2629d14':
        ('greetd', 'greetd-0.10.3'),
    'eb98fb86cd346ff39bf2f6c181834f807d8525f650fb304d208a75443bb91543':
        ('greetd-regreet', 'ReGreet-0.5.0'),
    'f1c2f40ca3a5e0ba563a80558fbc5061a44cb2038d3ffa573f11c022b03448d4':
        ('ripgrep-all', 'ripgrep-all-0.10.10'),
    'b7f9865c72b9e72dbb6b079b0176aff4dcfeb8478ad77f4194709e05b64d02fb':
        ('sequoia-sq', 'sequoia-sq'),
    '12b196608868663de6cf5cddb43dcbca435f3e49c70d2c0b46da4c42e1ac2209':
        ('thin-provisioning-tools', 'thin-provisioning-tools'),
    'ec47c5bc391e769104d1e6129ad7c6f240f04b392b94826b62c7d5230914e176':
        ('wpaperd', 'wpaperd'),
}


# Whole-recipe review of apparent fetchers that add no external build inputs.
IN_TREE_RECIPES = {
    # The only apparent fetcher runs the newly built ./rsync -V in check().
    'f02c414fb424eb95513d3111a17dce252f1a0fa31a2eb09c0e0f75ca25753327': 'rsync',
    # go/Makefile uses -mod=vendor and symlinks the retained go/{cap,psx} modules.
    'ab8bb79629c958de181d68739cad92d4b36829abada1ede71eab67d32092bd74': 'libcap',
    # updatelist is a manual maintainer helper; package installs the pinned file.
    'a5e858391af5d3bca57b45959189ac0ced69b0243e33d57dacb7f5f0c9865e97': 'pacman-mirrorlist',
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def dependency_fetches(recipe_text):
    """Conservatively inspect the whole recipe, including build/check/package hooks.

    Tool options, toolchain selectors and environment command aliases cannot hide
    a fetch. Indirect build tools are covered by reviewed base identities; this
    scan does not claim to interpret arbitrary shell or upstream build programs.
    """
    text = '\n'.join(line for line in recipe_text.splitlines()
                     if not line.lstrip().startswith('#')).replace('\\\n', ' ')
    # Array assignments contain package names and prose, not shell commands.
    # Preserve command substitutions because they do execute while assigning.
    text = re.sub(r'(?m)^\s*[A-Za-z_][A-Za-z_0-9]*(?:\+)?=\(([^)]*)\)',
                  lambda match: match[0] if '$(' in match[0] or '`' in match[0] else '', text)
    command = (r'(?:^|[\n;|&(){}`])\s*'
               r'(?:(?:command|exec|time|if|then|elif|while|until|do|!)\s+|'
               r'env(?:\s+(?:-u|--unset)\s+[A-Za-z_][A-Za-z_0-9]*|'
               r'\s+(?:-i|--ignore-environment))*\s+)*'
               r'(?:[A-Za-z_][A-Za-z_0-9]*=(?:[^\s;\'"]|\'[^\']*\'|"[^"]*")*\s+)*'
               r'(?:(?:timeout|xargs|eval|(?:ba|da)?sh)\b[^;\n]*?)*'
               r'["\']?(?:(?:[./][A-Za-z0-9_./-]*|\$[A-Za-z_][A-Za-z_0-9]*|\$\{[^}]+\})["\']?/)?')
    tools = (r'(?:cargo|go|npm|npx|yarn|pnpm|pip(?:[0-9]+(?:\.[0-9]+)*)?|pipx|pipenv|poetry|uv|'
             r'gem|bundle|bundler|composer|mvn|gradle|lein|cabal|stack|'
             r'curl|wget|aria2c|svn|rsync|cpanm)["\']?\s')
    patterns = (
        command + tools,
        command + r'\$(?:\{)?(?:CARGO|GO|NPM|PIP|CURL|WGET)(?:_BIN)?\b',
        r'\b(?:CARGO_HOME|GOFLAGS|GOMODCACHE|GOPATH)\s*=',
        command + r'python[0-9.]*["\']?\s+-m\s+(?:pip|ensurepip)\b',
        command + r'git["\']?\s+[^;\n]*?\b(?:clone|fetch|pull)\b',
        command + r'meson["\']?\s+subprojects\s+download\b',
        r'\.wrap\b|\[wrap-(?:file|git|hg|svn)\]',
    )
    return any(re.search(pattern, text) for pattern in patterns)



def required_tree(record, recipe_text=None, source_names=()):
    match = RECIPES.get(record['recipe_sha256'])
    if match:
        if record['base'] != match[0]:
            raise ValueError('Cargo recipe base differs from its reviewed identity')
        return match[1]
    if record['base'] in {item[0] for item in RECIPES.values()}:
        raise ValueError('dependency inputs require a reviewed recipe')
    import go_sources
    if go_sources.required_tree(record):
        return None
    in_tree = IN_TREE_RECIPES.get(record['recipe_sha256'])
    if in_tree:
        if record['base'] != in_tree:
            raise ValueError('in-tree recipe base differs from its reviewed identity')
        return None
    if record['base'] in IN_TREE_RECIPES.values():
        raise ValueError('dependency inputs require a reviewed recipe')
    if (any(str(name).endswith('.wrap') for name in source_names)
            or (recipe_text and dependency_fetches(recipe_text))):
        raise ValueError('dependency inputs require a reviewed recipe')
    return None


GLYCIN_COMMITS = ('476720131410e80436e65b9e24fcab52467e1b0f',
                  '590c6d215afb3ed055909d5f208d2db200386409')
RESTORE = """Copyright (C) 2026 Artur Yakymenko
SPDX-License-Identifier: GPL-3.0-or-later

Retained preparation-time Cargo sources

Restore the primary source and apply the archived recipe's preparation patches.
Its Cargo.lock must match this supplement's Cargo.lock exactly. For glycin this
includes both cherry-picks recorded in the recipe and supplement evidence.
Copy the vendor directory into that workspace's root. Merge config.toml into
<workspace>/.cargo/config.toml (preserve other workspace configuration). Cargo
resolves its directory = "vendor" relative to the workspace containing .cargo.
Use a writable temporary CARGO_HOME and run cargo with --frozen; no registry
cache is needed. Keep the original PKGBUILD for the build flags and features.
"""


def prepare_tree(record, tree, downloads, run):
    """Replay only reviewed dependency-affecting preparation, without fetching."""
    required_tree(record)
    tree = Path(tree)
    reconstruction = None
    if record['base'] == 'libimagequant':
        lock, reconstruction = reconstructed_lock(record)
        for name, checksum in reconstruction['manifests'].items():
            if sha256((tree / name).read_bytes()) != checksum:
                raise ValueError('reconstructed Cargo lock source manifest mismatch')
        if (tree / 'Cargo.lock').exists() and (tree / 'Cargo.lock').read_bytes() != lock:
            raise ValueError('unexpected lock in reconstructed Cargo workspace')
        (tree / 'Cargo.lock').write_bytes(lock)
    if not (tree / 'Cargo.lock').is_file():
        raise ValueError('reviewed Cargo source has no pinned Cargo.lock; '
                         'the binary build lock is required')
    original = (tree / 'Cargo.lock').read_bytes()
    proof = {'original_lock_sha256': sha256(original), 'commits': []}
    if reconstruction:
        proof['reconstruction'] = reconstruction
        # The upstream archive has no lock; do not claim this was an original lock.
        proof.pop('original_lock_sha256')
    if (tree / '.git').exists():
        head = run('git', '-C', str(tree), 'rev-parse', 'HEAD').strip()
        # This tree must come from the same verified primary archived by makepkg.
        name = tree.name
        matches = [pin for pin in record.get('git_sources', [])
                   if pin['source'].split('::')[-1].split('#')[0].split('?')[0]
                   .rstrip('/').rsplit('/', 1)[-1].removesuffix('.git') == name]
        if len(matches) != 1 or matches[0]['commit'] != head:
            raise ValueError('Cargo workspace differs from the verified primary Git source')
        committed = run('git', '-C', str(tree), 'show', head + ':Cargo.lock').encode()
        if committed != original:
            raise ValueError('Cargo workspace lock differs from its primary Git commit')
        proof['source_commit'] = head
    if record['base'] == 'glycin':
        for commit in GLYCIN_COMMITS:
            actual = run('git', '-C', str(tree), 'rev-parse', commit + '^{commit}').strip()
            if actual != commit:
                raise ValueError('missing reviewed glycin preparation commit')
            run('git', '-c', 'core.hooksPath=/dev/null', '-C', str(tree),
                'cherry-pick', '--no-commit', commit)
        proof['commits'] = list(GLYCIN_COMMITS)
    # librsvg's checksum-pinned patch changes only one PNG reference fixture;
    # greetd removes a Rust documentation attribute. Neither changes Cargo inputs.
    proof['prepared_lock_sha256'] = sha256((tree / 'Cargo.lock').read_bytes())
    return proof


def reconstructed_lock(record):
    """Return only the reviewed image binary's date-filtered registry resolution."""
    directory = Path(__file__).with_name('cargo-locks')
    proof = json.loads((directory / 'libimagequant-4.4.1-2.json').read_text())
    for key in ('base', 'version', 'recipe_sha256', 'binary_sha256'):
        if record.get(key) != proof[key]:
            raise ValueError('reconstructed Cargo lock requires the reviewed image binary')
    lock = (directory / 'libimagequant-4.4.1-2.lock').read_bytes()
    if sha256(lock) != proof['lock_sha256']:
        raise ValueError('reconstructed Cargo lock checksum mismatch')
    versions = {item['name']: item['version'] for item in locked_packages(lock)}
    if any(versions.get(name) != version for name, version in proof['binary_versions'].items()):
        raise ValueError('reconstructed Cargo lock differs from binary crate versions')
    selected = {item['name']: item for item in proof['packages']}
    for package in locked_packages(lock):
        item = selected.get(package['name'], {})
        entry = item.get('selected_entry', {})
        if (item.get('version') != package['version']
                or entry.get('cksum') != package['checksum']
                or entry.get('vers') != package['version']
                or not entry.get('pubtime')
                or entry['pubtime'] > proof['cutoff']):
            raise ValueError('reconstructed Cargo lock publication evidence mismatch')
    return lock, proof


def locked_packages(data):
    packages = tomllib.loads(data.decode())['package']
    result = []
    for package in packages:
        source = package.get('source')
        if not source:
            continue
        identity = {key: package[key] for key in ('name', 'version', 'source')}
        if source.startswith('registry+'):
            checksum = package.get('checksum', '')
            if not re.fullmatch('[0-9a-f]{64}', checksum):
                raise ValueError('Cargo registry input has no checksum in archived lock')
            identity['checksum'] = checksum
        elif source.startswith('git+'):
            if not re.search(r'#[0-9a-f]{40}$', source):
                raise ValueError('Cargo Git input has no full commit in archived lock')
        else:
            raise ValueError('unsupported Cargo locked source')
        result.append(identity)
    return sorted(result, key=lambda item: (item['name'], item['version'], item['source']))


def inspect_vendor(lock, read, names):
    """Check Cargo's per-file hashes and require every external locked package."""
    packages = locked_packages(lock)
    remaining = list(packages)
    files = {}
    for path in sorted(names):
        if not path.startswith('vendor/'):
            continue
        files[path] = sha256(read(path))
    for path in sorted(files):
        if not path.endswith('/.cargo-checksum.json'):
            continue
        directory = path.rsplit('/', 1)[0]
        checksum = json.loads(read(path))
        manifest = tomllib.loads(read(directory + '/Cargo.toml').decode())['package']
        matches = [item for item in remaining
                   if item['name'] == manifest['name'] and item['version'] == manifest['version']
                   and item.get('checksum') == checksum.get('package')]
        if len(matches) != 1:
            raise ValueError('vendored package does not uniquely match archived Cargo.lock')
        remaining.remove(matches[0])
        expected = checksum.get('files', {})
        actual = {name[len(directory) + 1:] for name in files
                  if name.startswith(directory + '/') and name != path}
        if actual != set(expected):
            raise ValueError('vendored Cargo file inventory differs from checksum manifest')
        for name, digest in expected.items():
            if files.get(directory + '/' + name) != digest:
                raise ValueError('vendored Cargo file checksum mismatch')
    if remaining:
        raise ValueError('archived Cargo supplement is missing locked dependencies')
    return packages, files


def create_supplement(record, source_tree, destination, env=None, preparation=None):
    """Run against the verified source tree, without invoking prepare() or build()."""
    if required_tree(record) is None:
        raise ValueError('Cargo supplement requires a reviewed recipe')
    source_tree, destination = Path(source_tree).resolve(), Path(destination).resolve()
    destination.mkdir(parents=True)
    lock = (source_tree / 'Cargo.lock').read_bytes()
    locked_packages(lock)
    if preparation is None:
        if record['base'] in ('glycin', 'libimagequant'):
            raise ValueError(record['base'] + ' requires reviewed preparation before vendoring')
        preparation = {'original_lock_sha256': sha256(lock),
                       'prepared_lock_sha256': sha256(lock), 'commits': []}
    if preparation.get('prepared_lock_sha256') != sha256(lock):
        raise ValueError('Cargo lock changed after reviewed preparation')
    vendor_env = dict(os.environ if env is None else env,
                      CARGO_HTTP_USER_AGENT='emaki-publish',
                      CARGO_NET_GIT_FETCH_WITH_CLI='true',
                      CARGO_HOME=str(destination.parent / (destination.name + '-cargo-home')))
    result = subprocess.run(['cargo', 'vendor', '--locked', '--versioned-dirs',
                             str(destination / 'vendor')], cwd=source_tree, env=vendor_env,
                            check=True, capture_output=True, text=True)
    if (source_tree / 'Cargo.lock').read_bytes() != lock:
        raise ValueError('cargo vendor changed the archived source lock')
    (destination / 'Cargo.lock').write_bytes(lock)
    restore = RESTORE
    if 'reconstruction' in preparation:
        restore += ('\nThis source has no upstream Cargo.lock. Copy this supplement\'s Cargo.lock\n'
                    'into the workspace before building. Its publication-date reconstruction\n'
                    'and binary version cross-check are recorded in reconstruction.json;\n'
                    'historical yank state and unnamed versions are not independently proven.\n')
        (destination / 'reconstruction.json').write_text(json.dumps(
            preparation['reconstruction'], indent=2, sort_keys=True) + '\n')
    (destination / 'README.sources').write_text(restore)
    # Cargo prints the absolute destination; retained configuration is relocatable.
    (destination / 'config.toml').write_text(result.stdout.replace(
        str(destination / 'vendor'), 'vendor'))
    names = []
    for path in destination.rglob('*'):
        if path.is_symlink():
            raise ValueError('Cargo supplement contains a symbolic link')
        if path.is_file():
            names.append(path.relative_to(destination).as_posix())
    packages, files = inspect_vendor(lock, lambda name: (destination / name).read_bytes(), names)
    return {'path': record['base'] + '/_cargo', 'lock_sha256': sha256(lock),
            'preparation': preparation, 'readme_sha256': sha256(restore.encode()),
            'config_sha256': sha256((destination / 'config.toml').read_bytes()),
            'packages': packages, 'files': files,
            'method': 'cargo vendor --locked --versioned-dirs'}


def add_supplement(source_archive, destination, target_archive, record):
    """Embed the supplement in the same content-addressed object as the source."""
    prefix = record['base'] + '/_cargo'
    with tarfile.open(source_archive, 'r:*') as source, tarfile.open(target_archive, 'w') as target:
        for member in source:
            if member.name == prefix or member.name.startswith(prefix + '/'):
                raise ValueError('source archive already contains a Cargo supplement')
            target.addfile(member, source.extractfile(member) if member.isfile() else None)
        target.add(destination, arcname=prefix, recursive=True)


def validate_supplement(archive, record):
    required = required_tree(record)
    evidence = record.get('cargo_supplement')
    if not required:
        if evidence:
            raise ValueError('unreviewed Cargo supplement')
        return
    prefix = record['base'] + '/_cargo'
    if not isinstance(evidence, dict) or evidence.get('path') != prefix:
        raise ValueError('preparation-time Cargo dependencies are not archived and listed')
    members = {}
    for member in archive.getmembers():
        if member.name.startswith(prefix + '/'):
            name = member.name[len(prefix) + 1:]
            if '..' in PurePosixPath(name).parts or name.startswith('/'):
                raise ValueError('unsafe Cargo supplement member')
            if member.isdir():
                continue
            if not member.isfile() or name in members:
                raise ValueError('Cargo supplement contains links or duplicate files')
            members[name] = member
    def read(name):
        if name not in members:
            raise ValueError('Cargo supplement is incomplete')
        return archive.extractfile(members[name]).read()
    lock = read('Cargo.lock')
    proof = evidence.get('preparation', {})
    if proof.get('prepared_lock_sha256') != sha256(lock):
        raise ValueError('Cargo preparation evidence differs from retained lock')
    expected_commits = list(GLYCIN_COMMITS) if record['base'] == 'glycin' else []
    if proof.get('commits') != expected_commits:
        raise ValueError('Cargo preparation evidence omits reviewed changes')
    if record['base'] == 'libimagequant':
        expected_lock, reconstruction = reconstructed_lock(record)
        if (lock != expected_lock or proof.get('reconstruction') != reconstruction
                or json.loads(read('reconstruction.json')) != reconstruction):
            raise ValueError('Cargo reconstruction evidence mismatch')
    elif not re.fullmatch('[0-9a-f]{64}', proof.get('original_lock_sha256', '')):
        raise ValueError('Cargo preparation has no original source lock checksum')
    if 'source_commit' in proof and proof['source_commit'] not in {
            pin['commit'] for pin in record.get('git_sources', [])}:
        raise ValueError('Cargo preparation is not bound to archived Git sources')
    if sha256(read('README.sources')) != evidence.get('readme_sha256'):
        raise ValueError('Cargo restore instructions checksum mismatch')
    if sha256(lock) != evidence.get('lock_sha256'):
        raise ValueError('Cargo supplement lock checksum mismatch')
    if sha256(read('config.toml')) != evidence.get('config_sha256'):
        raise ValueError('Cargo supplement configuration checksum mismatch')
    packages, files = inspect_vendor(lock, read, members)
    if packages != evidence.get('packages') or files != evidence.get('files'):
        raise ValueError('Cargo supplement differs from manifest evidence')
