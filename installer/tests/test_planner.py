import copy
import unittest

from emaki_installer.constants import BTRFS_OPTIONS, GIB, MIB, SUBVOLUMES
from emaki_installer.errors import Code, InstallError
from emaki_installer.planner import erase_partitions, make_plan, shrink_bounds, validate_config
from support import config, inventory, manual


class PlannerTests(unittest.TestCase):
    def rejects(self, c, inv, code):
        with self.assertRaises(InstallError) as raised:
            make_plan(c, inv)
        self.assertEqual(raised.exception.code, code)

    def test_erase_alignment_and_backup_gpt(self):
        for size in (24 * GIB, 256 * GIB, 2 * 1024 * GIB, 24 * GIB + 512):
            for fs in ('btrfs', 'ext4'):
                with self.subTest(size=size, fs=fs):
                    esp, root = erase_partitions(size, fs)
                    self.assertEqual((esp.start, esp.size, esp.mountpoint), (MIB, GIB, '/efi'))
                    self.assertEqual(esp.start + esp.size, root.start)
                    self.assertLessEqual(root.start + root.size, size - MIB)
                    self.assertLess(size - root.start - root.size, 2 * MIB)
                    for p in (esp, root):
                        self.assertEqual(p.start % MIB, 0)
                        self.assertEqual(p.size % MIB, 0)
                    self.assertEqual(root.subvolumes, SUBVOLUMES if fs == 'btrfs' else {})

    def test_erase_layout_keeps_boot_and_pacman_in_root(self):
        p = make_plan(config(), inventory())
        self.assertEqual(p.root.options, BTRFS_OPTIONS)
        self.assertEqual({m for _, m, _ in p.mounts}, {'/', '/efi', '/home', '/var/log', '/var/cache/pacman/pkg', '/.snapshots'})
        self.assertNotIn('a-secret-for-tests', repr(p))

    def test_disk_refusals(self):
        cases = [('uefi', False, Code.UEFI_REQUIRED), ('_boot_medium_known', False, Code.UNSAFE_DISK)]
        for key, value, code in cases:
            inv = inventory()
            inv[key] = value
            self.rejects(config(), inv, code)
        for key, value, code in [('is_boot_medium', True, Code.BOOT_MEDIUM),
                                 ('_busy', True, Code.DISK_BUSY), ('_read_only', True, Code.UNSAFE_DISK),
                                 ('size_bytes', 24 * GIB - 1, Code.DISK_TOO_SMALL)]:
            inv = inventory()
            inv['disks'][0][key] = value
            self.rejects(config(), inv, code)

    def test_manual_format_flags_and_implicit_subvolumes(self):
        p = make_plan(manual(), inventory(partitions=True))
        by_path = {r.path: r for r in p.partitions}
        self.assertFalse(by_path['/dev/vda1'].format)
        self.assertTrue(by_path['/dev/vda2'].format)
        self.assertEqual(p.root.subvolumes, SUBVOLUMES)
        self.assertEqual(p.partitions[0], p.root)

    def test_manual_preserve_root_never_becomes_format(self):
        p = make_plan(manual(format=False), inventory(partitions=True))
        self.assertFalse(p.root.format)

    def test_manual_missing_or_duplicate_root_esp(self):
        for change in ('missing_root', 'missing_esp', 'duplicate_root'):
            c = manual()
            if change == 'missing_root':
                c['mounts'][1]['mountpoint'] = '/home'
            elif change == 'missing_esp':
                c['mounts'][0]['mountpoint'] = '/home'
            else:
                c['mounts'].append(copy.deepcopy(c['mounts'][1]))
            self.rejects(c, inventory(partitions=True), Code.MANUAL_LAYOUT)

    def test_manual_esp_free_not_total_size(self):
        for free in (None, 32 * MIB - 1):
            inv = inventory(partitions=True)
            inv['disks'][0]['partitions'][0]['_esp_free_bytes'] = free
            self.rejects(manual(), inv, Code.ESP_SPACE)
        inv['disks'][0]['partitions'][0]['_esp_free_bytes'] = 32 * MIB
        make_plan(manual(), inv)

    def test_manual_reject_type_change_without_format(self):
        self.rejects(manual('ext4', False), inventory(partitions=True), Code.MANUAL_LAYOUT)

    def test_manual_reject_unknown_geometry_and_readonly(self):
        for key, value in [('_geometry_known', False), ('_read_only', True)]:
            inv = inventory(partitions=True)
            inv['disks'][0]['partitions'][1][key] = value
            self.rejects(manual(), inv, Code.MANUAL_LAYOUT)

    def test_manual_reject_unsupported_mounts_and_injection(self):
        for mp in ('/boot', '/boot/efi', '/usr', '/var', '/var/lib/pacman', '/../etc', '/home/../../etc', '/home//x'):
            c = manual()
            c['mounts'][1]['mountpoint'] = mp
            self.rejects(c, inventory(partitions=True), Code.MANUAL_LAYOUT)

    def test_explicit_root_subvolumes(self):
        c = manual()
        c['mounts'] = c['mounts'][:1] + [
            {'partition_id': '/dev/vda2', 'mountpoint': mp, 'fs': 'btrfs', 'format': False, 'subvolume': 'custom' + name}
            for name, mp in SUBVOLUMES.items()]
        p = make_plan(c, inventory(partitions=True))
        self.assertEqual(set(p.root.subvolumes), {'custom' + name for name in SUBVOLUMES})
        c['mounts'][-1]['format'] = True
        self.rejects(c, inventory(partitions=True), Code.MANUAL_LAYOUT)

    def test_root_and_data_mount_order(self):
        p = make_plan(manual(), inventory(partitions=True))
        self.assertEqual(p.mounts[0][1], '/')
        self.assertLess([m for _, m, _ in p.mounts].index('/'), [m for _, m, _ in p.mounts].index('/var/log'))

    def test_multidevice_btrfs_preservation_refused(self):
        inv = inventory(partitions=True)
        inv['disks'][0]['partitions'][1]['_btrfs_devices'] = 2
        self.rejects(manual(format=False), inv, Code.MANUAL_LAYOUT)

    def test_alongside_bounds_and_explicit_deferral(self):
        shrink_bounds(41 * GIB, 40 * GIB, 80 * GIB)
        for new in (40 * GIB, 56 * GIB + 1, 80 * GIB, True):
            with self.assertRaises(InstallError):
                shrink_bounds(new, 40 * GIB, 80 * GIB)
        c = config('alongside')
        c['shrink_bytes'] = 41 * GIB
        self.rejects(c, inventory(), Code.UNSUPPORTED_MODE)

    def test_config_rejects_command_and_password_line_injection(self):
        for key, value in [('hostname', 'host\nBAD=1'), ('timezone', '../../etc'),
                           ('layouts', ['us"}']), ('online_update', 'false'), ('scale_guess', float('nan')),
                           ('repo_server', 'http://user:secret@example.com/repo'),
                           ('repo_server', 'https://example.com\nSigLevel=Never')]:
            c = config()
            c[key] = value
            with self.assertRaises(InstallError):
                validate_config(c)
        c = config()
        c['user']['password'] = 'secret\nroot:injected'
        with self.assertRaises(InstallError):
            validate_config(c)


if __name__ == '__main__':
    unittest.main()
