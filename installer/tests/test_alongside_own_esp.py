# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Next to Windows Emaki creates its own ESP in the freed space and never writes Windows' ESP (0.9.0).

With Secure Boot the A/B boot slots did not fit Windows' usual 100 MB ESP; the same layout is
used with Secure Boot on and off. No real block device is opened, mounted or written.
"""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from emaki_installer import inventory as ntfs
from emaki_installer import planner
from emaki_installer.arch_backend import DeviceOperations, resize_partition
from emaki_installer.constants import GIB, MIB
from emaki_installer.errors import Code, InstallError
from emaki_installer.planner import make_plan
from emaki_installer.render import normalize_fstab
from emaki_installer.runtime import Redactor
from emaki_installer.worker import Worker
from support import FakeInventory, RecordingRunner, config, inventory
from test_alongside import AlongsideOnTestCase, OK, gpt_text, windows
import test_worker

WINDOWS_ESP_PARTUUID = '33333333-3333-4333-8333-333333333333'  # Windows Boot Manager's entry in MAC_LISTING
WINDOWS_ESP_UUID = '1234-ABCD'  # WINDOWS_CHAINLOAD searches this FAT volume id
EMAKI_ESP_PARTUUID = '22222222-2222-4222-8222-222222222222'
EMAKI_ESP_UUID = 'ABCD-0001'
LSBLK = ('lsblk', '--tree', '-J', '-b', '-o', 'PATH,TYPE,PARTN,START,SIZE,PTTYPE,PARTUUID,PARTTYPE', '/dev/vda')


def small_esp_windows(secure_boot, free=MIB):
    """Windows' usual 100 MiB ESP, by default with about 1 MiB free; Windows' NTFS frees 40 GiB.
    The tests of later steps leave 64 MiB free, so the code before 0.9.0 fails them for their own reasons."""
    c, inv = windows()
    esp = inv['disks'][0]['partitions'][0]
    esp.update(size_bytes=100 * MIB, _esp_free_bytes=free, _partuuid=WINDOWS_ESP_PARTUUID, uuid=WINDOWS_ESP_UUID)
    esp['_gpt'].update(guid=WINDOWS_ESP_PARTUUID, end=(esp['start_bytes'] + esp['size_bytes']) // 512 - 1)
    if secure_boot:
        inv['hardware'] = {'secure_boot': True, 'secure_boot_cas': 'both'}
    return c, inv


class OwnEspPlanTests(AlongsideOnTestCase):
    """(a) plan review admits a full 100 MiB Windows ESP: Emaki's ESP is new, 1 GiB, in the freed extent."""

    def test_small_windows_esp_is_admitted_and_never_planned(self):
        for secure_boot in (False, True):
            for fs in ('btrfs', 'ext4'):
                with self.subTest(secure_boot=secure_boot, fs=fs):
                    c, inv = small_esp_windows(secure_boot)
                    c['fs'] = fs
                    plan = make_plan(c, inv)
                    disk = inv['disks'][0]
                    windows_part = disk['partitions'][1]
                    freed_start = windows_part['start_bytes'] + 56 * GIB
                    esp, root = plan.partitions
                    self.assertEqual((esp.path, esp.number, esp.start, esp.size, esp.fs, esp.format, esp.mountpoint, esp.esp),
                                     (None, 3, freed_start, GIB, 'vfat', True, '/efi', True))
                    self.assertEqual((root.path, root.number, root.start, root.size, root.fs, root.format),
                                     (None, 4, freed_start + GIB, 40 * GIB - GIB, fs, True))
                    self.assertIs(plan.root, root)
                    self.assertEqual(planner.OWN_ESP_BYTES, GIB)
                    # Windows' ESP is no planned partition: never mounted, never in fstab.
                    self.assertFalse(any(p.path == disk['partitions'][0]['path'] or p.start == disk['partitions'][0]['start_bytes']
                                         for p in plan.partitions))
                    self.assertEqual([p.mountpoint for p, mp, _ in plan.mounts if mp == '/efi'], ['/efi'])
                    self.assertIs(next(p for p, mp, _ in plan.mounts if mp == '/efi'), esp)
                    # Labelled EMAKI with Secure Boot on and off: it tells Emaki's ESP from Windows'.
                    self.assertEqual(plan.esp_label, 'EMAKI')
                    self.assertIn("Create Emaki's own startup partition (1 GiB) and root in the freed space; "
                                  "Windows' startup partition is not changed.", plan.summary)
                    self.assertIn('Windows keeps 60.13 GB, Emaki gets 42.95 GB; back up your files first.', plan.summary)

    def test_two_free_gpt_entries_are_required(self):
        c, inv = small_esp_windows(False)
        disk = inv['disks'][0]
        # Entries 3..128 taken by tiny partitions far after the freed extent: only one stays free.
        end = disk['partitions'][1]['start_bytes'] + disk['partitions'][1]['size_bytes']
        for number in range(3, 128):
            disk['partitions'].append({'id': f'/dev/vda{number}', 'path': f'/dev/vda{number}', 'number': number,
                                       'uuid': None, 'fs': None, 'size_bytes': 512, 'esp': False,
                                       'start_bytes': end + MIB + number * MIB})
        with self.assertRaisesRegex(InstallError, 'two free GPT partition entries') as caught:
            make_plan(c, inv)
        self.assertEqual(caught.exception.code, Code.SHRINK_BOUNDS)

    def test_inventory_offers_alongside_next_to_a_full_windows_esp(self):
        # Its free space is not needed, known or not; its Microsoft loader still is.
        for free in (MIB, None):
            with self.subTest(free=free):
                _, inv = small_esp_windows(True, free=free)
                disk = inv['disks'][0]
                part = disk['partitions'][1]
                runner = RecordingRunner()
                runner.responses[('sgdisk', '--verify', disk['path'])] = 'No problems found.'
                for p in disk['partitions']:
                    runner.responses[('sgdisk', f'--info={p["number"]}', disk['path'])] = gpt_text(p['_gpt'])
                runner.responses[('ntfsresize', '--info', '--no-action', part['path'])] = OK
                with tempfile.TemporaryDirectory() as temp, \
                        patch.object(ntfs, 'bitlocker_signature', return_value=False), \
                        patch.object(ntfs.tempfile, 'mkdtemp', return_value=str(Path(temp) / 'mount')), \
                        patch.object(ntfs, 'windows_system', return_value=True):
                    (Path(temp) / 'mount').mkdir()
                    ntfs.Inventory(runner)._alongside(disk, True)
                self.assertNotIn('reason', part['shrink'])
                self.assertEqual(part['shrink']['min_bytes'], 20 * GIB)
        disk['partitions'][0]['_efi_loaders'] = {}
        runner = RecordingRunner()
        with patch.object(ntfs, 'bitlocker_signature', return_value=False):
            self.assertIn('No verified Microsoft EFI loader', ntfs.Inventory(runner)._ntfs_shrink(disk, part)['reason'])
        self.assertEqual(runner.commands, [])


class OwnEspRootSizeTests(AlongsideOnTestCase):
    """(b) the root-size rule applies to the freed space less Emaki's ESP."""

    def test_hibernation_root_must_fit_beside_the_own_esp(self):
        c, inv = small_esp_windows(False, free=64 * MIB)
        c.update(shrink_bytes=32 * GIB, hibernation=True)
        # 32 GiB freed >= 20 GiB + 12 GiB RAM, but 31 GiB of root is not.
        inv['memory_bytes'] = 12 * GIB
        with self.assertRaisesRegex(InstallError, 'Root needs 20 GiB plus the RAM-sized hibernation file') as caught:
            make_plan(c, inv)
        self.assertEqual(caught.exception.code, Code.ROOT_TOO_SMALL)
        inv['memory_bytes'] = 11 * GIB
        plan = make_plan(c, inv)
        self.assertEqual(plan.root.size, 31 * GIB)
        self.assertEqual(plan.swap_bytes, 11 * GIB)
        # The person's choice is unchanged: Windows keeps the same size.
        self.assertIn('Windows keeps 68.72 GB, Emaki gets 34.36 GB; back up your files first.', plan.summary)


class OwnEspWorkerTests(AlongsideOnTestCase):
    def setUp(self):
        super().setUp()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.worker = Worker(None, None, Redactor(), lambda *a, **kw: None, lambda line: None, target=self.root)
        self.c, self.inv = small_esp_windows(False, free=64 * MIB)
        self.worker.plan = make_plan(self.c, self.inv)
        for p in self.worker.plan.partitions:
            p.path = '/dev/vda' + str(p.number)
        self.worker.runner = RecordingRunner()
        self.esp = next(p for p in self.worker.plan.partitions if p.esp)
        self.esp.partuuid, self.esp.uuid = EMAKI_ESP_PARTUUID, EMAKI_ESP_UUID
        self.device = self.root.parent / (self.root.name + '-windows-esp')
        self.device.write_bytes(b'FAT32 Windows ESP' + bytes(3 * MIB))
        self.addCleanup(self.device.unlink, missing_ok=True)
        self.windows_esp = next(p for p in self.worker.plan.disk['partitions'] if p['esp'])
        self.windows_esp['path'] = str(self.device)

    def test_marker_records_both_esps_and_no_hibernate_dropins(self):
        """(c) the contract the boot code reads; Windows never touches Emaki's own ESP."""
        self.windows_esp['_partuuid'] = WINDOWS_ESP_PARTUUID.upper()
        self.esp.partuuid = EMAKI_ESP_PARTUUID.upper()
        self.worker.record_alongside_boot()
        path = '/var/lib/emaki/alongside-boot.json'
        text = self.worker.files.read(path)
        self.assertTrue(text.endswith('}\n'))
        self.assertEqual(json.loads(text), {'version': 1, 'esp_partuuid': WINDOWS_ESP_PARTUUID,
                                            'emaki_esp_partuuid': EMAKI_ESP_PARTUUID,
                                            'windows_esp_uuid': WINDOWS_ESP_UUID})
        self.assertEqual(list(json.loads(text)), ['version', 'esp_partuuid', 'emaki_esp_partuuid', 'windows_esp_uuid'])
        self.assertEqual(self.worker.files.path(path).stat().st_mode & 0o777, 0o644)
        for mode in ('hibernate', 'hybrid-sleep', 'suspend-then-hibernate'):
            self.assertFalse(self.worker.files.path(
                f'/etc/systemd/system/systemd-{mode}.service.d/50-emaki-shared-esp.conf').exists())
        self.assertTrue(any('emaki-boot-order.service' in str(command) for command, _ in self.worker.runner.commands))
        self.assertFalse(hasattr(self.worker, 'protect_shared_esp'))

    def test_secure_boot_paths_use_emakis_own_esp(self):
        # Slots, firmware entries and the kept-ESP room check: the planned (own) ESP, never Windows'.
        self.worker.plan.secure_boot = 'both'
        self.windows_esp['_partuuid'] = WINDOWS_ESP_PARTUUID
        self.assertEqual(self.worker.esp_partuuid(), EMAKI_ESP_PARTUUID)
        self.assertIsNone(self.worker.check_secure_boot_esp([], {'disks': []}))
        self.assertEqual(self.worker.runner.commands, [])

    def test_marker_refuses_an_unidentified_esp(self):
        for change in ({'_partuuid': None}, {'uuid': 'not-a-fat-id'}):
            with self.subTest(change=change):
                saved = dict(self.windows_esp)
                self.windows_esp.update(change)
                with self.assertRaises(InstallError) as caught:
                    self.worker.record_alongside_boot()
                self.assertEqual(caught.exception.code, Code.BOOT_VERIFY)
                self.assertFalse(self.worker.files.path('/var/lib/emaki/alongside-boot.json').exists())
                self.windows_esp.clear()
                self.windows_esp.update(saved)

    def test_windows_esp_bytes_are_proven_unchanged(self):
        """(d) sha256 of Windows' whole ESP before the first disk write and after the bootloader."""
        worker = Worker(None, FakeInventory(self.inv), Redactor(), Mock(), Mock(), target=self.root)
        worker.plan, worker.backend = self.worker.plan, Mock()
        order = []
        worker.backend.prepare.side_effect = lambda: order.append(worker.windows_esp_sha256)
        worker.prepare_disk()
        expected = hashlib.sha256(self.device.read_bytes()).hexdigest()
        self.assertEqual(order, [expected])
        worker.check_windows_esp()
        data = bytearray(self.device.read_bytes())
        data[2 * MIB] ^= 1
        self.device.write_bytes(bytes(data))
        with self.assertRaises(InstallError) as caught:
            worker.check_windows_esp()
        self.assertEqual(caught.exception.code, Code.BOOT_VERIFY)
        self.assertFalse(caught.exception.retryable)
        self.assertIn("Windows' startup partition changed during the installation", caught.exception.message)
        self.assertIn('Do not retry installation', caught.exception.message)
        self.device.unlink()
        with self.assertRaisesRegex(InstallError, "Windows' startup partition could not be read"):
            worker.check_windows_esp()

    def test_unreadable_windows_esp_stops_before_the_first_write(self):
        worker = Worker(None, FakeInventory(self.inv), Redactor(), Mock(), Mock(), target=self.root)
        worker.plan, worker.backend = self.worker.plan, Mock()
        self.device.unlink()
        with self.assertRaisesRegex(InstallError, "Windows' startup partition could not be read"):
            worker.prepare_disk()
        worker.backend.prepare.assert_not_called()
        self.assertFalse(worker.disk_changed)

    def boot(self):
        test_worker.BootAndSnapshotTests.boot_files(self)
        self.esp.partuuid, self.esp.uuid = EMAKI_ESP_PARTUUID, EMAKI_ESP_UUID
        fstab = [f'UUID={p.uuid or f"uuid-{p.number}"} {mp} {p.fs} '
                 f'{",".join(p.options + (["subvol=/" + sv] if sv else []))} 0 0'
                 for p, mp, sv in self.worker.plan.mounts]
        self.worker.runner.responses[('genfstab', '-U', '-f', str(self.root), str(self.root))] = '\n'.join(fstab)
        f = self.worker.files
        f.write('/boot/grub/grub.cfg', f.read('/boot/grub/grub.cfg') + test_worker.WINDOWS_CHAINLOAD)
        self.worker.windows_esp_sha256 = hashlib.sha256(self.device.read_bytes()).hexdigest()

    def test_bootloader_checks_windows_esp_after_grub_config_and_writes_removable_copies(self):
        """(d) in the bootloader phase; EFI/BOOT is Emaki's on its own ESP, like the erase layout."""
        self.boot()
        self.worker.bootloader()
        commands = [c for c, _ in self.worker.runner.commands]
        self.assertTrue(any('--removable' in c for c in commands))
        self.assertTrue(self.worker.files.path('/etc/fstab').exists())
        original = self.worker.runner.chroot

        def chroot(command, *args, **kwargs):
            if 'grub-mkconfig' in command:  # os-prober mounts Windows' ESP while GRUB's menu is made
                self.device.write_bytes(b'changed' + self.device.read_bytes())
            return original(command, *args, **kwargs)
        self.worker.runner.chroot = chroot
        self.worker.files.path('/etc/fstab').unlink()
        self.boot()
        with self.assertRaisesRegex(InstallError, "Windows' startup partition changed") as caught:
            self.worker.bootloader()
        self.assertEqual(caught.exception.code, Code.BOOT_VERIFY)
        self.assertFalse(self.worker.files.path('/etc/fstab').exists())

    def test_a_new_esp_that_is_not_fat32_stops_before_any_boot_file(self):
        """R9E0: a formatted ESP must really be FAT32 (>= 65,525 + 16 data clusters)."""
        from types import SimpleNamespace
        from unittest.mock import patch
        import emaki_installer.worker as worker_module
        for clusters, ok in ((65540, False), (65541, True), (262000, True)):
            with self.subTest(clusters=clusters), patch.object(
                    worker_module.os, 'statvfs', return_value=SimpleNamespace(f_blocks=clusters)):
                self.assertEqual(worker_module.fat32_esp('/efi'), ok)
        with patch.object(worker_module.os, 'statvfs', side_effect=OSError('gone')):
            self.assertFalse(worker_module.fat32_esp('/efi'))
        self.boot()
        with patch.object(worker_module, 'fat32_esp', return_value=False), \
                self.assertRaisesRegex(InstallError, 'did not get a valid FAT32') as caught:
            self.worker.bootloader()
        self.assertEqual(caught.exception.code, Code.BOOT_VERIFY)
        self.assertEqual(self.worker.runner.commands, [])

    def test_unreadable_new_esp_stops_before_any_boot_write(self):
        import emaki_installer.worker as worker_module
        self.boot()
        with patch.object(worker_module.os, 'statvfs', side_effect=OSError('gone')), \
                patch.object(self.worker.files, 'write') as write, \
                self.assertRaisesRegex(InstallError, 'did not get a valid FAT32') as caught:
            self.worker.bootloader()
        self.assertEqual(caught.exception.code, Code.BOOT_VERIFY)
        self.assertEqual(self.worker.runner.commands, [])
        write.assert_not_called()

    def test_grub_windows_entry_uses_windows_esp_identity(self):
        """(e) GRUB's Windows entry restarts through the firmware entry on Windows' ESP."""
        self.boot()
        self.windows_esp.update(_partuuid=WINDOWS_ESP_PARTUUID, uuid=WINDOWS_ESP_UUID)
        self.worker.grub_config()
        menu = self.worker.files.read('/boot/grub/grub.cfg')
        self.assertIn('bootnext 0002', menu)
        self.assertNotIn('chainloader /EFI/Microsoft/Boot/bootmgfw.efi', menu)

    def test_fstab_mounts_own_esp_like_erase_and_never_windows_esp(self):
        """(f) the own ESP at /efi read-write with fsck pass 2; Windows' ESP has no line."""
        plan = self.worker.plan
        for p in plan.partitions:
            p.uuid = EMAKI_ESP_UUID if p.esp else f'uuid-root-{p.number}'
        rows = [f'UUID={p.uuid}\t{mp}\t{p.fs}\t{",".join(p.options + (["subvol=/" + sv] if sv else []))}\t0\t'
                f'{"2" if p.esp else "0"}' for p, mp, sv in plan.mounts]
        efi = next(i for i, row in enumerate(rows) if '\t/efi\t' in row)
        rows[efi] = rows[efi].replace('umask=0077', 'rw,relatime,fmask=0077,dmask=0077,codepage=437,'
                                      'iocharset=ascii,shortname=mixed,utf8,errors=remount-ro')
        fstab = normalize_fstab('\n'.join(rows) + '\n', plan)
        line = next(row.split('\t') for row in fstab.splitlines() if '\t/efi\t' in row)
        self.assertEqual(line[0], 'UUID=' + EMAKI_ESP_UUID)
        self.assertIn('rw', line[3].split(','))
        self.assertNotIn('ro', line[3].split(','))
        self.assertEqual(line[5], '2')
        self.assertNotIn(WINDOWS_ESP_UUID, fstab)
        self.assertNotIn(self.windows_esp['uuid'], fstab)
        with self.assertRaises(InstallError):
            normalize_fstab(fstab + f'UUID={WINDOWS_ESP_UUID}\t/boot/efi\tvfat\tro\t0\t0\n', plan)


class OwnEspBackendTests(AlongsideOnTestCase):
    """(g) the backend creates the ESP (GPT type EF00) and formats it FAT32, labelled like erase."""

    def plan(self, secure_boot=False):
        self.c, self.inv = small_esp_windows(secure_boot, free=64 * MIB)
        return make_plan(self.c, self.inv)

    def runner(self, plan):
        runner = RecordingRunner()
        meta = plan.disk['partitions'][1]['_gpt']
        esp = next(p for p in plan.partitions if p.esp)
        runner.responses[('sgdisk', '--info=2', '/dev/vda')] = gpt_text(meta)
        runner.responses[('sgdisk', '--info=1', '/dev/vda')] = gpt_text(plan.disk['partitions'][0]['_gpt'])
        runner.responses[('sgdisk', '--verify', '/dev/vda')] = 'No problems found.'
        runner.responses[('ntfsresize', '--no-action', '--size', str(56 * GIB), '/dev/vda2')] = \
            'The read-only test run ended successfully.'
        runner.responses[('ntfsresize', '--size', str(56 * GIB), '/dev/vda2')] = \
            "Successfully resized NTFS on device '/dev/vda2'."
        original = runner.run

        def run(argv, **kwargs):
            result = original(argv, **kwargs)
            if argv[:2] == ['sgdisk', '--delete=2']:
                runner.responses[('sgdisk', '--info=2', '/dev/vda')] = gpt_text(dict(meta, end=esp.start // 512 - 1))
            return result
        runner.run = run
        return runner

    @patch.object(ntfs, 'verify_resized_ntfs')
    def test_resize_creates_esp_then_root_in_the_freed_extent(self, verify):
        plan = self.plan()
        esp, root = plan.partitions
        runner = self.runner(plan)
        resize_partition(plan, runner, Mock())
        commands = [cmd for cmd, _ in runner.commands]
        create = [cmd for cmd in commands if cmd[0] == 'sgdisk' and any(arg.startswith(f'--new={esp.number}:') for arg in cmd)]
        self.assertEqual(len(create), 1)
        create = create[0]
        self.assertIn(f'--new={esp.number}:{esp.start // 512}:{(esp.start + esp.size) // 512 - 1}', create)
        self.assertIn(f'--typecode={esp.number}:EF00', create)
        self.assertIn(f'--new={root.number}:{root.start // 512}:{(root.start + root.size) // 512 - 1}', create)
        self.assertIn(f'--typecode={root.number}:8300', create)
        self.assertEqual(create[-1], '/dev/vda')
        # Windows ends where Emaki's ESP starts.
        self.assertIn(['sgdisk', '--delete=2', f'--new=2:{plan.disk["partitions"][1]["_gpt"]["start"]}:{esp.start // 512 - 1}',
                       f'--partition-guid=2:{plan.disk["partitions"][1]["_partuuid"]}',
                       f'--typecode=2:{plan.disk["partitions"][1]["_parttype"]}',
                       "--change-name=2:Windows owner's data", '--attributes=2:=:0x8000000000000000', '/dev/vda'], commands)
        self.assertEqual(commands[-1], ['sgdisk', '--verify', '/dev/vda'])
        verify.assert_called_once_with(runner, '/dev/vda2', 56 * GIB)

    def layout(self, plan, esp_type=ntfs.ESP_GUID):
        esp, root = plan.partitions
        children = [dict(path=p['path'], partn=p['number'], start=p['start_bytes'] // 512,
                         size=p['size_bytes'] - (40 * GIB if p['number'] == 2 else 0),
                         partuuid=p['_partuuid'], parttype=p['_parttype']) for p in plan.disk['partitions']]
        children.append(dict(path='/dev/vda3', partn=esp.number, start=esp.start // 512, size=esp.size,
                             partuuid=EMAKI_ESP_PARTUUID, parttype=esp_type))
        children.append(dict(path='/dev/vda4', partn=root.number, start=root.start // 512, size=root.size,
                             partuuid='44444444-4444-4444-8444-444444444444',
                             parttype='0fc63daf-8483-4772-8e79-3d69d8477de4'))
        return json.dumps({'blockdevices': [{'pttype': 'gpt', 'children': children}]})

    def test_readback_requires_the_esp_type_and_sets_both_paths(self):
        plan = self.plan()
        esp, root = plan.partitions
        runner = RecordingRunner()
        runner.responses[LSBLK] = self.layout(plan)
        parts = [SimpleNamespace(partn=esp.number, dev_path=None), SimpleNamespace(partn=root.number, dev_path=None)]
        DeviceOperations(plan, runner).verify_alongside_layout(SimpleNamespace(partitions=parts))
        self.assertEqual((esp.path, esp.partuuid, root.path), ('/dev/vda3', EMAKI_ESP_PARTUUID, '/dev/vda4'))
        self.assertEqual([p.dev_path for p in parts], [Path('/dev/vda3'), Path('/dev/vda4')])
        plan = self.plan()
        runner.responses[LSBLK] = self.layout(plan, esp_type='0fc63daf-8483-4772-8e79-3d69d8477de4')
        with self.assertRaisesRegex(InstallError, 'STOP'):
            DeviceOperations(plan, runner).verify_alongside_layout(SimpleNamespace(partitions=parts))

    def test_format_esp_fat32_labelled_like_erase_and_never_windows(self):
        for secure_boot in (False, True):
            with self.subTest(secure_boot=secure_boot):
                plan = self.plan(secure_boot)
                esp, root = plan.partitions
                esp.path, root.path = '/dev/vda3', '/dev/vda4'
                runner = RecordingRunner()
                ops = DeviceOperations(plan, runner)
                ops.format(SimpleNamespace(value='fat32'), '/dev/vda3')
                erase = make_plan(config(), dict(inventory(), hardware=self.inv.get('hardware', {})))
                self.assertEqual(runner.commands, [(['mkfs.fat', '-F', '32', '-n', 'EMAKI', '/dev/vda3'], {})])
                for fs, path in (('fat32', '/dev/vda1'), ('fat32', '/dev/vda2'), ('btrfs', '/dev/vda3'),
                                 ('fat32', '/dev/vda4')):
                    with self.assertRaises(InstallError):
                        ops.format(SimpleNamespace(value=fs), path)
                self.assertEqual(len(runner.commands), 1)


if __name__ == '__main__':
    unittest.main()
