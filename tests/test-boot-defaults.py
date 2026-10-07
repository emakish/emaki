#!/usr/bin/python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Scratch-root upgrades from the released installer's boot configuration."""
import importlib.util
import itertools
import sys
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('boot_defaults', REPO / 'upkeep/emaki_boot_defaults.py')
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)
LEGACY_HOOKS = ('base udev autodetect microcode modconf kms keyboard keymap consolefont block '
                'encrypt resume filesystems grub-btrfs-overlayfs emaki-snapshot-fstab')
CURRENT_HOOKS = LEGACY_HOOKS.replace(' resume ', ' emaki-resume ')
LEGACY_CONFIG = ('umask 0077\nMODULES=()\nBINARIES=()\n'
                 'FILES=(/etc/cryptsetup-keys.d/emaki-root.key)\nHOOKS=(' + LEGACY_HOOKS + ')\n')


def released_config(btrfs, encrypted, hibernation):
    hooks = next(line for line, choices in boot.LEGACY_CHOICES.items()
                 if choices == (btrfs, encrypted, hibernation))
    files = '/etc/cryptsetup-keys.d/emaki-root.key' if encrypted else ''
    return (('umask 0077\n' if encrypted else '')
            + f'MODULES=()\nBINARIES=()\nFILES=({files})\n' + hooks)


class BootDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.main = self.write('etc/mkinitcpio.conf', LEGACY_CONFIG)
        for kernel in ('linux', 'linux-lts'):
            self.write(f'etc/mkinitcpio.d/{kernel}.preset',
                       'ALL_config="/etc/mkinitcpio.conf"\n'
                       f'ALL_kver="/boot/vmlinuz-{kernel}"\n'
                       "PRESETS=('default' 'fallback')\n"
                       f'default_image="/boot/initramfs-{kernel}.img"\n'
                       f'fallback_image="/boot/initramfs-{kernel}-fallback.img"\n'
                       'fallback_options="-S autodetect"\n')
            self.write(f'usr/share/emaki/boot/{kernel}.preset',
                       (REPO / f'boot/defaults/{kernel}.preset').read_text())
        self.write('usr/share/emaki/boot/grub-btrfs.conf',
                   (REPO / 'boot/defaults/grub-btrfs.conf').read_text())
        self.grub = self.write('etc/default/grub-btrfs/config',
                               '# Upstream comment\nGRUB_BTRFS_LIMIT="50"\n'
                               'GRUB_BTRFS_SUBMENUNAME="Emaki snapshots"\n')
        self.machine = self.write('etc/default/grub',
                                  'GRUB_CMDLINE_LINUX="cryptdevice=UUID=abc:emaki-root '
                                  'resume=UUID=def resume_offset=12345"\n')

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def hooks(self):
        result = subprocess.run(['bash', '-c', '. "$1"; printf "%s\\n" "${HOOKS[*]}"',
                                 'boot-test', str(self.main)],
                                text=True, capture_output=True, check=True)
        return result.stdout.strip()

    def test_main_configuration_is_complete_without_dropins(self):
        boot.apply(self.root)
        result = subprocess.run(['bash', '-c', '. "$1"; printf "%s\\n" "${HOOKS[*]}"',
                                 'boot-test', str(self.main)],
                                text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.strip(), CURRENT_HOOKS)

    def test_installer_configuration_is_complete_without_dropins(self):
        sys.path.insert(0, str(REPO / 'installer'))
        from emaki_installer.render import mkinitcpio_machine_config
        self.main.write_text(mkinitcpio_machine_config(True, True, True))
        result = subprocess.run(['bash', '-c', '. "$1"; printf "%s\\n" "${HOOKS[*]}"',
                                 'boot-test', str(self.main)],
                                text=True, capture_output=True, check=True)
        self.assertEqual(result.stdout.strip(), CURRENT_HOOKS)

    def test_fresh_machine_variants_expand_native_policy(self):
        sys.path.insert(0, str(REPO / 'installer'))
        from emaki_installer.render import mkinitcpio_machine_config
        for btrfs, encrypted, hibernation in itertools.product((False, True), repeat=3):
            self.main.write_text(mkinitcpio_machine_config(btrfs, encrypted, hibernation))
            hooks = self.hooks().split()
            self.assertEqual('encrypt' in hooks, encrypted)
            self.assertEqual('emaki-resume' in hooks, hibernation)
            self.assertEqual('emaki-snapshot-fstab' in hooks, btrfs)
            self.assertEqual('fsck' in hooks, not btrfs)

    def test_all_installer_choices_are_recorded_and_stable(self):
        sys.path.insert(0, str(REPO / 'installer'))
        from emaki_installer.render import mkinitcpio_machine_config
        for choices in itertools.product((False, True), repeat=3):
            btrfs, encrypted, hibernation = map(int, choices)
            marker = f'# Emaki btrfs={btrfs} encrypted={encrypted} hibernation={hibernation} '
            for render in (released_config, mkinitcpio_machine_config):
                with self.subTest(choices=choices, renderer=render.__name__):
                    self.main.write_text(render(*choices))
                    boot.apply(self.root)
                    self.assertIn(marker + 'HOOKS=(', self.main.read_text())
                    before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
                    boot.apply(self.root)
                    self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)

    def test_recorded_choices_survive_later_hook_names(self):
        previous = boot.hook_line
        def renamed(*choices):
            return previous(*choices).replace(' encrypt', ' sd-encrypt').replace(' emaki-resume', ' next-resume')
        for choices in itertools.product((False, True), repeat=3):
            with self.subTest(choices=choices):
                self.main.write_text(released_config(*choices))
                boot.apply(self.root)
                with patch.object(boot, 'hook_line', renamed):
                    boot.apply(self.root)
                    self.assertEqual(self.hooks(), renamed(*choices)[7:-2])
                    before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
                    boot.apply(self.root)
                    self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)
                # The legacy recognition table must not follow current rendering policy.
                self.main.write_text(released_config(*choices))
                with patch.object(boot, 'hook_line', renamed):
                    boot.apply(self.root)
                    self.assertEqual(self.hooks(), renamed(*choices)[7:-2])

    def test_older_installer_lines_and_early_markers_use_exact_table(self):
        base = 'base udev autodetect microcode modconf kms keyboard keymap consolefont block'
        for btrfs in (False, True):
            old = 'HOOKS=(' + base + ' filesystems' + (' grub-btrfs-overlayfs' if btrfs else '') + ' fsck)\n'
            for marker in ('', '# Emaki ' + old):
                with self.subTest(btrfs=btrfs, marker=bool(marker)):
                    updated = boot.machine_hooks(old + marker)
                    self.assertTrue(updated.startswith(boot.hook_line(btrfs, False, False)))
                    self.assertIn(f'# Emaki btrfs={int(btrfs)} encrypted=0 hibernation=0 ', updated)
                    self.assertEqual(boot.machine_hooks(updated), updated)

    def test_unknown_legacy_lines_and_invalid_markers_stay_unchanged(self):
        old = 'HOOKS=(' + LEGACY_HOOKS + ')\n'
        recorded = boot.machine_hooks(old)
        variants = (old.replace(' encrypt', ' sd-encrypt'),
                    recorded.replace('encrypted=1', 'encrypted=2'),
                    recorded + recorded.splitlines(keepends=True)[-1],
                    old + '# Emaki HOOKS=(base custom)\n')
        for text in variants:
            with self.subTest(text=text):
                self.assertEqual(boot.machine_hooks(text), text)

    def test_upgrade_adopts_defaults_preserving_machine_values_and_is_idempotent(self):
        machine = self.machine.read_bytes()
        self.assertTrue(boot.apply(self.root))
        self.assertIn('# Emaki btrfs=1 encrypted=1 hibernation=1 HOOKS=(' + CURRENT_HOOKS + ')', self.main.read_text())
        self.assertIn('umask 0077\n', self.main.read_text())
        self.assertIn('FILES=(/etc/cryptsetup-keys.d/emaki-root.key)', self.main.read_text())
        self.assertEqual(self.hooks(), CURRENT_HOOKS)
        self.assertIn(boot.GRUB_SOURCE, self.grub.read_text())
        self.assertIn('GRUB_BTRFS_LIMIT="50"', self.grub.read_text())
        before = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        self.assertFalse(boot.apply(self.root))
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        self.assertEqual(self.machine.read_bytes(), machine)
        # A later revision can update exactly the managed line, preserving other lines.
        previous = boot.hook_line
        self.addCleanup(setattr, boot, 'hook_line', previous)
        boot.hook_line = lambda *choices: previous(*choices).replace('modconf kms', 'modconf testhook kms')
        self.assertTrue(boot.apply(self.root))
        self.assertIn('testhook', self.hooks())
        self.assertIn('FILES=(/etc/cryptsetup-keys.d/emaki-root.key)', self.main.read_text())

    def test_local_hook_and_title_edits_stay_byte_for_byte(self):
        self.main.write_text(LEGACY_CONFIG.replace(' microcode', ' custom'))
        self.grub.write_text('GRUB_BTRFS_SUBMENUNAME="My snapshots"\n')
        before = {p: p.read_bytes() for p in (self.main, self.grub, self.machine)}
        self.assertFalse(boot.apply(self.root))
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertEqual(self.hooks(), LEGACY_HOOKS.replace(' microcode', ' custom'))

    def test_other_local_lines_and_file_mode_survive_hook_migration(self):
        self.main.write_text(LEGACY_CONFIG.replace('MODULES=()', 'MODULES=(nvme)') + '# Local note\n')
        self.main.chmod(0o600)
        self.assertTrue(boot.apply(self.root))
        self.assertIn('MODULES=(nvme)', self.main.read_text())
        self.assertIn('# Local note\n', self.main.read_text())
        self.assertEqual(self.main.stat().st_mode & 0o777, 0o600)

    def test_custom_preset_prevents_partial_adoption(self):
        preset = self.root / 'etc/mkinitcpio.d/linux-lts.preset'
        preset.write_text(preset.read_text() + 'default_options="-S kms"\n')
        boot.apply(self.root)
        self.assertEqual(self.main.read_text(), LEGACY_CONFIG)
        self.assertIn('ALL_config=', (self.root / 'etc/mkinitcpio.d/linux.preset').read_text())
        self.assertIn('default_options=', preset.read_text())

    def test_local_hooks_after_adoption_bypass_package_defaults(self):
        boot.apply(self.root)
        self.main.write_text(self.main.read_text().replace('\nHOOKS=(' + CURRENT_HOOKS + ')', '\nHOOKS=(base custom)'))
        self.assertEqual(self.hooks(), 'base custom')

    def test_appended_hook_survives_later_migration(self):
        boot.apply(self.root)
        self.main.write_text(self.main.read_text().replace('\nHOOKS=(' + CURRENT_HOOKS + ')',
                             '\nHOOKS=(' + CURRENT_HOOKS + ' shutdown)'))
        expected = self.main.read_bytes()
        self.assertFalse(boot.apply(self.root))
        self.assertEqual(self.main.read_bytes(), expected)
        self.assertEqual(self.hooks(), CURRENT_HOOKS + ' shutdown')

    def test_020_hibernation_migration_queues_both_kernels_once(self):
        presets = [self.root / f'etc/mkinitcpio.d/{kernel}.preset' for kernel in boot.KERNELS]
        before = {p: p.read_bytes() for p in (*presets, self.machine)}
        self.assertIn(' resume ', self.main.read_text())
        self.assertTrue(boot.apply(self.root))
        self.assertEqual(self.hooks(), CURRENT_HOOKS)
        self.assertIn('# Emaki btrfs=1 encrypted=1 hibernation=1 HOOKS=('
                      + CURRENT_HOOKS + ')\n', self.main.read_text())
        self.assertIn('FILES=(/etc/cryptsetup-keys.d/emaki-root.key)', self.main.read_text())
        pending = self.root / 'var/lib/emaki/migrations/initramfs-pending'
        self.assertEqual(pending.read_text(), 'linux\nlinux-lts\n')
        self.assertFalse(boot.apply(self.root))
        self.assertEqual(pending.read_text(), 'linux\nlinux-lts\n')
        pending.unlink()
        self.assertFalse(boot.apply(self.root))
        self.assertFalse(pending.exists())
        self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_released_nonhibernation_presets_stay_without_rebuild(self):
        for btrfs, encrypted in itertools.product((False, True), repeat=2):
            with self.subTest(btrfs=btrfs, encrypted=encrypted):
                self.main.write_text(released_config(btrfs, encrypted, False))
                boot.apply(self.root)
                self.assertFalse((self.root / 'var/lib/emaki/migrations/initramfs-pending').exists())
                self.assertFalse(boot.apply(self.root))

    def test_released_presets_do_not_depend_on_packaged_presets(self):
        for kernel in boot.KERNELS:
            (self.root / f'usr/share/emaki/boot/{kernel}.preset').unlink()
        boot.apply(self.root)
        self.assertIn('# Emaki btrfs=1 encrypted=1 hibernation=1 ', self.main.read_text())
        self.assertEqual((self.root / 'var/lib/emaki/migrations/initramfs-pending').read_text(),
                         'linux\nlinux-lts\n')

    def test_replacement_and_removal_use_native_fallbacks(self):
        for choices in ((True, False, True), (True, True, True), (False, False, False)):
            for removing in (False, True):
                with self.subTest(choices=choices, removing=removing):
                    self.main.write_text(released_config(*choices))
                    boot.apply(self.root)
                    boot.release(self.root, removing=removing)
                    hooks = self.hooks().split()
                    self.assertNotIn('emaki-resume', hooks)
                    self.assertEqual('resume' in hooks, choices[2])
                    self.assertEqual('encrypt' in hooks, choices[1])
                    self.assertEqual('emaki-snapshot-fstab' in hooks, choices[0] and not removing)
                    before = self.main.read_bytes()
                    boot.release(self.root, removing=removing)
                    self.assertEqual(self.main.read_bytes(), before)
                    if not removing:
                        boot.apply(self.root)
                        self.assertEqual(self.hooks(), boot.hook_line(*choices)[7:-2])

    def test_release_restores_sourced_presets(self):
        for kernel in boot.KERNELS:
            preset = self.root / f'etc/mkinitcpio.d/{kernel}.preset'
            preset.write_text(f'. /usr/share/emaki/boot/{kernel}.preset\n')
        boot.release(self.root)
        for kernel in boot.KERNELS:
            self.assertEqual((self.root / f'etc/mkinitcpio.d/{kernel}.preset').read_text(),
                             boot.legacy_preset(kernel))

    def test_upgrade_reclaims_sourced_presets_and_dropins(self):
        boot.apply(self.root)
        for kernel in boot.KERNELS:
            self.write(f'etc/mkinitcpio.d/{kernel}.preset',
                       f'. /usr/share/emaki/boot/{kernel}.preset\n')
        boot.release(self.root)
        boot.release(self.root)
        boot.apply(self.root)
        for kernel in boot.KERNELS:
            preset = self.root / f'etc/mkinitcpio.d/{kernel}.preset'
            self.assertEqual(preset.read_text(), f'. /usr/share/emaki/boot/{kernel}.preset\n')

    def test_reinstall_reclaims_removed_btrfs_hooks(self):
        for encrypted in (False, True):
            with self.subTest(encrypted=encrypted):
                self.main.write_text(released_config(True, encrypted, True))
                boot.apply(self.root)
                before = self.main.read_text()
                boot.release(self.root, removing=True)
                self.assertNotIn('emaki-snapshot-fstab', self.hooks())
                self.assertNotIn('emaki-resume', self.hooks())
                boot.apply(self.root)
                self.assertEqual(self.main.read_text(), before)

    def test_release_and_reclaim_preserve_locally_reordered_hooks(self):
        for removing in (False, True):
            with self.subTest(removing=removing):
                self.main.write_text(released_config(True, True, True))
                boot.apply(self.root)
                self.main.write_text(''.join(
                    line.replace('keymap consolefont', 'consolefont keymap')
                    if line.startswith('HOOKS=') else line
                    for line in self.main.read_text().splitlines(keepends=True)))
                before = self.main.read_text()
                boot.release(self.root, removing=removing)
                self.assertIn('consolefont keymap', self.hooks())
                self.assertIn(' resume ', self.hooks())
                self.assertNotIn('emaki-resume', self.hooks())
                self.assertEqual('emaki-snapshot-fstab' in self.hooks(), not removing)
                boot.apply(self.root)
                self.assertEqual(self.main.read_text(), before)

    def test_custom_preset_does_not_block_reclaiming_released_hooks(self):
        boot.apply(self.root)
        before = self.main.read_text()
        preset = self.root / 'etc/mkinitcpio.d/linux.preset'
        preset.write_text(preset.read_text() + '# Local comment\n')
        custom = preset.read_text()
        boot.release(self.root)
        boot.apply(self.root)
        self.assertEqual(self.main.read_text(), before)
        self.assertEqual(preset.read_text(), custom)

    def test_release_only_changes_complete_array_tokens(self):
        original = ('HOOKS=("emaki-resume" \'emaki-snapshot-fstab\' custom-emaki-resume)'
                    ' # emaki-resume stays a comment\n')
        self.main.write_text(original)
        boot.release(self.root, removing=True)
        self.assertEqual(self.main.read_text(), 'HOOKS=("resume" custom-emaki-resume)'
                         ' # emaki-resume stays a comment\n')
        boot.apply(self.root)
        self.assertEqual(self.main.read_text(), original)

    def test_changes_to_released_hooks_and_presets_are_not_reclaimed(self):
        boot.apply(self.root)
        preset = self.write('etc/mkinitcpio.d/linux.preset',
                            '. /usr/share/emaki/boot/linux.preset\n')
        boot.release(self.root)
        self.main.write_text(self.main.read_text().replace(' resume ', ' custom-resume '))
        preset.write_text(preset.read_text() + '# Local change\n')
        before = {path: path.read_bytes() for path in (self.main, preset)}
        boot.apply(self.root)
        self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_old_grub_btrfs_source_becomes_optional(self):
        self.grub.write_text('. /usr/share/emaki/boot/grub-btrfs.conf\n')
        boot.apply(self.root)
        self.assertEqual(self.grub.read_text(), boot.GRUB_SOURCE)
        self.assertFalse(boot.apply(self.root))
        source = self.grub.read_text().replace('/usr/share/emaki/', str(self.root / 'absent') + '/')
        subprocess.run(['bash', '-eu', '-c', source], check=True)

    def test_symlinks_and_hardlinks_are_not_replaced(self):
        for kind in ('symlink', 'hardlink'):
            with self.subTest(kind=kind):
                other = self.root / ('other-' + kind)
                other.write_text(LEGACY_CONFIG)
                self.main.unlink()
                if kind == 'symlink':
                    self.main.symlink_to(other)
                else:
                    os.link(other, self.main)
                boot.apply(self.root)
                self.assertEqual(other.read_text(), LEGACY_CONFIG)
                self.assertEqual(self.main.read_text(), LEGACY_CONFIG)

    def test_packaged_presets_enable_dropins_and_keep_images_under_boot(self):
        for kernel in ('linux', 'linux-lts'):
            result = subprocess.run(['bash', '-c',
                                     'ALL_config=old; . "$1"; '
                                     'printf "%s\\n" "${ALL_config-unset}" "$ALL_kver" '
                                     '"$default_image" "$fallback_image" "${PRESETS[*]}"',
                                     'boot-test', str(REPO / f'boot/defaults/{kernel}.preset')],
                                    text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.splitlines(),
                             ['unset', f'/boot/vmlinuz-{kernel}', f'/boot/initramfs-{kernel}.img',
                              f'/boot/initramfs-{kernel}-fallback.img', 'default fallback'])



if __name__ == '__main__':
    unittest.main()
