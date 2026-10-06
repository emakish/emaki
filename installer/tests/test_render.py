import io
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

from emaki_installer.constants import GRUB_VISIBLE_FONT, SUBVOLUMES
from emaki_installer.errors import Code, InstallError
from emaki_installer.planner import make_plan
from emaki_installer.render import (CONSOLE, GRUB_EARLY_MODULES, GRUB_GFXMODE, console_keymap, grub_defaults,
                                    grub_early_config, grub_image_carries, grub_prefix, grub_unlock_memdisk, grub_unlock_script,
                                    mkinitcpio_config, mkinitcpio_preset, niri_config, normalize_fstab, snapper_config,
                                    unlock_layout, validate_xkb_layouts, vconsole_conf, verify_grub, wireless_regdom)
from support import config, inventory, manual

LUKS_UUID = '98cf80d3-e611-4dea-b415-c2c783782a89'

# emaki-config ships this hook; emaki-installer's package check has only installer/.
SNAPSHOT_HOOK = Path(__file__).resolve().parents[2] / 'initcpio/hooks/emaki-snapshot-fstab'


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
        self.assertIn('GRUB_GFXMODE=1024x768,800x600,640x480,auto\n', text)
        self.assertIn('GRUB_TERMINAL_OUTPUT=gfxterm\n', text)
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

    def test_grub_early_config_enters_the_memdisk_script_without_a_menu(self):
        load_cfg = f'cryptomount -u {LUKS_UUID}\n'
        text = grub_early_config(load_cfg, LUKS_UUID)
        self.assertEqual(text, 'source (memdisk)/unlock.cfg\nterminal_output console\n')
        script = grub_unlock_script(load_cfg, LUKS_UUID)
        # A nonempty "C" locale tries catalogs on the locked prefix and prints an error.
        self.assertTrue(script.startswith('set lang=\n'))
        # The rescue parser never sees control flow. The source command enters the normal
        # parser in nested batch mode, and each failure stays in its unbounded loop.
        self.assertNotIn('while', text)
        self.assertIn('set cryptodisk_passphrase_tries=1\n', script)
        self.assertIn(f'  if cryptomount -u {LUKS_UUID}; then\n    break\n  fi\n', script)
        self.assertIn('loadfont (memdisk)/hidden.pf2', script)
        self.assertIn('set color_normal=black/black', script)
        self.assertIn('unset gfxterm_font', script)
        self.assertNotIn('read ', script)
        self.assertNotIn('cryptomount -p', script)
        # Every pre-unlock file names the memdisk; the initial root is still encrypted.
        for line in script.split(f'  if cryptomount -u {LUKS_UUID}; then')[0].splitlines():
            for word in line.split()[1:]:
                if word.startswith(('/', '(')):
                    self.assertTrue(word.startswith('(memdisk)/'), line)
        self.assertIn('for emaki_mode in ' + GRUB_GFXMODE.replace(',', ' ') + '; do', script)
        self.assertIn('GRUB_GFXMODE=' + GRUB_GFXMODE + '\n', grub_defaults())

    def test_unlock_hides_output_only_inside_successful_graphics_and_image_branches(self):
        script = grub_unlock_script(f'cryptomount -u {LUKS_UUID}\n', LUKS_UUID)
        graphics = script.index('    if terminal_output gfxterm; then')
        picture = script.index('      if background_image --mode normal (memdisk)/unlock-')
        hide = script.index('set color_normal=black/black')
        self.assertLess(graphics, picture)
        self.assertLess(picture, hide)
        self.assertIn('if [ "$emaki_unlock_graphics" != 1 ]; then\n  emaki_unlock_console\nfi', script)
        fallback = script.split('function emaki_unlock_console {\n', 1)[1].split('\n}', 1)[0]
        self.assertIn('terminal_output console', fallback)
        # EFI defers its first output; prime it before applying the visible colors.
        self.assertLess(fallback.index('clear'), fallback.index('set color_normal='))
        self.assertIn('set color_normal=light-gray/black', fallback)
        for sentence in ("This computer's disk is encrypted.",
                         'Type your disk password and press Enter.',
                         'The letters do not appear while you type.',
                         'After Enter, wait a few seconds.'):
            self.assertIn(f'echo "{sentence}"', fallback)
        self.assertIn('if background_image --mode normal (memdisk)/wrong-$emaki_unlock_size.png; then\n'
                      '      true\n    else\n      set emaki_unlock_graphics=', script)

    def test_console_retry_clears_and_reprints_instructions_before_the_error(self):
        script = grub_unlock_script(f'cryptomount -u {LUKS_UUID}\n', LUKS_UUID)
        retry = script.split(f'  if cryptomount -u {LUKS_UUID}; then\n    break\n  fi\n', 1)[1]
        self.assertIn('  if [ "$emaki_unlock_graphics" != 1 ]; then\n'
                      '    emaki_unlock_console\n'
                      '    echo "Wrong password. Try again."\n  fi\ndone\n', retry)
        fallback = script.split('function emaki_unlock_console {\n', 1)[1].split('\n}', 1)[0]
        self.assertLess(fallback.rindex('clear'), fallback.index('echo '))

    def test_unlock_drains_input_clears_the_picture_and_restores_a_visible_font(self):
        script = grub_unlock_script(f'cryptomount -u {LUKS_UUID}\n', LUKS_UUID)
        self.assertIn('while ! sleep --interruptible 1; do\n  true\ndone\n'
                      'if [ "$emaki_unlock_graphics" = 1 ]; then\n'
                      '  background_image\nfi\nloadfont (memdisk)/visible.pf2\n', script)
        self.assertLess(script.index('loadfont (memdisk)/visible.pf2'), script.rindex('unset gfxterm_font'))

    def test_grub_early_config_keeps_every_grub_install_line_and_refuses_a_wrong_one(self):
        load_cfg = (f'cryptomount -u {LUKS_UUID}\nsearch.fs_uuid 1234-abcd root \n'
                    "set prefix=($root)'/@/boot/grub'\n")
        script = grub_unlock_script(load_cfg, LUKS_UUID)
        self.assertIn("done\nsearch.fs_uuid 1234-abcd root\nset prefix=($root)'/@/boot/grub'\n", script)
        self.assertEqual(script.count(f'cryptomount -u {LUKS_UUID}'), 1)
        for bad in ('', f'# {LUKS_UUID}\n', 'cryptomount -u 00000000-0000-0000-0000-000000000000\n',
                    f'cryptomount -u {LUKS_UUID}\necho "—"\n'):
            for render in (grub_early_config, grub_unlock_script):
                with self.subTest(bad=bad, render=render), self.assertRaises(InstallError) as raised:
                    render(bad, LUKS_UUID)
                self.assertEqual(raised.exception.code, Code.BOOT_VERIFY)

    def test_grub_prefix_is_the_unlocked_device_and_the_root_subvolume_path(self):
        # grub-install names the unlocked root by its device-mapper UUID, which has no dashes
        # (grub-core/osdep/devmapper/getroot.c, GRUB_DEV_ABSTRACTION_LUKS). Seen in the EFI
        # image grub-install wrote on an encrypted VM install with LUKS UUID
        # 2dde29d4-1317-46dc-8f20-69fe26a1a0ad: prefix (cryptouuid/2dde29d4131746dc8f2069fe26a1a0ad)/@/boot/grub.
        plan = make_plan(dict(config(), encryption='account'), inventory())
        plan.root.luks_uuid = '2dde29d4-1317-46dc-8f20-69fe26a1a0ad'
        self.assertEqual(grub_prefix(plan), '(cryptouuid/2dde29d4131746dc8f2069fe26a1a0ad)/@/boot/grub')
        device = '(cryptouuid/98cf80d3e6114deab415c2c783782a89)'
        for fs, path in (('btrfs', '/@/boot/grub'), ('ext4', '/boot/grub')):
            plan = make_plan(dict(config(fs=fs), encryption='account'), inventory())
            plan.root.luks_uuid = LUKS_UUID
            self.assertEqual(grub_prefix(plan), device + path)
        c = manual()
        c['mounts'] = c['mounts'][:1] + [
            {'partition_id': '/dev/vda2', 'mountpoint': mp, 'fs': 'btrfs', 'format': True, 'subvolume': 'custom' + name}
            for name, mp in SUBVOLUMES.items()]
        c['encryption'] = 'account'
        plan = make_plan(c, inventory(partitions=True))
        plan.root.luks_uuid = LUKS_UUID
        self.assertEqual(grub_prefix(plan), device + '/custom@/boot/grub')
        for missing in (None, '', 'not-a-uuid'):
            plan.root.luks_uuid = missing
            with self.assertRaises(InstallError):
                grub_prefix(plan)

    def test_grub_image_carries_the_prefix_as_one_nul_terminated_string(self):
        prefix = f'(cryptouuid/{LUKS_UUID})/@/boot/grub'
        self.assertTrue(grub_image_carries(b'MZ' + b'\0' * 64 + prefix.encode() + b'\0' + b'tail', prefix))
        self.assertFalse(grub_image_carries(b'MZ' + prefix.encode() + b'/x\0', prefix))
        self.assertFalse(grub_image_carries(b'MZ' + prefix.encode(), prefix))
        self.assertFalse(grub_image_carries(b'\x7fELF' + prefix.encode() + b'\0', prefix))
        self.assertFalse(grub_image_carries(b'', prefix))

    def test_early_image_module_list_covers_the_unlock_and_the_graphics(self):
        for name in ('part_gpt', 'cryptodisk', 'luks2', 'argon2', 'gcry_rijndael', 'gcry_sha256', 'pbkdf2',
                     'btrfs', 'ext2', 'memdisk', 'tar', 'font', 'gfxterm', 'gfxterm_background', 'png',
                     'efi_gop', 'terminal', 'echo', 'gzio', 'normal', 'configfile', 'test', 'true', 'sleep'):
            self.assertIn(name, GRUB_EARLY_MODULES)
        self.assertEqual(len(GRUB_EARLY_MODULES), len(set(GRUB_EARLY_MODULES)))
        # The full parser is embedded. Its initializer can load gzio without the locked prefix.
        self.assertLess(GRUB_EARLY_MODULES.index('gzio'), GRUB_EARLY_MODULES.index('normal'))
        # grub 2:2.16-1 has no efi_uga.mod and no shim_lock.mod (a kernel flag there).
        # Nothing is read from the ESP, so no FAT driver.
        for name in ('efi_uga', 'shim_lock', 'fat', 'all_video', 'video_bochs', 'video_cirrus'):
            self.assertNotIn(name, GRUB_EARLY_MODULES)

    def test_unlock_memdisk_is_self_contained_and_reproducible(self):
        font = (Path(__file__).resolve().parents[1] / 'assets/grub' / GRUB_VISIBLE_FONT.name).read_bytes()
        load_cfg = f'cryptomount -u {LUKS_UUID}\n'
        data = grub_unlock_memdisk(font, load_cfg, LUKS_UUID)
        # GRUB's tar driver (grub-core/fs/tar.c) knows the "ustar" header only.
        self.assertEqual(data[257:263], b'ustar\0')
        self.assertEqual(len(data) % 512, 0)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            members = archive.getmembers()
            self.assertEqual({m.name for m in members}, {'visible.pf2', 'unlock.cfg', 'hidden.pf2',
                             *(f'{state}-{size}.png' for state in ('unlock', 'wrong', 'checking')
                               for size in GRUB_GFXMODE.split(',')[:-1])})
            self.assertTrue(all(m.isreg() and m.mode == 0o644 for m in members))
            self.assertEqual(archive.extractfile('visible.pf2').read(), font)
            self.assertEqual(archive.extractfile('unlock.cfg').read().decode(),
                             grub_unlock_script(load_cfg, LUKS_UUID))
        # The same files give the same bytes: no time stamps or owner names of the build.
        self.assertEqual(data, grub_unlock_memdisk(font, load_cfg, LUKS_UUID))

    def test_unlock_rejects_a_blank_fallback_font(self):
        from emaki_installer import grub_screen
        load_cfg = f'cryptomount -u {LUKS_UUID}\n'
        with self.assertRaises(InstallError) as raised:
            grub_unlock_memdisk(grub_screen._hidden_font(), load_cfg, LUKS_UUID)
        self.assertEqual(raised.exception.code, Code.BOOT_VERIFY)

    def test_overlay_hook_after_filesystems_and_busybox(self):
        text = mkinitcpio_config(True)
        self.assertIn('filesystems grub-btrfs-overlayfs emaki-snapshot-fstab)', text)
        self.assertIn('base udev', text)
        self.assertNotIn('systemd', text)
        self.assertNotIn('grub-btrfs-overlayfs', mkinitcpio_config(False))
        self.assertNotIn('emaki-snapshot-fstab', mkinitcpio_config(False))

    @unittest.skipUnless(SNAPSHOT_HOOK.is_file(), 'the snapshot hook is not in this source tree')
    def test_snapshot_overlay_removes_only_root_fstab_entry(self):
        busybox = Path('/usr/lib/initcpio/busybox')
        shells = [[shutil.which('sh')]]
        if busybox.exists():
            shells.append([str(busybox), 'ash'])
        with tempfile.TemporaryDirectory(prefix='emaki-snapshot-') as temp:
            root = Path(temp)
            (root / 'etc').mkdir()
            fstab = root / 'etc/fstab'
            script = SNAPSHOT_HOOK.read_text().replace('/new_root', str(root))
            retained = ('# Root / entry is replaced only in the recovery overlay.\n'
                        'UUID=home /home btrfs rw,subvol=/@home 0 0\n'
                        'UUID=efi /efi vfat defaults 0 2\n'
                        'UUID=usr /usr ext4 defaults 0 2\n\n')
            for shell in shells:
                for magic in ('794c7630', '9123683e', 'ef53', ''):
                    for prefix in ('', '  ', '\t'):
                        with self.subTest(shell=shell, magic=magic, prefix=prefix):
                            original = retained + prefix + 'UUID=root\t/\tbtrfs\trw,compress=zstd:3,subvol=/@\t0 0\n'
                            fstab.write_text(original)
                            command = script + f'\nstat() {{ echo "{magic}"; }}\nrun_latehook\nrun_latehook\n'
                            if len(shell) == 2:
                                command = f'sed() {{ {busybox} sed "$@"; }}\n' + command
                            result = subprocess.run([*shell, '-c', command], capture_output=True, text=True)
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertEqual(fstab.read_text(), retained if magic == '794c7630' else original)

    def test_kernel_presets_keep_images_inside_root_and_pin_hooks(self):
        for kernel in ('linux', 'linux-lts'):
            text = mkinitcpio_preset(kernel)
            self.assertIn('ALL_config="/etc/mkinitcpio.conf"', text)
            self.assertIn(f'/boot/initramfs-{kernel}.img', text)
            self.assertIn(f'/boot/initramfs-{kernel}-fallback.img', text)
            self.assertNotIn('/efi', text)

    def test_vconsole_common_layouts(self):
        # XKB cz is QWERTZ; kbd's plain cz map is QWERTY and would swap y/z on the console.
        expected = {'us': 'us', 'ru': 'ru', 'ua': 'ua-utf', 'cz': 'cz-qwertz', 'de': 'de-latin1',
                    'fr': 'fr-pc', 'es': 'es', 'gb': 'ie', 'it': 'it', 'be': 'be-latin1', 'dvorak': 'dvorak',
                    'colemak': 'colemak', 'sk': 'us', 'unknown': 'us'}
        self.assertEqual({k: console_keymap(k) for k in expected}, expected)

    def test_vconsole_conf_holds_the_console_map_and_the_whole_list(self):
        self.assertEqual(vconsole_conf(['cz', 'us']), 'KEYMAP=cz-qwertz\nXKBLAYOUT=cz,us\n')
        # The list is never narrowed to one layout, reordered or given a silent us.
        self.assertEqual(vconsole_conf(['ru', 'us']), 'KEYMAP=ru\nXKBLAYOUT=ru,us\n')
        self.assertEqual(vconsole_conf(['ru']), 'KEYMAP=ru\nXKBLAYOUT=ru\n')
        self.assertEqual(vconsole_conf(['de', 'fr', 'ru', 'ua']), 'KEYMAP=de-latin1\nXKBLAYOUT=de,fr,ru,ua\n')
        # A first layout without a vetted console map leaves the console on us.
        self.assertEqual(vconsole_conf(['sk', 'us']), 'KEYMAP=us\nXKBLAYOUT=sk,us\n')
        self.assertEqual(vconsole_conf(['it', 'us']), 'KEYMAP=it\nXKBLAYOUT=it,us\n')
        # Dvorak/Colemak are XKB variants of us; variants are positional.
        self.assertEqual(vconsole_conf(['dvorak', 'ru', 'colemak']),
                         'KEYMAP=dvorak\nXKBLAYOUT=us,ru,us\nXKBVARIANT=dvorak,,colemak\n')
        self.assertEqual(vconsole_conf(['ru', 'dvorak']), 'KEYMAP=ru\nXKBLAYOUT=ru,us\nXKBVARIANT=,dvorak\n')

    def test_vconsole_conf_never_carries_a_switch_option_or_a_model(self):
        for layouts in (['us'], ['us', 'ru'], ['cz', 'us'], ['dvorak', 'ru', 'colemak'], ['it', 'de', 'fr', 'ua']):
            text = vconsole_conf(layouts)
            for word in ('XKBOPTIONS', 'XKBMODEL', 'grp:'):
                self.assertNotIn(word, text)
            self.assertEqual(text.count('KEYMAP='), 1)
            self.assertEqual(text.count('XKBLAYOUT='), 1)

    def test_unlock_layout_is_derived_from_the_list_and_the_prompt(self):
        self.assertEqual(unlock_layout(['de', 'ru'], 'initramfs'), 'de')
        self.assertEqual(unlock_layout(['cz', 'us'], 'initramfs'), 'cz')
        self.assertEqual(unlock_layout(['fr'], 'initramfs'), 'fr')
        self.assertEqual(unlock_layout(['dvorak', 'ru'], 'initramfs'), 'dvorak')
        # Latin, but no vetted console map: the console in the image is us.
        self.assertEqual(unlock_layout(['sk', 'us'], 'initramfs'), 'us')
        self.assertEqual(unlock_layout(['it', 'us'], 'initramfs'), 'it')
        # Not Latin: its console map types ASCII on the us positions.
        self.assertEqual(unlock_layout(['ru', 'us'], 'initramfs'), 'us')
        self.assertEqual(unlock_layout(['ua'], 'initramfs'), 'us')
        for layouts in (['us'], ['de', 'ru'], ['cz', 'us'], ['ru', 'us'], ['dvorak']):
            self.assertEqual(unlock_layout(layouts, 'grub'), 'us')
        with self.assertRaises(ValueError):
            unlock_layout(['us'], 'plymouth')

    def test_console_map_in_the_image_matches_the_unlock_layout(self):
        # The image carries the map of the first layout; where the unlock layout is us although
        # the first layout is not, that map must be one that types ASCII on the us positions.
        for first in list(CONSOLE) + ['it', 'jp', 'gr']:
            unlock = unlock_layout([first], 'initramfs')
            if unlock != first:
                self.assertEqual(unlock, 'us')
                self.assertIn(console_keymap(first), ('us', 'ru', 'ua-utf'), first)

    @unittest.skipUnless(Path('/usr/share/kbd/keymaps').is_dir(), 'kbd keymaps are not installed')
    def test_every_console_map_exists_in_kbd(self):
        present = {p.name for p in Path('/usr/share/kbd/keymaps').rglob('*.map.gz')}
        for layout, keymap in CONSOLE.items():
            self.assertIn(keymap + '.map.gz', present, layout)

    def test_unknown_xkb_rejected_but_known_unmapped_console_falls_back(self):
        rules = '! layout\n us English\n jp Japanese\n ru Russian\n! variant\n intl us: International\n'
        validate_xkb_layouts(['us', 'jp', 'dvorak'], rules)
        self.assertEqual(console_keymap('jp'), 'jp106')
        self.assertEqual(console_keymap('sk'), 'us')
        with self.assertRaises(InstallError):
            validate_xkb_layouts(['zzqq'], rules)

    def test_niri_config_is_the_include_and_the_scale_only(self):
        text = niri_config(1.25, ['eDP-1'])
        self.assertTrue(text.startswith('include "/usr/share/emaki/niri/default.kdl"'))
        self.assertIn('output "eDP-1" {\n    scale 1.25', text)
        self.assertNotIn('output', niri_config(1, ['eDP-1']))
        self.assertNotIn('output', niri_config(None, ['eDP-1']))
        self.assertEqual(niri_config(), 'include "/usr/share/emaki/niri/default.kdl"\n')

    def test_niri_config_carries_no_keyboard_settings(self):
        # Layouts live in /etc/vconsole.conf; niri reads them through localed only while its
        # merged xkb section is empty, so any xkb node here would cut the session off from it.
        for text in (niri_config(), niri_config(1.25, ['eDP-1', 'HDMI-A-1'])):
            for word in ('xkb', 'input', 'layout', 'variant', 'options', 'grp:'):
                self.assertNotIn(word, text)

    @unittest.skipUnless(shutil.which('niri'), 'niri is optional for no-disk unit tests')
    def test_generated_niri_syntax_with_real_validator(self):
        with tempfile.TemporaryDirectory(prefix='emaki-niri-') as temp:
            path = Path(temp) / 'config.kdl'
            # This checks the generated layer, not installed Emaki defaults.
            path.write_text(niri_config(1.25, ['eDP-1']).split('\n', 1)[1])
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
