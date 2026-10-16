#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The cleanup of the FSCKnnnn.REC files fsck.fat saves in the ESP root after a power cut.

grub/emaki_boot/esp_records.py removes a record only on an Emaki-only ESP and only when its
content is proven to be a lost copy of known Emaki content; every other record is kept and
reported. The unit tests drive it in temporary directories. The real test builds a small
FAT32 image (in Python, 4 KiB clusters, no mkfs and no root needed), writes a lost cluster
chain into it, lets the real fsck.fat -a save it, reads the record back with a minimal FAT32
reader and checks the matching rule on it. It is skipped when fsck.fat is missing.
"""
import os
from pathlib import Path
import random
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'grub'))
from emaki_boot import esp_records  # noqa: E402

C = 4096
GRUB_SIZE = 16639584  # the GRUB image of the live test (S9V F17): 4063 clusters on the ESP


def blob(seed, size):
    return random.Random(seed).randbytes(size)


def tree(root):
    """Every path under root with its kind and content, to prove nothing else changed."""
    found = {}
    for path in sorted(Path(root).rglob('*')):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            found[relative] = ('link', os.readlink(path))
        elif path.is_dir():
            found[relative] = ('dir', None)
        else:
            found[relative] = ('file', path.read_bytes())
    return found


class Matching(unittest.TestCase):
    """emaki_copy: what proves that a record is a lost copy of known Emaki content."""

    def setUp(self):
        self.file = blob(1, 3 * C + 100)
        self.known = {'EFI/Emaki/a/grubx64.efi': self.file}

    def test_whole_copy_with_the_rest_of_its_last_sector_zero(self):
        # Linux writes a FAT file in blocks of the logical sector: the rest of the last sector
        # is zero, later sectors of the last cluster keep older bytes (R9S11).
        stale = b'\xa5' * (C - 512)
        self.assertEqual(esp_records.emaki_copy(self.file + bytes(412) + stale, self.known, C),
                         'EFI/Emaki/a/grubx64.efi')
        self.assertEqual(esp_records.emaki_copy(self.file + bytes(C - 100), self.known, C),
                         'EFI/Emaki/a/grubx64.efi')
        for tail in (b'\xa5' * (C - 100), blob(2, C - 100), bytes(411) + b'\x01' + stale):
            self.assertIsNone(esp_records.emaki_copy(self.file + tail, self.known, C))
        # A 4 KiB logical sector: the whole rest of the cluster belongs to the last sector.
        self.assertIsNone(esp_records.emaki_copy(self.file + bytes(412) + stale, self.known, C, 4096))
        self.assertEqual(esp_records.emaki_copy(self.file + bytes(C - 100), self.known, C, 4096),
                         'EFI/Emaki/a/grubx64.efi')
        # 8 KiB clusters, 512-byte sectors: everything after the last sector may be older bytes.
        file = blob(1, 3 * 2 * C + 100)
        tail = bytes(412) + blob(2, 2 * C - 512)
        self.assertEqual(esp_records.emaki_copy(file + tail, {'grub': file}, 2 * C), 'grub')
        self.assertIsNone(esp_records.emaki_copy(file + blob(2, 2 * C - 100), {'grub': file}, 2 * C))

    def test_whole_copy_of_a_cluster_aligned_file(self):
        aligned = blob(3, 2 * C)
        self.assertEqual(esp_records.emaki_copy(aligned, {'shim': aligned}, C), 'shim')

    def test_cluster_aligned_prefix(self):
        for clusters in (1, 2, 3):
            self.assertEqual(esp_records.emaki_copy(self.file[:clusters * C], self.known, C),
                             'EFI/Emaki/a/grubx64.efi')

    def test_middle_or_tail_slice_kept(self):
        self.assertIsNone(esp_records.emaki_copy(self.file[C:3 * C], self.known, C))
        self.assertIsNone(esp_records.emaki_copy(self.file[3 * C:] + bytes(C - 100), self.known, C))
        self.assertIsNone(esp_records.emaki_copy(self.file[2 * C:] + bytes(C - 100), self.known, C))

    def test_one_repeated_byte_kept(self):
        zeros = bytes(4 * C)
        known = {'zeros-first': zeros + blob(4, C), 'ones-first': b'\xff' * (2 * C) + blob(5, 10)}
        self.assertIsNone(esp_records.emaki_copy(bytes(2 * C), known, C))
        self.assertIsNone(esp_records.emaki_copy(b'\xff' * (2 * C), known, C))
        self.assertIsNone(esp_records.emaki_copy(bytes(4 * C), {'zeros': bytes(4 * C)}, C))

    def test_shorter_than_a_cluster_kept(self):
        small = blob(6, 100)
        self.assertIsNone(esp_records.emaki_copy(small, {'slot.json': small}, C))
        self.assertIsNone(esp_records.emaki_copy(self.file[:C // 2], self.known, C))
        self.assertIsNone(esp_records.emaki_copy(b'', self.known, C))

    def test_length_not_whole_clusters_kept(self):
        self.assertIsNone(esp_records.emaki_copy(self.file[:C + 10], self.known, C))
        self.assertIsNone(esp_records.emaki_copy(self.file, self.known, C))

    def test_longer_than_the_rounded_file_kept(self):
        self.assertIsNone(esp_records.emaki_copy(self.file + bytes(C - 100) + blob(7, C),
                                                 self.known, C))

    def test_one_changed_byte_kept(self):
        changed = bytearray(self.file[:2 * C])
        changed[C + 5] ^= 1
        self.assertIsNone(esp_records.emaki_copy(bytes(changed), self.known, C))

    def test_known_content_shorter_than_a_cluster_proves_nothing(self):
        # R9S10 #1: a slot file a cut left as b'MZ' made any 4 KiB record starting with MZ a copy.
        manifest = b'{"slot": "a"}\n'
        for known in (manifest, b'MZ', blob(8, C - 1)):
            for record in (known + bytes(C - len(known)), known + blob(8, C - len(known))):
                self.assertIsNone(esp_records.emaki_copy(record, {'short': known}, C))

    def test_empty_known_content_and_bad_cluster(self):
        self.assertIsNone(esp_records.emaki_copy(blob(9, C), {'empty': b''}, C))
        self.assertIsNone(esp_records.emaki_copy(self.file[:C], self.known, 0))

    def test_first_label_in_known_order(self):
        known = {'EFI/Emaki/b/shimx64.efi': self.file, 'EFI/BOOT/BOOTX64.EFI': self.file}
        self.assertEqual(esp_records.emaki_copy(self.file[:C], known, C), 'EFI/Emaki/b/shimx64.efi')


class Esp(unittest.TestCase):
    """records and tidy on a temporary ESP root."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.esp = Path(temporary.name) / 'esp'
        self.outside = Path(temporary.name) / 'outside'
        self.outside.mkdir()
        self.grub = blob(10, 5 * C + 300)
        self.shim = blob(11, 2 * C + 7)
        self.sidecar = blob(12, 3 * C + 1)
        for slot in ('a', 'b'):
            directory = self.esp / 'EFI/Emaki' / slot
            directory.mkdir(parents=True)
            (directory / 'grubx64.efi').write_bytes(self.grub)
            (directory / 'shimx64.efi').write_bytes(self.shim)
        (self.esp / 'EFI/Emaki/b/grubx64.efi.emaki-new-0a1b').write_bytes(self.sidecar)
        (self.esp / 'EFI/BOOT').mkdir(parents=True)
        (self.esp / 'EFI/BOOT/BOOTX64.EFI').write_bytes(self.shim)
        self.known = {'EFI/Emaki/b/grubx64.efi': self.grub, 'EFI/Emaki/b/shimx64.efi': self.shim,
                      'EFI/BOOT/BOOTX64.EFI': self.shim,
                      'EFI/Emaki/b/grubx64.efi.emaki-new-0a1b': self.sidecar}
        self.lines = []

    def record(self, name, data):
        (self.esp / name).write_bytes(data)
        return data

    def tidy(self, shared=False):
        return esp_records.tidy(self.esp, self.known, shared, C, self.lines.append)

    def test_records_match_names_case_insensitively_in_the_root_only(self):
        for name in ('FSCK0000.REC', 'fsck0001.rec', 'Fsck0002.Rec', 'FSCK000.REC', 'FSCK00000.REC',
                     'FSCKABCD.REC', 'FSCK0003.REC.bak', 'XFSCK0004.REC', 'FSCK0005REC'):
            self.record(name, b'x')
        (self.esp / 'FSCK0006.REC').mkdir()
        (self.outside / 'target').write_bytes(b'x')
        (self.esp / 'FSCK0007.REC').symlink_to(self.outside / 'target')
        (self.esp / 'EFI/FSCK0008.REC').write_bytes(b'x')
        self.assertEqual([path.name for path in esp_records.records(self.esp)],
                         ['FSCK0000.REC', 'Fsck0002.Rec', 'fsck0001.rec'])
        self.assertTrue(all(path.parent == self.esp for path in esp_records.records(self.esp)))

    def test_removes_proven_copies_keeps_the_rest_and_says_so(self):
        self.record('FSCK0000.REC', blob(13, 2 * C))                   # unknown content
        self.record('FSCK0001.REC', bytes(2 * C))                      # a chain of zeros
        self.record('FSCK0002.REC', self.grub + bytes(C - 300))        # whole GRUB, zero page tail
        self.record('fsck0003.rec', self.sidecar[:2 * C])              # a cut write of a sidecar
        self.record('FSCK0004.REC', self.grub[C:3 * C])                # a middle slice
        self.record('FSCK0005.REC', self.shim + bytes(C - 7))          # whole shim
        before = tree(self.esp)
        result = self.tidy()
        self.assertEqual(self.lines, [
            'Kept FSCK0000.REC (8192 bytes): its content is not a known Emaki boot file.',
            'Kept FSCK0001.REC (8192 bytes): its content is not a known Emaki boot file.',
            'Removed FSCK0002.REC (24576 bytes): a lost copy of EFI/Emaki/b/grubx64.efi left by a '
            'power cut.',
            'Kept FSCK0004.REC (8192 bytes): its content is not a known Emaki boot file.',
            'Removed FSCK0005.REC (12288 bytes): a lost copy of EFI/Emaki/b/shimx64.efi left by a '
            'power cut.',
            'Removed fsck0003.rec (8192 bytes): a lost copy of '
            'EFI/Emaki/b/grubx64.efi.emaki-new-0a1b left by a power cut.',
        ])
        self.assertEqual(result.removed, ['FSCK0002.REC', 'FSCK0005.REC', 'fsck0003.rec'])
        self.assertEqual(result.kept, ['FSCK0000.REC', 'FSCK0001.REC', 'FSCK0004.REC'])
        self.assertEqual(result.kept_bytes, 3 * 2 * C)
        for name in result.removed:
            del before[name]
        self.assertEqual(tree(self.esp), before)

    def test_shared_esp_never_touched(self):
        self.record('FSCK0000.REC', self.grub + bytes(C - 300))
        self.record('FSCK0001.REC', blob(15, C))
        self.record('fsck0002.rec', b'partial')
        before = tree(self.esp)
        times = {path.name: path.stat().st_mtime_ns for path in esp_records.records(self.esp)}
        with patch.object(esp_records, '_read', side_effect=AssertionError('read on a shared ESP')), \
                patch.object(Path, 'unlink', side_effect=AssertionError('unlink on a shared ESP')):
            result = self.tidy(shared=True)
        shared = ('the startup partition is shared with Windows; it was not removed and counts '
                  'against its free space.')
        self.assertEqual(self.lines, [f'Kept FSCK0000.REC (24576 bytes): {shared}',
                                      f'Kept FSCK0001.REC (4096 bytes): {shared}',
                                      f'Kept fsck0002.rec (7 bytes): {shared}'])
        self.assertEqual(result.removed, [])
        self.assertEqual(result.kept, ['FSCK0000.REC', 'FSCK0001.REC', 'fsck0002.rec'])
        self.assertEqual(result.kept_bytes, 6 * C + C + C)
        self.assertEqual(tree(self.esp), before)
        self.assertEqual({path.name: path.stat().st_mtime_ns
                          for path in esp_records.records(self.esp)}, times)

    def test_unreadable_record_kept_and_reported(self):
        self.record('FSCK0000.REC', self.grub + bytes(C - 300))
        self.record('FSCK0001.REC', self.shim + bytes(C - 7))
        real = esp_records._read

        def read(path, limit):
            if path.name == 'FSCK0000.REC':
                raise PermissionError(13, 'Permission denied')
            return real(path, limit)
        with patch.object(esp_records, '_read', side_effect=read):
            result = self.tidy()
        self.assertEqual(self.lines, [
            'Kept FSCK0000.REC (24576 bytes): it could not be read (Permission denied).',
            'Removed FSCK0001.REC (12288 bytes): a lost copy of EFI/Emaki/b/shimx64.efi left by a '
            'power cut.'])
        self.assertEqual((result.removed, result.kept, result.kept_bytes),
                         (['FSCK0001.REC'], ['FSCK0000.REC'], 6 * C))
        self.assertTrue((self.esp / 'FSCK0000.REC').exists())

    def test_record_gone_before_stat_kept_and_reported(self):
        self.record('FSCK0000.REC', self.shim + bytes(C - 7))
        with patch.object(esp_records.os, 'lstat', side_effect=FileNotFoundError(2, 'No such file')):
            result = self.tidy()
        self.assertEqual(self.lines, ['Kept FSCK0000.REC: it could not be read (No such file).'])
        self.assertEqual((result.removed, result.kept, result.kept_bytes), ([], ['FSCK0000.REC'], 0))

    def test_unremovable_record_kept_and_reported(self):
        self.record('FSCK0000.REC', self.shim + bytes(C - 7))
        with patch.object(Path, 'unlink', side_effect=OSError(30, 'Read-only file system')):
            result = self.tidy()
        self.assertEqual(self.lines, ['Kept FSCK0000.REC (12288 bytes): it could not be removed '
                                      '(Read-only file system).'])
        self.assertEqual((result.removed, result.kept, result.kept_bytes), ([], ['FSCK0000.REC'], 3 * C))

    def test_symlink_and_directory_with_record_names_ignored(self):
        (self.outside / 'grub-copy').write_bytes(self.grub + bytes(C - 300))
        (self.esp / 'FSCK0000.REC').symlink_to(self.outside / 'grub-copy')
        (self.esp / 'FSCK0001.REC').mkdir()
        (self.esp / 'FSCK0001.REC/inner').write_bytes(self.shim)
        before = tree(self.esp)
        result = self.tidy()
        self.assertEqual((self.lines, result.removed, result.kept), ([], [], []))
        self.assertEqual(tree(self.esp), before)
        self.assertEqual((self.outside / 'grub-copy').read_bytes(), self.grub + bytes(C - 300))

    def test_too_large_record_is_not_read(self):
        self.record('FSCK0000.REC', self.grub + bytes(C - 300) + bytes(C))
        with patch.object(esp_records, 'emaki_copy', side_effect=AssertionError('compared')):
            result = self.tidy()
        self.assertEqual(self.lines, ['Kept FSCK0000.REC (28672 bytes): its content is not a known '
                                      'Emaki boot file.'])
        self.assertEqual(result.kept, ['FSCK0000.REC'])

    def test_free_space_measured_after_removal(self):
        self.record('FSCK0000.REC', self.grub + bytes(C - 300))
        self.record('FSCK0001.REC', blob(16, C))

        def statvfs(path):
            used = sum(-(-(item.stat().st_size) // C) for item in Path(path).iterdir() if item.is_file())
            return os.statvfs_result((C, C, 1000, 1000 - used, 1000 - used, 0, 0, 0, 0, 255))
        with patch.object(esp_records.os, 'statvfs', side_effect=statvfs):
            result = self.tidy()
        self.assertEqual(result.removed, ['FSCK0000.REC'])
        self.assertEqual(result.free, (1000 - 1) * C)

    def test_no_records_no_lines(self):
        result = self.tidy()
        self.assertEqual((self.lines, result.removed, result.kept, result.kept_bytes), ([], [], [], 0))
        self.assertIsInstance(result.free, int)

    def test_missing_esp_reported_not_raised(self):
        result = esp_records.tidy(self.esp / 'missing', self.known, False, C, self.lines.append)
        self.assertEqual(len(self.lines), 1)
        self.assertRegex(self.lines[0], r'^Could not look for files saved by a disk check in the '
                                        r'startup partition \(.+\)\.$')
        self.assertEqual((result.removed, result.kept, result.free), ([], [], None))


# A minimal FAT32 image: 512-byte sectors, 4 KiB clusters, 65536 clusters (just above the
# FAT32 minimum of 65525), two FATs, the root directory in cluster 2. The file is sparse.
SECTOR, PER_CLUSTER, RESERVED, FATS, CLUSTERS = 512, 8, 32, 2, 65536
FAT_SECTORS = -(-(CLUSTERS + 2) * 4 // SECTOR)
TOTAL_SECTORS = RESERVED + FATS * FAT_SECTORS + CLUSTERS * PER_CLUSTER
END = 0x0FFFFFFF


def make_fat32(path):
    boot = bytearray(SECTOR)
    boot[0:11] = b'\xeb\x58\x90EMAKITST'
    struct.pack_into('<HBHBHHBHHHLL', boot, 11, SECTOR, PER_CLUSTER, RESERVED, FATS, 0, 0, 0xF8,
                     0, 32, 64, 0, TOTAL_SECTORS)
    struct.pack_into('<LHHLHH', boot, 36, FAT_SECTORS, 0, 0, 2, 1, 6)
    struct.pack_into('<BBBL11s8s', boot, 64, 0x80, 0, 0x29, 0x0E5A4B1, b'NO NAME    ', b'FAT32   ')
    boot[510:512] = b'\x55\xaa'
    info = bytearray(SECTOR)
    struct.pack_into('<L', info, 0, 0x41615252)
    struct.pack_into('<LLL', info, 484, 0x61417272, CLUSTERS - 1, 3)
    struct.pack_into('<L', info, 508, 0xAA550000)
    with open(path, 'wb') as image:
        image.truncate(TOTAL_SECTORS * SECTOR)
        for first in (0, 6):
            image.seek(first * SECTOR)
            image.write(boot + info)
        for copy in range(FATS):
            image.seek((RESERVED + copy * FAT_SECTORS) * SECTOR)
            image.write(struct.pack('<LLL', 0x0FFFFFF8, END, END))


def lose_chain(path, first, payload):
    """Allocate a chain for payload in both FATs and write payload into its clusters, with no
    directory entry: what a power cut leaves of a file being written. Marks the volume dirty
    as the kernel does while it is mounted."""
    count = len(payload) // (SECTOR * PER_CLUSTER)
    entries = b''.join(struct.pack('<L', END if index == count - 1 else first + index + 1)
                       for index in range(count))
    with open(path, 'r+b') as image:
        for copy in range(FATS):
            image.seek((RESERVED + copy * FAT_SECTORS) * SECTOR + first * 4)
            image.write(entries)
        image.seek((RESERVED + FATS * FAT_SECTORS + (first - 2) * PER_CLUSTER) * SECTOR)
        image.write(payload)
        image.seek(65)
        image.write(b'\x01')


class Fat32Reader:
    """Short names in the root directory and file bytes, from the geometry in the boot sector."""

    def __init__(self, image):
        self.image = image
        image.seek(0)
        boot = image.read(512)
        sector, per_cluster, reserved, fats = struct.unpack_from('<HBHB', boot, 11)
        fat_sectors, = struct.unpack_from('<L', boot, 36)
        self.root, = struct.unpack_from('<L', boot, 44)
        self.cluster = sector * per_cluster
        self.fat = reserved * sector
        self.data = (reserved + fats * fat_sectors) * sector

    def chain(self, first):
        found = []
        while 2 <= first < 0x0FFFFFF8 and len(found) <= CLUSTERS:
            found.append(first)
            self.image.seek(self.fat + first * 4)
            first = struct.unpack('<L', self.image.read(4))[0] & 0x0FFFFFFF
        return found

    def read(self, first, size=None):
        parts = []
        for cluster in self.chain(first):
            self.image.seek(self.data + (cluster - 2) * self.cluster)
            parts.append(self.image.read(self.cluster))
        data = b''.join(parts)
        return data if size is None else data[:size]

    def root_entries(self):
        directory = self.read(self.root)
        for offset in range(0, len(directory), 32):
            entry = directory[offset:offset + 32]
            if entry[0] == 0:
                break
            if entry[0] == 0xE5 or entry[11] == 0x0F:
                continue
            high, = struct.unpack_from('<H', entry, 20)
            low, size = struct.unpack_from('<HL', entry, 26)
            yield entry[:11].decode('ascii'), entry[11], high << 16 | low, size


FSCK = shutil.which('fsck.fat') or shutil.which('fsck.vfat')


@unittest.skipUnless(FSCK, 'fsck.fat is not installed')
class RealFsck(unittest.TestCase):
    """The rule on real fsck.fat output: two lost GRUB-sized chains, one with the rest of its
    last page as Linux writes it (zero), one with other bytes there."""

    def test_fsck_saves_the_lost_chain_with_its_tail(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / 'esp.img'
            make_fat32(image)
            clean = subprocess.run([FSCK, '-n', str(image)], capture_output=True, text=True)
            self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)
            self.assertNotIn('Reclaimed', clean.stdout)
            self.assertRegex(clean.stdout, r': 0 files, 1/65536 clusters')
            grub = blob(20, GRUB_SIZE)
            tail = (b'TAIL\x5a\xa5' * C)[:C - GRUB_SIZE % C]
            written = grub + tail
            self.assertEqual(len(written), 4063 * C)
            # The same file as Linux writes it: the rest of its last page zero-filled.
            linux = blob(22, GRUB_SIZE)
            zeroed = linux + bytes(C - GRUB_SIZE % C)
            lose_chain(image, 1000, written)
            lose_chain(image, 6000, zeroed)
            fixed = subprocess.run([FSCK, '-a', str(image)], capture_output=True, text=True)
            self.assertIn(fixed.returncode, (0, 1), fixed.stdout + fixed.stderr)
            self.assertIn('Reclaimed 8126 unused clusters (33284096 bytes) in 2 chains.', fixed.stdout)
            again = subprocess.run([FSCK, '-n', str(image)], capture_output=True, text=True)
            self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
            with open(image, 'rb') as stream:
                reader = Fat32Reader(stream)
                self.assertEqual(reader.cluster, C)
                entries = {first: (name, size) for name, _, first, size in reader.root_entries()}
                self.assertEqual(sorted(name for name, _ in entries.values()), ['FSCK0000REC', 'FSCK0001REC'])
                self.assertEqual(entries[1000][1], 16642048)
                self.assertEqual(entries[6000][1], 16642048)
                record, record_zeroed = reader.read(1000, 16642048), reader.read(6000, 16642048)
            # fsck.fat keeps each chain byte for byte, its tail included.
            self.assertEqual(record, written)
            self.assertEqual(record[GRUB_SIZE:], tail)
            self.assertEqual(record_zeroed, zeroed)
            # A tail Linux would have zero-filled is no proof; the zero-filled one is a copy.
            self.assertIsNone(esp_records.emaki_copy(record, {'grub': grub}, C))
            self.assertEqual(esp_records.emaki_copy(record_zeroed, {'grub': linux}, C), 'grub')
            self.assertIsNone(esp_records.emaki_copy(record_zeroed, {'other': blob(21, GRUB_SIZE)}, C))
            if os.environ.get('EMAKI_SHOW_FSCK'):
                print(fixed.stdout, file=sys.stderr)


    def test_fsck_keeps_older_sectors_after_the_last_file_sector(self):
        # R9S11's construction: the file, zeros to its last 512-byte sector, then 2,048 bytes
        # an earlier file left in the cluster. A complete copy: it is recognised.
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / 'esp.img'
            make_fat32(image)
            content = blob(22, GRUB_SIZE)
            sector_end = esp_records.whole_clusters(len(content), 512)
            cluster_end = esp_records.whole_clusters(len(content), C)
            written = content + bytes(sector_end - len(content)) + b'\xa5' * (cluster_end - sector_end)
            self.assertEqual((sector_end - len(content), cluster_end - sector_end), (416, 2048))
            lose_chain(image, 1000, written)
            fixed = subprocess.run([FSCK, '-a', str(image)], capture_output=True, text=True)
            self.assertIn(fixed.returncode, (0, 1), fixed.stdout + fixed.stderr)
            with open(image, 'rb') as stream:
                reader = Fat32Reader(stream)
                first, size = next((first, size) for _, _, first, size in reader.root_entries())
                record = reader.read(first, size)
            self.assertEqual(record, written)
            self.assertEqual(esp_records.emaki_copy(record, {'grub': content}, C), 'grub')
            self.assertEqual(esp_records.emaki_copy(record, {'grub': content}, C, 512), 'grub')
            self.assertIsNone(esp_records.emaki_copy(record, {'grub': content}, C, 4096))

if __name__ == '__main__':
    unittest.main()
