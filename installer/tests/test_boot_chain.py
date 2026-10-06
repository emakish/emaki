"""Boot chain of an installed system: GRUB defaults, initramfs hooks and where the disk key lives."""
import re
import unittest

from emaki_installer.constants import GIB, MIB
from emaki_installer.errors import InstallError
from emaki_installer.planner import make_plan
from emaki_installer.render import grub_defaults, mkinitcpio_config, mkinitcpio_preset
from support import config, inventory, manual


def hooks(text):
    return text.split('HOOKS=(')[1].split(')')[0].split()


def encrypted(c):
    return dict(c, encryption='separate', disk_password='separate-secret')


class BootChainTests(unittest.TestCase):
    def test_disk_key_never_leaves_the_encrypted_root(self):
        # The key file is inside every initramfs and named on the kernel command line.
        # That is safe only while the images live under /boot inside the encrypted root:
        # no mount at /boot, the ESP (the one unencrypted mount) at /efi, image paths
        # under /boot/, and a root-only umask for the images.
        for fs in ('btrfs', 'ext4'):
            for mode, c, parts in (('erase', config(fs=fs), False), ('manual', manual(fs=fs), True)):
                with self.subTest(fs=fs, mode=mode):
                    plan = make_plan(encrypted(c), inventory(partitions=parts))
                    self.assertTrue(plan.encrypted)
                    for _, mp, _ in plan.mounts:
                        self.assertFalse(mp == '/boot' or mp.startswith('/boot/'), mp)
                    self.assertEqual([p.mountpoint for p in plan.partitions if p.esp], ['/efi'])
                    for kernel in ('linux', 'linux-lts'):
                        paths = re.findall(r'^(?:ALL_kver|default_image|fallback_image)="([^"]*)"$',
                                           mkinitcpio_preset(kernel), re.M)
                        self.assertEqual(len(paths), 3)
                        for path in paths:
                            self.assertTrue(path.startswith('/boot/'), path)
                    text = mkinitcpio_config(plan.btrfs, encrypted=True, hibernation=bool(plan.swap_bytes))
                    self.assertTrue(text.startswith('umask 0077\n'))
                    self.assertIn('FILES=(/etc/cryptsetup-keys.d/emaki-root.key)', text)
                    self.assertIn('cryptkey=rootfs:/etc/cryptsetup-keys.d/emaki-root.key',
                                  grub_defaults(False, 'luks-uuid'))

    def test_manual_layout_refuses_a_separate_boot(self):
        for fs in ('btrfs', 'ext4'):
            with self.subTest(fs=fs):
                data = inventory(partitions=True)
                parts = data['disks'][0]['partitions']
                parts[1]['size_bytes'] = 30 * GIB
                start = parts[1]['start_bytes'] + parts[1]['size_bytes']
                parts.append(dict(parts[1], id='/dev/vda3', path='/dev/vda3', number=3, uuid='uuid-3',
                                  fs='ext4', start_bytes=start, size_bytes=data['disks'][0]['size_bytes'] - start - MIB,
                                  _partuuid='partuuid-3'))
                c = encrypted(manual(fs=fs))
                c['mounts'].append({'partition_id': '/dev/vda3', 'mountpoint': '/boot', 'fs': 'ext4', 'format': True})
                with self.assertRaisesRegex(InstallError, 'must stay in root'):
                    make_plan(c, data)

    def test_initramfs_checks_the_root_only_without_btrfs(self):
        # Once /sbin/fsck is in the image, mkinitcpio's fsck_root() prints its "not
        # configured to be mounted read-write" banner on every read-only snapshot boot,
        # and fsck.btrfs is a stub anyway. An ext4 root keeps its check.
        for encrypted in (False, True):
            for hibernation in (False, True):
                self.assertNotIn('fsck', hooks(mkinitcpio_config(True, encrypted, hibernation)))
                self.assertEqual(hooks(mkinitcpio_config(False, encrypted, hibernation))[-1], 'fsck')

    def test_grub_loads_no_separate_microcode_image(self):
        # The microcode hook puts the CPU's microcode into every initramfs. GRUB's stock
        # early-initrd list would load intel-ucode.img and amd-ucode.img a second time, and
        # grub-btrfs multiplies every snapshot's rows by that list.
        for text in (grub_defaults(), grub_defaults(True, 'luks', 'root', 1)):
            self.assertRegex(text, r'(?m)^GRUB_EARLY_INITRD_LINUX_STOCK=""$')


if __name__ == '__main__':
    unittest.main()
