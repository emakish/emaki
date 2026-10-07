#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Save the prepared source tree used by a package, before compilation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import shlex
import subprocess
import sys
import tarfile
import tempfile

PACKAGES = ('niri-emaki', 'quickshell-emaki', 'xdg-desktop-portal-gnome-emaki',
            'emaki-config', 'emaki-nvidia', 'emaki-desktop', 'emaki-apps', 'emaki-installer',
            'emaki-keyring', 'emaki-mirrorlist', 'emaki')


def run(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout


def public_policy(repo, commit):
    """Read the committed exclusions and transformation programs."""
    policy = subprocess.run(['git', '-C', str(repo), 'show',
                             f'{commit}:scripts/make-public.sh'], capture_output=True, text=True)
    if policy.returncode != 0:
        raise ValueError('cannot read committed public source exclusion policy')
    exclusions, notes = [], []
    for name, target in (('EXCLUDE', exclusions), ('NOTES', notes)):
        match = re.search(r'^' + name + r'=\((.*?)\)', policy.stdout, re.M | re.S)
        if not match:
            raise ValueError('missing public source exclusion policy')
        target.extend(shlex.split(match[1], comments=True))
        if not target:
            raise ValueError('empty public source exclusion policy')
    programs = {}
    for name in ('tree', 'sanitize', 'guard'):
        marker = f"cat > \"$work/{name}.py\" <<'PY'\n"
        if policy.stdout.count(marker) != 1:
            raise ValueError(f'missing public source {name} policy')
        body, separator, _ = policy.stdout.split(marker, 1)[1].partition('\nPY\n')
        if not separator:
            raise ValueError(f'incomplete public source {name} policy')
        programs[name] = body
    return exclusions, notes, programs


def public_excluded(path, exclusions, notes):
    parts = path.casefold().split('/')
    return (any(path == item or path.startswith(item + '/') for item in exclusions)
            or any(parts[i:i + len(n)] == n for item in notes
                   for n in [item.casefold().split('/')]
                   for i in range(len(parts) - len(n) + 1)))


def checkout(repo, output, commit):
    """Extract the build inputs using the committed public export exclusions."""
    exclusions, notes, programs = public_policy(repo, commit)
    # Archive substitution could insert private commit metadata before the guard sees it.
    paths = run('git', '-C', str(repo), 'ls-tree', '-r', '-z', '--name-only', commit).split('\0')
    for path in paths:
        if path and Path(path).name == '.gitattributes':
            if 'export-subst' in run('git', '-C', str(repo), 'show', f'{commit}:{path}'):
                raise ValueError('public source attributes enable export-subst')
    attributes = Path(run('git', '-C', str(repo), 'rev-parse', '--path-format=absolute',
                          '--git-path', 'info/attributes').strip())
    if attributes.exists() and 'export-subst' in attributes.read_text():
        raise ValueError('repository attributes enable export-subst')

    def excluded(path):
        return public_excluded(path, exclusions, notes)

    output.mkdir(parents=True, exist_ok=False)
    with subprocess.Popen(['git', '-C', str(repo), '-c', 'core.attributesFile=/dev/null',
                           'archive', '--format=tar', commit], stdout=subprocess.PIPE) as process:
        with tarfile.open(fileobj=process.stdout, mode='r|') as contents:
            for member in contents:
                if not excluded(member.name.rstrip('/')):
                    if member.issym():
                        target = posixpath.normpath(posixpath.join(posixpath.dirname(member.name), member.linkname))
                        system_link = (member.name.startswith('iso/profile/airootfs/')
                                       and member.linkname.startswith(('/usr/', '/etc/', '/run/', '/dev/')))
                        if not system_link and (target.startswith(('/', '../')) or excluded(target)):
                            raise ValueError(f'source link leaves public inputs: {member.name}')
                    # Git trees cannot contain files below a tracked symbolic link.
                    contents.extract(member, output, filter='fully_trusted')
        if process.wait() != 0:
            raise ValueError('could not read committed build inputs')
    with tempfile.TemporaryDirectory(prefix='emaki-public-policy-') as temporary:
        work = Path(temporary)
        for name, body in programs.items():
            (work / f'{name}.py').write_text(body)
        try:
            run(sys.executable, str(work / 'tree.py'), 'links', str(output),
                *exclusions, '--', *notes)
            run(sys.executable, str(work / 'sanitize.py'), str(output))
            public_readme = run('git', '-C', str(repo), 'show',
                                f'{commit}:scripts/public/wallpaper-README.md')
            (output / 'art/wallpaper/README.md').write_text(public_readme)
            allow = work / 'allow.txt'
            allow.write_text(run('git', '-C', str(repo), 'show',
                                 f'{commit}:scripts/public/allow.txt'))
            # An external index lets the exact export guard inspect every extracted file,
            # including ignored files, without adding repository metadata to the sources.
            env = dict(os.environ, GIT_DIR=str(work / 'index.git'),
                       GIT_WORK_TREE=str(output.resolve()), GIT_INDEX_FILE=str(work / 'index'))
            run('git', 'init', '--bare', '-q', str(work / 'index.git'))
            run('git', 'add', '--all', '--force', env=env)
            run(sys.executable, str(work / 'guard.py'), str(output), str(allow),
                str(work / 'allowed.txt'), env=env)
        except subprocess.CalledProcessError as error:
            raise ValueError('public source policy failed: ' +
                             (error.stdout + error.stderr).strip()) from error


def vendor(src):
    """Vendor the locked graph, including git crates, and use it for the actual build."""
    roots = list(src.glob('niri-*/Cargo.lock')) + list(src.glob('emaki/Cargo.lock'))
    if len(roots) != 1:
        raise ValueError('expected one prepared Cargo.lock')
    project = roots[0].parent
    config = project / '.cargo/config.toml'
    if config.exists() or (project / '.cargo/config').exists():
        raise ValueError('project already has Cargo configuration; merge must be reviewed')
    env = dict(os.environ, CARGO_HOME=str(src / 'cargo-home'))
    configuration = run('cargo', 'vendor', '--locked', '--versioned-dirs', 'vendor', cwd=project, env=env)
    (project / 'vendor').mkdir(exist_ok=True)
    config.parent.mkdir(exist_ok=True)
    config.write_text(configuration + '\n[net]\noffline = true\n')
    # Check resolution using only the vendored graph, not the prepare() download cache.
    with tempfile.TemporaryDirectory(prefix='emaki-cargo-check-') as empty:
        run('cargo', 'metadata', '--frozen', '--format-version', '1', cwd=project,
            env=dict(os.environ, CARGO_HOME=empty))


def copy_sources(src, destination):
    shutil.copytree(src, destination, symlinks=True,
                    ignore=lambda folder, names: ['cargo-home'] if Path(folder) == src else [])
    # makepkg's VCS checkouts can borrow objects from SRCDEST. Repack the copied checkout
    # while that store still exists, then remove the external dependency.
    for alternates in destination.glob('**/.git/objects/info/alternates'):
        original = src / alternates.relative_to(destination)
        entries = original.read_text().splitlines()
        alternates.write_text(''.join(str((original.parent.parent / p).resolve()) + '\n'
                                      for p in entries))
        run('git', '-C', str(alternates.parents[3]), 'repack', '-a', '-d')
        alternates.unlink()
    for item in destination.rglob('*'):
        if not item.is_symlink():
            continue
        original = src / item.relative_to(destination)
        if (str(item.relative_to(destination)).startswith('emaki/iso/profile/airootfs/')
                and os.readlink(item).startswith(('/usr/', '/etc/', '/run/', '/dev/'))):
            # Image-root links refer to files installed in the image, not source inputs.
            continue
        resolved = original.resolve(strict=True)
        item.unlink()
        if resolved.is_relative_to(src):
            target = destination / resolved.relative_to(src)
            item.symlink_to(os.path.relpath(target, item.parent))
        elif resolved.is_file():
            # Patches and downloaded tarballs are linked from the recipe or SRCDEST.
            shutil.copy2(resolved, item)
        else:
            raise ValueError(f'external directory symlink in sources: {original}')
    for gitfile in destination.glob('**/.git'):
        if gitfile.is_file():
            raise ValueError(f'external git directory reference: {gitfile}')


def archive(recipe, src, output, commit, version):
    recipe, src, output = recipe.resolve(), src.resolve(), output.resolve()
    name = recipe.name
    if name not in PACKAGES or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('invalid package or commit')
    if not re.fullmatch(r'[A-Za-z0-9_.+]+-[0-9.]+', version):
        raise ValueError('invalid package version')
    required = {'niri-emaki': [f'niri-{version.rsplit("-", 1)[0]}',
                               f'niri-{version.rsplit("-", 1)[0]}/vendor',
                               f'niri-{version.rsplit("-", 1)[0]}/.cargo/config.toml'],
                'quickshell-emaki': ['quickshell'],
                'xdg-desktop-portal-gnome-emaki': ['xdg-desktop-portal-gnome', 'libgxdp'],
                'emaki-config': ['emaki', 'emaki/vendor', 'emaki/.cargo/config.toml',
                                 'emaki-source-commit'],
                'emaki-installer': ['emaki-installer'],
                'emaki-nvidia': ['emaki-nvidia']}.get(name, [])
    for relative in required:
        if not (src / relative).exists():
            raise ValueError(f'missing prepared source: {relative}')
    output.mkdir(parents=True, exist_ok=True)
    metadata_path = output / 'SOURCES.json'
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {
        'format': 1, 'commit': commit, 'packages': {}}
    if metadata.get('commit') != commit or metadata.get('format') != 1:
        raise ValueError('source metadata belongs to another build')
    filename = f'{name}-{version}.sources.tar.gz'
    target = output / filename
    if target.exists() or name in metadata['packages']:
        raise ValueError('source archive already exists')
    with tempfile.TemporaryDirectory(prefix='emaki-source-') as temporary:
        root = Path(temporary) / f'{name}-{version}'
        root.mkdir()
        # Copy only committed recipe inputs, never a developer's build products.
        paths = run('git', '-C', str(recipe), 'ls-files', '-z', '.').split('\0')
        for relative in filter(None, paths):
            path = recipe / relative
            rel_repo = path.relative_to(recipe.parent.parent)
            committed = subprocess.run(['git', '-C', str(recipe), 'show', f'{commit}:{rel_repo}'],
                                       check=True, capture_output=True).stdout
            if path.read_bytes() != committed:
                raise ValueError(f'recipe changed since build commit: {relative}')
            dest = root / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
        if not (root / 'PKGBUILD').is_file():
            raise ValueError('missing committed PKGBUILD')
        copy_sources(src, root / 'src')
        (root / 'SOURCE-COMMIT').write_text(commit + '\n')
        (root / 'REBUILD.txt').write_text(
            'Prepared sources for ' + name + ' ' + version + '\n'
            'Copyright (C) 2026 Artur Yakymenko\nSPDX-License-Identifier: GPL-3.0-or-later\n\n'
            'The build recipe and local inputs are included in this directory.\n'
            'Project: https://github.com/emakish/emaki\n'
            'The src/ tree already contains the prepared sources used for the binary.\n'
            'Install the PKGBUILD build and runtime dependencies on Arch Linux first.\n'
            'Then, as a normal user, from this directory run:\n'
            '  env -u SRCDEST BUILDDIR="$PWD" CARGO_NET_OFFLINE=true makepkg --noextract\n'
            'Do not use --cleanbuild or rerun prepare(): patches have already been applied.\n'
            'Rust dependencies, when needed, are included in the source tree for offline use.\n'
            'Portal archives include both the portal and pinned libgxdp git sources.\n'
            'System build dependencies are not bundled; this is not a bit-for-bit build guarantee.\n')
        with tarfile.open(target, 'w:gz', dereference=False) as tar:
            tar.add(root, arcname=root.name)
    digest = hashlib.sha256()
    with target.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    metadata['packages'][name] = {'version': version, 'archive': filename,
                                  'sha256': digest.hexdigest()}
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + '\n')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='action', required=True)
    tree = subs.add_parser('checkout')
    for option in ('repo', 'output'):
        tree.add_argument('--' + option, type=Path, required=True)
    tree.add_argument('--commit', required=True)
    prepare = subs.add_parser('vendor')
    prepare.add_argument('--src', type=Path, required=True)
    capture = subs.add_parser('archive')
    for option in ('recipe', 'src', 'output'):
        capture.add_argument('--' + option, type=Path, required=True)
    for option in ('commit', 'version'):
        capture.add_argument('--' + option, required=True)
    args = vars(parser.parse_args())
    action = args.pop('action')
    try:
        {'vendor': vendor, 'archive': archive, 'checkout': checkout}[action](**args)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'ERROR: {error}\n')


if __name__ == '__main__':
    main()
