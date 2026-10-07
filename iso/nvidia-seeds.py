#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve official NVIDIA open module variants into the target repository."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys


COMMON = ('emaki-nvidia',)
HEADERS = ('dkms', 'linux-headers', 'linux-lts-headers')
FAMILIES = {
    'open-prebuilt': COMMON + ('nvidia-open', 'nvidia-open-lts', 'nvidia-utils', 'libva-nvidia-driver'),
    'open-dkms': COMMON + HEADERS + ('nvidia-open-dkms', 'nvidia-utils', 'libva-nvidia-driver'),
}
CONDITIONAL = frozenset(package for packages in FAMILIES.values() for package in packages)
LOCAL_REQUIRED = frozenset(COMMON)


def base_packages(text):
    return [line for line in text.splitlines() if line and not line.startswith('#')
            and line not in CONDITIONAL]


def selected_families(available, local):
    missing = LOCAL_REQUIRED - local
    if missing:
        raise ValueError('Signed input repository lacks NVIDIA packages: ' + ', '.join(sorted(missing)))
    family = 'open-prebuilt' if {'nvidia-open', 'nvidia-open-lts'} <= available else 'open-dkms'
    selected = {family: FAMILIES[family]}
    missing = set(package for packages in selected.values() for package in packages) - available
    if missing:
        raise ValueError('NVIDIA target packages unavailable: ' + ', '.join(sorted(missing)))
    return selected


def run(command, capture=False):
    return subprocess.run(command, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def resolve(config, db, cache, seeds, closure=None, target_closure=None, verify=False):
    """Download with normal dependency/conflict checks; never install into the host."""
    options = ['--dbpath', str(db), '--config', str(config)]
    available = set(run(['pacman', '-Slq', *options], capture=True).splitlines())
    local = set(run(['pacman', '-Slq', *options, 'emaki-offline'], capture=True).splitlines())
    families = selected_families(available, local)
    seed_text = seeds.read_text()
    missing = CONDITIONAL - set(seed_text.split())
    if missing:
        raise ValueError('Conditional NVIDIA seeds absent from target-packages.txt: ' + ', '.join(sorted(missing)))
    base = base_packages(seed_text)
    union = set()
    for family, packages in families.items():
        # Only the Emaki policy package comes from the supplied signed repository.
        requested = base + ['emaki-offline/' + name if name in LOCAL_REQUIRED else name
                            for name in packages]
        family_options = options
        family_cache = cache
        if verify:
            family_db = db.parent / ('nvidia-' + family + '-db')
            family_cache = cache.parent / ('nvidia-' + family + '-cache')
            for path in (family_db, family_cache):
                if path.exists() or path.is_symlink():
                    raise ValueError(f'Conditional verification path already exists: {path}')
                path.mkdir()
            family_options = ['--dbpath', str(family_db), '--config', str(config)]
        print(f'NVIDIA offline transaction: {family}', flush=True)
        try:
            run(['pacman', '-Syw' if verify else '-Sw', '--noconfirm',
                 '--cachedir', str(family_cache), *family_options, *requested])
            if not verify:
                names = run(['pacman', '-Sp', '--print-format', '%f', *family_options,
                             *requested], capture=True).splitlines()
                if not names or any(Path(name).name != name or not name.endswith('.pkg.tar.zst')
                                    for name in names):
                    raise ValueError('Unexpected NVIDIA package transaction manifest')
                union.update(names)
        finally:
            if verify:
                shutil.rmtree(family_db)
                shutil.rmtree(family_cache)
    if not verify:
        for manifest in (closure, target_closure):
            names = set(manifest.read_text().splitlines()) | union
            manifest.write_text(''.join(name + '\n' for name in sorted(names)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('base', 'download', 'verify', 'conditional'))
    parser.add_argument('--config', type=Path)
    parser.add_argument('--db', type=Path)
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--seeds', type=Path, default=Path(__file__).with_name('target-packages.txt'))
    parser.add_argument('--closure', type=Path)
    parser.add_argument('--target-closure', type=Path)
    args = parser.parse_args()
    if args.action == 'base':
        print('\n'.join(base_packages(sys.stdin.read())))
    elif args.action == 'conditional':
        print('\n'.join(sorted(CONDITIONAL)))
    else:
        if not all((args.config, args.db, args.cache)):
            parser.error('download and verify require --config, --db and --cache')
        if args.action == 'download' and not all((args.closure, args.target_closure)):
            parser.error('download requires --closure and --target-closure')
        resolve(args.config, args.db, args.cache, args.seeds, args.closure,
                args.target_closure, args.action == 'verify')


if __name__ == '__main__':
    main()
