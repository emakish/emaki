"""Encryption and resume contracts without touching any block devices."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from emaki_installer.arch_backend import DeviceOperations
from emaki_installer.constants import GIB
from emaki_installer.errors import InstallError
from emaki_installer.inventory import memory_size
from emaki_installer.planner import make_plan
from emaki_installer.protocol import Controller
from emaki_installer.render import grub_defaults, mkinitcpio_config, normalize_fstab
from emaki_installer.runtime import Redactor
from emaki_installer.worker import Worker
from support import FakeInventory, RecordingRunner, config, inventory, manual


def kdf(argv):
    """The key-derivation arguments of a cryptsetup command: from --pbkdf up to --key-file."""
    return argv[argv.index('--pbkdf'):argv.index('--key-file')]


class StorageTests(unittest.TestCase):
    def test_choice_is_required_and_separate_password_validated(self):
        for value in (None, '', True, 'invalid'):
            c = config()
            c['encryption'] = value
            with self.assertRaises(InstallError):
                make_plan(c, inventory())
        for value in (None, '', '\n', 'a\0b', 42, 'x' * 1025):
            c = dict(config(), encryption='separate', disk_password=value)
            with self.assertRaises(InstallError):
                make_plan(c, inventory())
        for encryption in ('account', 'separate'):
            c = dict(config(), encryption=encryption)
            if encryption == 'account':
                c['user']['password'] = '\u00e9'
            else:
                c['disk_password'] = '\u00e9'
            with self.assertRaisesRegex(InstallError, 'English'):
                make_plan(c, inventory())

    def test_layout_matrix_and_review_never_contain_passwords(self):
        for fs in ('btrfs', 'ext4'):
            for mode in ('erase', 'manual'):
                for encryption in ('none', 'account', 'separate'):
                    for hibernation in (True, False):
                        c = config(fs=fs) if mode == 'erase' else manual(fs=fs)
                        c.update(encryption=encryption, hibernation=hibernation)
                        if encryption == 'separate':
                            c['disk_password'] = 'separate-secret'
                        plan = make_plan(c, inventory(partitions=mode == 'manual'))
                        self.assertEqual(plan.swap_bytes, 4 * GIB if hibernation else 0)
                        self.assertEqual(plan.root.subvolumes.get('@swap'), '/swap' if hibernation and fs == 'btrfs' else None)
                        self.assertFalse(next(p for p in plan.partitions if p.esp).mapper)
                        self.assertNotIn(c['user']['password'], repr(plan))
                        self.assertNotIn('separate-secret', repr(plan))
                        self.assertEqual(any(w['code'] == 'unencrypted_memory' for w in plan.warnings),
                                         hibernation and encryption == 'none')

    def test_manual_preserved_root_cannot_be_encrypted(self):
        with self.assertRaisesRegex(InstallError, 'formatting'):
            make_plan(dict(manual(format=False), encryption='account'), inventory(partitions=True))

    def test_swap_requires_known_ram_and_space(self):
        c = dict(config(), hibernation=True)
        for ram in (0, None, -4096, 1025, 32 * GIB):
            with self.assertRaises(InstallError):
                make_plan(c, dict(inventory(), memory_bytes=ram))

    def test_reserved_swap_mount_cannot_shadow_root(self):
        c = dict(manual(), hibernation=True)
        c['mounts'].append(dict(c['mounts'][1], mountpoint='/swap', subvolume='@other'))
        with self.assertRaises(InstallError):
            make_plan(c, inventory(partitions=True))

    def test_hooks_keep_busybox_encrypt_resume_filesystems_overlay_order(self):
        text = mkinitcpio_config(True, True, True)
        hooks = text.split('HOOKS=(')[1].split(')')[0].split()
        for before, after in [('udev', 'encrypt'), ('keyboard', 'encrypt'), ('block', 'encrypt'),
                              ('encrypt', 'resume'), ('resume', 'filesystems'),
                              ('filesystems', 'grub-btrfs-overlayfs')]:
            self.assertLess(hooks.index(before), hooks.index(after))
        self.assertNotIn('systemd', hooks)
        self.assertIn('umask 0077', text)
        self.assertIn('FILES=(/etc/cryptsetup-keys.d/emaki-root.key)', text)
        self.assertNotIn('resume', mkinitcpio_config(False))
        grub = grub_defaults(False, 'luks-uuid', 'root-uuid', 123)
        for word in ('cryptdevice=UUID=luks-uuid:emaki-root', 'resume=UUID=root-uuid',
                     'resume_offset=123', 'GRUB_ENABLE_CRYPTODISK=y', 'argon2',
                     'cryptkey=rootfs:/etc/cryptsetup-keys.d/emaki-root.key'):
            self.assertIn(word, grub)

    def test_cryptsetup_secret_stdin_and_owned_mapping_cleanup(self):
        c = dict(config(), encryption='separate', disk_password='separate-secret')
        plan = make_plan(c, inventory())
        plan.root.path = '/dev/test2'
        runner = RecordingRunner()
        runner.responses[('cryptsetup', 'luksUUID', '/dev/test2')] = 'luks-uuid\n'
        ops = DeviceOperations(plan, runner)
        ops.format(SimpleNamespace(value='btrfs'), Path('/dev/test2'))
        crypt = [(cmd, kw) for cmd, kw in runner.commands if cmd[0] == 'cryptsetup' and cmd[1] != 'luksUUID']
        for cmd, kw in crypt:
            self.assertNotIn('separate-secret', ' '.join(cmd))
            self.assertEqual(kw['input'], 'separate-secret')
            self.assertTrue(kw['secret'])
        # A fixed pass count, not a time calibration: GRUB's wait must not depend on the load
        # while installing. Retain the slot's memory and single lane; the keyfile slot is separate.
        self.assertEqual(kdf(next(cmd for cmd, _ in crypt if cmd[1] == 'luksFormat')),
                         ['--pbkdf', 'argon2id', '--pbkdf-memory', '65536', '--pbkdf-parallel', '1',
                          '--pbkdf-force-iterations', '5'])
        self.assertIn(['mkfs.btrfs', '-f', '-K', '/dev/mapper/emaki-root'], [c for c, _ in runner.commands])
        ops.close()
        ops.close()
        self.assertEqual(sum(c == ['cryptsetup', 'close', 'emaki-root'] for c, _ in runner.commands), 1)

    def test_failed_format_can_close_mapping_but_failed_open_cannot(self):
        for fail in ('mkfs.btrfs', 'open'):
            plan = make_plan(dict(config(), encryption='account'), inventory())
            plan.root.path = '/dev/test2'
            runner = RecordingRunner()
            runner.responses[('cryptsetup', 'luksUUID', '/dev/test2')] = 'luks-uuid'
            runner.fail = lambda c: fail in c
            ops = DeviceOperations(plan, runner)
            with self.assertRaises(InstallError):
                ops.format(SimpleNamespace(value='btrfs'), Path('/dev/test2'))
            ops.close()
            self.assertEqual(any('close' in c for c, _ in runner.commands), fail == 'mkfs.btrfs')

    def test_lsblk_mapper_row_is_not_mistaken_for_partition(self):
        plan = make_plan(dict(config(), encryption='account'), inventory())
        plan.root.path = '/dev/test2'
        plan.root.mapper = '/dev/mapper/emaki-root'
        runner = RecordingRunner()
        runner.responses[('lsblk', '-J', '-b', '-o', 'PATH,PARTN,PARTUUID,UUID', '/dev/test2')] = json.dumps({
            'blockdevices': [{'path': plan.root.mapper, 'uuid': 'fs', 'partuuid': None},
                             {'path': plan.root.path, 'uuid': 'luks', 'partuuid': 'partition', 'partn': 2}]})
        runner.responses[('blkid', '-s', 'UUID', '-o', 'value', plan.root.mapper)] = 'fs\n'
        result = DeviceOperations(plan, runner).fetch_part_info(Path(plan.root.path))
        self.assertEqual((result.uuid, result.partuuid), ('fs', 'partition'))

    def test_protocol_redacts_separate_password_and_forgets_expired_plan(self):
        controller = Controller(FakeInventory(), Mock())
        c = dict(config(), encryption='separate', disk_password='separate-secret')
        reply = controller.handle({'type': 'plan', 'id': 'test', 'config': c})
        self.assertNotIn('separate-secret', json.dumps(reply))
        self.assertEqual(controller.redactor.text('separate-secret'), '[REDACTED]')
        plan = controller.pending['plan']
        controller.invalidate()
        self.assertEqual(plan.config['disk_password'], '')
        self.assertEqual(plan.config['user']['password'], '')

    def test_bootloader_embeds_private_key_and_resume_without_replacing_contract(self):
        from test_worker import BootAndSnapshotTests
        fixture = BootAndSnapshotTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        worker = fixture.worker
        worker.plan = make_plan(dict(config(), encryption='separate',
                                    disk_password='separate-secret', hibernation=True), inventory())
        worker.plan.root.path = '/dev/test2'
        worker.plan.root.mapper = '/dev/mapper/emaki-root'
        worker.plan.root.luks_uuid = fixture.UUID
        worker.plan.root.uuid = 'uuid-2'
        fixture.boot_files()
        fixture.grub_install_leftovers()
        with patch.object(worker, 'create_swap', return_value=12345):
            fixture.run_bootloader()
        key = worker.files.path('/etc/cryptsetup-keys.d/emaki-root.key')
        self.assertEqual(key.stat().st_size, 64)
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        self.assertEqual(key.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(worker.files.path('/boot').stat().st_mode & 0o777, 0o700)
        calls = worker.runner.commands
        add_key = next((cmd, kw) for cmd, kw in calls if 'luksAddKey' in cmd)
        # 64 random bytes need no slow KDF; only the passphrase slot keeps Argon2id.
        self.assertEqual(kdf(add_key[0]), ['--pbkdf', 'pbkdf2', '--pbkdf-force-iterations', '1000'])
        self.assertTrue(add_key[1]['secret'])
        self.assertEqual(add_key[1]['input'], 'separate-secret')
        self.assertNotIn('separate-secret', ' '.join(add_key[0]))
        self.assertEqual(worker.plan.config['disk_password'], '')
        self.assertEqual(sum('grub-install' in cmd for cmd, _ in calls), 2)
        self.assertEqual(sum('grub-mkimage' in cmd for cmd, _ in calls), 1)
        self.assertIn('resume_offset=12345', worker.files.read('/etc/default/grub'))
        self.assertIn('subvol=/@swap', worker.files.read('/etc/fstab'))
        self.assertIn('/swap/swapfile\tnone\tswap\tdefaults,pri=10', worker.files.read('/etc/fstab'))

    def test_swap_offsets_use_filesystem_specific_units_and_preserve_existing_files(self):
        for fs in ('btrfs', 'ext4'):
            with tempfile.TemporaryDirectory() as temp:
                worker = Worker(None, None, Redactor(), Mock(), Mock(), target=Path(temp))
                worker.plan = make_plan(dict(config(fs=fs), hibernation=True), inventory())
                worker.runner = RecordingRunner()
                original = worker.runner.run

                def run(argv, **kw):
                    original(argv, **kw)
                    if 'mkswapfile' in argv or 'fallocate' in argv:
                        with Path(argv[-1]).open('wb') as stream:
                            stream.truncate(worker.plan.swap_bytes)
                    if 'map-swapfile' in argv:
                        return '12345\n'
                    if 'filefrag' in argv:
                        return '   0:        0..   1000:      12345..     13345:   1001:\n'
                    return ''

                worker.runner.run = run
                # The swapfile is sparse here; the free-space check must not depend on the
                # build machine's temporary directory (a 6 GB build VM has a 2.9 GiB /tmp).
                real_statvfs = os.statvfs
                roomy = lambda p: os.statvfs_result((4096, 4096, 1 << 24, 1 << 24, 1 << 24, 0, 0, 0, 0, 255)) \
                    if str(p).startswith(temp) else real_statvfs(p)
                with patch('emaki_installer.worker.os.statvfs', roomy):
                    self.assertEqual(worker.create_swap(), 12345)
                path = Path(temp) / 'swap/swapfile'
                self.assertEqual(path.stat().st_size, 4 * GIB)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                with self.assertRaisesRegex(InstallError, 'already exists'):
                    worker.create_swap()
                self.assertFalse(any(c[0] == 'swapon' for c, _ in worker.runner.commands))

    def test_fstab_includes_swap_subvolume_but_not_a_snapshot_of_swap(self):
        plan = make_plan(dict(config(), hibernation=True), inventory())
        text = '\n'.join(f'UUID=test {mp} {part.fs} ' + ','.join(part.options +
                         (['subvol=/' + sv] if sv else [])) + ' 0 0'
                         for part, mp, sv in plan.mounts)
        self.assertIn('subvol=/@swap', normalize_fstab(text, plan))
        self.assertEqual(plan.root.subvolumes['@'], '/')

    def test_ram_uses_installed_dimms_including_extended_sizes(self):
        with tempfile.TemporaryDirectory() as temp:
            table = Path(temp) / 'DMI'
            def record(size, extended=0):
                data = bytearray(32)
                data[0:2] = bytes([17, 32])
                data[12:14] = size.to_bytes(2, 'little')
                data[28:32] = extended.to_bytes(4, 'little')
                return data + b'\0\0'
            table.write_bytes(record(8192) + record(0x7fff, 32768) + record(0))
            self.assertEqual(memory_size(table), 40 * GIB)
            table.write_bytes(record(0xffff))
            self.assertEqual(memory_size(table), 0)
            table.write_bytes(b'\x11\x20')
            self.assertEqual(memory_size(table), 0)


if __name__ == '__main__':
    unittest.main()
