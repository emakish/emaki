# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Selective signature removal against real filesystem tools on regular images."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from emaki_installer import arch_backend
from emaki_installer.errors import Code, InstallError
from emaki_installer.runtime import Runner


MIB = 1024 * 1024
SIZE = 256 * MIB
LABEL = 256 * 1024
TOOLS = ('sfdisk', 'wipefs', 'blkid', 'mkfs.fat', 'mkntfs')


def nvpair(name, value):
    name = name.encode()
    body = struct.pack('>I', len(name)) + name.ljust((len(name) + 3) & ~3, b'\0')
    body += struct.pack('>IIQ', 8, 1, value)
    return struct.pack('>II', len(body) + 8, len(body) + 8) + body


def nvlist():
    # XDR label accepted by libblkid: top-level guid, state and nonzero txg.
    header = bytes([1, 1, 0, 0]) + struct.pack('>II', 0, 1)
    values = [('version', 5000), ('state', 1), ('txg', 4242),
              ('pool_guid', 0x1234567890abcdef), ('guid', 0x0fedcba987654321)]
    return header + b''.join(nvpair(name, value) for name, value in values) + bytes(8)


def digest(path, start=0, size=SIZE, zero_offsets=()):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        stream.seek(start)
        end = start + size
        while start < end:
            data = bytearray(stream.read(min(MIB, end - start)))
            if not data:
                raise AssertionError('Unexpected end of image')
            for offset in zero_offsets:
                left, right = max(start, offset), min(start + len(data), offset + 16)
                if left < right:
                    data[left - start:right - start] = bytes(right - left)
            result.update(data)
            start += len(data)
    return result.hexdigest()


@unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS),
                     'requires sfdisk, wipefs, blkid, mkfs.fat and mkntfs')
class ZfsSignatureImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='emaki-zfs-image-')
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.disk = self.work / 'disk.img'
        self.logs = []
        self.runner = Runner(self.logs.append)
        self.enterContext(patch.object(arch_backend, 'WORK', self.work))

    def tags(self, start=0, size=SIZE):
        output = self.runner.run(['blkid', '--probe', '--output', 'export',
                                  '--offset', str(start), '--size', str(size), self.disk])
        return dict(line.split('=', 1) for line in output.splitlines() if '=' in line)

    def image(self, ntfs_end=213 * MIB):
        with self.disk.open('wb') as stream:
            stream.truncate(SIZE)
        ntfs_size = ntfs_end - 33 * MIB
        self.runner.run(['sfdisk', '--wipe=never', '--wipe-partitions=never', self.disk],
                        input=('label: gpt\nunit: sectors\n\n'
                               'start=2048, size=65536, type=U\n'
                               f'start=67584, size={ntfs_size // 512}, '
                               'type=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7\n'))
        partitions = []
        for number, start, size, fs in [(1, MIB, 32 * MIB, 'vfat'),
                                        (2, 33 * MIB, ntfs_size, 'ntfs')]:
            path = self.work / f'{fs}.img'
            with path.open('wb') as stream:
                stream.truncate(size)
            command = ['mkfs.fat', '-F', '32'] if fs == 'vfat' else ['mkntfs', '-F', '-Q']
            self.runner.run([*command, path])
            with path.open('rb') as source, self.disk.open('r+b') as target:
                target.seek(start)
                shutil.copyfileobj(source, target, MIB)
            partitions.append({'number': number, 'start_bytes': start, 'size_bytes': size,
                               'fs': fs, 'uuid': self.tags(start, size)['UUID']})
        # L0's nvlist would overlap this GPT's primary entry array; L1 is in the front gap.
        self.offsets = [LABEL + 16384, SIZE - 2 * LABEL + 16384, SIZE - LABEL + 16384]
        with self.disk.open('r+b') as stream:
            for offset in self.offsets:
                stream.seek(offset)
                stream.write(nvlist())
        ops = arch_backend.DeviceOperations(SimpleNamespace(disk={'path': str(self.disk),
                                            '_sector_size': 512, 'size_bytes': SIZE}), self.runner)

        @contextmanager
        def reviewed_image():
            # Regular files exercise descriptor passing, not block-device exclusive claims.
            fd = os.open(self.disk, os.O_RDONLY | os.O_EXCL | os.O_CLOEXEC)
            try:
                yield fd
            finally:
                os.close(fd)

        ops.reviewed_disk = reviewed_image
        ops.check_disk_sequence = Mock()
        ops.changed = Mock()
        return ops, partitions

    def test_removal_preserves_gpt_and_complete_fat_ntfs_images(self):
        ops, partitions = self.image()
        signatures = json.loads(self.runner.run(['wipefs', '--json', '--types', 'zfs_member',
                                                  '--output', 'TYPE,OFFSET,LENGTH', self.disk]))
        self.assertEqual(sorted((int(row['offset'], 0), int(row['length']))
                                for row in signatures['signatures']),
                         [(offset, 16) for offset in self.offsets])
        regions = [(0, 34 * 512), (SIZE - 33 * 512, 33 * 512)]
        regions += [(part['start_bytes'], part['size_bytes']) for part in partitions]
        hashes = [digest(self.disk, start, size) for start, size in regions]
        # This whole-image oracle permits exactly the three magic headers to become zero.
        expected = digest(self.disk, zero_offsets=self.offsets)
        original = digest(self.disk)
        ops.remove_stale_zfs_label(partitions)
        self.assertNotEqual(original, expected)
        self.assertEqual(digest(self.disk), expected)
        self.assertEqual([digest(self.disk, start, size) for start, size in regions], hashes)
        backups = list(self.work.glob('zfs-signatures-*/*.bak'))
        self.assertEqual(len(backups), 3)
        for backup in backups:
            self.assertEqual(backup.read_bytes(), nvlist()[:16])
        with self.disk.open('rb') as stream:
            for offset in self.offsets:
                stream.seek(offset)
                self.assertEqual(stream.read(16), bytes(16))
        tags = self.tags()
        self.assertEqual(tags['PTTYPE'], 'gpt')
        self.assertNotIn('TYPE', tags)
        for part in partitions:
            tags = self.tags(part['start_bytes'], part['size_bytes'])
            self.assertEqual((tags['TYPE'], tags['UUID']), (part['fs'], part['uuid']))
        self.runner.run(['sfdisk', '--verify', self.disk])
        ops.changed.assert_called_once_with()
        ops.check_disk_sequence.assert_called_once()

    def test_partition_overlapping_magic_is_refused_without_any_writes(self):
        # L2's magic is inside NTFS but its 112 KiB nvlist crosses the partition end,
        # so libblkid reports it despite its normal partition-coverage exclusion.
        ops, partitions = self.image(ntfs_end=SIZE - 448 * 1024)
        signatures = json.loads(self.runner.run(['wipefs', '--json', '--types', 'zfs_member',
                                                  '--output', 'OFFSET', self.disk]))
        self.assertIn(hex(self.offsets[1]), [row['offset'] for row in signatures['signatures']])
        original = digest(self.disk)
        with self.assertRaises(InstallError) as caught:
            ops.remove_stale_zfs_label(partitions)
        self.assertEqual(caught.exception.code, Code.UNSAFE_DISK)
        self.assertEqual(digest(self.disk), original)
        self.assertFalse(list(self.work.glob('zfs-signatures-*')))
        ops.changed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
