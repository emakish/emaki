#!/usr/bin/python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Startup logo: the Plymouth theme, its two drop-ins and the upgrade adoption."""
import configparser
import contextlib
import hashlib
import importlib.util
import io
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

from PIL import Image
import reaper
reaper.guard()  # nothing this test starts outlives it

REPO = Path(__file__).resolve().parents[1]
THEME = REPO / 'boot/plymouth'
spec = importlib.util.spec_from_file_location('boot_defaults', REPO / 'upkeep/emaki_boot_defaults.py')
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)

# Plymouth 26.134.222 themes/spinner, as Arch's plymouth package installs them: the password
# prompt pictures two-step draws. Without entry.png, bullet.png or lock.png two-step refuses
# the whole theme (plugins/splash/two-step/plugin.c view_load, show_splash_screen).
STOCK = {
    'entry.png': '8325ccd1676dd318498d',
    'bullet.png': 'f577b79cbe0a6576b285',
    'lock.png': '4854942e80051cec8cf6',
    'capslock.png': '5e4877145fce9be41148',
    'keyboard.png': 'cf6674ab2f7fa5bc401d',
    'keymap-render.png': '69260b03e596743394a3',
}


def theme():
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(THEME / 'emaki.plymouth')
    return parser


class ThemeTests(unittest.TestCase):
    def test_watermark_is_the_boot_splash_wordmark(self):
        subprocess.run([sys.executable, str(REPO / 'scripts/build-plymouth-theme'), '--check'], check=True)
        with Image.open(THEME / 'watermark.png') as image:
            self.assertEqual((image.format, image.mode, image.size), ('PNG', 'RGBA', (600, 144)))
            histogram = image.getchannel('A').histogram()
            self.assertEqual(sum(histogram[1:255]), 0)  # letters opaque, the rest transparent
            self.assertTrue(histogram[0] and histogram[255])
            on_black = Image.new('RGBA', image.size, (0, 0, 0, 255))
            on_black.alpha_composite(image)
        # The same pixels as the systemd-stub splash, without its 12-pixel black margins.
        with Image.open(REPO / 'boot/emaki-splash.bmp') as splash:
            letters = splash.convert('RGB').crop((0, 12, 600, 156))
        self.assertEqual(on_black.convert('RGB').tobytes(), letters.tobytes())

    def test_theme_centres_the_wordmark_on_black_without_end_animation(self):
        parser = theme()
        self.assertEqual(parser['Plymouth Theme']['ModuleName'], 'two-step')
        step = parser['two-step']
        self.assertEqual(step['ImageDir'], '/usr/share/plymouth/themes/emaki')
        # two-step's own default horizontal watermark alignment is 1.0, the right edge.
        self.assertEqual((step['WatermarkHorizontalAlignment'], step['WatermarkVerticalAlignment']), ('.5', '.5'))
        self.assertEqual((step['BackgroundStartColor'], step['BackgroundEndColor']), ('0x000000', '0x000000'))
        # The prompt sits under the wordmark (600x144 centred) at every screen height.
        self.assertGreater(float(step['DialogVerticalAlignment']), 0.5)
        for mode in ('boot-up', 'shutdown', 'reboot'):
            # An end animation can keep plymouthd from quitting (Plymouth issue 346), and greetd
            # starts only after plymouth-quit-wait.service.
            self.assertEqual(parser[mode]['UseEndAnimation'], 'false', mode)
            self.assertNotIn('UseFirmwareBackground', parser[mode], mode)

    def test_prompt_pictures_are_plymouths_unchanged(self):
        for name, digest in STOCK.items():
            self.assertEqual(hashlib.sha256((THEME / name).read_bytes()).hexdigest()[:20], digest, name)
        self.assertEqual(sorted(path.name for path in THEME.glob('*.png')), sorted([*STOCK, 'watermark.png']))

    def test_package_installs_every_theme_file(self):
        expected = (REPO / 'packaging/emaki-config/expected-files.list').read_text().splitlines()
        for path in [*THEME.glob('*.png'), THEME / 'emaki.plymouth']:
            self.assertIn('/usr/share/plymouth/themes/emaki/' + path.name, expected)
        for path in ('/etc/mkinitcpio.conf.d/50-emaki-splash.conf', '/etc/default/grub.d/50-emaki-splash.cfg',
                     '/usr/share/licenses/emaki-config/NOTICE-Plymouth.txt'):
            self.assertIn(path, expected)
        recipe = (REPO / 'packaging/emaki-config/PKGBUILD').read_text()
        self.assertIn("'plymouth'", recipe)
        for path in ('etc/mkinitcpio.conf.d/50-emaki-splash.conf', 'etc/default/grub.d/50-emaki-splash.cfg'):
            self.assertIn(f"'{path}'", recipe.split('backup=(', 1)[1].split(')', 1)[0])


class DropInTests(unittest.TestCase):
    def hooks(self, hooks, installed=True, assignment=None):
        """HOOKS after the drop-in, sourced twice as mkinitcpio may; `declare -p` shows its form."""
        with tempfile.TemporaryDirectory() as tmp:
            hook = Path(tmp) / 'plymouth'
            if installed:
                hook.write_text('')
            text = (THEME / 'mkinitcpio.conf').read_text().replace('/usr/lib/initcpio/install/plymouth', str(hook))
            self.assertEqual(text.count(str(hook)), 1)
            script = Path(tmp) / 'drop-in.conf'
            script.write_text(text)
            assignment = assignment or f'HOOKS=({hooks})'
            result = subprocess.run(['bash', '-euc', f'{assignment}; . "$1"; . "$1"; declare -p HOOKS', 'x', str(script)],
                                    check=True, capture_output=True, text=True)
            declared = result.stdout.strip()
            if declared.startswith('declare -a'):
                values = subprocess.run(['bash', '-c', f'{declared}; echo "${{HOOKS[*]}}"'],
                                        check=True, capture_output=True, text=True).stdout.strip()
                return values
            return declared

    def test_old_string_form_is_left_for_mkinitcpio_to_split(self):
        # Rewriting a string as a one-element array would make the whole list one hook name.
        self.assertEqual(self.hooks(None, assignment='HOOKS="base udev autodetect block filesystems"'),
                         'declare -- HOOKS="base udev autodetect block filesystems"')

    def test_plymouth_starts_after_udev_and_before_the_disk_is_unlocked(self):
        hooks = 'base udev autodetect microcode modconf kms keyboard keymap consolefont block encrypt filesystems'
        self.assertEqual(self.hooks(hooks), hooks.replace('udev ', 'udev plymouth ', 1))
        plain = 'base udev autodetect microcode modconf kms keyboard keymap consolefont block filesystems fsck'
        self.assertEqual(self.hooks(plain), plain.replace('udev ', 'udev plymouth ', 1))

    def test_other_hook_lists_are_left_alone(self):
        for hooks in ('base udev plymouth autodetect block encrypt filesystems',  # the administrator's choice
                      'base systemd autodetect sd-vconsole block sd-encrypt filesystems',  # systemd initramfs
                      'base udev microcode modconf kms memdisk archiso archiso_loop_mnt block filesystems'):
            self.assertEqual(self.hooks(hooks), hooks)
        self.assertEqual(self.hooks('base udev block filesystems', installed=False), 'base udev block filesystems')

    def grub(self, assignment):
        script = f'{assignment}\n. "$1"\nprintf %s "$GRUB_CMDLINE_LINUX_DEFAULT"'
        return subprocess.run(['sh', '-c', script, 'x', str(THEME / 'grub.cfg')],
                              check=True, capture_output=True, text=True).stdout

    def test_grub_adds_splash_once(self):
        both = 'splash plymouth.use-legacy-input'
        self.assertEqual(self.grub('GRUB_CMDLINE_LINUX_DEFAULT="quiet zswap.enabled=0"'), 'quiet zswap.enabled=0 ' + both)
        self.assertEqual(self.grub('GRUB_CMDLINE_LINUX_DEFAULT=""'), both)
        self.assertEqual(self.grub(':'), both)
        self.assertEqual(self.grub('GRUB_CMDLINE_LINUX_DEFAULT="quiet splash loglevel=3"'),
                         'quiet splash loglevel=3 plymouth.use-legacy-input')
        self.assertEqual(self.grub('GRUB_CMDLINE_LINUX_DEFAULT="plymouth.use-legacy-input quiet"'),
                         'plymouth.use-legacy-input quiet splash')
        # grub-mkconfig may read the drop-in after another one that read it already.
        self.assertEqual(self.grub(f'GRUB_CMDLINE_LINUX_DEFAULT="{both}"\n. "$1"'), both)

    def test_the_disk_password_is_read_through_the_console_keymap(self):
        # Without a usable keyfile the encrypt hook asks through Plymouth. Plymouth reads keys
        # with libxkbcommon in the first XKBLAYOUT of /etc/vconsole.conf unless the command line
        # has plymouth.use-legacy-input (ply-device-manager.c, create_devices_for_subsystem);
        # with it, keys come through the text console and the keymap hook's KEYMAP.
        sys.path.insert(0, str(REPO / 'installer'))
        try:
            from emaki_installer import latin_layouts, render
        finally:
            sys.path.remove(str(REPO / 'installer'))
        for layouts in (['ru', 'us'], ['ua', 'us'], ['ru']):
            first = render.vconsole_conf(layouts).splitlines()[1]
            self.assertTrue(first.startswith(f'XKBLAYOUT={layouts[0]}'), first)
            # In that layout the US letter keys type no Latin letters at all...
            self.assertFalse(latin_layouts.is_latin(layouts[0]))
            # ...while its console map types the English (US) keys, which the installer
            # requires of the startup password (printable ASCII on US key positions).
            self.assertTrue(latin_layouts.CONSOLE_CHARS[layouts[0]][0])
            self.assertEqual(render.unlock_layout(layouts, 'initramfs'), 'us')
        self.assertIn('plymouth.use-legacy-input', self.grub(':').split())

    # grub-btrfs 4.14's 41_snapshots-btrfs, lines 41-45 and 145 (Arch packages it unpatched):
    # its own config first, then /etc/default/grub again, which replaces the
    # GRUB_CMDLINE_LINUX_DEFAULT grub-mkconfig exported after reading the drop-ins. Emaki's
    # automatic recovery runs the generator directly, with no grub-mkconfig around it at all.
    SNAPSHOT_GENERATOR = '''set -e
sysconfdir="$1"
grub_btrfs_config="${sysconfdir}/default/grub-btrfs/config"

[ -f "$grub_btrfs_config" ] && . "$grub_btrfs_config"
[ -f "${sysconfdir}/default/grub" ] && . "${sysconfdir}/default/grub"
kernel_parameters="$GRUB_CMDLINE_LINUX $GRUB_CMDLINE_LINUX_DEFAULT $GRUB_BTRFS_SNAPSHOT_KERNEL_PARAMETERS"
printf %s "$kernel_parameters"
'''

    def snapshot_arguments(self, drop_in=None, own=''):
        with tempfile.TemporaryDirectory() as directory:
            etc = Path(directory)
            (etc / 'default/grub-btrfs').mkdir(parents=True)
            (etc / 'default/grub.d').mkdir()
            # The installer's /etc/default/grub, and its line at the end of grub-btrfs' config.
            (etc / 'default/grub').write_text('GRUB_CMDLINE_LINUX="rw cryptdevice=UUID=x:emaki-root"\n'
                                              'GRUB_CMDLINE_LINUX_DEFAULT="quiet zswap.enabled=0"\n')
            source = REPO / 'boot/defaults/grub-btrfs.conf'
            (etc / 'default/grub-btrfs/config').write_text(f'{own}if [ -r {source} ]; then . {source}; fi\n')
            if drop_in is not None:
                (etc / 'default/grub.d/50-emaki-splash.cfg').write_text(drop_in)
            # What grub-mkconfig exports to its scripts after reading the drop-in.
            exported = 'quiet zswap.enabled=0 splash plymouth.use-legacy-input'
            return subprocess.run(['bash', '-c', self.SNAPSHOT_GENERATOR, 'x', str(etc)], check=True,
                                  env={'PATH': '/usr/bin:/bin', 'GRUB_CMDLINE_LINUX_DEFAULT': exported},
                                  capture_output=True, text=True).stdout.split()

    def test_snapshot_entries_read_the_password_through_the_console_keymap(self):
        drop_in = (THEME / 'grub.cfg').read_text()
        arguments = self.snapshot_arguments(drop_in)
        self.assertEqual(arguments, ['rw', 'cryptdevice=UUID=x:emaki-root', 'quiet', 'zswap.enabled=0',
                                     'splash', 'plymouth.use-legacy-input'])
        # An administrator's own snapshot arguments stay, ours are added once.
        own = 'GRUB_BTRFS_SNAPSHOT_KERNEL_PARAMETERS="nomodeset splash"\n'
        self.assertEqual(self.snapshot_arguments(drop_in, own)[4:], ['nomodeset', 'splash', 'plymouth.use-legacy-input'])
        # The drop-in decides: "splash" removed there leaves the messages, but not the keymap.
        self.assertEqual(self.snapshot_arguments(drop_in.replace('in splash plymouth', 'in plymouth'))[4:],
                         ['plymouth.use-legacy-input'])
        # Without emaki-config's drop-in (removed, or another distribution) nothing is added.
        self.assertEqual(self.snapshot_arguments()[4:], [])


# /etc/plymouth/plymouthd.conf exactly as Arch's plymouth 26.134.222-3 installs it (read in the VM).
PACKAGED = '# Administrator customizations go in this file\n#[Daemon]\n#Theme=fade-in\n'
CHOSEN = PACKAGED + '[Daemon]\nTheme=emaki\n'


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name in ('emaki.plymouth', 'watermark.png', 'entry.png'):
            self.write('usr/share/plymouth/themes/emaki/' + name, (THEME / name).read_bytes())
        self.write('usr/lib/initcpio/install/plymouth', b'')
        self.write('etc/mkinitcpio.conf.d/50-emaki-splash.conf', (THEME / 'mkinitcpio.conf').read_bytes())
        self.config = self.write('etc/plymouth/plymouthd.conf', PACKAGED.encode())
        self.write('etc/mkinitcpio.conf', b'HOOKS=(base udev autodetect block filesystems fsck)\n')
        for kernel in boot.KERNELS:
            self.write(f'etc/mkinitcpio.d/{kernel}.preset', f'. /usr/share/emaki/boot/{kernel}.preset\n'.encode())
            self.write(f'usr/share/emaki/boot/{kernel}.preset', b'')
        self.pending = self.root / 'var/lib/emaki/migrations/initramfs-pending'

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_plymouths_default_file_gets_the_emaki_theme_once(self):
        self.assertTrue(boot.apply(self.root))
        self.assertEqual(self.config.read_text(), CHOSEN)
        # The images carry plymouthd.conf: both are queued for the rebuild.
        self.assertEqual(self.pending.read_text(), 'linux\nlinux-lts\n')
        self.pending.unlink()
        self.assertFalse(boot.apply(self.root))
        self.assertFalse(self.pending.exists())
        # Chosen once: Plymouth's own default restored later by the administrator stays.
        self.config.write_text(PACKAGED)
        self.assertFalse(boot.apply(self.root))
        self.assertEqual(self.config.read_text(), PACKAGED)

    def test_other_daemon_settings_keep_their_place(self):
        self.config.write_text('[Daemon]\nShowDelay=5\n')
        self.assertTrue(boot.apply(self.root))
        self.assertEqual(self.config.read_text(), '[Daemon]\nTheme=emaki\nShowDelay=5\n')

    def test_administrator_theme_is_kept(self):
        self.config.write_text('[Daemon]\nTheme = spinner\n')
        self.assertFalse(boot.apply(self.root))
        self.assertEqual(self.config.read_text(), '[Daemon]\nTheme = spinner\n')
        # Their theme removed later stays removed: Plymouth's own fallback is their choice now.
        self.config.write_text('[Daemon]\n')
        self.assertFalse(boot.apply(self.root))
        self.assertEqual(self.config.read_text(), '[Daemon]\n')

    def test_nothing_happens_without_the_theme_or_plymouth(self):
        (self.root / 'usr/lib/initcpio/install/plymouth').unlink()
        self.assertFalse(boot.apply(self.root))
        self.write('usr/lib/initcpio/install/plymouth', b'')
        (self.root / 'usr/share/plymouth/themes/emaki/emaki.plymouth').unlink()
        self.assertFalse(boot.apply(self.root))
        self.assertEqual(self.config.read_text(), PACKAGED)

    def test_removal_gives_back_the_file_it_replaced(self):
        boot.apply(self.root)
        boot.release(self.root)  # replacement, not removal: the theme stays selected
        self.assertEqual(self.config.read_text(), CHOSEN)
        boot.release(self.root, removing=True)
        self.assertEqual(self.config.read_text(), PACKAGED)
        # A reinstallation chooses the theme again.
        self.assertTrue(boot.apply(self.root))
        self.assertEqual(self.config.read_text(), CHOSEN)

    def test_a_failed_write_is_tried_again(self):
        original = boot.replace
        for failing in (self.config.name, Path(boot.SPLASH_LEDGER).name):
            def fail(path, text, failing=failing):
                if path.name == failing:
                    raise OSError('disk full')
                return original(path, text)
            boot.replace = fail
            try:
                with self.assertRaises(OSError):
                    boot.splash(self.root)
            finally:
                boot.replace = original
            self.assertEqual(self.config.read_text(), PACKAGED, failing)
        self.assertTrue(boot.splash(self.root))
        self.assertEqual(self.config.read_text(), CHOSEN)

    def fires(self, name, package):
        """Whether pacman runs upkeep/NAME when PACKAGE is upgraded (package triggers only)."""
        text = (REPO / 'upkeep' / name).read_text().split('[Action]')[0]
        for block in text.split('[Trigger]')[1:]:
            lines = {line.strip() for line in block.splitlines()}
            if {'Type = Package', 'Operation = Upgrade', 'Target = ' + package} <= lines:
                return True
        return False

    def test_a_retry_in_a_desktop_or_apps_transaction_rebuilds_the_images(self):
        sys.path.insert(0, str(REPO / 'upkeep'))
        try:
            import emaki_initramfs as transaction
            refresh = runpy.run_path(str(REPO / 'upkeep/emaki-initramfs-refresh'))['refresh']
        finally:
            sys.path.remove(str(REPO / 'upkeep'))
        for kernel in boot.KERNELS:
            self.write(f'boot/vmlinuz-{kernel}', b'kernel')
        for package in ('emaki-desktop', 'emaki-apps'):
            self.config.write_text(PACKAGED)
            # An emaki-config transaction whose configuration write fails; its stock
            # mkinitcpio hook (Emaki's wrapper) still runs and leaves its receipt in /run.
            original = boot.replace

            def fail(path, text):
                if path == self.config:
                    raise OSError('disk full')
                return original(path, text)
            boot.replace = fail
            try:
                with self.assertRaises(OSError):
                    boot.apply(self.root)
            finally:
                boot.replace = original
            with contextlib.redirect_stdout(io.StringIO()):
                transaction.stock(self.root, 'usr/lib/initcpio/install/emaki-resume\n', Mock())
            self.assertFalse(self.pending.exists())
            # Then a transaction that upgrades only emaki-desktop or emaki-apps, its hooks in
            # pacman's order.
            run = Mock()
            actions = {
                '89-emaki-initramfs-begin.hook': lambda: transaction.begin(self.root),
                '89-emaki-system-migrate.hook': lambda: boot.apply(self.root),
                '93-emaki-initramfs-refresh.hook': lambda: refresh(self.root, run, in_transaction=True),
            }
            with contextlib.redirect_stdout(io.StringIO()):
                for name in sorted(actions):
                    if self.fires(name, package):
                        actions[name]()
            self.assertEqual(self.config.read_text(), CHOSEN, package)
            run.assert_called_once_with(['mkinitcpio', '-p', 'linux', '-p', 'linux-lts'], check=True)
            # Nothing is left to block the boot refresh.
            self.assertFalse(self.pending.exists(), package)
            (self.root / boot.SPLASH_LEDGER).unlink()

    def test_images_are_rebuilt_by_the_stock_hook_in_the_same_transaction(self):
        # In an emaki-config transaction the queue from splash() is built by the
        # 90-mkinitcpio-install hook (Emaki's --stock wrapper) after the 89 hook, because
        # emaki-config ships files under usr/lib/initcpio.
        expected = (REPO / 'packaging/emaki-config/expected-files.list').read_text().splitlines()
        self.assertTrue([path for path in expected
                         if path.startswith('/usr/lib/initcpio/') and not path.endswith('/')])

    def test_removal_keeps_a_file_edited_after_the_choice(self):
        boot.apply(self.root)
        self.config.write_text(CHOSEN + 'ShowDelay=2\n')
        boot.release(self.root, removing=True)
        self.assertEqual(self.config.read_text(), CHOSEN + 'ShowDelay=2\n')


if __name__ == '__main__':
    unittest.main()
