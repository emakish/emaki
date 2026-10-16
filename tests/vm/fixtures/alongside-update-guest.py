#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run only as root inside a disposable installed alongside test guest. Never on the host.

Evidence and fixture steps for tests/vm/alongside-update-check.py: the complete file
manifest of Emaki's own ESP (/efi), the digest and mount state of Windows' ESP (never
mounted here), the candidate boot files, a synthetic Windows hibernation record on the
fixture's NTFS volume, and firmware entries that imitate a Windows update putting its
boot manager first.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import time

WINDOWS = '/dev/disk/by-id/virtio-emaki-target-part3'
WINDOWS_ESP = '/dev/disk/by-id/virtio-emaki-target-part1'
MARKER = Path('/var/lib/emaki/alongside-boot.json')
SHARED_DROPINS = [Path(f'/etc/systemd/system/systemd-{mode}.service.d/50-emaki-shared-esp.conf')
                  for mode in ('hibernate', 'hybrid-sleep', 'suspend-then-hibernate')]
OVERLAY = {'boot/': Path('/usr/lib/emaki/boot/emaki_boot'), 'bin/': Path('/usr/bin')}
OVERLAY['hooks/'] = OVERLAY['bin/'].parent / 'share/libalpm/hooks'


def run(*argv, check=True):
    result = subprocess.run(argv, capture_output=True, text=True, env=dict(os.environ, LC_ALL='C'))
    if check and result.returncode:
        raise RuntimeError(f'{argv}: {result.returncode}: {result.stdout}{result.stderr}')
    return result


def guest_only():
    if os.geteuid() != 0 or run('systemd-detect-virt', '--vm').stdout.strip() not in ('qemu', 'kvm'):
        raise RuntimeError('requires root inside the disposable QEMU/KVM guest')
    if not MARKER.is_file():
        raise RuntimeError('requires an installed alongside test guest')


def windows_esp():
    """Windows' ESP read as a block device, never mounted: its digest and any mounts."""
    digest = hashlib.sha256()
    with open(WINDOWS_ESP, 'rb', buffering=0) as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    mounts = run('findmnt', '-nr', '-o', 'TARGET', '--source', os.path.realpath(WINDOWS_ESP), check=False)
    return {'sha256': digest.hexdigest(), 'mounted': mounts.stdout.split()}


def manifest():
    esp = Path('/efi')
    files = {}
    for path in sorted(esp.rglob('*')):
        relative = str(path.relative_to(esp))
        files[relative] = ('directory' if path.is_dir() else
                           hashlib.sha256(path.read_bytes()).hexdigest())
    mount = json.loads(run('findmnt', '--json', '-o', 'SOURCE,FSTYPE,OPTIONS,PARTUUID',
                           '--mountpoint', '/efi').stdout)
    return {'mount': mount['filesystems'][0], 'files': files, 'windows': windows_esp(),
            'marker': json.loads(MARKER.read_text()), 'efibootmgr': run('efibootmgr', '-v').stdout}


def overlay(archive):
    """Install the candidate boot files over the packaged ones and list what changed."""
    changed = []
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            if not member.isfile():
                continue
            prefix = next(p for p in OVERLAY if member.name.startswith(p))
            name = member.name[len(prefix):]
            if '/' in name or not re.fullmatch(r'[A-Za-z0-9_.-]+', name):
                raise RuntimeError('unexpected overlay member ' + member.name)
            target = OVERLAY[prefix] / name
            data = source.extractfile(member).read()
            old = target.read_bytes() if target.exists() else None
            if old != data:
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as staged:
                    staged.write(data)
                os.chmod(staged.name, 0o755 if prefix == 'bin/' else 0o644)
                os.replace(staged.name, target)
                changed.append({'path': str(target), 'new': old is None,
                                'sha256': hashlib.sha256(data).hexdigest()})
    verify = run('pacman', '-Qkk', 'emaki-config', check=False)
    return {'changed': changed, 'pacman_Qkk': verify.stdout + verify.stderr}


def ntfs(options):
    directory = tempfile.mkdtemp(prefix='n8a1-windows-')
    run('ntfs-3g', '-o', options, WINDOWS, directory)
    return Path(directory)


def unmount(directory):
    run('umount', str(directory))
    directory.rmdir()


def hibernate_windows():
    """The record Windows leaves after Fast Startup; ntfs-3g reports it as hibernated."""
    root = ntfs('rw')
    try:
        (root / 'hiberfil.sys').write_bytes(b'hibr' + bytes(4092))
    finally:
        unmount(root)
    return {'hiberfil': 'hibr header written'}


def wake_windows():
    root = ntfs('rw,remove_hiberfile')
    try:
        present = (root / 'hiberfil.sys').exists()
        if present:
            (root / 'hiberfil.sys').unlink()
    finally:
        unmount(root)
    return {'hiberfil_removed': present}


def hibernate_attempt():
    """Start the real hibernate service once. The guest has no swap, so sleep itself
    fails; Emaki's own ESP must stay mounted read-write with the counters unchanged."""
    env = Path('/efi/EFI/Emaki/update.env')

    def state():
        mount = run('findmnt', '-no', 'OPTIONS', '/efi', check=False).stdout.strip()
        # FAT keeps a two-second mtime: a changed one shows that both steps wrote.
        return {'efi_options': mount, 'update_env': env.read_bytes().rstrip(b'#').decode()
                if env.exists() else None, 'update_env_mtime': env.stat().st_mtime if env.exists() else None}
    before = state()
    time.sleep(2.5)
    since = run('date', '+%Y-%m-%d %H:%M:%S').stdout.strip()
    started = run('systemctl', 'start', 'systemd-hibernate.service', check=False)
    after = state()
    journal = run('journalctl', '--since', since, '-o', 'cat', '--no-pager', '-u', 'systemd-hibernate.service',
                  check=False).stdout
    dropin = run('systemctl', 'cat', 'systemd-hibernate.service', check=False).stdout
    return {'before': before, 'start_exit': started.returncode, 'after': after, 'journal': journal,
            'unit': dropin, 'shared_esp_dropins': [str(path) for path in SHARED_DROPINS if path.exists()]}


def entries():
    text = run('efibootmgr', '-v').stdout
    found = {}
    for line in text.splitlines():
        match = re.match(r'^Boot([0-9A-F]{4})(\*?) ([^\t]+)\t', line)
        if match:
            found.setdefault(match[3].strip(), []).append(match[1])
    order = re.search(r'^BootOrder: (\S+)$', text, re.M)
    return found, (order[1].split(',') if order else []), text


def windows_first():
    """Imitate a Windows update: its boot manager entry, first in BootOrder."""
    found, order, before = entries()
    if 'Windows Boot Manager' not in found:
        disk = os.path.realpath(WINDOWS_ESP)
        match = re.fullmatch(r'(/dev/[a-z]+)([0-9]+)', disk)
        run('efibootmgr', '--create', '--disk', match[1], '--part', match[2],
            '--label', 'Windows Boot Manager', '--loader', '\\EFI\\Microsoft\\Boot\\bootmgfw.efi')
        found, order, _ = entries()
    windows = found['Windows Boot Manager'][0]
    run('efibootmgr', '--bootorder', ','.join([windows] + [n for n in order if n != windows]))
    return {'before': before, 'after': entries()[2]}


def next_boot(label):
    """One boot of a firmware entry, as a person choosing it in the firmware menu."""
    found, _, _ = entries()
    number = found[label][0]
    run('efibootmgr', '--bootnext', number)
    return {'bootnext': number, 'efibootmgr': entries()[2]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['manifest', 'overlay', 'hibernate-windows', 'wake-windows',
                                           'hibernate-attempt', 'windows-first', 'next-boot'])
    parser.add_argument('argument', nargs='?')
    args = parser.parse_args()
    guest_only()
    if args.action == 'manifest':
        result = manifest()
    elif args.action == 'overlay':
        result = overlay(args.argument)
    elif args.action == 'hibernate-windows':
        result = hibernate_windows()
    elif args.action == 'wake-windows':
        result = wake_windows()
    elif args.action == 'hibernate-attempt':
        result = hibernate_attempt()
    elif args.action == 'windows-first':
        result = windows_first()
    else:
        result = next_boot(args.argument)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as error:
        sys.exit(f'FAIL: {error}')
