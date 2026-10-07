#!/usr/bin/python3 -I
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Session policy and a read-only check of installed NVIDIA kernel modules."""
import argparse
from pathlib import Path
import subprocess
import sys

# Isolated Python deliberately omits the script directory from sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from graphics import detect, family

MODULES = ('nvidia', 'nvidia_modeset', 'nvidia_uvm', 'nvidia_drm')


def read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ''


def environment(sysfs_root=Path('/sys')):
    devices = detect(sysfs_root)
    nvidia = [gpu for gpu in devices if gpu['vendor'] == 0x10de
              and gpu['driver'] == 'nvidia' and family(gpu['device']) == 'open']
    if not nvidia:
        return {}
    result = {'NVD_BACKEND': 'direct'}
    active = set()
    for gpu in devices:
        for card in gpu['cards']:
            for connector in (sysfs_root / 'class/drm').glob(card + '-*'):
                if read(connector / 'status') == 'connected' and read(connector / 'enabled') == 'enabled':
                    active.add((gpu['vendor'], gpu['driver']))
    # Unknown, disconnected and mixed-vendor displays keep Mesa's automatic
    # selection. Do not infer scanout from PCI order, boot_vga or card0.
    if active == {(0x10de, 'nvidia')}:
        result['__GLX_VENDOR_LIBRARY_NAME'] = 'nvidia'
        result['LIBVA_DRIVER_NAME'] = 'nvidia'
    return result


def missing_modules(root, kernel, run=subprocess.run):
    missing = []
    for module in MODULES:
        result = run(['modinfo', '-b', str(root), '-k', kernel, module],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if result.returncode:
            missing.append(module)
    return missing


def check(root=Path('/'), *, run=subprocess.run):
    """Inspect both installed default kernels without changing their configuration."""
    kernels = [p.name for p in sorted((root / 'usr/lib/modules').glob('*'))
               if read(p / 'pkgbase') in ('linux', 'linux-lts')]
    failures = {name: absent for name in kernels if (absent := missing_modules(root, name, run))}
    if not kernels:
        print('NVIDIA graphics: no installed kernel found (linux, linux-lts).', file=sys.stderr)
        return 1
    for name, absent in failures.items():
        print(f'NVIDIA graphics unavailable for kernel ({name}): missing modules '
              f'({", ".join(absent)}). Use a console login or another kernel to repair the driver.',
              file=sys.stderr)
    return int(bool(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('environment', 'check'))
    parser.add_argument('--root', type=Path, default=Path('/'))
    parser.add_argument('--sysfs-root', type=Path, default=Path('/sys'))
    args = parser.parse_args()
    if args.action == 'environment':
        for name, value in environment(args.sysfs_root).items():
            print(f'{name}={value}')
        return 0
    return check(args.root)


if __name__ == '__main__':
    raise SystemExit(main())
