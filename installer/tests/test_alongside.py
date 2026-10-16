# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Alongside safety and ordering; no real block device is opened or mounted."""
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from emaki_installer import constants, inventory as ntfs
from emaki_installer.arch_backend import DeviceOperations, resize_partition
from emaki_installer.constants import GIB, MIB
from emaki_installer.errors import Code, InstallError
from emaki_installer.planner import make_plan, shrink_bounds, validate_config
from emaki_installer.runtime import Redactor
from emaki_installer.worker import Worker
from support import alongside_on, config, inventory, FakeInventory, RecordingRunner

GUID = 'aabbccdd-1111-2222-3333-123456789abc'
TYPE = 'ebd0a0a2-b9e5-4433-87c0-68b6b72699c7'
OK = 'Checking filesystem consistency ...\nYou might resize at 21474836480 bytes or 21475 MB (freeing 50000 MB).\n'


def windows():
    inv = inventory(100 * GIB, partitions=True)
    disk = inv['disks'][0]
    esp, p = disk['partitions']
    esp['_parttype'] = ntfs.ESP_GUID
    esp['_partuuid'] = 'aabbccdd-1111-2222-3333-123456789abd'
    esp['_partlabel'] = 'ESP'
    esp['_gpt'] = dict(guid=esp['_partuuid'], type=ntfs.ESP_GUID,
                       start=esp['start_bytes'] // 512,
                       end=(esp['start_bytes'] + esp['size_bytes']) // 512 - 1,
                       name='ESP', attributes='0000000000000000')
    esp['_efi_loaders'] = {'EFI/Microsoft/Boot/bootmgfw.efi': 'fixture-sha256'}
    p.update(fs='ntfs', os_hint='windows', size_bytes=96 * GIB, _partuuid=GUID, _parttype=TYPE)
    p['_gpt'] = dict(guid=GUID, type=TYPE, start=p['start_bytes'] // 512,
                     end=(p['start_bytes'] + p['size_bytes']) // 512 - 1,
                     name="Windows owner's data", attributes='8000000000000000')
    p['_partlabel'] = p['_gpt']['name']
    p['shrink'] = ntfs.shrink_offer(20 * GIB, p['size_bytes'], p['start_bytes'])
    c = config('alongside')
    c.update(partition_id=p['id'], shrink_bytes=40 * GIB)
    return c, inv


def gpt_text(data):
    return (f'Partition GUID code: {data["type"]} (Microsoft basic data)\n'
            f'Partition unique GUID: {data["guid"]}\nFirst sector: {data["start"]} (at 1 MiB)\n'
            f'Last sector: {data["end"]} (at 97 GiB)\nAttribute flags: {data["attributes"]}\n'
            f'Partition name: \'{data["name"]}\'\n')


class AlongsideOnTestCase(unittest.TestCase):
    """These tests exercise the alongside code, so they offer the mode explicitly."""

    def setUp(self):
        switch = alongside_on()
        switch.start()
        self.addCleanup(switch.stop)
        journal = patch.object(ntfs, 'check_ntfs_journal')
        self.journal = journal.start()
        self.addCleanup(journal.stop)


class NtfsTests(AlongsideOnTestCase):
    def test_windows_system_volume_required_and_symlinks_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertFalse(ntfs.windows_system(root))
            hive = root / 'windows/system32/CONFIG/system'
            hive.parent.mkdir(parents=True)
            hive.write_bytes(b'fixture hive')
            self.assertTrue(ntfs.windows_system(root))
            hive.unlink()
            hive.symlink_to('/missing')
            self.assertFalse(ntfs.windows_system(root))

    def test_post_resize_boot_record_sizes_and_metadata_bounds(self):
        for sector, encoded in ((512, 8), (4096, 8), (512, 244)):
            with self.subTest(sector=sector, encoded=encoded), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / 'ntfs-image'
                size = 64 * MIB
                boot = bytearray(512)
                boot[3:11], boot[510:512] = b'NTFS    ', b'\x55\xaa'
                boot[11:13], boot[13] = sector.to_bytes(2, 'little'), encoded
                per_cluster = 1 << (256 - encoded) if encoded > 128 else encoded
                clusters = (size - sector) // (sector * per_cluster)
                boot[40:48] = (clusters * per_cluster).to_bytes(8, 'little')
                boot[48:56], boot[56:64] = (4).to_bytes(8, 'little'), (8).to_bytes(8, 'little')
                def write(record, backup=None):
                    with path.open('wb') as stream:
                        stream.truncate(size)
                        stream.write(record)
                        stream.seek(size - sector)
                        stream.write(record if backup is None else backup)
                write(boot)
                ntfs.verify_ntfs_size(path, size)
                for offset, value in ((40, (clusters * per_cluster + 1).to_bytes(8, 'little')),
                                      (48, clusters.to_bytes(8, 'little')),
                                      (56, bytes(8)), (510, bytes(2))):
                    bad = bytearray(boot)
                    bad[offset:offset + len(value)] = value
                    write(bad)
                    with self.assertRaises(InstallError):
                        ntfs.verify_ntfs_size(path, size)
                write(boot, bytes(512))
                with self.assertRaisesRegex(InstallError, 'backup'):
                    ntfs.verify_ntfs_size(path, size)

    def test_post_resize_requires_kernel_size_and_readable_metadata(self):
        runner = RecordingRunner()
        with self.assertRaisesRegex(InstallError, 'Kernel partition size'):
            ntfs.verify_resized_ntfs(runner, '/fake', 64 * MIB)
        runner.responses[('blockdev', '--getsize64', '/fake')] = str(64 * MIB)
        with tempfile.TemporaryDirectory() as temp, patch.object(ntfs, 'verify_ntfs_size'), \
                patch.object(ntfs.tempfile, 'mkdtemp', return_value=str(Path(temp) / 'mount')):
            root = Path(temp) / 'mount'
            root.mkdir()
            with self.assertRaisesRegex(InstallError, 'system metadata'):
                ntfs.verify_resized_ntfs(runner, '/fake', 64 * MIB)
            self.assertEqual(runner.commands[-1][0], ['umount', '--', str(root)])
            root.mkdir()
            with patch.object(ntfs, 'windows_system', return_value=True):
                ntfs.verify_resized_ntfs(runner, '/fake', 64 * MIB)
        commands = [cmd for cmd, _ in runner.commands]
        self.assertFalse(any(cmd[0] in ('ntfsresize', 'ntfsfix') or '--force' in cmd for cmd in commands))
        self.assertIn(['ntfs-3g', '-o', 'ro,norecover,nodev,nosuid,noexec,show_sys_files', '/fake', str(root)], commands)

    def test_minimum_and_refusals(self):
        self.assertEqual(ntfs.parse_ntfs_minimum(OK), 20 * GIB)
        for output, reason in [('ERROR: Volume is scheduled for check.', 'dirty'),
                               ('The NTFS partition is hibernated.', 'hibernation'),
                               ('Metadata kept in Windows cache, refused to mount.', 'Fast Startup'),
                               ('BitLocker -FVE-FS-', 'BitLocker'),
                               ('ERROR: failed to read clusters', 'error'),
                               ('Device is mounted read-write.', 'mounted'),
                               ('', 'minimum'), (OK + OK, 'minimum')]:
            with self.subTest(output=output), self.assertRaisesRegex(InstallError, reason):
                ntfs.parse_ntfs_minimum(output + (OK if output and output != OK + OK else ''))

    def test_too_small_and_alignment(self):
        self.assertEqual(ntfs.shrink_offer(20 * GIB, 54 * GIB, MIB)['max_free_bytes'], 32 * GIB)
        # An unaligned end is fine (UnalignedWindowsEndTests); 54 GiB less 512 bytes keeps only 31 GiB whole.
        for size, start in [(54 * GIB - MIB, MIB), (60 * GIB, MIB + 512), (54 * GIB - 512, MIB)]:
            with self.assertRaises(InstallError):
                ntfs.shrink_offer(20 * GIB, size, start)
        with self.assertRaises(InstallError):
            shrink_bounds(40 * GIB, 20 * GIB, 60 * GIB, disk_free=100 * GIB)

    def test_bitlocker_offset_and_hiber_headers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dev = root / 'disk'
            dev.write_bytes(b'abc-FVE-FS-' + bytes(512))
            self.assertTrue(ntfs.bitlocker_signature(dev))
            dev.write_bytes(b'abcNTFS    ' + bytes(512))
            self.assertFalse(ntfs.bitlocker_signature(dev))
            self.assertIsNone(ntfs.hibernation_reason(root))
            hiber = root / 'HiBeRfIl.SyS'
            for header in (b'hibr' + bytes(4092), b'HIBR' + bytes(4092), b'wake' + bytes(4092), bytes(4095)):
                hiber.write_bytes(header)
                self.assertIn('hibernation', ntfs.hibernation_reason(root))
            hiber.write_bytes(bytes(4096))
            self.assertIsNone(ntfs.hibernation_reason(root))

    def test_probe_readonly(self):
        _, inv = windows()
        disk = inv['disks'][0]
        p = disk['partitions'][1]
        runner = RecordingRunner()
        runner.responses[('sgdisk', '--verify', '/dev/vda')] = 'No problems found.'
        runner.responses[('sgdisk', '--info=2', '/dev/vda')] = gpt_text(p['_gpt'])
        runner.responses[('sgdisk', '--info=1', '/dev/vda')] = gpt_text(disk['partitions'][0]['_gpt'])
        runner.responses[('ntfsresize', '--info', '--no-action', p['path'])] = OK
        with tempfile.TemporaryDirectory() as temp, patch.object(ntfs, 'bitlocker_signature', return_value=False), \
                patch.object(ntfs.tempfile, 'mkdtemp', return_value=str(Path(temp) / 'mount')), \
                patch.object(ntfs, 'windows_system', return_value=True):
            (Path(temp) / 'mount').mkdir()
            ntfs.Inventory(runner)._alongside(disk, True)
        self.assertEqual(p['_shrink_probe']['min_bytes'], 20 * GIB)
        self.journal.assert_called_once_with(runner, p['path'], p['size_bytes'], 512)
        self.assertNotIn('reason', p['shrink'])
        commands = [cmd for cmd, _ in runner.commands]
        self.assertEqual([cmd[0] for cmd in commands], ['sgdisk', 'sgdisk', 'sgdisk', 'ntfsresize', 'ntfs-3g', 'umount'])
        self.assertEqual(commands[4][2], 'ro,norecover,nodev,nosuid,noexec,show_sys_files')
        self.assertFalse(any('--force' in cmd or '--readwrite' in cmd for cmd in commands))

    def test_probe_ntfs_error_preserves_reason_and_does_not_mount(self):
        _, inv = windows()
        disk = inv['disks'][0]
        p = disk['partitions'][1]
        runner = RecordingRunner()
        runner.responses[('sgdisk', '--verify', '/dev/vda')] = 'No problems found.'
        runner.responses[('sgdisk', '--info=2', '/dev/vda')] = gpt_text(p['_gpt'])
        runner.responses[('sgdisk', '--info=1', '/dev/vda')] = gpt_text(disk['partitions'][0]['_gpt'])
        original = runner.run
        def run(argv, **kwargs):
            if argv[0] == 'ntfsresize':
                raise InstallError(Code.COMMAND_FAILED, 'ntfsresize exited with status 1.',
                                   output='ERROR: Volume is scheduled for check.', returncode=1)
            return original(argv, **kwargs)
        runner.run = run
        with patch.object(ntfs, 'bitlocker_signature', return_value=False):
            offer = ntfs.Inventory(runner)._ntfs_shrink(disk, p)
        self.assertIn('dirty', offer['reason'])
        self.assertFalse(any(cmd[0] in ('mount', 'ntfs-3g') for cmd, _ in runner.commands))

    def test_disk_refusals_explain_known_causes_and_remedies(self):
        # Windows' ESP free space refuses nothing since 0.9.0 (test_alongside_own_esp).
        for cause in ('hibernation', 'bitlocker', 'space'):
            with self.subTest(cause=cause):
                _, inv = windows()
                disk = inv['disks'][0]
                esp, part = disk['partitions']
                runner = RecordingRunner()
                runner.responses[('sgdisk', '--verify', disk['path'])] = 'No problems found.'
                for p in disk['partitions']:
                    runner.responses[('sgdisk', f'--info={p["number"]}', disk['path'])] = gpt_text(p['_gpt'])
                runner.responses[('ntfsresize', '--info', '--no-action', part['path'])] = (
                    OK if cause != 'space' else f'You might resize at {part["size_bytes"] - 30 * GIB} bytes\n')
                self.journal.side_effect = (InstallError(
                    Code.SHRINK_BOUNDS, 'The NTFS writable safety check refused this volume.',
                    output='The NTFS partition is hibernated.') if cause == 'hibernation' else None)
                with tempfile.TemporaryDirectory() as temp, \
                        patch.object(ntfs, 'bitlocker_signature', return_value=cause == 'bitlocker'), \
                        patch.object(ntfs.tempfile, 'mkdtemp', return_value=str(Path(temp) / 'mount')), \
                        patch.object(ntfs, 'windows_system', return_value=True):
                    if cause == 'space':
                        (Path(temp) / 'mount').mkdir()
                    ntfs.Inventory(runner)._alongside(disk, True)
                reason = disk['shrink']['reason']
                expected = {
                    'hibernation': ('Windows is asleep, not shut down', 'powercfg /hibernate off',
                                    'as administrator', 'shut Windows down', 'start from the USB stick again'),
                    'bitlocker': ('BitLocker', 'turn off BitLocker', 'wait until it finishes'),
                    'space': ('Not enough free space', '35 GB', 'Delete or move files in Windows'),
                }[cause]
                for text in expected:
                    self.assertIn(text, reason)
                self.assertNotIn('[PLACEHOLDER]', reason)
                self.assertNotIn('Cannot verify NTFS', reason)
                self.assertEqual(disk['shrink']['max_free_bytes'], 0)
                self.assertEqual(part['_shrink_probe']['reason'], reason)
                self.assertEqual(ntfs.public_inventory(disk)['shrink']['reason'], reason)
                if cause != 'space':
                    self.assertFalse(any(cmd[0] == 'ntfs-3g' for cmd, _ in runner.commands))

    def test_unverifiable_efi_is_an_alongside_refusal_not_an_inventory_failure(self):
        _, inv = windows()
        disk = inv['disks'][0]
        esp, windows_part = disk['partitions']
        runner = RecordingRunner()
        probe = ntfs.Inventory(runner)
        error = InstallError(Code.UNSAFE_DISK, ntfs.EFI_MANIFEST_UNSAFE)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'mount'
            root.mkdir()
            with patch.object(ntfs.tempfile, 'mkdtemp', return_value=str(root)), \
                    patch.object(ntfs, 'efi_loaders', side_effect=error):
                probe._inspect(disk, esp)
            self.assertFalse(root.exists())
        self.assertEqual(esp['_efi_loaders'], {})
        self.assertEqual(esp['_efi_error'], ntfs.EFI_MANIFEST_UNSAFE)
        runner.commands.clear()
        with patch.object(ntfs, 'bitlocker_signature', return_value=False):
            offer = probe._ntfs_shrink(disk, windows_part)
        self.assertEqual(offer['reason'], ntfs.EFI_MANIFEST_UNSAFE)
        self.assertEqual(runner.commands, [])
        self.assertNotIn('_efi_error', ntfs.public_inventory(esp))

    def test_busy_no_gpt_no_esp_bitlocker_unknown_fail_closed(self):
        for change, reason in [({'_busy': True}, 'in use'), ({'_pttype': 'dos'}, 'GPT'),
                               ({'_read_only': True}, 'read-only')]:
            _, inv = windows()
            disk = inv['disks'][0]
            disk.update(change)
            runner = RecordingRunner()
            self.assertIn(reason, ntfs.Inventory(runner)._ntfs_shrink(disk, disk['partitions'][1])['reason'])
            self.assertEqual(runner.commands, [])
        _, inv = windows()
        disk = inv['disks'][0]
        with patch.object(ntfs, 'bitlocker_signature', return_value=True):
            runner = RecordingRunner()
            reason = ntfs.Inventory(runner)._ntfs_shrink(disk, disk['partitions'][1])['reason']
            self.assertEqual(reason, ntfs.ntfs_error('-FVE-FS-'))
            self.assertIn('after you changed Secure Boot, change it back', reason)
            self.assertIn('Never delete or reset the firmware keys', reason)
            self.assertEqual(runner.commands, [])
        disk['partitions'][0]['esp'] = False
        self.assertEqual(ntfs.Inventory(RecordingRunner())._ntfs_shrink(disk, disk['partitions'][1])['reason'],
                         'Installing next to Windows requires a GPT disk with an existing startup partition.')

    def test_largest_only_and_ties(self):
        _, inv = windows()
        disk = inv['disks'][0]
        small = copy.deepcopy(disk['partitions'][1])
        small.update(id='smaller', size_bytes=60 * GIB)
        disk['partitions'].append(small)
        probe = ntfs.Inventory(RecordingRunner())
        with patch.object(probe, '_ntfs_shrink', side_effect=lambda d, p: dict(min_bytes=GIB, max_free_bytes=40 * GIB)):
            probe._alongside(disk, True)
            self.assertNotIn('reason', disk['partitions'][1]['shrink'])
            self.assertIn('largest', small['shrink']['reason'])
            small['size_bytes'] = disk['partitions'][1]['size_bytes']
            probe._alongside(disk, True)
            self.assertTrue(all(p['shrink']['reason'] for p in disk['partitions'][1:]))


class EfiManifestTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def put(self, name, contents=b'fixture'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
        return path

    def test_all_foreign_files_case_insensitive_and_own_subtree_excluded(self):
        self.put('efi/mIcRoSoFt/BOOT/BOOTMGFW.EFI')
        bcd = self.put('efi/mIcRoSoFt/BOOT/BCD', b'configuration')
        self.put('efi/mIcRoSoFt/BOOT/BCD.LOG', b'')
        fallback = self.put('efi/boot/bootx64.efi')
        vendor = self.put('efi/Other/shim.efi')
        own = self.put('efi/Emaki/grubx64.efi')
        before = ntfs.efi_loaders(self.root)
        self.assertEqual(set(before), {'EFI/Microsoft/Boot/bootmgfw.efi',
                         'EFI/microsoft/boot/bcd', 'EFI/microsoft/boot/bcd.log',
                         'EFI/BOOT/BOOTX64.EFI', 'EFI/other/shim.efi'})
        own.write_bytes(b'new bootloader')
        self.assertEqual(before, ntfs.efi_loaders(self.root))
        for path in (bcd, fallback, vendor):
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(b'changed')
                self.assertNotEqual(before, ntfs.efi_loaders(self.root))
                path.write_bytes(original)
                path.unlink()
                self.assertNotEqual(before, ntfs.efi_loaders(self.root))
                path.write_bytes(original)

    def test_symlinks_special_files_and_ambiguous_names_refused(self):
        for name in ('efi', 'efi/Microsoft', 'efi/Microsoft/Boot/BCD', 'efi/Emaki'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                link = root / name
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to('/missing')
                with self.assertRaises(InstallError):
                    ntfs.efi_loaders(root)
        self.put('efi/Microsoft/Boot/BCD')
        self.put('efi/Microsoft/Boot/bcd')
        with self.assertRaises(InstallError):
            ntfs.efi_loaders(self.root)
        (self.root / 'efi/Microsoft/Boot/bcd').unlink()
        os.mkfifo(self.root / 'efi/pipe')
        with self.assertRaises(InstallError):
            ntfs.efi_loaders(self.root)

    def test_bounds_limit_entries_depth_files_and_aggregate_reads(self):
        self.put('efi/Microsoft/Boot/BCD', b'1234')
        self.put('efi/boot/bootx64.efi', b'1234')
        for limit, value in (('EFI_MANIFEST_MAX_ENTRIES', 2),
                             ('EFI_MANIFEST_MAX_DEPTH', 1),
                             ('EFI_MANIFEST_MAX_FILE_BYTES', 3),
                             ('EFI_MANIFEST_MAX_BYTES', 7)):
            with self.subTest(limit=limit), patch.object(ntfs, limit, value):
                with self.assertRaises(InstallError):
                    ntfs.efi_loaders(self.root)
        (self.root / 'efi/boot/bootx64.efi').write_bytes(b'')
        with self.assertRaises(InstallError):
            ntfs.efi_loaders(self.root)


class AlongsideDefaultTests(unittest.TestCase):
    def test_switch_is_a_plain_boolean(self):
        # 0.8.0 ships alongside on (DECISIONS 2026-10-13); the release-input gate, not this test,
        # requires the real-Windows acceptance row or an owner-signed exception.
        self.assertIsInstance(constants.ALONGSIDE, bool)


class AlongsideOffTests(unittest.TestCase):
    """An explicit disabled switch prevents probing, offering and writing."""

    def setUp(self):
        switch = patch.object(constants, 'ALONGSIDE', False)
        switch.start()
        self.addCleanup(switch.stop)
        self.c, self.inv = windows()

    def test_explicit_switch_is_off(self):
        self.assertIs(constants.ALONGSIDE, False)

    def test_inventory_does_not_probe_or_offer_windows(self):
        disk = self.inv['disks'][0]
        disk['shrink'] = None
        for part in disk['partitions']:
            part.pop('shrink', None)
        runner = RecordingRunner()
        ntfs.Inventory(runner)._alongside(disk, True)
        self.assertEqual(runner.commands, [])
        self.assertIsNone(disk['shrink'])
        self.assertFalse(any('shrink' in p or '_shrink_probe' in p for p in disk['partitions']))

    def test_planner_refuses_before_looking_at_the_disk(self):
        with self.assertRaisesRegex(InstallError, 'not available') as raised:
            make_plan(self.c, self.inv)
        self.assertEqual(raised.exception.code, Code.UNSUPPORTED_MODE)
        self.c['disk_id'] = '/dev/absent'
        with self.assertRaises(InstallError) as raised:
            validate_config(self.c)
        self.assertEqual(raised.exception.code, Code.UNSUPPORTED_MODE)

    def test_a_plan_made_while_offered_cannot_run_now(self):
        with alongside_on():
            plan = make_plan(self.c, self.inv)
        runner = RecordingRunner()
        ops = DeviceOperations(plan, runner)
        for step in (lambda: ops.partition(SimpleNamespace()),
                     lambda: ops.format(SimpleNamespace(value='btrfs'), '/dev/vda3')):
            with self.assertRaises(InstallError) as raised:
                step()
            self.assertEqual(raised.exception.code, Code.UNSUPPORTED_MODE)
        self.assertEqual(runner.commands, [])
        worker = Worker(None, FakeInventory(self.inv), Redactor(), Mock(), Mock())
        worker.plan, worker.backend = plan, Mock()
        with self.assertRaises(InstallError) as raised:
            worker.prepare_disk()
        self.assertEqual(raised.exception.code, Code.UNSUPPORTED_MODE)
        worker.backend.prepare.assert_not_called()
        self.assertFalse(worker.disk_changed)


class AlongsideTests(AlongsideOnTestCase):
    def setUp(self):
        super().setUp()
        self.c, self.inv = windows()

    def plan(self):
        return make_plan(self.c, self.inv)

    def test_layout_creates_own_esp_and_frees_exact_extent_both_filesystems(self):
        for fs in ('btrfs', 'ext4'):
            self.c['fs'] = fs
            plan = self.plan()
            esp, root = plan.partitions
            freed = self.inv['disks'][0]['partitions'][1]['start_bytes'] + 56 * GIB
            self.assertEqual((esp.path, esp.number, esp.start, esp.size, esp.format), (None, 3, freed, GIB, True))
            self.assertEqual(root.size, 39 * GIB)
            self.assertEqual(root.start, freed + GIB)
            self.assertEqual(root.number, 4)
            self.assertIn('Windows keeps 60.13 GB, Emaki gets 42.95 GB; back up your files first.', plan.summary)
            self.assertEqual('/' in root.subvolumes.values(), fs == 'btrfs')

    def test_encryption_and_hibernation_matrix(self):
        for fs in ('btrfs', 'ext4'):
            for encryption in ('none', 'account', 'separate'):
                for hibernation in (False, True):
                    with self.subTest(fs=fs, encryption=encryption, hibernation=hibernation):
                        self.c.update(fs=fs, encryption=encryption, hibernation=hibernation,
                                      disk_password='disposable-disk-password' if encryption == 'separate' else '')
                        plan = self.plan()
                        self.assertEqual(plan.encrypted, encryption != 'none')
                        self.assertEqual(plan.swap_bytes, self.inv['memory_bytes'] if hibernation else 0)
                        self.assertEqual(plan.root.subvolumes.get('@swap'), '/swap' if fs == 'btrfs' and hibernation else None)
                        self.assertTrue(plan.partitions[0].format and plan.partitions[0].esp)
        self.inv['memory_bytes'] = 32 * GIB
        with self.assertRaisesRegex(InstallError, '20 GiB'):
            self.plan()

    def test_bounds_esp_geometry_and_candidate_changes(self):
        for key, val in [('shrink_bytes', True), ('shrink_bytes', 31 * GIB), ('shrink_bytes', 40 * GIB + 1),
                         ('shrink_bytes', 75 * GIB), ('partition_id', '/dev/other')]:
            before = dict(self.c)
            self.c[key] = val
            with self.assertRaises(InstallError):
                self.plan()
            self.c = before
        # Windows' ESP is never written, so its free space does not count (0.9.0).
        self.inv['disks'][0]['partitions'][0]['_esp_free_bytes'] = 32 * MIB - 1
        self.plan()
        self.inv['disks'][0]['partitions'][0]['_efi_loaders'] = {}
        with self.assertRaisesRegex(InstallError, 'No verified Microsoft EFI loader'):
            self.plan()

    def runner(self, plan):
        runner = RecordingRunner()
        meta = plan.disk['partitions'][1]['_gpt']
        runner.responses[('sgdisk', '--info=2', '/dev/vda')] = gpt_text(meta)
        runner.responses[('sgdisk', '--info=1', '/dev/vda')] = gpt_text(plan.disk['partitions'][0]['_gpt'])
        runner.responses[('sgdisk', '--verify', '/dev/vda')] = 'No problems found.'
        runner.responses[('ntfsresize', '--no-action', '--size', str(56 * GIB), '/dev/vda2')] = 'The read-only test run ended successfully.'
        runner.responses[('ntfsresize', '--size', str(56 * GIB), '/dev/vda2')] = "Successfully resized NTFS on device '/dev/vda2'."
        runner.responses[('ntfsresize', '--info', '--no-action', '/dev/vda2')] = OK
        original_run = runner.run
        def run(argv, **kwargs):
            result = original_run(argv, **kwargs)
            if argv[:2] == ['sgdisk', '--delete=2']:
                after = dict(meta, end=(plan.partitions[0].start // 512 - 1))
                runner.responses[('sgdisk', '--info=2', '/dev/vda')] = gpt_text(after)
            return result
        runner.run = run
        return runner

    @patch.object(ntfs, 'verify_resized_ntfs')
    def test_exact_resize_sequence_and_preserved_metadata(self, verify):
        plan = self.plan()
        runner = self.runner(plan)
        changed = Mock()
        resize_partition(plan, runner, changed)
        self.journal.assert_called_once_with(runner, '/dev/vda2', 96 * GIB, 512)
        commands = [cmd for cmd, _ in runner.commands]
        self.assertEqual([cmd[0] for cmd in commands], ['sgdisk', 'sgdisk', 'ntfsresize', 'ntfsresize',
                         'sgdisk', 'partprobe', 'udevadm', 'sgdisk', 'sgdisk', 'sgdisk', 'partprobe', 'udevadm', 'sgdisk'])
        self.assertEqual(commands[2], ['ntfsresize', '--no-action', '--size', str(56 * GIB), '/dev/vda2'])
        self.assertEqual(commands[3], ['ntfsresize', '--size', str(56 * GIB), '/dev/vda2'])
        self.assertEqual(runner.commands[3][1], {'input': 'y\n'})
        meta = plan.disk['partitions'][1]['_gpt']
        esp, root = plan.partitions
        self.assertEqual(commands[4], ['sgdisk', '--delete=2', f'--new=2:{meta["start"]}:{esp.start // 512 - 1}',
                         f'--partition-guid=2:{GUID}', f'--typecode=2:{TYPE}', "--change-name=2:Windows owner's data",
                         '--attributes=2:=:0x8000000000000000', '/dev/vda'])
        self.assertEqual(commands[9], ['sgdisk', f'--new=3:{esp.start // 512}:{root.start // 512 - 1}',
                         '--typecode=3:EF00', '--change-name=3:Emaki EFI',
                         f'--new=4:{root.start // 512}:{(root.start + root.size) // 512 - 1}',
                         '--typecode=4:8300', '--change-name=4:Emaki', '/dev/vda'])
        verify.assert_called_once_with(runner, '/dev/vda2', 56 * GIB)
        self.assertFalse(any('--info' in cmd and cmd[0] == 'ntfsresize' for cmd in commands))
        changed.assert_called_once()

    def test_abort_dryrun_or_metadata_before_any_write(self):
        for fail in ('dry-run', 'metadata'):
            plan = self.plan()
            runner = self.runner(plan)
            if fail == 'dry-run':
                runner.fail = lambda argv: argv[:2] == ['ntfsresize', '--no-action']
            else:
                runner.responses[('sgdisk', '--info=2', '/dev/vda')] = gpt_text(dict(plan.disk['partitions'][1]['_gpt'], name='changed'))
            changed = Mock()
            with self.assertRaises(InstallError):
                resize_partition(plan, runner, changed)
            changed.assert_not_called()
            self.assertFalse(any('--delete=2' in cmd or cmd[:2] == ['ntfsresize', '--size'] for cmd, _ in runner.commands))

    def test_zero_exit_without_dryrun_success_and_resize_failure(self):
        for stage in ('dry-run', 'resize'):
            plan = self.plan()
            runner = self.runner(plan)
            key = (('ntfsresize', '--no-action', '--size', str(56 * GIB), '/dev/vda2') if stage == 'dry-run'
                   else ('ntfsresize', '--size', str(56 * GIB), '/dev/vda2'))
            runner.responses[key] = 'Nothing to do: NTFS volume size is already OK.'
            changed = Mock()
            with self.assertRaises(InstallError):
                resize_partition(plan, runner, changed)
            if stage == 'dry-run':
                changed.assert_not_called()
            else:
                changed.assert_called_once()
            self.assertFalse(any('--delete=2' in cmd for cmd, _ in runner.commands))

    @patch.object(ntfs, 'verify_resized_ntfs')
    def test_expected_dirty_flag_does_not_trigger_another_resize_probe(self, verify):
        plan = self.plan()
        runner = self.runner(plan)
        runner.responses[('ntfsresize', '--info', '--no-action', '/dev/vda2')] = 'ERROR: Volume is scheduled for check.\n' + OK
        resize_partition(plan, runner, Mock())
        verify.assert_called_once()
        self.assertTrue(any('--new=3:' in ' '.join(cmd) for cmd, _ in runner.commands))

    def test_abort_post_shrink_before_root_create(self):
        plan = self.plan()
        runner = self.runner(plan)
        runner.fail = lambda cmd: cmd[:2] == ['blockdev', '--getsize64']
        with self.assertRaisesRegex(InstallError, 'STOP: Windows was resized'):
            resize_partition(plan, runner, Mock())
        self.assertEqual(runner.commands[-1][0], ['blockdev', '--getsize64', '/dev/vda2'])
        self.assertFalse(any('--new=3:' in ' '.join(cmd) or cmd[0].startswith('mkfs') for cmd, _ in runner.commands))

    @patch.object(ntfs, 'verify_resized_ntfs')
    def test_post_shrink_refusal_is_not_retryable(self, verify):
        plan = self.plan()
        runner = self.runner(plan)
        verify.side_effect = InstallError(Code.COMMAND_FAILED, 'read failed', retryable=True)
        with self.assertRaises(InstallError) as caught:
            resize_partition(plan, runner, Mock())
        self.assertEqual(caught.exception.code, Code.UNSAFE_DISK)
        self.assertFalse(caught.exception.retryable)
        self.assertFalse(any('--new=3:' in ' '.join(cmd) for cmd, _ in runner.commands))

    def test_reprobe_changed_in_prepare_aborts_before_backend(self):
        plan = self.plan()
        worker = Worker(None, FakeInventory(self.inv), Redactor(), Mock(), Mock())
        worker.plan, worker.backend = plan, Mock()
        self.inv['disks'][0]['partitions'][1]['shrink']['min_bytes'] += MIB
        with self.assertRaisesRegex(InstallError, 'changed after review'):
            worker.prepare_disk()
        worker.backend.prepare.assert_not_called()
        self.assertFalse(worker.disk_changed)

    def test_changed_foreign_efi_manifest_aborts_before_any_write(self):
        for name in ('EFI/microsoft/boot/bcd', 'EFI/BOOT/BOOTX64.EFI', 'EFI/other/shim.efi'):
            with self.subTest(name=name):
                self.c, self.inv = windows()
                manifest = self.inv['disks'][0]['partitions'][0]['_efi_loaders']
                manifest[name] = 'reviewed-content'
                plan = self.plan()
                worker = Worker(None, FakeInventory(self.inv), Redactor(), Mock(), Mock())
                worker.plan, worker.backend = plan, Mock()
                manifest[name] = 'changed-content'
                with self.assertRaisesRegex(InstallError, 'changed after review'):
                    worker.prepare_disk()
                worker.backend.prepare.assert_not_called()
                self.assertFalse(worker.disk_changed)

    def test_format_guard_never_formats_esp_or_windows(self):
        plan = self.plan()
        plan.root.path = '/dev/vda3'
        runner = RecordingRunner()
        ops = DeviceOperations(plan, runner)
        for path in ('/dev/vda1', '/dev/vda2'):
            with self.assertRaises(InstallError):
                ops.format(SimpleNamespace(value='btrfs'), path)
        ops.format(SimpleNamespace(value='btrfs'), '/dev/vda3')
        self.assertEqual(runner.commands, [(['mkfs.btrfs', '-f', '-K', '/dev/vda3'], {})])

    def test_windows_esp_is_never_formatted_or_labelled(self):
        # Windows' ESP keeps whatever label Windows set; the label is Emaki's own ESP's.
        plan = self.plan()
        plan.root.path = '/dev/vda3'
        self.assertEqual(plan.esp_label, 'EMAKI')
        runner = RecordingRunner()
        with self.assertRaises(InstallError):
            DeviceOperations(plan, runner).format(SimpleNamespace(value='fat32'), '/dev/vda1')
        self.assertEqual(runner.commands, [])
        worker = Worker(None, None, Redactor(), Mock(), Mock())
        worker.plan, worker.mok_password = plan, '12345678'
        self.assertEqual(worker.secure_boot_done(), {'secure_boot': {'password': '12345678', 'esp_label': 'EMAKI'}})

    def test_4kn_and_reprobe_efi_hash_changes(self):
        disk = self.inv['disks'][0]
        disk['_sector_size'] = 4096
        p = disk['partitions'][1]
        p['_gpt'].update(start=p['start_bytes'] // 4096,
                         end=(p['start_bytes'] + p['size_bytes']) // 4096 - 1)
        plan = self.plan()
        self.assertEqual(plan.root.start % MIB, 0)
        before = plan.fingerprint
        disk['partitions'][0]['_efi_loaders']['EFI/Microsoft/Boot/bootmgfw.efi'] = 'changed'
        self.assertNotEqual(before, self.plan().fingerprint)

    def test_readback_accepts_only_expected_root_and_preserves_other_parts(self):
        plan = self.plan()
        runner = RecordingRunner()
        children = [dict(path=p['path'], partn=p['number'], start=p['start_bytes'] // 512,
                         size=p['size_bytes'] - (40 * GIB if p['number'] == 2 else 0),
                         partuuid=p['_partuuid'], parttype=p['_parttype']) for p in plan.disk['partitions']]
        esp = plan.partitions[0]
        children.append(dict(path='/dev/vda3', partn=3, start=esp.start // 512, size=esp.size,
                             partuuid='new-esp-guid', parttype=ntfs.ESP_GUID))
        children.append(dict(path='/dev/vda4', partn=4, start=plan.root.start // 512,
                             size=plan.root.size, partuuid='new-root-guid'))
        query = ('lsblk', '--tree', '-J', '-b', '-o', 'PATH,TYPE,PARTN,START,SIZE,PTTYPE,PARTUUID,PARTTYPE', '/dev/vda')
        archroot = SimpleNamespace(partn=4, dev_path=None)
        ops = DeviceOperations(plan, runner)
        runner.responses[query] = json.dumps({'blockdevices': [{'pttype': 'gpt', 'children': children}]})
        ops.verify_alongside_layout(SimpleNamespace(partitions=[archroot]))
        self.assertEqual(archroot.dev_path, Path('/dev/vda4'))
        children[0]['partuuid'] = 'changed-esp'
        runner.responses[query] = json.dumps({'blockdevices': [{'pttype': 'gpt', 'children': children}]})
        with self.assertRaisesRegex(InstallError, 'existing partition changed'):
            ops.verify_alongside_layout(SimpleNamespace(partitions=[archroot]))


# sgdisk --verify on a real Windows 10 disk as Windows Setup laid it out (2026-10-13).
MAC_VERIFY = ("Caution: Partition 3 doesn't end on a 2048-sector boundary. This may\n"
              "result in problems with some disk encryption tools.\n"
              "No problems found. 6412 free sectors (3.1 MiB) available in 3\n"
              "segments, the largest of which is 4063 (2.0 MiB) in size.\n")


class PartitionTableGapTests(unittest.TestCase):
    GAP = ("Warning: There is a gap between the main partition table (ending sector 33)\n"
           "and the first usable sector (2048). This is helpful in some exotic configurations,\n"
           "but is unusual. The util-linux fdisk program often creates disks like this.\n"
           "Using 'j' on the experts' menu can adjust this gap.\n")

    def verify(self, output):
        runner = RecordingRunner()
        runner.responses[('sgdisk', '--verify', '/dev/vda')] = output
        ntfs.verify_gpt(runner, '/dev/vda')

    def test_accepts_util_linux_gap_with_clean_verification(self):
        for output in (self.GAP + '\nNo problems found. 2015 free sectors available.\n',
                       self.GAP + '\n' + MAC_VERIFY,
                       self.GAP.replace('33)', '127)').replace('(2048)', '(4096)') + MAC_VERIFY):
            with self.subTest(output=output):
                self.verify(output)

    def test_gap_does_not_hide_corruption_or_incomplete_verification(self):
        for output in (self.GAP,
                       self.GAP + 'Identified 1 problems!\n',
                       self.GAP + MAC_VERIFY + 'Warning! Main partition table CRC mismatch!\n',
                       'Caution: invalid backup GPT header, but valid main header; regenerating\n' + self.GAP + MAC_VERIFY,
                       self.GAP.replace('but is unusual.', 'but is corrupt.') + MAC_VERIFY,
                       self.GAP.splitlines()[0] + '\n' + MAC_VERIFY):
            with self.subTest(output=output), self.assertRaisesRegex(InstallError, 'GPT validation failed'):
                self.verify(output)


class UnalignedWindowsEndTests(AlongsideOnTestCase):
    """Windows Setup ended C: 335 sectors short of the 1 MiB grid, its recovery partition after it.
    The sub-MiB tail stays unused: Windows' new end and the whole Emaki root are aligned."""
    TAIL = 335 * 512

    def setUp(self):
        super().setUp()
        self.c, self.inv = windows()
        p = self.inv['disks'][0]['partitions'][1]
        p['size_bytes'] = 96 * GIB - self.TAIL
        p['_gpt']['end'] = (p['start_bytes'] + p['size_bytes']) // 512 - 1
        p['shrink'] = ntfs.shrink_offer(20 * GIB, p['size_bytes'], p['start_bytes'])
        self.windows = p
        # 96 GiB less the tail, floored to whole MiB, less 40 GiB for Emaki.
        self.new_size = 56 * GIB - MIB

    def test_offer_counts_only_whole_mib(self):
        self.assertEqual(self.windows['shrink'], {'min_bytes': 20 * GIB, 'max_free_bytes': 74 * GIB - MIB})
        with self.assertRaisesRegex(InstallError, 'does not start on a 1 MiB boundary'):
            ntfs.shrink_offer(20 * GIB, 96 * GIB, MIB + 512)

    def test_gpt_check_ignores_only_the_alignment_caution(self):
        runner = RecordingRunner()
        runner.responses[('sgdisk', '--verify', '/dev/sda')] = MAC_VERIFY
        ntfs.verify_gpt(runner, '/dev/sda')
        for output in (MAC_VERIFY + 'Warning! Secondary partition table overlaps the last partition by 33 blocks!\n',
                       'Caution: invalid backup GPT header, but valid main header; regenerating\n' + MAC_VERIFY,
                       MAC_VERIFY.replace('No problems found.', 'Problem: partition 3 is too big for the disk.'),
                       MAC_VERIFY.replace('This may\n', 'This may\n\n'),
                       'Caution: Partition 3 doesn\'t end on a 2048-sector boundary.\nNo problems found.\n'):
            runner.responses[('sgdisk', '--verify', '/dev/sda')] = output
            with self.subTest(output=output), self.assertRaisesRegex(InstallError, 'GPT validation failed'):
                ntfs.verify_gpt(runner, '/dev/sda')

    def test_plan_aligns_windows_end_and_root(self):
        plan = make_plan(self.c, self.inv)
        esp, root = plan.partitions
        self.assertEqual(esp.start, self.windows['start_bytes'] + self.new_size)
        self.assertEqual(esp.start % MIB, 0)
        self.assertEqual(root.start, esp.start + GIB)
        self.assertEqual(root.size, 39 * GIB)
        self.assertEqual(root.start % MIB, 0)
        self.assertEqual((root.start + root.size) % MIB, 0)
        self.assertEqual(self.windows['start_bytes'] + self.windows['size_bytes'] - (root.start + root.size),
                         MIB - self.TAIL)
        self.assertIn(f'Windows keeps {self.new_size / 10**9:.2f} GB, Emaki gets 42.95 GB; back up your files first.',
                      plan.summary)

    @patch.object(ntfs, 'verify_resized_ntfs')
    def test_resize_uses_the_aligned_windows_size(self, verify):
        plan = make_plan(self.c, self.inv)
        meta = self.windows['_gpt']
        runner = RecordingRunner()
        runner.responses[('sgdisk', '--info=2', '/dev/vda')] = gpt_text(meta)
        runner.responses[('sgdisk', '--info=1', '/dev/vda')] = gpt_text(plan.disk['partitions'][0]['_gpt'])
        runner.responses[('sgdisk', '--verify', '/dev/vda')] = MAC_VERIFY.replace('Partition 3', 'Partition 2')
        size = str(self.new_size)
        runner.responses[('ntfsresize', '--no-action', '--size', size, '/dev/vda2')] = 'The read-only test run ended successfully.'
        runner.responses[('ntfsresize', '--size', size, '/dev/vda2')] = "Successfully resized NTFS on device '/dev/vda2'."
        original_run = runner.run

        def run(argv, **kwargs):
            result = original_run(argv, **kwargs)
            if argv[:2] == ['sgdisk', '--delete=2']:
                runner.responses[('sgdisk', '--info=2', '/dev/vda')] = gpt_text(dict(meta, end=esp.start // 512 - 1))
                runner.responses[('sgdisk', '--verify', '/dev/vda')] = 'No problems found.'
            return result
        runner.run = run
        esp, root = plan.partitions
        resize_partition(plan, runner, Mock())
        commands = [cmd for cmd, _ in runner.commands]
        self.assertIn(['ntfsresize', '--size', size, '/dev/vda2'], commands)
        self.assertIn(['sgdisk', '--delete=2', f'--new=2:{meta["start"]}:{esp.start // 512 - 1}',
                       f'--partition-guid=2:{GUID}', f'--typecode=2:{TYPE}', "--change-name=2:Windows owner's data",
                       '--attributes=2:=:0x8000000000000000', '/dev/vda'], commands)
        self.assertIn(['sgdisk', f'--new=3:{esp.start // 512}:{(esp.start + esp.size) // 512 - 1}',
                       '--typecode=3:EF00', '--change-name=3:Emaki EFI',
                       f'--new=4:{root.start // 512}:{(root.start + root.size) // 512 - 1}',
                       '--typecode=4:8300', '--change-name=4:Emaki', '/dev/vda'], commands)
        verify.assert_called_once_with(runner, '/dev/vda2', self.new_size)

    def test_resize_refuses_a_root_that_does_not_follow_windows(self):
        for moved in (0, 1):
            with self.subTest(moved=('esp', 'root')[moved]):
                plan = make_plan(self.c, self.inv)
                plan.partitions[moved].start += MIB
                runner = RecordingRunner()
                changed = Mock()
                with self.assertRaisesRegex(InstallError, 'disagree'):
                    resize_partition(plan, runner, changed)
                self.assertEqual(runner.commands, [])
                changed.assert_not_called()

    def test_readback_expects_the_aligned_windows_size(self):
        plan = make_plan(self.c, self.inv)
        runner = RecordingRunner()
        children = [dict(path=p['path'], partn=p['number'], start=p['start_bytes'] // 512,
                         size=self.new_size if p['number'] == 2 else p['size_bytes'],
                         partuuid=p['_partuuid'], parttype=p['_parttype']) for p in plan.disk['partitions']]
        esp = plan.partitions[0]
        children.append(dict(path='/dev/vda3', partn=3, start=esp.start // 512, size=esp.size,
                             partuuid='new-esp-guid', parttype=ntfs.ESP_GUID))
        children.append(dict(path='/dev/vda4', partn=4, start=plan.root.start // 512,
                             size=plan.root.size, partuuid='new-root-guid'))
        query = ('lsblk', '--tree', '-J', '-b', '-o', 'PATH,TYPE,PARTN,START,SIZE,PTTYPE,PARTUUID,PARTTYPE', '/dev/vda')
        runner.responses[query] = json.dumps({'blockdevices': [{'pttype': 'gpt', 'children': children}]})
        archroot = SimpleNamespace(partn=4, dev_path=None)
        DeviceOperations(plan, runner).verify_alongside_layout(SimpleNamespace(partitions=[archroot]))
        self.assertEqual(archroot.dev_path, Path('/dev/vda4'))
        children[1]['size'] = self.windows['size_bytes'] - 40 * GIB
        runner.responses[query] = json.dumps({'blockdevices': [{'pttype': 'gpt', 'children': children}]})
        with self.assertRaisesRegex(InstallError, 'existing partition changed'):
            DeviceOperations(plan, runner).verify_alongside_layout(SimpleNamespace(partitions=[archroot]))


if __name__ == '__main__':
    unittest.main()
