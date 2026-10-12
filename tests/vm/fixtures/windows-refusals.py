#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Refusal probes on the disposable synthetic Windows fixture, before resizing."""
from contextlib import contextmanager
import hashlib
import json
import os
import re
import struct
from pathlib import Path
import subprocess
import sys
import tempfile

from emaki_installer.arch_backend import resize_partition
from emaki_installer.errors import Code, InstallError
from emaki_installer.inventory import Inventory
from emaki_installer.planner import make_plan

DISK = '/dev/disk/by-id/virtio-emaki-target'
NTFS = DISK + '-part3'
MIB = 1024**2


class ProbeRunner:
    def run(self, argv, **kwargs):
        result = subprocess.run(argv, capture_output=True, text=True, **kwargs, env=dict(os.environ, LC_ALL='C'))
        if result.returncode:
            raise InstallError(Code.COMMAND_FAILED, 'Fixture probe command failed.',
                               output=result.stdout + result.stderr, returncode=result.returncode)
        return result.stdout

    def log(self, message):
        print(message, file=sys.stderr, flush=True)


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def write_counters():
    # The disposable target has no unrelated writer. Observe the entire device,
    # including writes beyond the sampled metadata and file extents below.
    name = Path(DISK).resolve(strict=True).name
    fields = (Path('/sys/class/block') / name / 'stat').read_text().split()
    return int(fields[4]), int(fields[6])


def digest():
    result = hashlib.sha256()
    for name, lengths in ((DISK, ((0, 4 * MIB), (-4 * MIB, 4 * MIB))), (NTFS, ((0, 32 * MIB), (-MIB, MIB)))):
        with open(name, 'rb', buffering=0) as stream:
            for offset, length in lengths:
                stream.seek(offset, os.SEEK_END if offset < 0 else os.SEEK_SET)
                result.update(stream.read(length))
    return result.hexdigest()



def full_digest():
    """Hash every origin byte, including unsampled allocated and free clusters."""
    with open(DISK, 'rb', buffering=0) as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def restart_page(log_size, *, clean=False):
    """NTFS 1.1 restart page with one active client and valid sector fixups.

    Layout follows libntfs-3g logfile.h. Only RESTART_VOLUME_IS_CLEAN differs
    between the clean control and the unclean journal; neither changes $Volume.
    """
    page = bytearray(4096)
    struct.pack_into('<4sHHQIIHHHH', page, 0,
                     b'RSTR', 32, 9, 0, 4096, 4096, 56, 1, 1, 0)
    struct.pack_into('<QHHHHIHHQ IHHII', page, 56,
                     0, 1, 0xffff, 0, 2 if clean else 0,
                     67 - log_size.bit_length(), 208, 48, log_size, 0, 48, 64, 1, 0)
    struct.pack_into('<HH', page, 56 + 48 + 16, 0xffff, 0xffff)
    struct.pack_into('<I', page, 56 + 48 + 28, 8)
    page[56 + 48 + 32:56 + 48 + 40] = 'NTFS'.encode('utf-16le')
    struct.pack_into('<H', page, 32, 1)
    for sector in range(1, 9):
        # Zero original sector tails are saved in the update sequence array.
        struct.pack_into('<H', page, sector * 512 - 2, 1)
    return bytes(page)


@contextmanager
def unclean_journal(device):
    """Temporarily replace only the two restart pages on the guarded fixture."""
    info = run('ntfsinfo', '-m', device)
    flags = re.search(r'Volume Flags: 0x([0-9a-fA-F]+)', info)
    assert flags and not (int(flags[1], 16) & 1), 'fixture must have a clear dirty flag'
    with open(device, 'rb', buffering=0) as stream:
        boot = stream.read(512)
    cluster = int.from_bytes(boot[11:13], 'little') * boot[13]
    assert cluster >= 512 and cluster & (cluster - 1) == 0
    listing = run('ntfscluster', '-I', '2', device)
    assert 'Dump: /$LogFile' in listing
    runs = [tuple(map(int, row)) for row in re.findall(r'^\s+(\d+)\s+(\d+)\s+(\d+)\s*$', listing, re.M)]
    assert runs and runs[0][0] == 0 and runs[0][2] * cluster >= 8192
    log_size = sum(length for _, _, length in runs) * cluster
    assert log_size >= 50 * 4096
    offset = runs[0][1] * cluster
    with open(device, 'r+b', buffering=0) as stream:
        stream.seek(offset)
        original = stream.read(8192)
        assert len(original) == 8192
        try:
            stream.seek(offset)
            stream.write(restart_page(log_size) * 2)
            os.fsync(stream.fileno())
            yield
        finally:
            stream.seek(offset)
            stream.write(original)
            os.fsync(stream.fileno())


class JournalRunner(ProbeRunner):
    def __init__(self):
        self.unclean_refusals = 0

    def run(self, argv, **kwargs):
        assert argv[0] != 'ntfsresize', 'unclean journal reached the resize utility'
        try:
            return super().run(argv, **kwargs)
        except InstallError as error:
            if argv[0] == 'ntfs-3g.probe' and '--readwrite' in argv:
                # 15 is libntfs-3g NTFS_VOLUME_UNCLEAN_UNMOUNT. A missing
                # device-mapper target or unavailable command is not a pass.
                assert error.returncode == 15, (error.returncode, error.output)
                self.unclean_refusals += 1
            raise


def journal_refusals(config, baseline):
    plan = make_plan(config, baseline)
    with unclean_journal(NTFS):
        os.sync()
        before, writes = full_digest(), write_counters()
        runner = JournalRunner()
        inv = Inventory(runner).probe()
        disk = next(d for d in inv['disks'] if d['id'] == DISK)
        assert (disk['shrink'] or {}).get('reason'), disk['shrink']
        changed = []
        try:
            resize_partition(plan, runner, lambda: changed.append(True))
        except InstallError as error:
            assert error.code == Code.SHRINK_BOUNDS, error
        else:
            raise AssertionError('Unclean journal reached resize')
        assert runner.unclean_refusals == 2 and not changed
        assert full_digest() == before, 'Journal refusal changed origin bytes'
        assert write_counters() == writes, 'Journal refusal wrote to the origin'
        print('PASS: unclean journal with clean dirty flag refused by inventory and resize; '
              'full origin SHA-256 and write counters unchanged.', flush=True)


def probe(reason=None):
    os.sync()
    writes = write_counters()
    before = digest()
    inv = Inventory(ProbeRunner()).probe()
    disk = next(d for d in inv['disks'] if d['id'] == DISK)
    assert digest() == before, 'Probe modified sampled primary/backup GPT or NTFS metadata/data'
    assert write_counters() == writes, 'Read-only probe wrote to the target disk'
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


def guest_guard():
    assert os.geteuid() == 0 and run('systemd-detect-virt', '--vm') in ('qemu', 'kvm')
    assert 'emaki.test=1' in Path('/proc/cmdline').read_text().split()
    assert run('lsblk', '-dn', '-o', 'SERIAL', DISK) == 'emaki-target'
    assert Path('/run/archiso/bootmnt').exists(), 'requires the disposable live test ISO'


def main():
    guest_guard()
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
    journal_refusals(config, baseline)
    for freed in (31 * 1024**3, 95 * 1024**3):
        config['shrink_bytes'] = freed
        os.sync()
        writes = write_counters()
        before = digest()
        try:
            make_plan(config, baseline)
        except InstallError as error:
            assert error.code == Code.SHRINK_BOUNDS, error
        else:
            raise AssertionError('Unsafe size was accepted')
        assert digest() == before
        assert write_counters() == writes, 'Refused plan wrote to the target disk'
        print('PASS: refused unsafe Emaki size', freed, flush=True)


if __name__ == '__main__':
    main()
