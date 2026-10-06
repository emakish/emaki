#!/usr/bin/env python3
"""Refusal probes on the disposable synthetic Windows fixture, before resizing."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from emaki_installer.errors import Code, InstallError
from emaki_installer.inventory import Inventory
from emaki_installer.planner import make_plan

DISK = '/dev/disk/by-id/virtio-emaki-target'
NTFS = DISK + '-part3'
MIB = 1024**2


class ProbeRunner:
    def run(self, argv):
        result = subprocess.run(argv, capture_output=True, text=True, env=dict(os.environ, LC_ALL='C'))
        if result.returncode:
            raise InstallError(Code.COMMAND_FAILED, 'Fixture probe command failed.',
                               output=result.stdout + result.stderr, returncode=result.returncode)
        return result.stdout


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def digest():
    result = hashlib.sha256()
    for name, lengths in ((DISK, ((0, 4 * MIB),)), (NTFS, ((0, 32 * MIB), (-MIB, MIB)))):
        with open(name, 'rb', buffering=0) as stream:
            for offset, length in lengths:
                stream.seek(offset, os.SEEK_END if offset < 0 else os.SEEK_SET)
                result.update(stream.read(length))
    return result.hexdigest()


def probe(reason=None):
    before = digest()
    inv = Inventory(ProbeRunner()).probe()
    disk = next(d for d in inv['disks'] if d['id'] == DISK)
    assert digest() == before, 'Probe modified GPT or sampled NTFS metadata/data'
    offer = disk['shrink'] or {}
    if reason:
        assert reason.lower() in offer.get('reason', '').lower(), offer
    else:
        assert offer.get('max_free_bytes', 0) >= 32 * 1024**3 and not offer.get('reason'), offer
    print('PASS:', reason or 'healthy Windows candidate', flush=True)
    return inv


@contextmanager
def mounted(options):
    with tempfile.TemporaryDirectory(prefix='fixture-refusal-') as directory:
        run('ntfs-3g', '-o', options, NTFS, directory)
        try:
            yield Path(directory)
        finally:
            run('umount', directory)


def main():
    assert os.geteuid() == 0 and run('systemd-detect-virt', '--vm') in ('qemu', 'kvm')
    assert 'emaki.test=1' in Path('/proc/cmdline').read_text().split()
    assert run('lsblk', '-dn', '-o', 'SERIAL', DISK) == 'emaki-target'
    Path('/run/emaki-installer').mkdir(mode=0o700, exist_ok=True)
    baseline = probe()
    with mounted('ro,norecover'):
        probe('in use')
    run('blockdev', '--setro', DISK)
    try:
        probe('read-only')
    finally:
        run('blockdev', '--setrw', DISK)
    with open(NTFS, 'r+b', buffering=0) as stream:
        stream.seek(3)
        signature = stream.read(8)
        assert signature == b'NTFS    '
        stream.seek(3)
        stream.write(b'-FVE-FS-')
        os.fsync(stream.fileno())
        try:
            probe('BitLocker')
        finally:
            stream.seek(3)
            stream.write(signature)
            os.fsync(stream.fileno())
    with mounted('rw') as root:
        (root / 'hiberfil.sys').write_bytes(b'hibr' + bytes(4092))
    probe('hibernat')
    # Fixture preparation only: discard the synthetic hibernation record.
    # The installer never uses this option or any repair/dirty-clearing command.
    with mounted('rw,remove_hiberfile') as root:
        if (root / 'hiberfil.sys').exists():
            (root / 'hiberfil.sys').unlink()
    probe()
    run('ntfsfix', NTFS)  # Deliberately mark this disposable volume for chkdsk.
    probe('dirty')
    run('ntfsfix', '--clear-dirty', NTFS)
    baseline = probe()
    config = json.load(sys.stdin)
    for freed in (31 * 1024**3, 95 * 1024**3):
        config['shrink_bytes'] = freed
        before = digest()
        try:
            make_plan(config, baseline)
        except InstallError as error:
            assert error.code == Code.SHRINK_BOUNDS, error
        else:
            raise AssertionError('Unsafe size was accepted')
        assert digest() == before
        print('PASS: refused unsafe Emaki size', freed, flush=True)


if __name__ == '__main__':
    main()
