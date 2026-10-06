"""Alongside safety and ordering; no real block device is opened or mounted."""
import copy
import json
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
                               ('The NTFS partition is hibernated.', 'hibernated'),
                               ('Metadata kept in Windows cache, refused to mount.', 'Fast Startup'),
                               ('BitLocker -FVE-FS-', 'BitLocker'),
                               ('ERROR: failed to read clusters', 'error'),
                               ('Device is mounted read-write.', 'mounted'),
                               ('', 'minimum'), (OK + OK, 'minimum')]:
            with self.subTest(output=output), self.assertRaisesRegex(InstallError, reason):
                ntfs.parse_ntfs_minimum(output + (OK if output and output != OK + OK else ''))

    def test_too_small_and_alignment(self):
        self.assertEqual(ntfs.shrink_offer(20 * GIB, 54 * GIB, MIB)['max_free_bytes'], 32 * GIB)
        for size, start in [(54 * GIB - MIB, MIB), (60 * GIB, MIB + 512), (60 * GIB - 512, MIB)]:
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
                self.assertIn('hibernated', ntfs.hibernation_reason(root))
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
            self.assertIn('BitLocker', ntfs.Inventory(RecordingRunner())._ntfs_shrink(disk, disk['partitions'][1])['reason'])
        disk['partitions'][0]['esp'] = False
        self.assertIn('ESP', ntfs.Inventory(RecordingRunner())._ntfs_shrink(disk, disk['partitions'][1])['reason'])

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


class AlongsideOffTests(unittest.TestCase):
    """The shipped default: the mode is neither probed, offered nor accepted."""

    def setUp(self):
        self.c, self.inv = windows()

    def test_default_switch_is_off(self):
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

    def test_layout_reuses_esp_and_frees_exact_extent_both_filesystems(self):
        for fs in ('btrfs', 'ext4'):
            self.c['fs'] = fs
            plan = self.plan()
            esp, root = plan.partitions
            self.assertFalse(esp.format)
            self.assertEqual(root.size, 40 * GIB)
            self.assertEqual(root.start, self.inv['disks'][0]['partitions'][1]['start_bytes'] + 56 * GIB)
            self.assertEqual(root.number, 3)
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
                        self.assertFalse(plan.partitions[0].format)
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
        self.inv['disks'][0]['partitions'][0]['_esp_free_bytes'] = 32 * MIB - 1
        with self.assertRaisesRegex(InstallError, '32 MiB'):
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
                after = dict(meta, end=(plan.root.start // 512 - 1))
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
        commands = [cmd for cmd, _ in runner.commands]
        self.assertEqual([cmd[0] for cmd in commands], ['sgdisk', 'sgdisk', 'ntfsresize', 'ntfsresize',
                         'sgdisk', 'partprobe', 'udevadm', 'sgdisk', 'sgdisk', 'sgdisk', 'partprobe', 'udevadm', 'sgdisk'])
        self.assertEqual(commands[2], ['ntfsresize', '--no-action', '--size', str(56 * GIB), '/dev/vda2'])
        self.assertEqual(commands[3], ['ntfsresize', '--size', str(56 * GIB), '/dev/vda2'])
        self.assertEqual(runner.commands[3][1], {'input': 'y\n'})
        meta = plan.disk['partitions'][1]['_gpt']
        self.assertEqual(commands[4], ['sgdisk', '--delete=2', f'--new=2:{meta["start"]}:{plan.root.start // 512 - 1}',
                         f'--partition-guid=2:{GUID}', f'--typecode=2:{TYPE}', "--change-name=2:Windows owner's data",
                         '--attributes=2:=:0x8000000000000000', '/dev/vda'])
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

    def test_reprobe_changed_in_prepare_aborts_before_backend(self):
        plan = self.plan()
        worker = Worker(None, FakeInventory(self.inv), Redactor(), Mock(), Mock())
        worker.plan, worker.backend = plan, Mock()
        self.inv['disks'][0]['partitions'][1]['shrink']['min_bytes'] += MIB
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
        children.append(dict(path='/dev/vda3', partn=3, start=plan.root.start // 512,
                             size=plan.root.size, partuuid='new-root-guid'))
        query = ('lsblk', '--tree', '-J', '-b', '-o', 'PATH,TYPE,PARTN,START,SIZE,PTTYPE,PARTUUID,PARTTYPE', '/dev/vda')
        archroot = SimpleNamespace(partn=3, dev_path=None)
        ops = DeviceOperations(plan, runner)
        runner.responses[query] = json.dumps({'blockdevices': [{'pttype': 'gpt', 'children': children}]})
        ops.verify_alongside_layout(SimpleNamespace(partitions=[archroot]))
        self.assertEqual(archroot.dev_path, Path('/dev/vda3'))
        children[0]['partuuid'] = 'changed-esp'
        runner.responses[query] = json.dumps({'blockdevices': [{'pttype': 'gpt', 'children': children}]})
        with self.assertRaisesRegex(InstallError, 'existing partition changed'):
            ops.verify_alongside_layout(SimpleNamespace(partitions=[archroot]))


if __name__ == '__main__':
    unittest.main()
