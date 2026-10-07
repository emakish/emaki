# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""New installs select package defaults without losing their machine choices."""
import itertools
from pathlib import Path
import unittest

from emaki_installer.render import (grub_btrfs_package_config, mkinitcpio_machine_config,
                                    mkinitcpio_package_preset)

REPO = Path(__file__).resolve().parents[2]


class BootDefaultsTests(unittest.TestCase):
    def test_all_machine_variants_consume_packaged_hooks(self):
        for btrfs, encrypted, hibernation in itertools.product((False, True), repeat=3):
            with self.subTest(btrfs=btrfs, encrypted=encrypted, hibernation=hibernation):
                config = mkinitcpio_machine_config(btrfs, encrypted, hibernation)
                hooks = next(line for line in config.splitlines() if line.startswith('HOOKS='))
                self.assertIn(f'# Emaki btrfs={int(btrfs)} encrypted={int(encrypted)} '
                              f'hibernation={int(hibernation)} ' + hooks + '\n', config)
                self.assertNotIn('emaki-defaults', config)
                self.assertEqual(' encrypt' in hooks, encrypted)
                self.assertEqual(' emaki-resume' in hooks, hibernation)
                self.assertEqual('/etc/cryptsetup-keys.d/emaki-root.key' in config, encrypted)
                self.assertEqual(config.startswith('umask 0077\n'), encrypted)
                self.assertEqual('grub-btrfs-overlayfs' in config, btrfs)

    def test_presets_source_package_files(self):
        for kernel in ('linux', 'linux-lts'):
            self.assertEqual(mkinitcpio_package_preset(kernel),
                             f'. /usr/share/emaki/boot/{kernel}.preset\n')

    def test_snapshot_config_preserves_other_options_and_is_idempotent(self):
        old = '# Upstream\nGRUB_BTRFS_LIMIT="50"\nGRUB_BTRFS_SUBMENUNAME="Old"\n'
        text = grub_btrfs_package_config(old)
        self.assertEqual(text, '# Upstream\nGRUB_BTRFS_LIMIT="50"\n'
                               'if [ -r /usr/share/emaki/boot/grub-btrfs.conf ]; then '
                               '. /usr/share/emaki/boot/grub-btrfs.conf; fi\n')
        self.assertEqual(grub_btrfs_package_config(text), text)


if __name__ == '__main__':
    unittest.main()
