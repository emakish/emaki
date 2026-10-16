#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The shared-ESP write gate: identity, Windows shutdown state and Windows' files.

Rootless: mount, findmnt, lsblk and ntfs-3g are fakes over private directories.
The real packaged commands run in the alongside VM check.
"""
from contextlib import ExitStack
import errno
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'installer'), str(ROOT / 'grub')]
from emaki_installer import boot
sys.modules['emaki_boot.boot'] = boot
from emaki_boot import shared
from emaki_installer import inventory

ESP_GUID = '4c6a2f50-8b1e-4d55-9a7e-0f1f2b3c4d5e'
BASIC = shared.BASIC_DATA


class Machine:
    """A fake disk: an ESP directory, Windows volumes and mount state."""

    def __init__(self, root):
        self.root = root
        self.esp = root / 'efi'
        self.esp.mkdir()
        self.volumes = {}
        self.mounted = {}
        self.options = ['ro', 'noatime']
        self.partuuid = ESP_GUID
        self.calls = []
        self.fail = set()
        self.extra_disks = []
        self.parts = [{'path': '/dev/vda1', 'type': 'part', 'fstype': 'vfat',
                       'parttype': 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b', 'size': 100 * 1024 ** 2,
                       'mountpoints': ['/efi']},
                      {'path': '/dev/vda2', 'type': 'part', 'fstype': None,
                       'parttype': 'e3c9e316-0b5c-4db8-817d-f92df00215ae', 'mountpoints': [None]}]

    def windows(self, path='/dev/vda3', hiberfil=None, system=True, parttype=BASIC, fstype='ntfs'):
        volume = self.root / ('volume' + path.replace('/', '-'))
        volume.mkdir()
        if system:
            hive = volume / 'Windows/System32/config/SYSTEM'
            hive.parent.mkdir(parents=True)
            hive.write_bytes(b'regf')
        if hiberfil is not None:
            (volume / 'hiberfil.sys').write_bytes(hiberfil)
        self.volumes[path] = volume
        self.parts.append({'path': path, 'type': 'part', 'fstype': fstype, 'parttype': parttype,
                           'mountpoints': [None]})
        return volume

    def alongside(self, encrypted=False):
        """Installed Emaki with the removable media people commonly leave attached."""
        self.windows()
        self.parts.append({'path': '/dev/vda4', 'type': 'part', 'fstype': None,
                           'parttype': shared.MS_RECOVERY, 'mountpoints': []})
        root = {'path': '/dev/vda5', 'type': 'part', 'fstype': 'btrfs',
                'parttype': '0fc63daf-8483-4772-8e79-3d69d8477de4', 'mountpoints': ['/']}
        if encrypted:
            root.update(fstype='crypto_LUKS', mountpoints=[None],
                        parttype='ca7d7ccb-63ed-4c53-861c-1742536059cc',
                        children=[{'path': '/dev/mapper/emaki-root', 'type': 'crypt',
                                   'fstype': 'btrfs', 'mountpoints': ['/']}])
        self.parts.append(root)
        self.extra_disks = [
            {'path': '/dev/zram0', 'type': 'disk', 'fstype': 'swap', 'mountpoints': ['[SWAP]']},
            {'path': '/dev/sdb', 'type': 'disk', 'rm': True, 'children': [
                {'path': '/dev/sdb1', 'type': 'part', 'fstype': 'exfat',
                 'parttype': '0x7', 'mountpoints': [None]}]},
            {'path': '/dev/sdc', 'type': 'disk', 'rm': True, 'fstype': 'iso9660', 'children': [
                {'path': '/dev/sdc1', 'type': 'part', 'fstype': 'iso9660',
                 'parttype': '0x0', 'mountpoints': ['/run/archiso/bootmnt']},
                {'path': '/dev/sdc2', 'type': 'part', 'fstype': 'vfat',
                 'parttype': '0xef', 'mountpoints': [None]}]},
            {'path': '/dev/loop0', 'type': 'loop', 'fstype': 'squashfs', 'mountpoints': [None]},
        ]

    def run(self, argv):
        argv = [str(item) for item in argv]
        self.calls.append(argv)
        name = argv[0]
        if name in self.fail:
            raise shared.Refused(shared.UNCHECKED + name + ': injected failure')
        if name == 'findmnt':
            if argv[-1] != str(shared.MOUNT) or 'unmounted' in self.fail:
                return json.dumps({'filesystems': []})
            return json.dumps({'filesystems': [{'target': '/efi', 'source': '/dev/vda1', 'fstype': 'vfat',
                                                'options': ','.join(self.options)}]})
        if name == 'lsblk' and '--nodeps' in argv:
            return json.dumps({'blockdevices': [{'path': '/dev/vda1', 'type': 'part',
                                                 'partuuid': self.partuuid, 'pkname': 'vda'}]})
        if name == 'lsblk':
            assert argv[-1] == 'PATH,TYPE,FSTYPE,PARTTYPE,MOUNTPOINTS'
            disk = {'path': '/dev/vda', 'type': 'disk', 'fstype': None, 'parttype': None, 'mountpoints': []}
            if '--tree' not in argv:
                # util-linux prints a flat list when the output has no NAME column.
                return json.dumps({'blockdevices': [disk, *self.parts]})
            return json.dumps({'blockdevices': [{**disk, 'children': self.parts}, *self.extra_disks]})
        if name == 'ntfs-3g':
            assert argv[1:3] == ['-o', shared.NTFS_OPTIONS], argv
            target = Path(argv[4])
            target.rmdir()
            shutil.copytree(self.volumes[argv[3]], target)
            self.mounted[str(target)] = argv[3]
            return ''
        if name == 'umount':
            target = Path(argv[-1])
            del self.mounted[str(target)]
            shutil.rmtree(target)
            target.mkdir()
            return ''
        if name == 'mount':
            assert argv[1:3] in (['-o', 'remount,rw'], ['-o', 'remount,ro']) and argv[3] == str(shared.MOUNT)
            mode = argv[2].split(',')[1]
            self.options = [mode] + [o for o in self.options if o not in ('ro', 'rw')]
            return ''
        if name == 'sync':
            return ''
        raise AssertionError('unexpected command ' + repr(argv))


class Gate(unittest.TestCase):
    def test_missing_or_damaged_installer_record_has_neutral_recovery_advice(self):
        for data in (None, '{'):
            with self.subTest(data=data):
                if data is None:
                    self.marker.unlink()
                else:
                    self.marker.write_text(data)
                with self.assertRaises(shared.Refused) as caught:
                    shared.record()
                message = str(caught.exception)
                self.assertIn('missing or damaged', message)
                self.assertNotIn('manual installation', message.lower())
                self.assertIn('without the installation record', message)

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        evidence = ROOT / '.cache/evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='shared-esp-', dir=evidence)))
        self.machine = Machine(self.root)
        marker = self.root / 'alongside-boot.json'
        marker.write_text(json.dumps({'version': 1, 'esp_partuuid': ESP_GUID.upper()}) + '\n')
        marker.chmod(0o644)
        self.marker = marker
        uid = os.getuid()
        real_fstat = os.fstat

        def fstat(fd):
            # The installer writes the record as root; this test runs unprivileged.
            info = real_fstat(fd)
            values = list(info)
            values[4] = 0 if info.st_uid == uid else info.st_uid
            return os.stat_result(values)

        for name, value in (('MARKER', marker), ('MOUNT', self.machine.esp),
                            ('CHECKS', self.root / 'checks'), ('run', self.machine.run)):
            self.stack.enter_context(patch.object(shared, name, value))
        self.stack.enter_context(patch.object(shared.os, 'fstat', side_effect=fstat))
        self.stack.enter_context(patch.dict(shared._session, {'depth': 0, 'baseline': None}))
        esp = self.machine.esp
        for name, data in {'EFI/Microsoft/Boot/bootmgfw.efi': b'MZ windows',
                           'EFI/Microsoft/Boot/BCD': b'bcd', 'EFI/BOOT/BOOTX64.EFI': b'MZ fallback',
                           'EFI/Emaki/grubx64.efi': b'MZ emaki'}.items():
            (esp / name).parent.mkdir(parents=True, exist_ok=True)
            (esp / name).write_bytes(data)

    def assertRefused(self, message, function, *args):
        with self.assertRaises(shared.Refused) as caught:
            function(*args)
        self.assertTrue(str(caught.exception).startswith(message), str(caught.exception))

    def assertNoMounts(self):
        self.assertEqual(self.machine.mounted, {})
        self.assertEqual(list((self.root / 'checks').iterdir()) if (self.root / 'checks').exists() else [], [])

    def test_clean_windows_passes_and_unmounts(self):
        self.machine.windows()
        self.machine.windows('/dev/vda4', hiberfil=bytes(8192), system=False)  # a data volume
        self.assertEqual(shared.check()['disk'], '/dev/vda')
        self.assertNoMounts()
        self.assertEqual(oct((self.root / 'checks').stat().st_mode & 0o777), '0o700')

    def test_r6_clean_windows_does_not_hide_hibernated_ldm_windows(self):
        self.machine.windows()
        self.machine.windows('/dev/vda5', parttype='af9b60a0-1431-4f62-bc68-3311714a69ad',
                             hiberfil=b'hibr' + bytes(4092))
        self.assertRefused(shared.UNCHECKED, shared.check)
        self.assertFalse(any(call[0] == 'mount' for call in self.machine.calls))

    def test_storage_spaces_types_refuse_before_any_windows_probe(self):
        self.machine.windows()
        for kind in ('e75caf8f-f680-4cee-afa3-b001e56efc2d',
                     '5808c8aa-7e8f-42e0-85d2-e1e90434cfb3'):
            with self.subTest(kind=kind):
                self.machine.parts[1]['parttype'] = kind
                self.machine.calls.clear()
                self.assertRefused(shared.UNCHECKED, shared.check)
                self.assertFalse(any(call[0] == 'ntfs-3g' for call in self.machine.calls))

    def test_unknown_non_windows_partition_types_are_ignored(self):
        self.machine.windows()
        for kind in ('unknown', None):
            with self.subTest(kind=kind):
                self.machine.parts[1]['parttype'] = kind
                shared.check()

    def test_windows_on_another_disk_is_checked(self):
        self.machine.windows()
        self.machine.windows('/dev/vdb1', hiberfil=b'hibr' + bytes(4092))
        other = self.machine.parts.pop()
        self.machine.extra_disks = [{'path': '/dev/vdb', 'type': 'disk', 'children': [other]}]
        self.assertRefused(shared.HIBERNATED, shared.check)
        self.assertEqual([call[3] for call in self.machine.calls if call[0] == 'ntfs-3g'],
                         ['/dev/vda3', '/dev/vdb1'])
        self.assertNoMounts()

    def test_windows_can_live_only_on_another_disk(self):
        self.machine.windows('/dev/vdb1')
        other = self.machine.parts.pop()
        self.machine.extra_disks = [{'path': '/dev/vdb', 'type': 'disk', 'children': [other]}]
        shared.check()
        self.assertNoMounts()

    def test_unpartitioned_disk_ignored_but_windows_candidate_unreadable_refuses(self):
        self.machine.windows()
        self.machine.extra_disks = [{'path': '/dev/vdb', 'type': 'disk', 'fstype': 'ntfs'}]
        shared.check()
        self.machine.extra_disks = []
        self.machine.parts[-1]['fstype'] = None
        self.assertRefused(shared.UNCHECKED, shared.check)

    def test_real_alongside_layout_allows_btrfs_root(self):
        self.machine.alongside()
        with shared.session():
            shared.open_writes()
        self.assertEqual([call[3] for call in self.machine.calls if call[0] == 'ntfs-3g'], ['/dev/vda3'])
        self.assertEqual(self.machine.parts[0]['size'], 100 * 1024 ** 2)
        self.assertNoMounts()

    def test_exact_guest_lsblk_layout_is_allowed(self):
        self.machine.windows()
        fixture = (ROOT / 'tests/vm/fixtures/lsblk-w100.json').read_text()
        run = self.machine.run

        def guest_run(argv):
            if argv[0] == 'lsblk' and '--tree' in argv:
                self.machine.calls.append(argv)
                return fixture
            return run(argv)

        with patch.object(shared, 'run', side_effect=guest_run), shared.session():
            shared.open_writes()
        self.assertEqual([call[3] for call in self.machine.calls if call[0] == 'ntfs-3g'], ['/dev/vda3'])
        self.assertIn('ro', self.machine.options)
        self.assertNoMounts()

    def test_recovery_with_ntfs_is_still_checked(self):
        self.machine.windows()
        self.machine.windows('/dev/vda4', parttype=shared.MS_RECOVERY,
                             hiberfil=b'hibr' + bytes(4092))
        self.assertRefused(shared.HIBERNATED, shared.check)

    def test_real_alongside_layout_allows_luks_btrfs_root(self):
        self.machine.alongside(encrypted=True)
        with shared.session():
            shared.open_writes()
        self.assertIn('ro', self.machine.options)
        self.assertNoMounts()

    def test_devices_without_windows_candidates_are_ignored(self):
        self.machine.windows()
        devices = [
            {'path': '/dev/zram0', 'type': 'disk', 'fstype': 'swap'},
            {'path': '/dev/loop0', 'type': 'loop', 'fstype': 'squashfs'},
            {'path': '/dev/dm-0', 'type': 'crypt', 'fstype': 'btrfs'},
            {'path': '/dev/md0', 'type': 'raid1', 'fstype': 'ext4'},
        ]
        for kind, filesystem in [('0x7', 'exfat'), ('0x7', 'ntfs'), ('0x83', 'ext4'),
                                 ('0xc', 'vfat'), (BASIC, 'exfat'), (BASIC, 'vfat')]:
            devices.append({'path': '/dev/sdb', 'type': 'disk', 'children': [
                {'path': '/dev/sdb1', 'type': 'part', 'parttype': kind, 'fstype': filesystem}]})
        for device in devices:
            with self.subTest(device=device):
                self.machine.extra_disks = [device]
                self.machine.calls.clear()
                shared.check()
                self.assertEqual([call[3] for call in self.machine.calls if call[0] == 'ntfs-3g'],
                                 ['/dev/vda3'])
                self.assertNoMounts()

    def test_oem_fat_partition_on_windows_disk_is_ignored(self):
        self.machine.windows()
        self.machine.parts.append({'path': '/dev/vda5', 'type': 'part', 'fstype': 'vfat',
                                   'parttype': BASIC, 'mountpoints': [None]})
        shared.check()
        self.assertNoMounts()

    def test_relevant_windows_refusals_identify_device(self):
        self.machine.windows()
        volume = self.machine.windows('/dev/vdb1', hiberfil=b'hibr' + bytes(4092))
        candidate = self.machine.parts.pop()
        self.machine.extra_disks = [{'path': '/dev/vdb', 'type': 'disk', 'children': [candidate]}]
        for state in ('hibernated', 'encrypted', 'unknown-filesystem', 'unreadable'):
            with self.subTest(state=state):
                candidate['fstype'] = {'encrypted': 'BitLocker', 'unknown-filesystem': None}.get(state, 'ntfs')
                candidate['mountpoints'] = [str(volume)]
                if state == 'unreadable':
                    candidate['mountpoints'] = [str(self.root / 'missing-volume')]
                with self.assertRaises(shared.Refused) as caught:
                    shared.check()
                self.assertIn('/dev/vdb1', str(caught.exception))
                self.assertFalse(any(call[:3] == ['mount', '-o', 'remount,rw'] for call in self.machine.calls))
                self.assertNoMounts()

    def test_linux_mappings_cannot_hide_unchecked_windows(self):
        self.machine.windows()
        part = {'path': '/dev/vda4', 'type': 'part', 'fstype': 'crypto_LUKS',
                'parttype': 'ca7d7ccb-63ed-4c53-861c-1742536059cc',
                'children': [{'path': '/dev/mapper/root', 'type': 'crypt', 'fstype': 'ext4'}]}
        self.machine.parts.append(part)
        shared.check()
        for filesystem in ('ntfs', 'BitLocker'):
            with self.subTest(filesystem=filesystem):
                part['children'][0]['fstype'] = filesystem
                self.assertRefused(shared.UNCHECKED, shared.check)
        for filesystem in (None, 'unknown'):
            part['children'][0]['fstype'] = filesystem
            shared.check()
        part['children'][0]['fstype'] = 'ext4'
        part['children'][0]['children'] = [{'type': 'part', 'fstype': 'ntfs'}]
        self.assertRefused(shared.UNCHECKED, shared.check)

    def test_pending_write_is_durable_and_only_cleared_after_success(self):
        self.machine.windows()
        with shared.session():
            shared.open_writes()
            self.assertEqual(json.loads(shared.stop_record().read_text())['state'], 'write-in-progress')
        self.assertFalse(shared.stop_record().exists())
        shared.stop_record().write_text('{"version": 1, "state": "write-in-progress"}')
        self.machine.calls.clear()
        with shared.session():
            self.assertRefused(shared.STOPPED, shared.open_writes)
        self.assertFalse(any(call[:3] == ['mount', '-o', 'remount,rw'] for call in self.machine.calls))

    def test_microsoft_mappings_cannot_hide_windows(self):
        self.machine.windows()
        part = {'path': '/dev/vda4', 'type': 'part', 'fstype': 'crypto_LUKS',
                'children': [{'path': '/dev/mapper/hidden', 'type': 'crypt', 'fstype': 'ntfs'}]}
        self.machine.parts.append(part)
        for kind in (BASIC, shared.MS_RECOVERY):
            for filesystem in ('ntfs', 'BitLocker'):
                with self.subTest(kind=kind, filesystem=filesystem):
                    part['parttype'] = kind
                    part['children'][0]['fstype'] = filesystem
                    with self.assertRaises(shared.Refused) as caught:
                        shared.check()
                    self.assertIn('/dev/vda4', str(caught.exception))
                    self.assertFalse(any(call[:3] == ['mount', '-o', 'remount,rw']
                                         for call in self.machine.calls))

    def test_invalid_stop_records_return_esp_read_only_before_refusing(self):
        self.machine.windows()
        for record in ('broken', '{"version": 1, "state": "write-in-progress"}',
                       json.dumps({'version': 2, 'state': 'write-in-progress',
                                   'esp_partuuid': 'foreign', 'baseline': {}})):
            with self.subTest(record=record):
                shared.stop_record().write_text(record)
                self.machine.options = ['rw']
                with self.assertRaises(shared.Stopped):
                    shared.check()
                self.assertIn('ro', self.machine.options)
                self.assertEqual(shared.stop_record().read_text(), record)

    def test_read_only_remount_still_attempted_when_stop_sync_fails(self):
        self.machine.windows()
        shared.stop_record().write_text('broken')
        self.machine.options = ['rw']
        self.machine.fail.add('sync')
        with self.assertRaises(shared.Stopped):
            shared.check()
        self.assertIn('ro', self.machine.options)

    def test_guard_returns_writable_esp_read_only_after_manual_record_archive(self):
        self.machine.windows()
        shared.stop_record().write_text('broken')
        shared.stop_record().rename(self.root / 'shared-esp-stop.reviewed.json')
        self.machine.options = ['rw']
        shared.check()
        self.assertIn('ro', self.machine.options)

    def test_stop_message_contains_standalone_recovery_steps(self):
        for instruction in ('recovery media', 'known-good backup', 'hibernation',
                            'mount -o remount,ro /efi', 'emaki-update-boot guard-alongside',
                            'mv -n -- /var/lib/emaki/shared-esp-stop.json',
                            '/var/lib/emaki/shared-esp-stop.reviewed.json',
                            'both successful boots'):
            self.assertIn(instruction, shared.STOPPED)

    def interrupted_write(self):
        self.machine.windows()
        baseline = shared.foreign_files(self.machine.esp)
        with shared.session():
            shared.open_writes()
            # A new process has no in-memory session after a power loss.
            shared._session['baseline'] = None
        return baseline

    def test_interrupted_write_records_identity_and_foreign_baseline(self):
        baseline = self.interrupted_write()
        record = json.loads(shared.stop_record().read_text())
        self.assertEqual(record['baseline'], baseline)
        self.assertEqual(record['esp_partuuid'], ESP_GUID)

    def test_interrupted_unchanged_write_recovers_read_only_and_clears(self):
        self.interrupted_write()
        self.machine.calls.clear()
        shared.check()
        self.assertFalse(shared.stop_record().exists())
        self.assertIn('ro', self.machine.options)
        self.assertFalse(any(call[:3] == ['mount', '-o', 'remount,rw'] for call in self.machine.calls))

    def test_interrupted_changed_files_stay_stopped_until_restored(self):
        self.interrupted_write()
        foreign = self.machine.esp / 'EFI/Microsoft/Boot/BCD'
        original = foreign.read_bytes()
        foreign.write_bytes(b'changed')
        with self.assertRaises(shared.Stopped) as caught:
            shared.check()
        self.assertIn('/var/lib/emaki/shared-esp-stop.json', str(caught.exception))
        self.assertIn('Restore shared startup files', str(caught.exception))
        self.assertTrue(shared.stop_record().exists())
        self.assertIn('ro', self.machine.options)
        foreign.write_bytes(original)
        shared.check()
        self.assertFalse(shared.stop_record().exists())

    def test_interrupted_record_cannot_clear_on_wrong_esp(self):
        self.interrupted_write()
        self.machine.partuuid = '00000000-0000-0000-0000-000000000000'
        self.assertRefused(shared.WRONG_ESP, shared.check)
        self.assertTrue(shared.stop_record().exists())

    def test_record_storage_failure_before_remount_leaves_no_stop(self):
        self.machine.windows()
        with patch.object(shared.os, 'fsync', side_effect=OSError(errno.ENOSPC, 'no space')):
            with self.assertRaises(OSError), shared.session():
                shared.open_writes()
        self.assertFalse(shared.stop_record().exists())
        self.assertFalse(any(call[0] == 'mount' for call in self.machine.calls))
        shared.check()

    def test_check_alone_never_creates_a_stop_record(self):
        self.machine.windows()
        shared.check()
        self.assertFalse(shared.stop_record().exists())
        self.assertFalse(any(call[0] == 'mount' for call in self.machine.calls))

    def test_shared_interface_has_final_english(self):
        for name in ('HIBERNATED', 'ENCRYPTED', 'NO_WINDOWS', 'UNCHECKED', 'WRONG_ESP',
                     'BAD_RECORD', 'STOPPED'):
            self.assertNotIn('[PLACEHOLDER]', getattr(shared, name))

    def test_close_failures_remain_stopped_and_attempt_read_only(self):
        self.machine.windows()
        for failure in ('sync', 'verification', 'remount'):
            with self.subTest(failure=failure):
                with self.assertRaises(shared.Stopped):
                    with shared.session():
                        shared.open_writes()
                        if failure == 'sync':
                            self.machine.fail.add('sync')
                        elif failure == 'verification':
                            (self.machine.esp / 'EFI/BOOT/link').symlink_to('BOOTX64.EFI')
                        else:
                            self.machine.fail.add('mount')
                        self.stack.enter_context(patch.object(shared, 'REMOUNT_TRIES', 1))
                self.assertTrue(shared.stop_record().exists())
                self.assertTrue(any(call[:3] == ['mount', '-o', 'remount,ro']
                                    for call in self.machine.calls))
                self.assertRefused(shared.STOPPED, shared.check)
                shared.stop_record().unlink()
                self.machine.fail.clear()
                (self.machine.esp / 'EFI/BOOT/link').unlink(missing_ok=True)

    def test_zero_hibernation_header_is_shut_down(self):
        self.machine.windows(hiberfil=bytes(4096) + b'tail')
        shared.check()
        self.assertNoMounts()

    def test_hibernated_or_unreadable_hibernation_refuses_and_unmounts(self):
        for header in (b'hibr' + bytes(4092), b'HIBR' + bytes(4092), b'wake' + bytes(4092),
                       bytes(4095) + b'\x01', bytes(100)):
            with self.subTest(header=header[:4]):
                machine = self.machine
                machine.parts = machine.parts[:2]
                volume = machine.root / 'volume-dev-vda3'
                if volume.exists():
                    shutil.rmtree(volume)
                machine.windows(hiberfil=header)
                self.assertRefused(shared.HIBERNATED, shared.check)
                self.assertNoMounts()

    def test_hibernated_windows_through_an_existing_mount(self):
        volume = self.machine.windows(hiberfil=b'hibr' + bytes(4092))
        self.machine.parts[-1]['mountpoints'] = [str(volume)]
        self.assertRefused(shared.HIBERNATED, shared.check)
        self.assertFalse(any(call[0] == 'ntfs-3g' for call in self.machine.calls))

    def test_case_insensitive_names_match_the_installer(self):
        volume = self.machine.windows(system=False)
        hive = volume / 'WINDOWS/system32/Config/system'
        hive.parent.mkdir(parents=True)
        hive.write_bytes(b'regf')
        (volume / 'HIBERFIL.SYS').write_bytes(b'hibr' + bytes(4092))
        self.assertRefused(shared.HIBERNATED, shared.check)

    def test_rules_agree_with_the_installer_inventory(self):
        cases = {'absent': None, 'zero': bytes(4096), 'short': bytes(10), 'hibr': b'hibr' + bytes(4092),
                 'tail': bytes(4095) + b'\x02', 'long-zero': bytes(9000)}
        for name, data in cases.items():
            with self.subTest(hiberfil=name):
                volume = self.root / ('agree-' + name)
                volume.mkdir()
                if data is not None:
                    (volume / 'hiberfil.sys').write_bytes(data)
                self.assertEqual(shared.hibernated(volume), inventory.hibernation_reason(volume) is not None)
        for layout in ('Windows/System32/config/SYSTEM', 'windows/SYSTEM32/config/system',
                       'Windows/System32/config', 'Windows/System32/SYSTEM'):
            with self.subTest(layout=layout):
                volume = self.root / ('layout-' + layout.replace('/', '-'))
                path = volume / layout
                path.parent.mkdir(parents=True)
                if layout.endswith('config'):
                    path.mkdir()
                else:
                    path.write_bytes(b'regf')
                self.assertEqual(shared.windows_system(volume), inventory.windows_system(volume))

    def test_encrypted_missing_or_uncheckable_windows_refuses(self):
        self.machine.windows(fstype='BitLocker')
        self.assertRefused(shared.ENCRYPTED, shared.check)
        self.machine.parts = self.machine.parts[:2]
        self.assertRefused(shared.NO_WINDOWS, shared.check)
        # Managed Windows layouts are refused before inspecting any Windows system.
        self.machine.windows('/dev/vda5', parttype='af9b60a0-1431-4f62-bc68-3311714a69ad')
        self.assertRefused(shared.UNCHECKED, shared.check)
        self.machine.parts = self.machine.parts[:2]
        self.machine.windows('/dev/vda6')
        self.machine.fail.add('ntfs-3g')
        self.assertRefused(shared.UNCHECKED, shared.check)
        self.assertNoMounts()

    def test_wrong_or_missing_esp_and_bad_records_refuse_before_probing(self):
        self.machine.windows()
        self.machine.partuuid = '00000000-0000-0000-0000-000000000000'
        self.assertRefused(shared.WRONG_ESP, shared.check)
        self.machine.partuuid = ESP_GUID
        self.machine.fail.add('unmounted')
        self.assertRefused(shared.WRONG_ESP, shared.check)
        self.machine.fail.clear()
        for content, mode in (('{}', 0o644), ('not json', 0o644),
                              (json.dumps({'version': 2, 'esp_partuuid': ESP_GUID}), 0o644),
                              (json.dumps({'version': 1, 'esp_partuuid': 'x'}), 0o644),
                              (json.dumps({'version': 1, 'esp_partuuid': ESP_GUID}), 0o666),
                              (' ' * 5000, 0o644)):
            with self.subTest(content=content[:20], mode=oct(mode)):
                self.marker.write_text(content)
                self.marker.chmod(mode)
                self.machine.calls.clear()
                self.assertRefused(shared.BAD_RECORD, shared.check)
                self.assertFalse(any(call[:3] == ['mount', '-o', 'remount,rw'] for call in self.machine.calls))
        self.marker.unlink()
        self.marker.symlink_to(self.root / 'elsewhere.json')
        self.assertRefused(shared.BAD_RECORD, shared.check)

    def test_own_esp_record_never_opens_the_gate_on_windows_esp(self):
        # Since 0.9 Emaki has its own ESP next to Windows. Even with Windows' ESP (the record's
        # esp_partuuid, Microsoft files) mounted at /efi and Windows shut down, nothing opens.
        own = '9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d'
        record = {'version': 1, 'esp_partuuid': ESP_GUID.upper(), 'emaki_esp_partuuid': own.upper(),
                  'windows_esp_uuid': 'abcd-1234'}
        self.machine.windows()
        self.marker.write_text(json.dumps(record) + '\n')
        self.assertEqual(shared.record(), {'esp_partuuid': ESP_GUID, 'emaki_esp_partuuid': own,
                                           'windows_esp_uuid': 'ABCD-1234'})
        self.assertRefused(shared.OWN_ESP, shared.check)
        with shared.session():
            self.assertRefused(shared.OWN_ESP, shared.open_writes)
        self.assertEqual(self.machine.calls, [])
        self.assertIn('ro', self.machine.options)
        self.assertFalse(shared.stop_record().exists())
        self.assertNoMounts()
        # The old record still names the shared ESP.
        self.marker.write_text(json.dumps({'version': 1, 'esp_partuuid': ESP_GUID.upper()}) + '\n')
        self.assertEqual(shared.record(), {'esp_partuuid': ESP_GUID})
        self.assertEqual(shared.marker(), ESP_GUID)
        # A damaged record of the new layout is a damaged record.
        for damaged in (dict(record, emaki_esp_partuuid='x'), dict(record, emaki_esp_partuuid=None),
                        dict(record, emaki_esp_partuuid=ESP_GUID), dict(record, windows_esp_uuid='ABCD1234'),
                        {key: value for key, value in record.items() if key != 'windows_esp_uuid'}):
            with self.subTest(damaged=damaged):
                self.marker.write_text(json.dumps(damaged) + '\n')
                self.assertRefused(shared.BAD_RECORD, shared.record)
                self.assertRefused(shared.BAD_RECORD, shared.check)
                self.assertEqual(self.machine.calls, [])

    def test_session_is_lazy_writable_once_and_read_only_after(self):
        self.machine.windows()
        with shared.session():
            pass
        self.assertFalse(any(call[:3] == ['mount', '-o', 'remount,rw'] for call in self.machine.calls))
        with shared.session():
            with shared.session():
                shared.open_writes()
                self.assertIn('rw', self.machine.options)
                shared.open_writes()
                (self.machine.esp / 'EFI/Emaki/trial.env').write_bytes(b'#' * 1024)
            self.assertIn('rw', self.machine.options)
        self.assertIn('ro', self.machine.options)
        self.assertNotIn('rw', self.machine.options)
        self.assertEqual(sum(call[0] == 'ntfs-3g' for call in self.machine.calls), 1)
        self.assertEqual([call[2] for call in self.machine.calls if call[0] == 'mount'],
                         ['remount,rw', 'remount,ro'])
        self.assertNoMounts()

    def test_refused_check_never_remounts(self):
        self.machine.windows(hiberfil=b'hibr' + bytes(4092))
        with shared.session():
            self.assertRefused(shared.HIBERNATED, shared.open_writes)
        self.assertFalse(any(call[0] == 'mount' for call in self.machine.calls))
        self.assertEqual(self.machine.options, ['ro', 'noatime'])

    def test_write_outside_a_session_is_refused(self):
        self.machine.windows()
        self.assertRefused(shared.UNCHECKED, shared.open_writes)
        self.assertFalse(any(call[:3] == ['mount', '-o', 'remount,rw'] for call in self.machine.calls))

    def test_changed_windows_files_stop_and_still_return_to_read_only(self):
        self.machine.windows()
        for change in ('modify', 'add', 'remove', 'add-directory'):
            with self.subTest(change=change):
                esp = self.machine.esp
                with self.assertRaisesRegex(shared.Stopped, 'STOP'):
                    with shared.session():
                        shared.open_writes()
                        if change == 'modify':
                            (esp / 'EFI/Microsoft/Boot/BCD').write_bytes(b'changed')
                        elif change == 'add':
                            (esp / 'EFI/BOOT/new.efi').write_bytes(b'x')
                        elif change == 'remove':
                            (esp / 'EFI/BOOT/BOOTX64.EFI').unlink()
                        else:
                            (esp / 'EFI/Linux').mkdir()
                self.assertNotIn('rw', self.machine.options)
                self.assertTrue(shared.stop_record().is_file())
                self.machine.calls.clear()
                self.assertRefused(shared.STOPPED, shared.check)
                self.assertFalse(any(call[:3] == ['mount', '-o', 'remount,rw'] for call in self.machine.calls))
                shared.stop_record().unlink()
                # Restore the fixture for the next change.
                (esp / 'EFI/Microsoft/Boot/BCD').write_bytes(b'bcd')
                (esp / 'EFI/BOOT/BOOTX64.EFI').write_bytes(b'MZ fallback')
                for extra in (esp / 'EFI/BOOT/new.efi', esp / 'EFI/Linux'):
                    if extra.is_dir():
                        extra.rmdir()
                    elif extra.exists():
                        extra.unlink()

    def test_emaki_files_in_any_case_are_not_windows_files(self):
        esp = self.machine.esp
        before = shared.foreign_files(esp)
        self.assertIn('EFI/Microsoft/Boot/BCD', before)
        self.assertIn('EFI/BOOT/', before)
        self.assertFalse(any(name.casefold().startswith('efi/emaki') for name in before))
        (esp / 'EFI/Emaki/loader-x.efi').write_bytes(b'new')
        self.assertEqual(shared.foreign_files(esp), before)
        # Another vendor directory whose name merely starts with Emaki is Windows-side.
        (esp / 'EFI/EmakiOld').mkdir()
        self.assertIn('EFI/EmakiOld/', shared.foreign_files(esp))

    def test_unverifiable_partition_contents_refuse(self):
        (self.machine.esp / 'EFI/BOOT/link').symlink_to('BOOTX64.EFI')
        self.assertRefused(shared.UNCHECKED, shared.foreign_files, self.machine.esp)
        (self.machine.esp / 'EFI/BOOT/link').unlink()
        with patch.object(shared, 'FILE_LIMIT', 2):
            self.assertRefused(shared.UNCHECKED, shared.foreign_files, self.machine.esp)


if __name__ == '__main__':
    unittest.main()
