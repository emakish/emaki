#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded, offline ISO input checks; signature trust remains a build-time gate."""
import argparse
import importlib.util
import os
from pathlib import Path
import re
import signal
import subprocess
import tarfile

HERE = Path(__file__).resolve().parent


def require(condition, message):
    if not condition:
        raise ValueError(message)


def package_lists():
    paths = [HERE / name for name in ('packages-extra.txt', 'target-packages.txt', 'emaki-packages.txt')]
    paths += sorted((HERE / 'profile').glob('packages.*'))
    paths.append(HERE / 'profile/bootstrap_packages')
    packages = {}
    for path in paths:
        names = [line.strip() for line in path.read_text().splitlines()
                 if line.strip() and not line.lstrip().startswith('#')]
        require(names == sorted(set(names)), f'{path}: package list must be sorted and unique')
        require(all(re.fullmatch(r'[a-z0-9@_+.-]+', name) for name in names),
                f'{path}: invalid package name')
        packages[path.name] = set(names)
    require('packages.x86_64' in packages, 'missing profile/packages.x86_64')
    return packages['packages.x86_64']


def profile_values(profile):
    result = subprocess.run(['bash', '-c', '''set -euo pipefail
    declare -A file_permissions=()
    source "$1"
    printf '%s\\0' "$iso_name" "$iso_version" "$iso_label" "$install_dir" "${buildmodes[*]}" "${bootmodes[*]}"
    for path in "${!file_permissions[@]}"; do printf '%s\\0%s\\0' "$path" "${file_permissions[$path]}"; done
    ''', '_', str(profile / 'profiledef.sh')], check=True, capture_output=True, text=True, timeout=3)
    values = result.stdout.rstrip('\0').split('\0')
    require(len(values) >= 6 and len(values) % 2 == 0, 'invalid profile definition output')
    return values[:6], dict(zip(values[6::2], values[7::2]))


def check_profile(profile, version):
    require(re.fullmatch(r'\d+\.\d+\.\d+', version), 'invalid iso/VERSION')
    require((profile / 'VERSION').read_text().strip() == version, 'profile/VERSION differs from iso/VERSION')
    values, permissions = profile_values(profile)
    require(values[:5] == ['emaki', version, 'EMAKI_' + version, 'emaki', 'iso'],
            'profile identity/version differs from iso/VERSION')
    require(bool(values[5]), 'profile has no boot modes')
    for name, mode in permissions.items():
        require(name.startswith('/') and '..' not in Path(name).parts,
                f'unsafe file_permissions path: {name}')
        require(os.path.lexists(profile / 'airootfs' / name.lstrip('/')),
                f'file_permissions entry does not exist: {name}')
        require(re.fullmatch(r'\d+:\d+:0?[0-7]{3,4}', mode), f'invalid file_permissions mode: {name}: {mode}')
    require(permissions.get('/etc/shadow') == '0:0:0400', 'unsafe shadow permissions')
    require(permissions.get('/root') == '0:0:0700', 'unsafe root permissions')
    for mode, pattern in [('bios.syslinux', 'syslinux/*.cfg'), ('uefi.grub', 'grub/*.cfg'),
                          ('uefi.systemd-boot', 'loader/entries/*.conf')]:
        if mode in values[5]:
            require(any(profile.glob(pattern)), f'no boot configuration for {mode}')


def check_loaders(profile, version, packages):
    paths = sorted({*profile.glob('grub/*.cfg'), *profile.glob('syslinux/*.cfg'),
                    *profile.glob('**/loader/**/*.conf')})
    kernel_count = 0
    for path in paths:
        text = path.read_text()
        versions = re.findall(r'\b(?:Emaki\s+|EMAKI_)(\d+\.\d+\.\d+)\b', text)
        require(all(item == version for item in versions), f'{path}: boot menu version differs from iso/VERSION')
        kernel = None
        initrds = []

        def finish_entry():
            if kernel:
                require(kernel in initrds, f'{path}: kernel {kernel} has no matching initrd')
                require(all(item == kernel for item in initrds if item.startswith('linux')),
                        f'{path}: kernel/initrd mismatch')
            elif initrds:
                raise ValueError(f'{path}: initrd without a kernel')

        for line in text.splitlines():
            stripped = line.strip()
            if re.match(r'(?:menuentry\s|LABEL\s|title\s|}$)', stripped):
                finish_entry()
                kernel, initrds = None, []
            match = re.match(r'(linux(?:efi)?|kernel|initrd(?:efi)?)\s+(.+)', stripped, re.I)
            if not match:
                continue
            command, arguments = match.groups()
            is_initrd = command.lower().startswith('initrd')
            for argument in (re.split(r'[\s,]+', arguments) if is_initrd else arguments.split()[:1]):
                name = argument.strip('"\'').rsplit('/', 1)[-1]
                kernel_match = re.fullmatch(r'vmlinuz-(linux(?:-lts|-zen|-hardened)?)', name)
                initrd_match = re.fullmatch(r'initramfs-(linux(?:-lts|-zen|-hardened)?)(?:-fallback)?\.img', name)
                if not is_initrd and kernel_match:
                    require(kernel is None, f'{path}: multiple kernels in one boot entry')
                    kernel = kernel_match[1]
                    kernel_count += 1
                    require(kernel in packages, f'{path}: {name} needs live package {kernel}')
                elif is_initrd and initrd_match:
                    producer = initrd_match[1]
                    require({producer, 'mkinitcpio', 'mkinitcpio-archiso'} <= packages,
                            f'{path}: {name} needs live packages {producer}, mkinitcpio and mkinitcpio-archiso')
                    initrds.append(producer)
                elif is_initrd and name in ('amd-ucode.img', 'intel-ucode.img'):
                    producer = name.removesuffix('.img')
                    require(producer in packages, f'{path}: {name} needs live package {producer}')
                elif not is_initrd and name in ('memtest', 'memtest.efi'):
                    producer = 'memtest86+-efi' if name.endswith('.efi') else 'memtest86+'
                    require(producer in packages, f'{path}: {name} needs live package {producer}')
                else:
                    raise ValueError(f'{path}: no known package produces boot asset {argument}')
        finish_entry()
    require(kernel_count > 0, 'no Linux boot entries found')


def check_repo(repo, *, test=False):
    require(repo.is_dir(), f'repository directory does not exist: {repo}')
    signature = repo / 'emaki.db.sig'
    if not test:
        require(signature.is_file() and signature.stat().st_size > 0,
                f'missing or empty signed database: {signature}')
    spec = importlib.util.spec_from_file_location('repo_files', HERE / 'repo-files.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.check_input(repo, HERE / 'emaki-packages.txt', verify=False)


def deadline(*_):
    raise TimeoutError('preflight exceeded 10 seconds')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, help='check local repository shape (no trust verification)')
    parser.add_argument('--test', action='store_true', help='allow an unsigned test repository database')
    args = parser.parse_args()
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(10)
    profile = HERE / 'profile'
    version = (HERE / 'VERSION').read_text().strip()
    packages = package_lists()
    check_profile(profile, version)
    check_loaders(profile, version, packages)
    if args.repo is not None:
        check_repo(args.repo, test=args.test)
    signal.alarm(0)
    repo_shape = ', test repository shape' if args.test else ', signed repository shape'
    print('OK: ISO preflight (offline profile' + (repo_shape if args.repo else '') + ')')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, IndexError, tarfile.TarError,
            subprocess.SubprocessError) as error:
        raise SystemExit(f'ERROR: ISO preflight: {error}')
