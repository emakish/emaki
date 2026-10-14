# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""New installs select package defaults without losing their machine choices."""
import itertools
from pathlib import Path
import unittest

from emaki_installer import boot
from emaki_installer.errors import InstallError

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


class WindowsBootNextTests(unittest.TestCase):
    PARTUUID = '14e269ee-0b88-441e-86f3-09b058e1ad76'
    UUID = '1234-ABCD'
    MENU = """### BEGIN /etc/grub.d/30_os-prober ###
menuentry 'Windows Boot Manager (on /dev/sda1)' --class windows $menuentry_id_option 'osprober-efi-1234-ABCD' {
    insmod part_gpt
    insmod fat
    if [ x$feature_platform_search_hint = xy ]; then
        search --no-floppy --fs-uuid --set=root --hint-efi=hd0,gpt1 1234-ABCD
    else
        search --no-floppy --fs-uuid --set=root 1234-ABCD
    fi
    chainloader /EFI/Microsoft/Boot/bootmgfw.efi
}
### END /etc/grub.d/30_os-prober ###
"""

    def listing(self, number='000A', *, wrapper=False, prefix='', suffix='', label='Windows Boot Manager'):
        loader = boot.WINDOWS_LOADER.replace('/', chr(92))
        path = f'File({loader})' if wrapper else loader
        return (f'BootOrder: {number}\nBoot{number}* {label}\t{prefix}'
                f'HD(1,GPT,{self.PARTUUID},0x800,0x32000)/{path}{suffix}\n')

    def render(self, listing=None, menu=None):
        return boot.windows_bootnext_menu(self.MENU if menu is None else menu, self.PARTUUID,
                                         self.UUID, self.listing() if listing is None else listing)

    def test_resolves_both_verbose_formats_and_keeps_title_id(self):
        for wrapper in (False, True):
            with self.subTest(wrapper=wrapper):
                text = self.render(self.listing('0bCd', wrapper=wrapper, prefix='PciRoot(0x0)/'))
                self.assertIn('if bootnext 0BCD; then\n        reboot\n', text)
                self.assertIn("menuentry 'Windows Boot Manager (on /dev/sda1)'", text)
                self.assertIn("'osprober-efi-1234-ABCD'", text)
                self.assertNotIn('chainloader', text)
                self.assertLess(text.index('if insmod efibootnext'), text.index('if insmod reboot'))
                self.assertLess(text.index('if insmod reboot'), text.index('if bootnext'))
                self.assertTrue(boot.top_level_entries(text).complete)

    def test_description_is_not_identity_and_optional_windows_data_is_allowed(self):
        text = self.render(self.listing(wrapper=True, label='Different name', suffix='WINDOWS....'))
        self.assertIn('if bootnext 000A; then', text)

    def test_plain_chainload_only_for_complete_inventory_without_windows_entry(self):
        listing = 'BootOrder: 0001\nBoot0001* Shell\tFvFile(1234)\n'
        self.assertEqual(self.render(listing), self.MENU)

    def test_missing_or_truncated_inventory_refuses(self):
        valid = self.listing()
        for listing in ('', valid.replace('BootOrder: 000A', 'BootOrder: 000A,000B'),
                        valid.replace('BootOrder: 000A', 'BootOrder: 000A,000A'),
                        valid + 'Boot000B* Broken\n', valid + valid,
                        valid.replace('BootOrder: 000A\n', '')):
            with self.subTest(listing=listing), self.assertRaises(InstallError):
                self.render(listing)

    def test_ambiguous_or_incomplete_windows_path_refuses(self):
        valid = self.listing()
        for listing in (valid + valid.splitlines(keepends=True)[1].replace('000A', '000B'),
                        self.listing(suffix='.old'), self.listing(wrapper=True, suffix='/File(other.efi)'),
                        valid.replace('HD(1,GPT,', 'HD(1,MBR,')):
            with self.subTest(listing=listing), self.assertRaises(InstallError):
                self.render(listing)

    def test_foreign_or_ambiguous_menu_target_refuses_even_for_fallback(self):
        absent = 'BootOrder: 0001\nBoot0001* Shell\tFvFile(1234)\n'
        for listing in (self.listing(), absent):
            for menu in ('', self.MENU.replace(self.UUID, 'FFFF-0000'),
                         self.MENU.replace('bootmgfw.efi', 'bootmgfw.efi.old'),
                         self.MENU + self.MENU,
                         self.MENU.replace('chainloader /EFI', 'chainloader ($root)/EFI')):
                with self.subTest(menu=menu, listing=listing), self.assertRaises(InstallError):
                    self.render(listing, menu)

    def test_inactive_firmware_entry_keeps_chainload(self):
        self.assertEqual(self.render(self.listing().replace('000A*', '000A ')), self.MENU)

    def test_foreign_windows_entries_are_skipped_inside_and_outside_boot_order(self):
        foreign = self.listing('000B').replace(self.PARTUUID, 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')
        self.assertEqual(self.render(foreign), self.MENU)
        for ordered in (False, True):
            with self.subTest(ordered=ordered):
                listing = self.listing() + foreign.splitlines(keepends=True)[1]
                if ordered:
                    listing = listing.replace('BootOrder: 000A', 'BootOrder: 000A,000B')
                self.assertIn('bootnext 000A', self.render(listing))

    def test_empty_windows_label_is_accepted(self):
        self.assertIn('bootnext 000A', self.render(self.listing(label='')))

    def test_verbatim_mac_inventories_resolve_windows(self):
        partuuid = '33333333-3333-4333-8333-333333333333'
        for name in ('mac-before', 'mac-created', 'mac-booted'):
            with self.subTest(name=name):
                listing = (REPO / 'tests/fixtures/firmware' / (name + '.txt')).read_text()
                self.assertEqual(boot.windows_firmware_entry(listing, partuuid), '0002')
                text = boot.windows_bootnext_menu(self.MENU, partuuid, self.UUID, listing)
                self.assertIn('bootnext 0002', text)

    def test_failed_bootnext_reports_error_without_reboot_or_chainload(self):
        text = self.render()
        failure = text.split('      else\n', 1)[1].split('      fi\n', 1)[0]
        self.assertIn('Windows startup could not be selected.', failure)
        self.assertNotIn('reboot', failure)
        self.assertNotIn('chainloader', failure)

    def test_raw_optional_data_needs_separate_proof_of_filename_boundary(self):
        data = '57494e444f5753000100000088000000'
        listing = self.listing(suffix=data)
        with self.assertRaises(InstallError):
            self.render(listing)
        listing += '    data: ' + ' '.join(data[i:i + 2] for i in range(0, len(data), 2)) + '\n'
        self.assertIn('bootnext 000A', self.render(listing))
        with self.assertRaises(InstallError):
            self.render(listing.replace('bootmgfw.efi', 'bootmgfw.efideadbeef'))

    def test_case_insensitive_identity_and_path(self):
        self.assertIn('bootnext 000A', self.render(self.listing().replace(self.PARTUUID, self.PARTUUID.upper()).replace('Microsoft', 'MICROSOFT')))


if __name__ == '__main__':
    unittest.main()
