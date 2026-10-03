from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from emaki_installer.errors import InstallError
from emaki_installer.planner import make_plan
from emaki_installer.render import (console_keymap, grub_defaults, mkinitcpio_config,
                                    mkinitcpio_preset, niri_config, normalize_fstab, snapper_config,
                                    validate_xkb_layouts, verify_grub, wireless_regdom)
from support import config, inventory


class RenderTests(unittest.TestCase):
    def test_wireless_regdom_from_timezone(self):
        tab = ('# comment\tUS\tAmerica/New_York\n'
               'US\t+404251-0740023\tAmerica/New_York\tEastern (most areas)\n'
               'PT\t+3843-00908\tEurope/Lisbon\tPortugal (mainland)\n'
               'xx\t+0000+00000\tEtc/Bogus\n')
        self.assertIn('\nWIRELESS_REGDOM="US"\n', wireless_regdom('America/New_York', tab))
        self.assertIn('\nWIRELESS_REGDOM="PT"\n', wireless_regdom('Europe/Lisbon', tab))
        for unset in ('UTC', 'Etc/Bogus', 'America/New_Yor'):
            self.assertIsNone(wireless_regdom(unset, tab))

    def test_grub_parameters(self):
        text = grub_defaults()
        for required in ('GRUB_DISTRIBUTOR="Emaki"', 'GRUB_DISABLE_SUBMENU=y', 'GRUB_TIMEOUT=5',
                         'GRUB_TOP_LEVEL="/boot/vmlinuz-linux"', 'GRUB_DISABLE_RECOVERY=true',
                         'GRUB_DISABLE_BOOTNEXT=true', 'GRUB_BACKGROUND=/usr/share/emaki/grub/background.png', 'GRUB_DISABLE_OS_PROBER=true'):
            self.assertIn(required, text)
        self.assertNotIn('GRUB_SAVEDEFAULT', text)
        self.assertNotIn('GRUB_DEFAULT=saved', text)
        self.assertIn('GRUB_DISABLE_OS_PROBER=false', grub_defaults(True))

    def test_grub_validation_does_not_confuse_linux_and_linux_lts(self):
        lts = 'menuentry "Emaki" {\n linux /@/boot/vmlinuz-linux-lts root=UUID=x\n}\n'
        with self.assertRaises(InstallError):
            verify_grub(lts + '# linux /boot/vmlinuz-linux\n')
        verify_grub(lts + ' linux /@/boot/vmlinuz-linux root=UUID=x\n')

    def test_overlay_hook_after_filesystems_and_busybox(self):
        text = mkinitcpio_config(True)
        self.assertIn('filesystems grub-btrfs-overlayfs fsck', text)
        self.assertIn('base udev', text)
        self.assertNotIn('systemd', text)
        self.assertNotIn('grub-btrfs-overlayfs', mkinitcpio_config(False))

    def test_kernel_presets_keep_images_inside_root_and_pin_hooks(self):
        for kernel in ('linux', 'linux-lts'):
            text = mkinitcpio_preset(kernel)
            self.assertIn('ALL_config="/etc/mkinitcpio.conf"', text)
            self.assertIn(f'/boot/initramfs-{kernel}.img', text)
            self.assertIn(f'/boot/initramfs-{kernel}-fallback.img', text)
            self.assertNotIn('/efi', text)

    def test_vconsole_common_layouts(self):
        expected = {'us': 'us', 'ru': 'ru', 'ua': 'ua-utf', 'cz': 'cz', 'de': 'de-latin1',
                    'fr': 'fr', 'es': 'es', 'gb': 'uk', 'dvorak': 'dvorak', 'colemak': 'colemak', 'unknown': 'us'}
        self.assertEqual({k: console_keymap(k) for k in expected}, expected)

    def test_unknown_xkb_rejected_but_known_unmapped_console_falls_back(self):
        rules = '! layout\n us English\n jp Japanese\n ru Russian\n! variant\n intl us: International\n'
        validate_xkb_layouts(['us', 'jp', 'dvorak'], rules)
        self.assertEqual(console_keymap('jp'), 'us')
        with self.assertRaises(InstallError):
            validate_xkb_layouts(['zzqq'], rules)

    def test_niri_include_layout_variants_and_scale(self):
        text = niri_config(['us', 'ru'], 1.25, ['eDP-1'])
        self.assertTrue(text.startswith('include "/usr/share/emaki/niri/default.kdl"'))
        self.assertIn('layout "us,ru"', text)
        self.assertIn('options "grp:win_space_toggle"', text)
        self.assertIn('output "eDP-1" {\n    scale 1.25', text)
        self.assertNotIn('output', niri_config(['us'], 1, ['eDP-1']))
        self.assertNotIn('output', niri_config(['us'], None, ['eDP-1']))
        self.assertIn('variant "dvorak,,colemak"', niri_config(['dvorak', 'ru', 'colemak']))

    @unittest.skipUnless(shutil.which('niri'), 'niri is optional for no-disk unit tests')
    def test_generated_niri_syntax_with_real_validator(self):
        with tempfile.TemporaryDirectory(prefix='emaki-niri-') as temp:
            path = Path(temp) / 'config.kdl'
            # This checks the generated layer, not installed Emaki defaults.
            path.write_text(niri_config(['us', 'ru', 'colemak'], 1.25, ['eDP-1']).split('\n', 1)[1])
            run = subprocess.run(['niri', 'validate', '-c', str(path)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_snapper_preserves_other_settings_and_replaces_limits(self):
        text = snapper_config('SUBVOLUME="/"\nNUMBER_LIMIT="50"\nTIMELINE_CREATE="no"\nTIMELINE_LIMIT_HOURLY="10"\n')
        self.assertEqual(text.count('NUMBER_LIMIT='), 1)
        self.assertIn('NUMBER_LIMIT="20"', text)
        self.assertIn('TIMELINE_CREATE="yes"', text)
        self.assertIn('TIMELINE_LIMIT_HOURLY="10"', text)

    def fstab(self, plan):
        lines = []
        for part, mp, subvolume in plan.mounts:
            options = part.options + ([f'subvol=/{subvolume}', 'subvolid=256'] if subvolume else [])
            lines.append(f'UUID=test-{part.number} {mp} {part.fs} {",".join(options)} 0 0')
        return '\n'.join(lines)

    def test_fstab_root_subvol_and_no_subvolid(self):
        for fs in ('btrfs', 'ext4'):
            plan = make_plan(config(fs=fs), inventory())
            text = normalize_fstab(self.fstab(plan), plan)
            self.assertNotIn('subvolid=', text)
            self.assertNotIn('\t/boot\t', text)
            self.assertNotIn('swap', text)
            if fs == 'btrfs':
                self.assertIn('subvol=/@\t', text)

    def test_fstab_missing_mount_swap_and_wrong_subvol_rejected(self):
        plan = make_plan(config(), inventory())
        text = self.fstab(plan)
        for altered in ('\n'.join(text.splitlines()[1:]), text + '\n/dev/x none swap defaults 0 0',
                        text.replace('subvol=/@home', 'subvol=/wrong')):
            with self.assertRaises(InstallError):
                normalize_fstab(altered, plan)


if __name__ == '__main__':
    unittest.main()
