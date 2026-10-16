# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The partition editor of manual mode: plan rules, refusals and the worker's changes.

The disk tests write only regular image files in a temporary directory with the real sfdisk;
the kernel calls (disk sequence, partition reread) and the udev/lsblk view are simulated.
"""
import contextlib
import copy
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from emaki_installer import arch_backend
from emaki_installer.arch_backend import Backend, DeviceOperations
from emaki_installer.constants import GIB, MIB
from emaki_installer.errors import Code, InstallError
from emaki_installer.inventory import ESP_GUID, other_system, public_inventory
from emaki_installer.planner import SECURE_BOOT_SUBVOLUME, deletion_identity, make_plan
from emaki_installer.runtime import Runner
from emaki_installer.worker import windows_kept
from support import RecordingRunner, config, inventory, manual

BASIC_DATA = 'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7'
LINUX = '0fc63daf-8483-4772-8e79-3d69d8477de4'
MSR = 'e3c9e316-0b5c-4db8-817d-f92df00215ae'


def part(number, start, size, fs, **extra):
    return {'id': f'/dev/vda{number}', 'path': f'/dev/vda{number}', 'number': number,
            'uuid': f'uuid-{number}', 'fs': fs, 'size_bytes': size, 'start_bytes': start,
            'mountpoint': None, 'label': None, 'os_hint': None, 'esp': False, 'other_system': None,
            '_geometry_known': True, '_read_only': False, '_partuuid': f'partuuid-{number}',
            '_parttype': LINUX, '_partlabel': '', '_esp_free_bytes': None, '_btrfs_devices': 1,
            '_subvolumes': [], **extra}


def shared_disk(size=128 * GIB):
    """A disk with a Windows ESP, Windows, an old Linux system, a data partition and free space."""
    inv = inventory(size=size, partitions=True)
    disk = inv['disks'][0]
    disk['partitions'] = [
        part(1, MIB, 512 * MIB, 'vfat', esp=True, _parttype=ESP_GUID, _esp_free_bytes=400 * MIB,
             _efi_loaders={'EFI/Microsoft/Boot/bootmgfw.efi': 'a' * 64}),
        part(2, 513 * MIB, 40 * GIB, 'ntfs', os_hint='windows', _parttype=BASIC_DATA),
        part(3, 513 * MIB + 40 * GIB, 30 * GIB, 'ext4', os_hint='linux'),
        part(4, 513 * MIB + 70 * GIB, 10 * GIB, 'ext4'),
    ]
    for p in disk['partitions']:
        p['other_system'] = other_system(p)
    return inv


def free_start(inv):
    disk = inv['disks'][0]
    return max(p['start_bytes'] + p['size_bytes'] for p in disk['partitions'])


def new(ident, kind, start, size):
    return {'id': ident, 'type': kind, 'start_bytes': start, 'size_bytes': size}


def editor(inv, *, delete=(), create=(), mounts=(), new_table=False, encryption='none', **extra):
    c = config('manual')
    c.pop('fs')
    c['encryption'] = encryption
    c['partitioning'] = {'delete': list(delete), 'create': list(create)}
    if new_table:
        c['partitioning']['new_table'] = True
    c['mounts'] = [dict(row) for row in mounts]
    c.update(extra)
    return c


def row(ident, mountpoint, fs, fmt=True, **extra):
    return {'partition_id': ident, 'mountpoint': mountpoint, 'fs': fs, 'format': fmt, **extra}


def free_layout(inv, encryption='none'):
    """Keep the Windows ESP and Windows; root, /home and swap in the free space at the end."""
    start = free_start(inv)
    return editor(inv, encryption=encryption, create=[
        new('new-1', 'linux', start, 24 * GIB), new('new-2', 'linux', start + 24 * GIB, 8 * GIB),
        new('new-3', 'swap', start + 32 * GIB, 2 * GIB)], mounts=[
        row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'btrfs'),
        row('new-2', '/home', 'ext4', encrypt=encryption != 'none'),
        row('new-3', 'swap', 'swap', encrypt=encryption != 'none')])


class EditorPlanTests(unittest.TestCase):
    def rejects(self, c, inv, code=Code.MANUAL_LAYOUT, text=None):
        with self.assertRaises(InstallError) as raised:
            make_plan(c, inv)
        self.assertEqual(raised.exception.code, code, raised.exception.message)
        if text:
            self.assertIn(text, raised.exception.message)
        return raised.exception.message

    def test_new_partitions_in_free_space_keep_every_other_partition(self):
        inv = shared_disk()
        plan = make_plan(free_layout(inv), inv)
        self.assertFalse(plan.edits.new_table)
        self.assertEqual(plan.edits.deleted, [])
        numbers = {p.number: p for p in plan.partitions}
        # The new partitions take the lowest free GPT entries.
        self.assertEqual(sorted(numbers), [1, 5, 6, 7])
        self.assertEqual([numbers[n].create for n in (5, 6, 7)], ['linux', 'linux', 'swap'])
        self.assertTrue(all(numbers[n].path is None and numbers[n].format for n in (5, 6, 7)))
        self.assertEqual(plan.root.number, 5)
        self.assertEqual(plan.root.subvolumes, {k: v for k, v in
                         {'@': '/', '@log': '/var/log', '@pkg': '/var/cache/pacman/pkg',
                          '@snapshots': '/.snapshots'}.items()})
        self.assertTrue(numbers[7].swap)
        self.assertFalse(numbers[1].format)
        self.assertEqual(plan.summary[1:6], [
            "Keep partition 1 (/dev/vda1, 0.50 GiB, vfat) and its files; use: /efi. "
            "It holds another system's startup files; its fallback loader will not be changed.",
            'Create partition 5: 24.00 GiB, btrfs; use: @ → /, @log → /var/log, @pkg → /var/cache/pacman/pkg, '
            '@snapshots → /.snapshots.',
            'Create partition 6: 8.00 GiB, ext4; use: /home.',
            'Create partition 7: 2.00 GiB, swap; use: swap.',
            'Partitions not listed here are not changed.'])
        # The disk fingerprint is the reviewed disk's: the worker refuses a changed disk.
        self.assertEqual(plan.disk['partitions'], inv['disks'][0]['partitions'])

    def test_delete_then_create_in_the_freed_space(self):
        inv = shared_disk()
        disk = inv['disks'][0]
        old = disk['partitions'][3]
        c = editor(inv, delete=[{'partition_id': old['id']}], create=[
            new('new-1', 'linux', old['start_bytes'], old['size_bytes'] + 30 * GIB)], mounts=[
            row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4')])
        plan = make_plan(c, inv)
        self.assertEqual(plan.edits.deleted, [old])
        # The first free entry is the deleted one.
        self.assertEqual(plan.root.number, 4)
        self.assertIn('Delete partition 4 (/dev/vda4, 10.00 GiB, ext4): all its files are lost.', plan.summary)
        self.assertIn('Create partition 4: 40.00 GiB, ext4; use: /.', plan.summary)
        self.assertIn('deleted_partitions', [w['code'] for w in plan.warnings])

    def test_numbers_are_the_lowest_free_entries_in_order(self):
        inv = shared_disk()
        disk = inv['disks'][0]
        disk['partitions'] = disk['partitions'][:1] + disk['partitions'][2:]
        disk['partitions'][1]['number'] = 3  # 1 and 3 used, 2 free
        start = free_start(inv)
        c = editor(inv, delete=[{'partition_id': '/dev/vda4'}], create=[
            new('new-1', 'linux', start, 24 * GIB), new('new-2', 'linux', start + 24 * GIB, 2 * GIB)], mounts=[
            row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4'), row('new-2', '/home', 'ext4')])
        self.assertEqual(sorted(p.number for p in make_plan(c, inv).partitions), [1, 2, 4])

    def test_empty_disk_gets_a_new_gpt_table(self):
        inv = inventory(size=64 * GIB)
        disk = inv['disks'][0]
        disk['_pttype'] = 'dos'
        c = editor(inv, new_table=True, create=[
            new('new-1', 'esp', MIB, GIB), new('new-2', 'linux', GIB + MIB, 40 * GIB)], mounts=[
            row('new-1', '/efi', 'vfat'), row('new-2', '/', 'btrfs')])
        plan = make_plan(c, inv)
        self.assertTrue(plan.edits.new_table)
        self.assertEqual([(p.number, p.create, p.esp) for p in sorted(plan.partitions, key=lambda p: p.number)],
                         [(1, 'esp', True), (2, 'linux', False)])
        self.assertEqual(plan.summary[1], 'Create a new GPT partition table on /dev/vda; the disk is empty.')
        self.assertIn('Create partition 1: 1.00 GiB, startup partition, FAT32; use: /efi.', plan.summary)
        # Without the new table an MBR disk stays refused.
        c['partitioning'].pop('new_table')
        self.rejects(c, inv, text='requires GPT')

    def test_new_table_only_on_an_empty_disk(self):
        inv = shared_disk()
        c = editor(inv, new_table=True, create=[new('new-1', 'linux', free_start(inv), 24 * GIB)],
                   mounts=[row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4')])
        self.rejects(c, inv, text='only on an empty disk')
        empty = inventory(size=64 * GIB)
        for key, value in (('_fs', 'crypto_LUKS'), ('closed_encrypted', [{'path': '/dev/vda'}])):
            with self.subTest(key):
                data = copy.deepcopy(empty)
                data['disks'][0][key] = value
                c = editor(data, new_table=True, create=[
                    new('new-1', 'esp', MIB, GIB), new('new-2', 'linux', GIB + MIB, 40 * GIB)],
                    mounts=[row('new-1', '/efi', 'vfat'), row('new-2', '/', 'ext4')])
                with self.assertRaises(InstallError):
                    make_plan(c, data)
        self.rejects(editor(empty, new_table=True, mounts=[row('x', '/', 'ext4'), row('y', '/efi', 'vfat')]),
                     empty, text='Create the partitions')

    def test_new_partition_geometry_refusals(self):
        inv = shared_disk()
        start, end = free_start(inv), (128 * GIB - MIB) // MIB * MIB
        root = row('new-1', '/', 'ext4')
        cases = {
            'unaligned start': ([new('new-1', 'linux', start + 4096, 24 * GIB)], 'whole MiB'),
            'unaligned size': ([new('new-1', 'linux', start, 24 * GIB + 512)], 'whole MiB'),
            'before 1 MiB': ([new('new-1', 'linux', 0, 24 * GIB)], 'whole MiB'),
            'past the backup GPT': ([new('new-1', 'linux', end - 24 * GIB + MIB, 24 * GIB)], 'whole MiB'),
            'over Windows': ([new('new-1', 'linux', 600 * MIB, 24 * GIB)], 'overlaps'),
            'over each other': ([new('new-1', 'linux', start, 24 * GIB),
                                 new('new-2', 'linux', start + 23 * GIB, 2 * GIB)], 'overlaps'),
            'small ESP': ([new('new-1', 'linux', start, 24 * GIB), new('new-2', 'esp', start + 24 * GIB, 511 * MIB)],
                          '512 MiB'),
            'small swap': ([new('new-1', 'linux', start, 24 * GIB), new('new-2', 'swap', start + 24 * GIB, 255 * MIB)],
                           '256 MiB'),
            'small data': ([new('new-1', 'linux', start, 24 * GIB), new('new-2', 'linux', start + 24 * GIB, 1023 * MIB)],
                           '1 GiB'),
            'unknown kind': ([new('new-1', 'ntfs', start, 24 * GIB)], 'Invalid new partition'),
            'bad id': ([new('part-1', 'linux', start, 24 * GIB)], 'Invalid new partition'),
            'same id': ([new('new-1', 'linux', start, 24 * GIB), new('new-1', 'linux', start + 24 * GIB, GIB)],
                        'Invalid new partition'),
            'float size': ([new('new-1', 'linux', start, 24.0 * GIB)], 'Invalid new partition'),
            'extra key': ([dict(new('new-1', 'linux', start, 24 * GIB), fs='ext4')], 'Invalid new partition'),
        }
        for name, (create, text) in cases.items():
            with self.subTest(name):
                c = editor(inv, create=create, mounts=[row('/dev/vda1', '/efi', 'vfat', False), root] +
                           [row(r['id'], {'esp': '/efi', 'swap': 'swap'}.get(r['type'], '/home'),
                                {'esp': 'vfat', 'swap': 'swap'}.get(r['type'], 'ext4'))
                            for r in create[1:] if r['id'] != 'new-1'])
                self.rejects(c, inv, text=text)
        too_many = [new(f'new-{n}', 'linux', start + n * GIB, GIB) for n in range(1, 18)]
        self.rejects(editor(inv, create=too_many, mounts=[row('/dev/vda1', '/efi', 'vfat', False)]), inv,
                     text='Invalid partition changes')
        self.rejects(editor(inv, create=[new('new-1', 'linux', start, 12 * GIB)], mounts=[
            row('/dev/vda1', '/efi', 'vfat', False), root]), inv, Code.ROOT_TOO_SMALL)

    def test_no_free_gpt_entry(self):
        inv = shared_disk()
        disk = inv['disks'][0]
        for n, p in enumerate(disk['partitions']):
            p['number'] = 125 + n
        used = {p['number'] for p in disk['partitions']}
        for n in range(1, 125):
            if n not in used:
                # Empty entries of no size at the very start: they take a number, not space.
                disk['partitions'].append(part(n, 0, 0, None))
        start = free_start(inv)
        c = editor(inv, create=[new('new-1', 'linux', start, 24 * GIB)], mounts=[
            row(disk['partitions'][0]['id'], '/efi', 'vfat', False), row('new-1', '/', 'ext4')])
        self.rejects(c, inv, text='no free GPT partition entry')

    def test_other_systems_are_never_formatted_or_mounted(self):
        inv = shared_disk()
        start = free_start(inv)
        base = [new('new-1', 'linux', start, 24 * GIB)]
        for name, mounts, text in (
                ('format Windows', [row('/dev/vda1', '/efi', 'vfat', False), row('/dev/vda2', '/', 'ext4')],
                 'holds Windows'),
                ('mount the old Linux as /home', [row('/dev/vda1', '/efi', 'vfat', False),
                                                 row('new-1', '/', 'ext4'), row('/dev/vda3', '/home', 'ext4', False)],
                 'holds Linux'),
                ('format the Windows ESP', [row('/dev/vda1', '/efi', 'vfat', True), row('new-1', '/', 'ext4')],
                 'holds Windows')):
            with self.subTest(name):
                c = editor(inv, create=base if 'new-1' in str(mounts) else [], mounts=mounts)
                self.rejects(c, inv, text=text)
        # Keeping the Windows ESP unformatted at /efi is the existing ESP reuse.
        make_plan(editor(inv, create=base, mounts=[row('/dev/vda1', '/efi', 'vfat', False),
                                                   row('new-1', '/', 'ext4')]), inv)

    def test_deleting_another_system_needs_its_typed_confirmation(self):
        inv = shared_disk()
        disk = inv['disks'][0]
        windows = disk['partitions'][1]
        mounts = [row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4')]
        create = [new('new-1', 'linux', windows['start_bytes'], 24 * GIB)]
        message = self.rejects(editor(inv, delete=[{'partition_id': '/dev/vda2'}], create=create, mounts=mounts),
                               inv, text='holds Windows')
        self.assertIn('typing DELETE', message)
        identity = deletion_identity(windows)
        self.assertEqual(identity, {'path': '/dev/vda2', 'start_bytes': 513 * MIB, 'size_bytes': 40 * GIB,
                                    'system': 'Windows'})
        for key, value in (('size_bytes', 41 * GIB), ('start_bytes', MIB), ('path', '/dev/vda3'), ('system', 'Linux')):
            with self.subTest(stale=key):
                stale = dict(identity, **{key: value})
                self.rejects(editor(inv, delete=[{'partition_id': '/dev/vda2', 'confirm': stale}], create=create,
                                    mounts=mounts), inv, text='typing DELETE')
        plan = make_plan(editor(inv, delete=[{'partition_id': '/dev/vda2', 'confirm': identity}], create=create,
                                mounts=mounts), inv)
        self.assertIn('Delete partition 2 (/dev/vda2, 40.00 GiB, ntfs, Windows): all its files are lost.', plan.summary)
        # Nothing to confirm on a partition of no other system: a confirmation there is refused too.
        self.rejects(editor(inv, delete=[{'partition_id': '/dev/vda4', 'confirm': deletion_identity(windows)}],
                            create=[new('new-1', 'linux', free_start(inv), 24 * GIB)], mounts=mounts), inv)

    def test_deleted_partitions_cannot_be_used_or_deleted_twice(self):
        inv = shared_disk()
        start = free_start(inv)
        create = [new('new-1', 'linux', start, 24 * GIB)]
        self.rejects(editor(inv, delete=[{'partition_id': '/dev/vda4'}], create=create, mounts=[
            row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4'), row('/dev/vda4', '/home', 'ext4')]),
            inv, text='deleted partition')
        self.rejects(editor(inv, delete=[{'partition_id': '/dev/vda4'}] * 2, create=create, mounts=[
            row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4')]), inv, text='deleted twice')
        self.rejects(editor(inv, delete=[{'partition_id': '/dev/vdb4'}], create=create, mounts=[
            row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4')]), inv, text='not on the selected disk')
        disk = inv['disks'][0]
        disk['partitions'][3]['_read_only'] = True
        self.rejects(editor(inv, delete=[{'partition_id': '/dev/vda4'}], create=create, mounts=[
            row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4')]), inv, text='cannot be deleted')

    def test_every_new_partition_needs_its_matching_use(self):
        inv = shared_disk()
        start = free_start(inv)
        root, esp = row('new-1', '/', 'ext4'), row('/dev/vda1', '/efi', 'vfat', False)
        cases = (
            ('unused', [new('new-2', 'linux', start + 24 * GIB, GIB)], [], 'needs a use'),
            ('unformatted', [], [dict(root, format=False)], 'requires format'),
            ('swap as /home', [new('new-2', 'swap', start + 24 * GIB, GIB)], [row('new-2', '/home', 'ext4')], 'only be used as swap'),
            ('ESP as /home', [new('new-2', 'esp', start + 24 * GIB, GIB)], [row('new-2', '/home', 'ext4')], 'only be mounted at /efi'),
            ('Linux as /efi', [new('new-2', 'linux', start + 24 * GIB, GIB)], [row('new-2', '/efi', 'vfat')], 'existing GPT EFI system partition'),
            ('Linux as swap', [new('new-2', 'linux', start + 24 * GIB, GIB)], [row('new-2', 'swap', 'swap')], 'Only a swap'),
            ('ESP as swap', [new('new-2', 'esp', start + 24 * GIB, GIB)], [row('new-2', 'swap', 'swap')], 'Only a swap'),
            ('swap without format', [new('new-2', 'swap', start + 24 * GIB, GIB)], [row('new-2', 'swap', 'swap', False)],
             'requires formatting'),
            ('two uses of swap', [new('new-2', 'swap', start + 24 * GIB, GIB)],
             [row('new-2', 'swap', 'swap'), row('new-2', 'swap', 'swap')], 'other uses'),
        )
        for name, extra, rows, text in cases:
            with self.subTest(name):
                mounts = ([] if name == 'Linux as /efi' else [esp]) + (rows if name == 'unformatted' else [root] + rows)
                c = editor(inv, create=[new('new-1', 'linux', start, 24 * GIB)] + extra, mounts=mounts)
                if name == 'two uses of swap':
                    c['mounts'][-1]['fs'] = 'swap'
                self.rejects(c, inv, text=text)

    def test_existing_partitions_as_swap_and_esp_rules(self):
        inv = shared_disk()
        start = free_start(inv)
        create = [new('new-1', 'linux', start, 24 * GIB)]
        esp = row('/dev/vda1', '/efi', 'vfat', False)
        plan = make_plan(editor(inv, create=create, mounts=[esp, row('new-1', '/', 'ext4'),
                                                           row('/dev/vda4', 'swap', 'swap')]), inv)
        swap = next(p for p in plan.partitions if p.swap)
        self.assertEqual((swap.number, swap.format, swap.create), (4, True, None))
        self.assertIn('Format partition 4 (/dev/vda4, 10.00 GiB) as swap: its files are lost; use: swap.', plan.summary)
        self.rejects(editor(inv, create=create, mounts=[esp, row('new-1', '/', 'ext4'),
                                                       row('/dev/vda4', 'swap', 'swap', False)]), inv,
                     text='requires formatting')
        # An ESP the installer formats must really be FAT32 and hold both Secure Boot slots.
        data = shared_disk()
        own = data['disks'][0]['partitions'][0]
        own.update(_efi_loaders={}, other_system=None, size_bytes=511 * MIB)
        self.rejects(editor(data, create=create, mounts=[row('/dev/vda1', '/efi', 'vfat'), row('new-1', '/', 'ext4')]),
                     data, Code.ESP_SPACE, '512 MiB')
        own['size_bytes'] = 512 * MIB
        make_plan(editor(data, create=create, mounts=[row('/dev/vda1', '/efi', 'vfat'), row('new-1', '/', 'ext4')]), data)

    def test_encryption_of_home_and_swap_follows_root(self):
        inv = shared_disk()
        self.rejects(free_layout(inv, 'none') | {'mounts': [dict(r, encrypt=True) if r['mountpoint'] == '/home' else r
                                                           for r in free_layout(inv)['mounts']]}, inv,
                     text='encrypted root')
        plan = make_plan(free_layout(inv, 'account'), inv)
        home, swap = (next(p for p in plan.partitions if p.number == n) for n in (6, 7))
        self.assertEqual((home.encrypt, home.mapper_name, swap.encrypt, swap.mapper_name),
                         (True, 'emaki-part6', True, 'emaki-part7'))
        self.assertFalse(plan.root.encrypt)
        self.assertIn('Create partition 6: 8.00 GiB, ext4, encrypted (LUKS2); use: /home.', plan.summary)
        self.assertIn('Create partition 5: 24.00 GiB, btrfs, encrypted (LUKS2); use: @ → /, @log → /var/log, '
                      '@pkg → /var/cache/pacman/pkg, @snapshots → /.snapshots.', plan.summary)
        c = free_layout(inv, 'account')
        c['mounts'][1]['encrypt'] = True
        self.rejects(c, inv, text='Encryption step')
        c = free_layout(inv, 'account')
        c['mounts'][0]['encrypt'] = True
        self.rejects(c, inv, text='other than root and /efi')
        c = free_layout(inv, 'account')
        c['mounts'][2]['encrypt'] = 'yes'
        self.rejects(c, inv, text='Invalid mount row')

    def test_deleted_locked_volume_needs_the_erase_confirmation(self):
        inv = shared_disk()
        disk = inv['disks'][0]
        locked = disk['partitions'][3]
        locked['fs'] = 'crypto_LUKS'
        volume = {'path': locked['path'], 'type': 'crypto_LUKS', 'uuid': 'locked', 'warning': 'Type ERASE'}
        disk['closed_encrypted'] = [volume]
        c = editor(inv, delete=[{'partition_id': locked['id']}], create=[new('new-1', 'linux', free_start(inv), 24 * GIB)],
                   mounts=[row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4')])
        self.rejects(c, inv, Code.ENCRYPTED_CONFIRMATION)
        make_plan(dict(c, confirmed_encrypted=[{'path': locked['path'], 'type': 'crypto_LUKS', 'uuid': 'locked'}]), inv)

    def test_partition_changes_only_in_manual_mode_and_well_formed(self):
        inv = shared_disk()
        for mode in ('erase',):
            c = dict(config(mode), partitioning={'create': []})
            self.rejects(c, inv, Code.BAD_CONFIG, 'manual installation')
        for value in ([], {'resize': []}, {'delete': {}}, {'create': None}, {'new_table': 1}):
            with self.subTest(value=value):
                c = free_layout(inv)
                c['partitioning'] = value
                self.rejects(c, inv, text='Invalid partition changes')
        # An empty change list is the plain manual layout.
        plain = manual()
        plain['partitioning'] = {'delete': [], 'create': []}
        plan = make_plan(plain, inventory(partitions=True))
        self.assertIsNone(plan.edits)

    def test_secure_boot_keeps_its_key_subvolume_and_kept_esp_room(self):
        inv = shared_disk()
        inv['hardware'] = {'secure_boot': True, 'secure_boot_cas': 'both'}
        disk = inv['disks'][0]
        disk['partitions'][0]['_esp_free_bytes'] = 40 * MIB
        self.rejects(free_layout(inv), inv, Code.ESP_SPACE, 'too full for Secure Boot')
        start = free_start(inv)
        c = editor(inv, create=[new('new-1', 'esp', start, GIB), new('new-2', 'linux', start + GIB, 24 * GIB)],
                   mounts=[row('new-1', '/efi', 'vfat'), row('new-2', '/', 'btrfs')])
        plan = make_plan(c, inv)
        self.assertEqual(plan.root.subvolumes[SECURE_BOOT_SUBVOLUME], '/var/lib/emaki/secureboot')
        self.assertEqual(plan.esp_label, 'EMAKI')

    def test_windows_kept_counts_deleted_partitions(self):
        inv = shared_disk()
        windows = inv['disks'][0]['partitions'][1]
        esp = inv['disks'][0]['partitions'][0]
        create = [new('new-1', 'linux', windows['start_bytes'], 24 * GIB)]
        mounts = [row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4')]
        keep = make_plan(editor(inv, create=create[:0] + [new('new-1', 'linux', free_start(inv), 24 * GIB)],
                                mounts=mounts), inv)
        self.assertTrue(windows_kept(keep, inv))
        gone = make_plan(editor(inv, delete=[{'partition_id': '/dev/vda2', 'confirm': deletion_identity(windows)}],
                                create=create, mounts=mounts), inv)
        # The Windows ESP is kept: its Microsoft loader still counts as Windows left behind.
        esp['os_hint'] = 'windows'
        self.assertTrue(windows_kept(gone, inv))
        esp['os_hint'] = None
        self.assertFalse(windows_kept(gone, inv))

    def test_other_system_evidence(self):
        cases = (({'os_hint': 'windows'}, 'Windows'), ({'fs': 'BitLocker'}, 'Windows'), ({'fs': 'ntfs'}, 'Windows'),
                 ({'fs': 'apfs'}, 'macOS'), ({'os_hint': 'macos'}, 'macOS'), ({'os_hint': 'linux'}, 'Linux'),
                 ({'_parttype': MSR.upper()}, 'Windows'),
                 ({'_efi_loaders': {'EFI/Microsoft/Boot/bootmgfw.efi': ''}}, 'Windows'),
                 ({'_efi_loaders': {'EFI/ubuntu/shimx64.efi': ''}}, 'another system'),
                 ({'_efi_loaders': {'EFI/BOOT/BOOTX64.EFI': ''}}, None),
                 ({'os_hint': 'emaki-incomplete', 'fs': 'btrfs'}, None), ({'fs': 'ext4'}, None))
        for fields, system in cases:
            with self.subTest(fields=fields):
                self.assertEqual(other_system(fields), system)
        # The window gets the verdict, never the private evidence.
        public = public_inventory(shared_disk())
        self.assertEqual([p['other_system'] for p in public['disks'][0]['partitions']],
                         ['Windows', 'Windows', 'Linux', None])
        self.assertNotIn('_efi_loaders', public['disks'][0]['partitions'][0])


class MissingEvidenceTests(unittest.TestCase):
    """Review R-P9: evidence the probe could not read is not taken for an empty partition."""

    def rejects(self, c, inv, text):
        with self.assertRaises(InstallError) as raised:
            make_plan(c, inv)
        self.assertIn(text, raised.exception.message)

    def test_an_unread_esp_or_file_system_is_never_formatted_or_mounted(self):
        start = free_start(shared_disk())
        create = [new('new-1', 'linux', start, 24 * GIB)]
        cases = (
            ('an ESP whose mount failed', 0, dict(os_hint=None, _efi_loaders=None, _unreadable=True), '/efi', 'vfat'),
            ('an ESP whose loaders could not be listed', 0, dict(os_hint=None, _efi_loaders={}, _efi_error='x'), '/efi', 'vfat'),
            ('xfs the probe never reads', 3, dict(fs='xfs', os_hint=None), '/home', 'ext4'),
            ('ext4 whose mount failed', 3, dict(os_hint=None, _unreadable=True), '/home', 'ext4'),
        )
        for name, index, fields, mountpoint, fs in cases:
            with self.subTest(name):
                inv = shared_disk()
                target = inv['disks'][0]['partitions'][index]
                target.update(fields)
                target['other_system'] = other_system(target)
                self.assertEqual(target['other_system'], 'files Emaki cannot check')
                mounts = [row('new-1', '/', 'ext4'), row(target['id'], mountpoint, fs)]
                if mountpoint != '/efi':
                    mounts.append(row('/dev/vda1', '/efi', 'vfat', False))
                self.rejects(editor(inv, create=create, mounts=mounts), inv,
                             f"Partition {target['number']} holds files Emaki cannot check.")
                # Deleting it needs the typed confirmation, which names what it holds.
                self.assertEqual(deletion_identity(target)['system'], 'files Emaki cannot check')

    def test_an_earlier_emaki_is_emakis_to_reuse(self):
        inv = shared_disk()
        old = inv['disks'][0]['partitions'][3]
        old.update(fs='btrfs', os_hint='emaki', _subvolumes=['@', '@home', '@log', '@pkg', '@snapshots'])
        old['other_system'] = other_system(old)
        self.assertIsNone(old['other_system'])
        old['size_bytes'] = 24 * GIB
        c = editor(inv, mounts=[row('/dev/vda1', '/efi', 'vfat', False)] + [
            row(old['id'], mp, 'btrfs', False, subvolume=name) for name, mp in
            (('@', '/'), ('@home', '/home'), ('@log', '/var/log'), ('@pkg', '/var/cache/pacman/pkg'),
             ('@snapshots', '/.snapshots'))])
        c['partitioning'] = None
        c.pop('partitioning')
        make_plan(c, inv)
        with tempfile.TemporaryDirectory() as temp:
            from emaki_installer.inventory import _root_hint
            root = Path(temp)
            (root / 'usr/lib').mkdir(parents=True)
            (root / 'usr/lib/os-release').write_text('NAME="Emaki"\nID=arch\nVARIANT_ID=emaki\n')
            self.assertEqual(_root_hint(root), 'emaki')
            (root / 'usr/lib/os-release').write_text('NAME="Arch Linux"\nID=arch\n')
            self.assertEqual(_root_hint(root), 'linux')

    def test_review_says_which_partitions_are_encrypted(self):
        inv = shared_disk()
        plan = make_plan(free_layout(inv, 'account'), inv)
        scope = next(w['msg'] for w in plan.warnings if w['code'] == 'encryption_scope')
        self.assertEqual(scope, 'The root partition and the partitions marked encrypted are encrypted. '
                                'The startup partition and the other partitions stay unencrypted.')
        c = free_layout(inv, 'account')
        for r in c['mounts']:
            r.pop('encrypt', None)
        scope = next(w['msg'] for w in make_plan(c, inv).warnings if w['code'] == 'encryption_scope')
        self.assertEqual(scope, 'The root partition is encrypted. The startup partition and any separate data partitions '
                                'stay unencrypted.')

    def test_a_partition_of_unknown_place_stops_the_editor_at_review(self):
        inv = shared_disk()
        inv['disks'][0]['partitions'][3]['_geometry_known'] = False
        self.rejects(free_layout(inv), inv, 'The place of partition 4 on the disk is unknown')


class ImageRunner(Runner):
    """The real sfdisk on the test image; the udev and lsblk view of the simulated kernel."""

    def __init__(self, image, disk_path, sector=512):
        super().__init__(lambda line: None)
        self.image, self.disk_path = image, disk_path
        self.sector = sector
        self.commands = []
        self.tamper = None

    def run(self, argv, **kwargs):
        argv = [str(x) for x in argv]
        self.commands.append(argv)
        if argv[0] == 'sfdisk':
            # Only a regular file inherited as the reviewed descriptor is ever written.
            fd = int(argv[[i for i, a in enumerate(argv) if a.startswith('/proc/self/fd/')][0]].rsplit('/', 1)[1])
            assert stat.S_ISREG(os.fstat(fd).st_mode)
            output = super().run([argv[0], '--sector-size', str(self.sector), *argv[1:]], **kwargs)
            if self.tamper and '--json' not in argv:
                self.tamper(argv)
            return output
        if argv[0] == 'udevadm':
            return ''
        if argv[0] == 'lsblk':
            table = json.loads(subprocess.run(['sfdisk', '--sector-size', str(self.sector), '--json', str(self.image)], capture_output=True,
                                              text=True, check=True).stdout)['partitiontable']
            children = []
            for p in table.get('partitions', []):
                number = int(p['node'][len(str(self.image)):].removeprefix('p'))
                separator = 'p' if self.disk_path[-1].isdigit() else ''
                children.append({'path': f'{self.disk_path}{separator}{number}', 'type': 'part', 'partn': number,
                                 'start': p['start'] * self.sector // 512, 'size': p['size'] * self.sector, 'pttype': 'gpt',
                                 'partuuid': p['uuid'].lower()})
            return json.dumps({'blockdevices': [{'path': self.disk_path, 'pttype': 'gpt', 'children': children}]})
        raise AssertionError(argv)


def write_table(image, script, sector=512):
    assert stat.S_ISREG(image.stat().st_mode)
    subprocess.run(['sfdisk', '--sector-size', str(sector), '--no-reread', '--no-tell-kernel', '-q', str(image)], input=script, text=True,
                   check=True, capture_output=True)


def read_table(image, sector=512):
    table = json.loads(subprocess.run(['sfdisk', '--sector-size', str(sector), '--json', str(image)], capture_output=True, text=True,
                                      check=True).stdout)['partitiontable']
    return {int(p['node'][len(str(image)):].removeprefix('p')): {k: v for k, v in p.items() if k != 'node'}
            for p in table['partitions']}, table['id']


@unittest.skipUnless(shutil.which('sfdisk'), 'sfdisk (util-linux) is required')
class ImageDiskTests(unittest.TestCase):
    """The worker's partition step on a 64 GiB sparse image file, with the real sfdisk."""
    SIZE = 64 * GIB

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='emaki-editor-')
        self.image = Path(self.temp.name) / 'disk.img'
        with self.image.open('wb') as stream:
            stream.truncate(self.SIZE)

    def tearDown(self):
        self.temp.cleanup()

    def inventory(self):
        """What the probe reports for the image: its partitions as the reviewed disk."""
        inv = inventory(size=self.SIZE)
        disk = inv['disks'][0]
        rows, _ = read_table(self.image) if self.image.stat().st_size and self.has_table() else ({}, None)
        disk['_pttype'] = 'gpt' if rows or self.has_table() else None
        for number, p in rows.items():
            fs = {ESP_GUID: 'vfat', BASIC_DATA: 'ntfs'}.get(p['type'].lower(), 'ext4')
            disk['partitions'].append(part(number, p['start'] * 512, p['size'] * 512, fs,
                                           esp=p['type'].lower() == ESP_GUID, _parttype=p['type'].lower(),
                                           _partuuid=p['uuid'].lower(), _esp_free_bytes=400 * MIB,
                                           os_hint='windows' if fs == 'ntfs' else None))
        for p in disk['partitions']:
            p['other_system'] = other_system(p)
        disk['_identity']['disk-seq'] = 42
        return inv

    def has_table(self):
        return subprocess.run(['sfdisk', '--json', str(self.image)], capture_output=True).returncode == 0

    def windows_layout(self):
        write_table(self.image, 'label: gpt\nunit: sectors\n\n'
                    'start=2048, size=1048576, type=U\n'
                    f'start=1050624, size=41943040, type={BASIC_DATA.upper()}, name="Basic data partition", attrs="GUID:63"\n'
                    'start=42993664, size=20971520, type=L\n')
        # Windows' bytes: a sentinel at its start and end that no write may change.
        with self.image.open('r+b') as stream:
            for offset in (1050624 * 512, (1050624 + 41943040) * 512 - 16):
                stream.seek(offset)
                stream.write(b'WINDOWS-SENTINEL')

    def sentinels(self):
        with self.image.open('rb') as stream:
            found = []
            for offset in (1050624 * 512, (1050624 + 41943040) * 512 - 16):
                stream.seek(offset)
                found.append(stream.read(16))
            return found

    def head(self):
        """The protective MBR, the primary GPT and the first partition's start."""
        with self.image.open('rb') as stream:
            return stream.read(2 * MIB)

    def kept_bytes(self):
        """Compare every allocated payload byte and every hole, plus both raw GPT entries."""
        parts, _ = read_table(self.image)
        result = []
        with self.image.open('rb', buffering=0) as stream:
            fd = stream.fileno()
            for number in (1, 2):
                start = parts[number]['start'] * 512
                end = start + parts[number]['size'] * 512
                spans = []
                cursor = start
                while cursor < end:
                    try:
                        data = os.lseek(fd, cursor, os.SEEK_DATA)
                    except OSError as exc:
                        if exc.errno == errno.ENXIO:
                            break
                        raise
                    if data >= end:
                        break
                    stop = min(os.lseek(fd, data, os.SEEK_HOLE), end)
                    stream.seek(data)
                    digest = hashlib.sha256()
                    remaining = stop - data
                    while remaining:
                        block = stream.read(min(MIB, remaining))
                        self.assertTrue(block)
                        digest.update(block)
                        remaining -= len(block)
                    spans.append((data, stop, digest.hexdigest()))
                    cursor = stop
                entries = []
                for header in (512, self.SIZE - 512):
                    stream.seek(header + 72)
                    lba, _, width = struct.unpack('<QII', stream.read(16))
                    stream.seek(lba * 512 + (number - 1) * width)
                    entries.append(stream.read(width))
                result.append((spans, entries))
        return result

    def apply(self, plan, runner=None, *, logical_sector=512):
        plan.disk['path'] = str(self.image)
        runner = runner or ImageRunner(self.image, '/dev/vda')
        mod = SimpleNamespace(wipe=False, partitions=[SimpleNamespace(partn=p.number, dev_path=None)
                                                      for p in plan.partitions if not p.swap])
        def ioctl(fd, request, argument=None):
            if request == 0x1268:  # BLKSSZGET: regular image files have no block-device ioctl.
                self.assertEqual(os.readlink(f'/proc/self/fd/{fd}'), str(self.image))
                if isinstance(logical_sector, OSError):
                    raise logical_sector
                return struct.pack('=I', logical_sector)
            return 0

        with patch.object(arch_backend, 'disk_sequence', return_value=42), \
                patch.object(arch_backend.fcntl, 'ioctl', side_effect=ioctl) as reread:
            DeviceOperations(plan, runner).partition(mod)
        return runner, mod, reread

    def test_delete_and_create_keep_windows_byte_for_byte(self):
        self.windows_layout()
        before, label = read_table(self.image)
        preserved = self.kept_bytes()
        inv = self.inventory()
        disk = inv['disks'][0]
        old = disk['partitions'][2]
        c = editor(inv, delete=[{'partition_id': old['id']}], create=[
            new('new-1', 'linux', old['start_bytes'], 24 * GIB), new('new-2', 'swap', old['start_bytes'] + 24 * GIB, 2 * GIB),
            new('new-3', 'linux', old['start_bytes'] + 26 * GIB, 4 * GIB)], mounts=[
            row(disk['partitions'][0]['id'], '/efi', 'vfat', False), row('new-1', '/', 'ext4'),
            row('new-2', 'swap', 'swap'), row('new-3', '/home', 'ext4')])
        plan = make_plan(c, inv)
        runner, mod, reread = self.apply(plan)
        after, after_label = read_table(self.image)
        self.assertEqual(after_label, label)
        # Kept entries: the same start, size, type, GUID, name and attributes.
        self.assertEqual(after[1], before[1])
        self.assertEqual(after[2], before[2])
        self.assertEqual(after[2]['name'], 'Basic data partition')
        self.assertEqual(after[2]['attrs'], 'GUID:63')
        self.assertEqual(self.sentinels(), [b'WINDOWS-SENTINEL'] * 2)
        self.assertEqual(self.kept_bytes(), preserved)
        self.assertEqual(sorted(after), [1, 2, 3, 4, 5])
        # The new partitions take the first free entries in order: 3 (the deleted one), 4 and 5.
        for number, start, size in ((3, old['start_bytes'], 24 * GIB), (4, old['start_bytes'] + 24 * GIB, 2 * GIB),
                                    (5, old['start_bytes'] + 26 * GIB, 4 * GIB)):
            self.assertEqual((after[number]['start'] * 512, after[number]['size'] * 512), (start, size))
            self.assertEqual(after[number]['type'].lower(), LINUX)
        paths = {p.number: p.path for p in plan.partitions}
        self.assertEqual(paths, {1: '/dev/vda1', 3: '/dev/vda3', 4: '/dev/vda4', 5: '/dev/vda5'})
        self.assertEqual({p.partn: str(p.dev_path) for p in mod.partitions}, {1: 'None', 3: '/dev/vda3', 5: '/dev/vda5'})
        # One sfdisk run per change, all through the reviewed descriptor without a kernel reread.
        writes = [argv for argv in runner.commands if argv[0] == 'sfdisk' and '--json' not in argv]
        self.assertEqual(len(writes), 4)
        self.assertTrue(all('--no-reread' in argv and '--no-tell-kernel' in argv and '--lock=yes' in argv
                            and argv[argv.index('--delete') + 1 if '--delete' in argv else -1].startswith('/proc/self/fd/')
                            for argv in writes))
        self.assertTrue(all('--wipe=never' in argv and '--wipe-partitions=always' in argv
                            for argv in writes if '--append' in argv))
        reread.assert_called_once()
        self.assertEqual(reread.call_args.args[1], arch_backend.BLKRRPART)

    def test_delete_and_create_on_a_grown_image_keep_windows_byte_for_byte(self):
        self.windows_layout()
        before, label = read_table(self.image)
        preserved = self.kept_bytes()
        self.SIZE *= 2
        with self.image.open('r+b') as stream:
            stream.truncate(self.SIZE)
        inv = self.inventory()
        old = inv['disks'][0]['partitions'][2]
        plan = make_plan(editor(inv, delete=[{'partition_id': old['id']}], create=[
            new('new-1', 'linux', old['start_bytes'], 24 * GIB),
            new('new-2', 'swap', old['start_bytes'] + 24 * GIB, 2 * GIB)], mounts=[
            row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4'),
            row('new-2', 'swap', 'swap')]), inv)
        runner = ImageRunner(self.image, '/dev/vda')
        real = runner.run
        headers = []

        def run(argv, **kwargs):
            output = real(argv, **kwargs)
            if argv[0] == 'sfdisk' and '--json' in argv:
                headers.append(json.loads(output[output.find('{'):output.rfind('}') + 1])['partitiontable'])
            return output

        runner.run = run
        _, mod, reread = self.apply(plan, runner)
        after, after_label = read_table(self.image)
        self.assertEqual(after_label, label)
        self.assertEqual(sorted(after), [1, 2, 3, 4])
        self.assertEqual(after[1], before[1])
        self.assertEqual(after[2], before[2])
        self.assertEqual(self.sentinels(), [b'WINDOWS-SENTINEL'] * 2)
        self.assertEqual(self.kept_bytes(), preserved)
        for number, start, size in ((3, old['start_bytes'], 24 * GIB),
                                    (4, old['start_bytes'] + 24 * GIB, 2 * GIB)):
            self.assertEqual((after[number]['start'] * 512, after[number]['size'] * 512), (start, size))
            self.assertEqual(after[number]['type'].lower(), LINUX)
        self.assertEqual(plan.root.path, '/dev/vda3')
        self.assertEqual({p.partn: str(p.dev_path) for p in mod.partitions}, {1: 'None', 3: '/dev/vda3'})
        # The first write relocates the backup table; later writes keep its new boundary.
        self.assertEqual(len(headers), 4)
        self.assertEqual(headers[1]['lastlba'] - headers[0]['lastlba'], self.SIZE // 2 // 512)
        self.assertTrue(all(h['lastlba'] == headers[1]['lastlba'] for h in headers[2:]))
        for key in ('label', 'id', 'firstlba', 'sectorsize'):
            self.assertTrue(all(h[key] == headers[0][key] for h in headers[1:]), key)
        writes = [argv for argv in runner.commands if argv[0] == 'sfdisk' and '--json' not in argv]
        self.assertEqual(len(writes), 3)
        self.assertIn('--delete', writes[0])
        self.assertTrue(all('--append' in argv for argv in writes[1:]))
        reread.assert_called_once()

    def test_each_write_is_checked_before_the_next_change(self):
        self.windows_layout()
        inv = self.inventory()
        old = inv['disks'][0]['partitions'][2]
        plan = make_plan(editor(inv, delete=[{'partition_id': old['id']}], create=[
            new('new-1', 'linux', old['start_bytes'], 24 * GIB)], mounts=[
            row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4')]), inv)
        runner = ImageRunner(self.image, '/dev/vda')

        def tamper(argv):
            subprocess.run(['sfdisk', '--no-reread', '--no-tell-kernel', '--part-label',
                            str(self.image), '2', 'changed'], check=True, capture_output=True)
        runner.tamper = tamper
        with self.assertRaises(InstallError):
            self.apply(plan, runner)
        writes = [argv for argv in runner.commands if argv[0] == 'sfdisk' and '--json' not in argv]
        self.assertEqual(len(writes), 1)
        self.assertIn('--delete', writes[0])
        self.assertNotIn(3, read_table(self.image)[0])

    def test_changed_partition_type_is_refused_before_writing(self):
        self.windows_layout()
        inv = self.inventory()
        old = inv['disks'][0]['partitions'][2]
        plan = make_plan(editor(inv, delete=[{'partition_id': old['id']}], create=[
            new('new-1', 'linux', old['start_bytes'], 24 * GIB)], mounts=[
            row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4')]), inv)
        subprocess.run(['sfdisk', '--part-type', str(self.image), '3', BASIC_DATA],
                       check=True, capture_output=True)
        before = self.head()
        runner = ImageRunner(self.image, '/dev/vda')
        with self.assertRaises(InstallError) as raised:
            self.apply(plan, runner)
        self.assertEqual(raised.exception.code, Code.DISK_CHANGED)
        self.assertEqual(self.head(), before)
        self.assertTrue(all('--json' in argv for argv in runner.commands))

    def test_unreadable_intermediate_table_stops_with_changed_disk_warning(self):
        for output in ('{', '{"partitiontable": {}}', ''):
            with self.subTest(output=output):
                self.windows_layout()
                inv = self.inventory()
                old = inv['disks'][0]['partitions'][2]
                plan = make_plan(editor(inv, delete=[{'partition_id': old['id']}], create=[
                    new('new-1', 'linux', old['start_bytes'], 24 * GIB)], mounts=[
                    row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4')]), inv)
                runner = ImageRunner(self.image, '/dev/vda')
                real = runner.run
                written = False

                def run(argv, **kwargs):
                    nonlocal written
                    result = real(argv, **kwargs)
                    if argv[0] == 'sfdisk' and '--json' not in argv:
                        written = True
                    elif written and '--json' in argv:
                        return output
                    return result
                runner.run = run
                with self.assertRaises(InstallError) as raised:
                    self.apply(plan, runner)
                self.assertIn('disk may have been changed', raised.exception.message)
                self.assertIn('Nothing was formatted', raised.exception.message)
                writes = [argv for argv in runner.commands if argv[0] == 'sfdisk' and '--json' not in argv]
                self.assertEqual(len(writes), 1)

    def test_changed_logical_sector_size_is_refused_before_writing(self):
        self.windows_layout()
        inv = self.inventory()
        inv['disks'][0]['_sector_size'] = 4096
        plan = make_plan(editor(inv, create=[new('new-1', 'linux', 32 * GIB, 24 * GIB)], mounts=[
            row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4')]), inv)
        runner = ImageRunner(self.image, '/dev/vda')
        before = self.head()
        with self.assertRaises(InstallError) as raised:
            self.apply(plan, runner)
        self.assertEqual(raised.exception.code, Code.DISK_CHANGED)
        self.assertIn('sector size', raised.exception.message)
        self.assertEqual(self.head(), before)
        self.assertTrue(all('--json' in argv for argv in runner.commands))

    def test_4kn_and_digit_ending_device_names(self):
        for disk_path in ('/dev/nvme0n1', '/dev/mmcblk0'):
            with self.subTest(disk_path=disk_path):
                write_table(self.image, 'label: gpt\nunit: sectors\n\n'
                            'start=256, size=131072, type=U\n', sector=4096)
                rows, _ = read_table(self.image, sector=4096)
                inv = inventory(size=self.SIZE)
                disk = inv['disks'][0]
                disk['_sector_size'], disk['_pttype'] = 4096, 'gpt'
                disk['_identity']['disk-seq'] = 42
                esp = part(1, MIB, 512 * MIB, 'vfat', esp=True, _parttype=ESP_GUID,
                           _partuuid=rows[1]['uuid'].lower(), _esp_free_bytes=400 * MIB)
                esp['id'] = esp['path'] = disk_path + 'p1'
                disk['partitions'] = [esp]
                plan = make_plan(editor(inv, create=[new('new-1', 'linux', GIB, 24 * GIB)], mounts=[
                    row(esp['id'], '/efi', 'vfat', False), row('new-1', '/', 'ext4')]), inv)
                self.apply(plan, ImageRunner(self.image, disk_path, sector=4096))
                after, _ = read_table(self.image, sector=4096)
                self.assertEqual(after[1], rows[1])
                self.assertEqual((after[2]['start'] * 4096, after[2]['size'] * 4096), (GIB, 24 * GIB))
                self.assertEqual(plan.root.path, disk_path + 'p2')

    def test_header_or_slot_tamper_stops_before_the_next_change(self):
        for changed in ('label', 'id', 'firstlba', 'lastlba', 'lastlba_after_growth', 'sectorsize', 'slot'):
            with self.subTest(changed=changed):
                self.windows_layout()
                inv = self.inventory()
                disk = inv['disks'][0]
                old = disk['partitions'][2]
                # Explicitly confirmed removal of Windows, followed by the Linux partition.
                windows = disk['partitions'][1]
                plan = make_plan(editor(inv, delete=[
                    {'partition_id': windows['id'], 'confirm': deletion_identity(windows)},
                    {'partition_id': old['id']}], create=[new('new-1', 'linux', GIB, 24 * GIB)], mounts=[
                    row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4')]), inv)
                runner = ImageRunner(self.image, '/dev/vda')
                real = runner.run
                written = 0

                def run(argv, **kwargs):
                    nonlocal written
                    output = real(argv, **kwargs)
                    if argv[0] == 'sfdisk' and '--json' not in argv:
                        written += 1
                    elif '--json' in argv and (written or changed == 'lastlba_after_growth'):
                        data = json.loads(output)
                        table = data['partitiontable']
                        if changed == 'label':
                            table['label'] = 'dos'
                        elif changed == 'id':
                            table['id'] = '11111111-2222-3333-4444-555555555555'
                        elif changed == 'firstlba':
                            table['firstlba'] += 2048
                        elif changed == 'lastlba':
                            table['lastlba'] -= 2048
                        elif changed == 'lastlba_after_growth':
                            # Grow after the first write, then shrink, still above the initial limit.
                            table['lastlba'] -= {0: 4096, 1: 0, 2: 2048}.get(written, 2048)
                        elif changed == 'sectorsize':
                            table['sectorsize'] = 4096
                        else:
                            table['partitions'][-1]['node'] = table['partitions'][-1]['node'][:-1] + '4'
                        return json.dumps(data)
                    return output
                runner.run = run
                with self.assertRaises(InstallError):
                    self.apply(plan, runner)
                writes = [argv for argv in runner.commands if argv[0] == 'sfdisk' and '--json' not in argv]
                self.assertEqual(len(writes), 2 if changed == 'lastlba_after_growth' else 1)
                self.assertEqual(3 in read_table(self.image)[0], changed != 'lastlba_after_growth')

    def test_new_table_on_an_empty_image(self):
        inv = self.inventory()
        self.assertEqual(inv['disks'][0]['partitions'], [])
        c = editor(inv, new_table=True, create=[new('new-1', 'esp', MIB, GIB), new('new-2', 'linux', GIB + MIB, 40 * GIB)],
                   mounts=[row('new-1', '/efi', 'vfat'), row('new-2', '/', 'btrfs')])
        plan = make_plan(c, inv)
        self.apply(plan)
        after, _ = read_table(self.image)
        self.assertEqual({n: (p['start'] * 512, p['size'] * 512, p['type'].lower()) for n, p in after.items()},
                         {1: (MIB, GIB, ESP_GUID), 2: (GIB + MIB, 40 * GIB, LINUX)})

    def test_new_table_sector_check_refuses_before_any_write(self):
        for logical_sector in (4096, OSError(errno.ENOTTY, 'unavailable')):
            with self.subTest(logical_sector=logical_sector):
                with self.image.open('wb') as stream:
                    stream.truncate(self.SIZE)
                inv = self.inventory()
                plan = make_plan(editor(inv, new_table=True, create=[
                    new('new-1', 'esp', MIB, GIB), new('new-2', 'linux', GIB + MIB, 40 * GIB)],
                    mounts=[row('new-1', '/efi', 'vfat'), row('new-2', '/', 'ext4')]), inv)
                before = self.head()
                runner = ImageRunner(self.image, '/dev/vda')
                with self.assertRaises(InstallError) as raised:
                    self.apply(plan, runner, logical_sector=logical_sector)
                self.assertEqual(raised.exception.code, Code.UNSAFE_DISK if isinstance(logical_sector, OSError)
                                 else Code.DISK_CHANGED)
                self.assertIn('sector size', raised.exception.message)
                self.assertEqual(self.head(), before)
                self.assertEqual([argv for argv in runner.commands if '--json' not in argv], [])

    def test_empty_mbr_is_replaced_but_nonempty_mbr_is_refused(self):
        write_table(self.image, 'label: dos\n')
        inv = inventory(size=self.SIZE)
        inv['disks'][0]['_pttype'] = 'dos'
        inv['disks'][0]['_identity']['disk-seq'] = 42
        configuration = editor(inv, new_table=True, create=[
            new('new-1', 'esp', MIB, GIB), new('new-2', 'linux', GIB + MIB, 40 * GIB)],
            mounts=[row('new-1', '/efi', 'vfat'), row('new-2', '/', 'ext4')])
        self.apply(make_plan(configuration, inv))
        self.assertEqual(sorted(read_table(self.image)[0]), [1, 2])
        write_table(self.image, 'label: dos\n\nstart=2048, size=1048576, type=83\n')
        before = self.head()
        runner = ImageRunner(self.image, '/dev/vda')
        # The old empty-MBR review is refused by the final table read.
        with self.assertRaises(InstallError) as raised:
            self.apply(make_plan(configuration, inv), runner)
        self.assertEqual(raised.exception.code, Code.DISK_CHANGED)
        self.assertEqual(self.head(), before)
        self.assertTrue(all('--json' in argv for argv in runner.commands))

    def test_a_gap_in_partition_numbers_is_filled_without_touching_later_entries(self):
        self.windows_layout()
        subprocess.run(['sfdisk', '--delete', str(self.image), '2'], check=True, capture_output=True)
        inv = self.inventory()
        before, _ = read_table(self.image)
        plan = make_plan(editor(inv, create=[new('new-1', 'linux', 32 * GIB, 24 * GIB)], mounts=[
            row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4')]), inv)
        self.apply(plan)
        after, _ = read_table(self.image)
        self.assertEqual(plan.root.number, 2)
        self.assertEqual(after[1], before[1])
        self.assertEqual(after[3], before[3])
        self.assertEqual(after[2]['start'] * 512, 32 * GIB)

    def test_a_full_disk_refuses_a_new_partition_without_writes(self):
        write_table(self.image, 'label: gpt\nunit: sectors\n\n'
                    'start=2048, size=1048576, type=U\nstart=1050624, type=L\n')
        inv = self.inventory()
        before = self.head()
        with self.assertRaises(InstallError) as raised:
            make_plan(editor(inv, create=[new('new-1', 'linux', 32 * GIB, 24 * GIB)], mounts=[
                row('/dev/vda1', '/efi', 'vfat', False), row('new-1', '/', 'ext4')]), inv)
        self.assertIn('overlaps', raised.exception.message)
        self.assertEqual(self.head(), before)

    def test_a_disk_changed_after_review_is_never_written(self):
        self.windows_layout()
        inv = self.inventory()
        disk = inv['disks'][0]
        c = editor(inv, delete=[{'partition_id': disk['partitions'][2]['id']}], create=[
            new('new-1', 'linux', disk['partitions'][2]['start_bytes'], 24 * GIB)], mounts=[
            row(disk['partitions'][0]['id'], '/efi', 'vfat', False), row('new-1', '/', 'ext4')])
        plan = make_plan(c, inv)
        # Another tool moved partition 3 after the review.
        write_table(self.image, 'label: gpt\nunit: sectors\n\n'
                    'start=2048, size=1048576, type=U\n'
                    f'start=1050624, size=41943040, type={BASIC_DATA.upper()}\n'
                    'start=42995712, size=20971520, type=L\n')
        before = self.head()
        runner = ImageRunner(self.image, '/dev/vda')
        with self.assertRaises(InstallError) as raised:
            self.apply(plan, runner)
        self.assertEqual(raised.exception.code, Code.DISK_CHANGED)
        self.assertIn('The disk was not changed', raised.exception.message)
        self.assertEqual(self.head(), before)
        self.assertEqual([argv for argv in runner.commands if '--json' not in argv], [])
        # A new table needs a disk that is still empty.
        empty = Path(self.temp.name) / 'empty.img'
        with empty.open('wb') as stream:
            stream.truncate(self.SIZE)
        self.image, image = empty, self.image
        plan = make_plan(editor(self.inventory(), new_table=True, create=[
            new('new-1', 'esp', MIB, GIB), new('new-2', 'linux', GIB + MIB, 40 * GIB)],
            mounts=[row('new-1', '/efi', 'vfat'), row('new-2', '/', 'ext4')]), self.inventory())
        write_table(self.image, 'label: gpt\n\nstart=2048, size=2048, type=L\n')
        with self.assertRaises(InstallError) as raised:
            self.apply(plan)
        self.assertEqual(raised.exception.code, Code.DISK_CHANGED)

    def test_a_new_partition_outside_the_tables_usable_area_is_refused_unwritten(self):
        # A GPT whose last usable sector ends before the last MiB the plan measured.
        write_table(self.image, 'label: gpt\nunit: sectors\nlast-lba: 100000000\n\nstart=2048, size=1048576, type=U\n')
        inv = self.inventory()
        disk = inv['disks'][0]
        end = (self.SIZE - MIB) // MIB * MIB
        c = editor(inv, create=[new('new-1', 'linux', end - 24 * GIB, 24 * GIB)],
                   mounts=[row(disk['partitions'][0]['id'], '/efi', 'vfat', False), row('new-1', '/', 'ext4')])
        plan = make_plan(c, inv)
        before = self.head()
        runner = ImageRunner(self.image, '/dev/vda')
        with self.assertRaises(InstallError) as raised:
            self.apply(plan, runner)
        self.assertIn('outside the space', raised.exception.message)
        self.assertIn('The disk was not changed', raised.exception.message)
        self.assertEqual(self.head(), before)
        self.assertEqual([argv for argv in runner.commands if '--json' not in argv], [])

    def test_a_failure_once_writing_began_says_the_disk_may_have_changed(self):
        self.windows_layout()
        inv = self.inventory()
        disk = inv['disks'][0]
        old = disk['partitions'][2]
        c = editor(inv, delete=[{'partition_id': old['id']}], create=[new('new-1', 'linux', old['start_bytes'], 24 * GIB)],
                   mounts=[row(disk['partitions'][0]['id'], '/efi', 'vfat', False), row('new-1', '/', 'ext4')])
        plan = make_plan(c, inv)
        runner = ImageRunner(self.image, '/dev/vda')
        real = runner.run

        def run(argv, **kwargs):
            if '--append' in argv:
                raise InstallError(Code.COMMAND_FAILED, 'injected sfdisk failure')
            return real(argv, **kwargs)
        runner.run = run
        with self.assertRaises(InstallError) as raised:
            self.apply(plan, runner)
        self.assertIn('STOP: changing the partition table failed after it had begun, so the disk may have been changed',
                      raised.exception.message)
        self.assertNotIn(3, read_table(self.image)[0])

    def test_a_table_that_differs_after_writing_stops_before_any_format(self):
        self.windows_layout()
        inv = self.inventory()
        disk = inv['disks'][0]
        c = editor(inv, create=[new('new-1', 'linux', 42993664 * 512 + 10 * GIB, 24 * GIB)], mounts=[
            row(disk['partitions'][0]['id'], '/efi', 'vfat', False), row('new-1', '/', 'ext4')])
        disk['partitions'][2]['size_bytes'] = 10 * GIB
        write_table(self.image, 'label: gpt\nunit: sectors\n\n'
                    'start=2048, size=1048576, type=U\n'
                    f'start=1050624, size=41943040, type={BASIC_DATA.upper()}\n'
                    'start=42993664, size=20971520, type=L\n')
        inv = self.inventory()
        c['mounts'][0]['partition_id'] = inv['disks'][0]['partitions'][0]['id']
        c['partitioning']['create'][0]['start_bytes'] = 42993664 * 512 + 10 * GIB
        plan = make_plan(c, inv)
        runner = ImageRunner(self.image, '/dev/vda')

        def tamper(argv):
            # A tool that rewrote another entry while adding the new one.
            subprocess.run(['sfdisk', '--no-reread', '--no-tell-kernel', '--part-label', str(self.image), '2', 'changed'],
                           check=True, capture_output=True)
        runner.tamper = tamper
        with self.assertRaises(InstallError) as raised:
            self.apply(plan, runner)
        self.assertEqual(raised.exception.code, Code.UNSAFE_DISK)
        self.assertIn('STOP', raised.exception.message)
        self.assertFalse(any(argv[0] in ('lsblk', 'udevadm') for argv in runner.commands))

    def busy_editor(self, busy=0, failure=None, stale=False):
        """Independent fake kernel state: writing the image never updates it implicitly."""
        self.windows_layout()
        inv = self.inventory()
        disk = inv['disks'][0]
        old = disk['partitions'][2]
        config = editor(inv, delete=[{'partition_id': old['id']}], create=[
            new('new-1', 'linux', old['start_bytes'] + MIB, 24 * GIB),
            new('new-2', 'swap', old['start_bytes'] + MIB + 24 * GIB, 2 * GIB)], mounts=[
            row(disk['partitions'][0]['id'], '/efi', 'vfat', False),
            row('new-1', '/', 'ext4'), row('new-2', 'swap', 'swap')])
        plan = make_plan(config, inv)
        plan.disk['path'] = str(self.image)
        runner = ImageRunner(self.image, '/dev/vda')
        logs, calls = [], []
        runner.log = logs.append
        run = runner.run
        kernel = json.loads(run(['lsblk']))
        preserved = self.kept_bytes()
        state = {'busy': busy, 'failure': failure, 'stale': stale}

        def command(argv, **kwargs):
            if argv[0] == 'lsblk':
                runner.commands.append(argv)
                return json.dumps(kernel)
            return run(argv, **kwargs)

        def ioctl(fd, request, argument=None):
            os.fstat(fd)
            if request == arch_backend.BLKRRPART:
                calls.append(('reread',))
                self.assertEqual(runner.commands[-1][0], 'udevadm')
                if state['failure'] == 'reread':
                    raise OSError(errno.EIO, 'read failed')
                if state['busy']:
                    state['busy'] -= 1
                    raise OSError(errno.EBUSY, 'partition open')
                kernel.update(json.loads(run(['lsblk'])))
                return 0
            self.assertEqual(request, 0x1269)  # BLKPG, linux/blkpg.h
            class Argument(ctypes.Structure):
                _fields_ = [('op', ctypes.c_int), ('flags', ctypes.c_int),
                            ('datalen', ctypes.c_int), ('data', ctypes.c_void_p)]
            class Partition(ctypes.Structure):
                _fields_ = [('start', ctypes.c_longlong), ('length', ctypes.c_longlong),
                            ('number', ctypes.c_int), ('devname', ctypes.c_char * 64),
                            ('volname', ctypes.c_char * 64)]
            arg = Argument.from_buffer_copy(argument)
            part = Partition.from_address(arg.data)
            self.assertEqual(arg.flags, 0)
            self.assertEqual(arg.datalen, ctypes.sizeof(Partition))
            self.assertNotIn(part.number, (1, 2))
            calls.append((arg.op, part.number, part.start, part.length))
            if state['failure'] == 'partition' or (state['failure'] == 'add4' and arg.op == 1 and part.number == 4):
                raise OSError(errno.EBUSY, 'changed partition open')
            children = kernel['blockdevices'][0]['children']
            if arg.op == 2:
                found = [p for p in children if p['partn'] == part.number]
                if not found:
                    raise OSError(errno.ENXIO, 'partition already absent')
                children.remove(found[0])
            else:
                self.assertEqual(arg.op, 1)
                if any(p['partn'] == part.number for p in children):
                    raise OSError(errno.EBUSY, 'partition already present')
                actual = json.loads(run(['lsblk']))['blockdevices'][0]['children']
                found = next(p for p in actual if p['partn'] == part.number)
                self.assertEqual((part.start, part.length), (found['start'] * 512, found['size']))
                if not state['stale']:
                    children.append(found)
            return 0

        runner.run = command
        ops = DeviceOperations(plan, runner)
        mod = SimpleNamespace(wipe=False, partitions=[SimpleNamespace(partn=p.number, dev_path=None)
                                                      for p in plan.partitions if not p.swap])
        return ops, mod, ioctl, logs, calls, state, preserved

    def run_busy_editor(self, ops, mod, ioctl):
        with patch.object(arch_backend, 'disk_sequence', return_value=42), \
                patch.object(arch_backend.fcntl, 'ioctl', side_effect=ioctl), \
                patch('time.sleep'):
            ops.partition(mod)

    def test_reread_retries_busy_after_settling_before_formatting(self):
        for busy in (0, 2, 4):
            with self.subTest(busy=busy):
                ops, mod, ioctl, logs, calls, _, preserved = self.busy_editor(busy)
                self.run_busy_editor(ops, mod, ioctl)
                self.assertEqual(calls, [('reread',)] * (busy + 1))
                self.assertEqual(sum('errno=16' in line for line in logs), busy)
                self.assertTrue(any('errno=0' in line for line in logs))
                self.assertEqual(self.kept_bytes(), preserved)

    def test_final_settle_waits_for_delayed_partition_identity(self):
        for identity in ('', 'old-partition-uuid'):
            with self.subTest(identity=identity):
                ops, mod, ioctl, _, _, _, _ = self.busy_editor()
                run, settled, stale_reads = ops.runner.run, [], []

                def delayed(argv, **kwargs):
                    if argv == ['udevadm', 'settle', '--timeout=30']:
                        self.assertEqual(kwargs['timeout'], 31)
                        settled.append(True)
                    result = run(argv, **kwargs)
                    if argv[0] == 'lsblk' and not settled:
                        stale_reads.append(True)
                        data = json.loads(result)
                        for part in data['blockdevices'][0]['children']:
                            part['partuuid'] = identity
                        return json.dumps(data)
                    return result

                ops.runner.run = delayed
                self.run_busy_editor(ops, mod, ioctl)
                self.assertEqual(settled, [True])
                self.assertEqual(stale_reads, [])
                self.assertTrue(next(p for p in mod.partitions if p.partn == 3).dev_path)
                self.assertIsNone(ops.pending_reread)

    def test_failed_final_settle_stops_before_verification(self):
        for unavailable in (False, True):
            with self.subTest(unavailable=unavailable):
                ops, mod, ioctl, _, _, _, _ = self.busy_editor()
                run = ops.runner.run

                def unsettled(argv, **kwargs):
                    if argv == ['udevadm', 'settle', '--timeout=30']:
                        failure = InstallError(Code.COMMAND_FAILED, 'udev settle failed')
                        if unavailable:
                            raise failure from OSError(errno.ENOENT, 'No such file')
                        raise failure
                    return run(argv, **kwargs)

                ops.runner.run = unsettled
                with patch.object(ops, 'verify_edited_kernel') as verify, self.assertRaises(InstallError) as raised:
                    self.run_busy_editor(ops, mod, ioctl)
                self.assertEqual(raised.exception.retryable, not unavailable)
                verify.assert_not_called()
                self.assertTrue(all(p.dev_path is None for p in mod.partitions))
                self.assertIsNotNone(ops.pending_reread)

    def test_busy_whole_disk_uses_only_changed_partition_ioctls(self):
        ops, mod, ioctl, logs, calls, _, preserved = self.busy_editor(100)
        self.run_busy_editor(ops, mod, ioctl)
        self.assertEqual(len([c for c in calls if c[0] == 'reread']), 5)
        self.assertEqual([(c[0], c[1]) for c in calls if c[0] != 'reread'], [(2, 3), (2, 4), (1, 3), (1, 4)])
        self.assertEqual(self.kept_bytes(), preserved)
        self.assertTrue(any('BLKPG' in line and 'errno=0' in line for line in logs))
        self.assertIsNone(ops.pending_reread)

    def test_nonbusy_reread_error_stops_without_partition_fallback(self):
        ops, mod, ioctl, logs, calls, _, _ = self.busy_editor(failure='reread')
        with self.assertRaises(InstallError):
            self.run_busy_editor(ops, mod, ioctl)
        self.assertEqual(calls, [('reread',)])
        self.assertTrue(any('errno=5' in line for line in logs))
        self.assertTrue(all(p.dev_path is None for p in mod.partitions))

    def test_busy_changed_partition_and_stale_kernel_both_stop_before_formatting(self):
        for failure, stale in (('partition', False), (None, True)):
            with self.subTest(failure=failure):
                ops, mod, ioctl, _, calls, _, _ = self.busy_editor(100, failure, stale)
                with self.assertRaises(InstallError):
                    self.run_busy_editor(ops, mod, ioctl)
                self.assertTrue(all(p.dev_path is None for p in mod.partitions))
                self.assertIsNotNone(ops.pending_reread)
                if failure:
                    self.assertFalse(any(c[0] == 1 for c in calls))

    def test_failed_reread_recovers_written_table_without_repeating_writes(self):
        ops, mod, ioctl, _, _, state, preserved = self.busy_editor(100, 'partition')
        with self.assertRaises(InstallError):
            self.run_busy_editor(ops, mod, ioctl)
        writes = [c for c in ops.runner.commands if c[0] == 'sfdisk' and '--json' not in c]
        state['busy'], state['failure'] = 0, None
        with patch.object(arch_backend, 'disk_sequence', return_value=42), \
                patch.object(arch_backend.fcntl, 'ioctl', side_effect=ioctl), patch('time.sleep'):
            ops.recover_edits()
        self.assertIsNone(ops.pending_reread)
        self.assertEqual([c for c in ops.runner.commands if c[0] == 'sfdisk' and '--json' not in c], writes)
        self.assertEqual(self.kept_bytes(), preserved)

    def test_settle_only_recovery_does_not_repeat_kernel_updates(self):
        ops, mod, ioctl, _, calls, _, preserved = self.busy_editor()
        run, settled, geometry_reads = ops.runner.run, [], []

        def delayed(argv, **kwargs):
            if argv[0] == 'lsblk' and argv[-1].startswith('/proc/self/fd/'):
                fd, = kwargs['pass_fds']
                self.assertEqual(argv[-1], f'/proc/self/fd/{fd}')
                os.fstat(fd)
                geometry_reads.append(fd)
            if argv == ['udevadm', 'settle', '--timeout=30']:
                self.assertEqual(kwargs['timeout'], 31)
                settled.append(True)
                if len(settled) < 3:
                    raise InstallError(Code.COMMAND_FAILED, 'udevadm timed out.')
            output = run(argv, **kwargs)
            if argv[0] == 'lsblk' and len(settled) < 3:
                data = json.loads(output)
                for child in data['blockdevices'][0]['children']:
                    child['partuuid'] = ''
                return json.dumps(data)
            return output

        ops.runner.run = delayed
        with self.assertRaises(InstallError):
            self.run_busy_editor(ops, mod, ioctl)
        written = len(ops.runner.commands)
        with patch.object(arch_backend, 'disk_sequence', return_value=42), \
                patch.object(arch_backend.fcntl, 'ioctl', side_effect=ioctl), patch('time.sleep'):
            with self.assertRaises(InstallError) as raised:
                ops.recover_edits()
            self.assertTrue(raised.exception.retryable)
            self.assertIsNotNone(ops.pending_reread)
            ops.recover_edits()
        self.assertEqual(calls, [('reread',)])
        self.assertEqual(len(geometry_reads), 2)
        self.assertEqual(len(settled), 3)
        self.assertIsNone(ops.pending_reread)
        self.assertTrue(all(p.dev_path is None for p in mod.partitions))
        self.assertFalse(any(c[0] == 'sfdisk' and '--json' not in c
                             for c in ops.runner.commands[written:]))
        self.assertFalse(any(c == ['udevadm', 'settle', '--timeout=1']
                             for c in ops.runner.commands[written:]))
        self.assertEqual(self.kept_bytes(), preserved)

    def test_recovery_waits_for_missing_partition_numbers(self):
        for missing in (False, True):
            for catches_up in (False, True):
                with self.subTest(missing=missing, catches_up=catches_up):
                    ops, mod, ioctl, _, calls, _, preserved = self.busy_editor()
                    run, settles = ops.runner.run, []

                    def backlog(argv, **kwargs):
                        if argv == ['udevadm', 'settle', '--timeout=30']:
                            settles.append(True)
                            if len(settles) == 1 or not catches_up:
                                raise InstallError(Code.COMMAND_FAILED, 'udevadm timed out.')
                        output = run(argv, **kwargs)
                        if argv[0] == 'lsblk' and (len(settles) < 2 or not catches_up):
                            data = json.loads(output)
                            for child in data['blockdevices'][0]['children']:
                                if child['partn'] == 4:
                                    child['partuuid'] = None
                                    if missing:
                                        del child['partn']
                                    else:
                                        child['partn'] = None
                            return json.dumps(data)
                        return output

                    ops.runner.run = backlog
                    with self.assertRaises(InstallError):
                        self.run_busy_editor(ops, mod, ioctl)
                    writes = [c for c in ops.runner.commands if c[0] == 'sfdisk' and '--json' not in c]
                    with patch.object(arch_backend, 'disk_sequence', return_value=42), \
                            patch.object(arch_backend.fcntl, 'ioctl', side_effect=ioctl), patch('time.sleep'):
                        if catches_up:
                            ops.recover_edits()
                            self.assertIsNone(ops.pending_reread)
                        else:
                            with self.assertRaises(InstallError) as raised:
                                ops.recover_edits()
                            self.assertEqual(raised.exception.code, Code.COMMAND_FAILED)
                            self.assertTrue(raised.exception.retryable)
                            self.assertIsNotNone(ops.pending_reread)
                    self.assertEqual(len(settles), 2)
                    self.assertEqual(calls, [('reread',), ('reread',)])
                    self.assertEqual([c for c in ops.runner.commands if c[0] == 'sfdisk' and '--json' not in c],
                                     writes)
                    self.assertTrue(all(p.dev_path is None for p in mod.partitions))
                    self.assertEqual(self.kept_bytes(), preserved)

    def test_final_kernel_verification_refuses_malformed_rows(self):
        ops, mod, ioctl, _, _, _, _ = self.busy_editor()
        self.run_busy_editor(ops, mod, ioctl)
        data = json.loads(ops.runner.run(['lsblk']))
        children = data['blockdevices'][0]['children']
        after = {row['partn']: {'start': row['start'] * 512, 'size': row['size'],
                               'uuid': row['partuuid']} for row in children}
        reviewed = {row['partn']: row for row in children}
        malformed = ['{', '{}', 'null', '{"blockdevices": []}', '{"blockdevices": [null]}',
                     '{"blockdevices": [{"children": null}]}',
                     '{"blockdevices": [{"children": [null]}]}']
        for field, value in (('partn', 'invalid'), ('start', None), ('size', 'invalid'), ('path', None)):
            bad = copy.deepcopy(data)
            row = bad['blockdevices'][0]['children'][0]
            if field == 'path':
                del row[field]
            else:
                row[field] = value
            malformed.append(json.dumps(bad))
        for output in malformed:
            with self.subTest(output=output), \
                    patch.object(ops.runner, 'run', return_value=output), \
                    patch.object(arch_backend, 'disk_sequence', return_value=42):
                with self.assertRaises(InstallError) as raised:
                    ops.verify_edited_kernel(after, {1, 2}, reviewed)
                self.assertEqual(raised.exception.code, Code.UNSAFE_DISK)
                self.assertFalse(raised.exception.retryable)
                self.assertEqual(str(raised.exception),
                                 'The partitions on the disk could not be read. Installation stopped.')

    def test_settle_only_recovery_checks_disk_sequence_after_geometry_probe(self):
        ops, mod, ioctl, _, calls, _, _ = self.busy_editor()
        run = ops.runner.run

        def unsettled(argv, **kwargs):
            if argv == ['udevadm', 'settle', '--timeout=30']:
                raise InstallError(Code.COMMAND_FAILED, 'udevadm timed out.')
            return run(argv, **kwargs)

        ops.runner.run = unsettled
        with self.assertRaises(InstallError):
            self.run_busy_editor(ops, mod, ioctl)
        sequence, commands = [42], []

        def replaced(argv, **kwargs):
            commands.append(argv)
            if argv[0] == 'lsblk':
                sequence[0] = 43
            return run(argv, **kwargs)

        with patch.object(ops.runner, 'run', side_effect=replaced), \
                patch.object(arch_backend, 'disk_sequence', side_effect=lambda fd: sequence[0]), \
                patch.object(arch_backend.fcntl, 'ioctl', side_effect=ioctl):
            with self.assertRaises(InstallError) as raised:
                ops.recover_edits()
        self.assertEqual(raised.exception.code, Code.DISK_CHANGED)
        self.assertEqual(calls, [('reread',)])
        self.assertFalse(any(c[0] == 'udevadm' for c in commands))
        self.assertIsNotNone(ops.pending_reread)

    def test_recovery_refuses_malformed_written_table_before_kernel_updates(self):
        ops, mod, ioctl, _, calls, _, _ = self.busy_editor(100, 'partition')
        with self.assertRaises(InstallError):
            self.run_busy_editor(ops, mod, ioctl)
        attempted, run = list(calls), ops.runner.run
        missing_sector = json.loads(subprocess.check_output(['sfdisk', '--json', str(self.image)], text=True))
        del missing_sector['partitiontable']['sectorsize']
        invalid_sector = copy.deepcopy(missing_sector)
        invalid_sector['partitiontable']['sectorsize'] = 'invalid'
        for output in ('{', json.dumps(missing_sector), json.dumps(invalid_sector)):
            with self.subTest(output=output):
                def malformed(argv, **kwargs):
                    if argv[0] == 'sfdisk' and '--json' in argv:
                        return output
                    return run(argv, **kwargs)

                with patch.object(ops.runner, 'run', side_effect=malformed), \
                        patch.object(arch_backend, 'disk_sequence', return_value=42), \
                        patch.object(arch_backend.fcntl, 'ioctl', side_effect=ioctl):
                    with self.assertRaises(InstallError) as raised:
                        ops.recover_edits()
                self.assertEqual(raised.exception.code, Code.UNSAFE_DISK)
                self.assertFalse(raised.exception.retryable)
                self.assertEqual(calls, attempted)
                self.assertIsNotNone(ops.pending_reread)

    def test_partial_partition_fallback_can_be_retried_while_whole_disk_stays_busy(self):
        ops, mod, ioctl, _, _, state, preserved = self.busy_editor(100, 'add4')
        with self.assertRaises(InstallError):
            self.run_busy_editor(ops, mod, ioctl)
        state['failure'] = None
        with patch.object(arch_backend, 'disk_sequence', return_value=42), \
                patch.object(arch_backend.fcntl, 'ioctl', side_effect=ioctl), patch('time.sleep'):
            ops.recover_edits()
        self.assertIsNone(ops.pending_reread)
        self.assertEqual(self.kept_bytes(), preserved)

    def test_recovery_refuses_replaced_disk_and_changed_written_table(self):
        ops, mod, ioctl, _, calls, _, _ = self.busy_editor(100, 'partition')
        with self.assertRaises(InstallError):
            self.run_busy_editor(ops, mod, ioctl)
        attempted = list(calls)
        with patch.object(arch_backend, 'disk_sequence', return_value=43), \
                patch.object(arch_backend.fcntl, 'ioctl', side_effect=ioctl):
            with self.assertRaises(InstallError) as raised:
                ops.recover_edits()
        self.assertEqual(raised.exception.code, Code.DISK_CHANGED)
        subprocess.run(['sfdisk', '--no-reread', '--no-tell-kernel', '--part-label', str(self.image), '3', 'changed'],
                       capture_output=True, check=True)
        with patch.object(arch_backend, 'disk_sequence', return_value=42), \
                patch.object(arch_backend.fcntl, 'ioctl', side_effect=ioctl):
            with self.assertRaises(InstallError) as raised:
                ops.recover_edits()
        self.assertEqual(raised.exception.code, Code.DISK_CHANGED)
        self.assertIn('disk was read again', raised.exception.message)
        self.assertEqual(calls, attempted)

    def test_busy_retries_share_a_deadline(self):
        ops, _, _, logs, _, _, _ = self.busy_editor()
        clock = [0]

        def settle(argv, **kwargs):
            clock[0] += 1
            raise InstallError(Code.COMMAND_FAILED, 'udev queue remains busy')

        with patch.object(ops.runner, 'run', side_effect=settle), \
                patch.object(arch_backend, 'REREAD_SECONDS', 2), \
                patch.object(arch_backend, 'disk_sequence', return_value=42), \
                patch.object(arch_backend.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(arch_backend.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)), \
                patch.object(arch_backend.fcntl, 'ioctl', side_effect=OSError(errno.EBUSY, 'busy')) as ioctl:
            with self.assertRaises(InstallError):
                ops.reread_edits(3, {}, {3}, set())
        self.assertEqual(ioctl.call_count, 2)
        self.assertLessEqual(clock[0], 3)
        self.assertTrue(any('deadline reached' in line for line in logs))


class EncryptedPartitionTests(unittest.TestCase):
    def plan(self):
        inv = shared_disk()
        plan = make_plan(dict(free_layout(inv, 'separate'), disk_password='disk-secret-1'), inv)
        for p in plan.partitions:
            p.path = p.path or f'/dev/vda{p.number}'
        return plan

    def test_home_and_swap_are_luks2_with_the_disk_password_and_closed_last_first(self):
        plan = self.plan()
        runner = RecordingRunner()
        runner.responses[('cryptsetup', 'luksUUID', '/dev/vda6')] = 'luks-6\n'
        runner.responses[('cryptsetup', 'luksUUID', '/dev/vda7')] = 'luks-7\n'
        runner.responses[('blkid', '-s', 'UUID', '-o', 'value', '/dev/mapper/emaki-part7')] = 'swap-uuid\n'
        ops = DeviceOperations(plan, runner)
        with patch.object(arch_backend.Path, 'exists', return_value=False):
            ops.format(SimpleNamespace(value='ext4'), Path('/dev/vda6'))
            ops.make_swap(next(p for p in plan.partitions if p.swap))
        commands = [argv for argv, _ in runner.commands]
        self.assertEqual(commands[1], ['cryptsetup', 'open', '--key-file', '-', '/dev/vda6', 'emaki-part6'])
        self.assertIn(['mkfs.ext4', '-F', '-E', 'nodiscard', '/dev/mapper/emaki-part6'], commands)
        self.assertIn(['mkswap', '/dev/mapper/emaki-part7'], commands)
        for argv, kwargs in runner.commands:
            if argv[:2] in (['cryptsetup', 'luksFormat'], ['cryptsetup', 'open']):
                self.assertEqual((kwargs['input'], kwargs['secret']), ('disk-secret-1', True))
                self.assertNotIn('disk-secret-1', argv)
        home, swap = (next(p for p in plan.partitions if p.number == n) for n in (6, 7))
        self.assertEqual((home.device, home.luks_uuid, swap.device, swap.luks_uuid, swap.uuid),
                         ('/dev/mapper/emaki-part6', 'luks-6', '/dev/mapper/emaki-part7', 'luks-7', 'swap-uuid'))
        runner.commands.clear()
        ops.close()
        self.assertEqual([argv for argv, _ in runner.commands],
                         [['cryptsetup', 'close', 'emaki-part7'], ['cryptsetup', 'close', 'emaki-part6']])

    def test_missing_partition_identifiers_use_plain_errors(self):
        plan = self.plan()
        operations = DeviceOperations(plan, RecordingRunner())
        with patch.object(arch_backend.Path, 'exists', return_value=False):
            with self.assertRaises(InstallError) as raised:
                operations.open_luks('/dev/vda6', 'emaki-part6')
        self.assertEqual(raised.exception.message, 'The encrypted partition /dev/vda6 could not be identified.')
        swap = next(p for p in plan.partitions if p.swap)
        swap.encrypt = False
        with self.assertRaises(InstallError) as raised:
            operations.make_swap(swap)
        self.assertEqual(raised.exception.message, 'The swap partition could not be identified.')

    def test_plain_swap_and_archinstall_never_see_swap(self):
        inv = shared_disk()
        plan = make_plan(free_layout(inv), inv)
        swap = next(p for p in plan.partitions if p.swap)
        swap.path = '/dev/vda7'
        runner = RecordingRunner()
        runner.responses[('blkid', '-s', 'UUID', '-o', 'value', '/dev/vda7')] = 'swap-uuid\n'
        DeviceOperations(plan, runner).make_swap(swap)
        self.assertEqual([argv for argv, _ in runner.commands][0], ['mkswap', '/dev/vda7'])
        self.assertEqual(swap.uuid, 'swap-uuid')
        from test_backend import Installer45  # noqa: F401  (the archinstall 4.5 data model fakes)

    def test_bootloader_writes_keys_crypttab_and_swap_lines(self):
        from test_worker import BootAndSnapshotTests
        fixture = BootAndSnapshotTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        worker = fixture.worker
        inv = shared_disk()
        worker.plan = make_plan(dict(free_layout(inv, 'separate'), disk_password='disk-secret-1'), inv)
        for p in worker.plan.partitions:
            p.path = p.path or f'/dev/vda{p.number}'
            p.uuid = p.uuid or f'fs-{p.number}'
        root = worker.plan.root
        root.mapper, root.luks_uuid = '/dev/mapper/emaki-root', fixture.UUID
        for p in worker.plan.partitions:
            if p.encrypt:
                p.mapper, p.luks_uuid = f'/dev/mapper/{p.mapper_name}', f'luks-{p.number}'
        fixture.boot_files()
        fixture.grub_install_leftovers()
        worker.files.write('/etc/crypttab', '# Configuration for encrypted block devices.\n')
        fixture.run_bootloader()
        files = worker.files
        for name in ('emaki-root', 'emaki-part6', 'emaki-part7'):
            key = files.path(f'/etc/cryptsetup-keys.d/{name}.key')
            self.assertEqual((key.stat().st_size, key.stat().st_mode & 0o777), (64, 0o600))
        self.assertEqual(files.read('/etc/crypttab'),
                         '# Configuration for encrypted block devices.\n'
                         'emaki-part6\tUUID=luks-6\t/etc/cryptsetup-keys.d/emaki-part6.key\tluks\n'
                         'emaki-part7\tUUID=luks-7\t/etc/cryptsetup-keys.d/emaki-part7.key\tluks\n')
        self.assertEqual(files.path('/etc/crypttab').stat().st_mode & 0o777, 0o600)
        adds = [(argv, kw) for argv, kw in worker.runner.commands if 'luksAddKey' in argv]
        self.assertEqual([argv[-2] for argv, _ in adds], ['/dev/vda5', '/dev/vda6', '/dev/vda7'])
        self.assertTrue(all(kw['input'] == 'disk-secret-1' and kw['secret'] for _, kw in adds))
        self.assertIn('/dev/mapper/emaki-part7\tnone\tswap\tdefaults,pri=10\t0 0\n', files.read('/etc/fstab'))
        self.assertEqual(worker.plan.config['disk_password'], '')


if __name__ == '__main__':
    unittest.main()
