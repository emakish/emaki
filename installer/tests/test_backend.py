from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from emaki_installer.arch_backend import (DeviceOperations, OfflinePacman, missing_grub_modules, offline_config,
                                          validate_live_plan)
from emaki_installer.constants import OFFLINE_REPO
from emaki_installer.errors import Code, InstallError
from emaki_installer.planner import make_plan
from emaki_installer.render import GRUB_EARLY_MODULES
from support import RecordingRunner, config, inventory, manual

FONT = Path(__file__).resolve().parents[1] / 'assets/grub/unlock-24.pf2'


class BackendTests(unittest.TestCase):
    def test_signed_offline_config_only(self):
        valid = f'[options]\nArchitecture = auto\n[emaki-offline]\nSigLevel = Required DatabaseOptional TrustedOnly\nServer = {OFFLINE_REPO}\n'
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'pacman.conf'
            path.write_text(valid)
            self.assertEqual(offline_config(path), path)
            for bad in (valid + '[core]\nServer = https://example.org\n',
                        valid.replace('Required', 'Never'),
                        valid.replace(OFFLINE_REPO, 'https://example.org'),
                        valid.replace('Architecture = auto', 'HookDir = /etc/pacman.d/hooks'),
                        valid + 'Include = /etc/pacman.conf\n',
                        valid + 'XferCommand = malicious\n'):
                path.write_text(bad)
                with self.assertRaises(InstallError):
                    offline_config(path)

    def test_generated_config_accepts_exactly_one_private_hook_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            path, hooks = Path(temp) / 'pacman.conf', Path(temp) / 'hooks'
            directive = f'HookDir = {hooks}\n'
            valid = ('[options]\n' + directive + '[emaki-offline]\n'
                     'SigLevel = Required DatabaseOptional TrustedOnly\n'
                     f'Server = {OFFLINE_REPO}\n')
            path.write_text(valid)
            self.assertEqual(offline_config(path, hookdir=hooks), path)
            for bad in (valid.replace(directive, ''),
                        valid.replace(directive, directive * 2),
                        valid.replace(directive, directive + 'HookDir = /etc/pacman.d/hooks\n'),
                        valid.replace(str(hooks), str(hooks) + ' /etc/pacman.d/hooks'),
                        valid.replace(str(hooks), str(hooks) + ' # /etc/pacman.d/hooks'),
                        valid.replace(directive, '') + directive):
                with self.subTest(config=bad):
                    path.write_text(bad)
                    with self.assertRaises(InstallError):
                        offline_config(path, hookdir=hooks)

    def test_pacstrap_explicit_config_and_keyring_no_shell(self):
        runner = RecordingRunner()
        with patch('emaki_installer.arch_backend.offline_config', return_value=Path('/offline.conf')):
            OfflinePacman(runner, Path('/target')).strap(['linux-lts', 'linux', 'linux'])
        argv = runner.commands[0][0]
        self.assertEqual(argv[:5], ['pacstrap', '-C', '/offline.conf', '-K', '/target'])
        self.assertEqual(argv.count('linux'), 1)
        self.assertIn('linux-lts', argv)

    def test_manual_partition_never_rewrites_table(self):
        runner = RecordingRunner()
        plan = make_plan(manual(), inventory(partitions=True))
        DeviceOperations(plan, runner).partition(SimpleNamespace(wipe=False))
        self.assertEqual(runner.commands, [])

    def test_format_only_invokes_requested_filesystem(self):
        runner = RecordingRunner()
        ops = DeviceOperations(make_plan(config(), inventory()), runner)
        ops.format(SimpleNamespace(value='fat32'), Path('/dev/fake1'))
        self.assertEqual(runner.commands[0][0], ['mkfs.fat', '-F', '32', '/dev/fake1'])

    def test_format_never_discards_the_device(self):
        # mkfs.btrfs and mkfs.ext4 discard the whole device first by default. On a VM disk whose
        # discard handling was broken, mkfs.btrfs exited 0 and left no superblock (VM check d3,
        # 2026-10-05); some USB bridges and SSDs mishandle it as well. fstrim.timer trims later.
        for fs, argv in (('btrfs', ['mkfs.btrfs', '-f', '-K', '/dev/fake2']),
                         ('ext4', ['mkfs.ext4', '-F', '-E', 'nodiscard', '/dev/fake2']),
                         ('fat32', ['mkfs.fat', '-F', '32', '/dev/fake2'])):  # mkfs.fat never discards
            with self.subTest(fs):
                runner = RecordingRunner()
                DeviceOperations(make_plan(config(), inventory()), runner).format(SimpleNamespace(value=fs),
                                                                                  Path('/dev/fake2'))
                self.assertEqual(runner.commands, [(argv, {})])

    def test_erase_sfdisk_half_open_geometry_and_tree_verification(self):
        import json
        plan = make_plan(config(), inventory())
        runner = RecordingRunner()
        query = ('lsblk', '--tree', '-J', '-b', '-o', 'PATH,TYPE,PARTN,START,SIZE,PTTYPE', '/dev/vda')
        runner.responses[query] = json.dumps({'blockdevices': [{'pttype': 'gpt', 'children': [
            {'path': f'/dev/vda{p.number}', 'partn': p.number, 'start': p.start // 512, 'size': p.size}
            for p in plan.partitions]}]})
        archparts = [SimpleNamespace(dev_path=None) for _ in plan.partitions]
        DeviceOperations(plan, runner).partition(SimpleNamespace(wipe=True, partitions=archparts))
        command, kwargs = runner.commands[0]
        self.assertEqual(command[0], 'sfdisk')
        self.assertIn('start=2048, size=2097152, type=U', kwargs['input'])
        self.assertEqual(archparts[0].dev_path, Path('/dev/vda1'))
        self.assertEqual(plan.partitions[1].path, '/dev/vda2')

    def test_post_partition_mismatch_fails_before_format(self):
        plan = make_plan(config(), inventory())
        runner = RecordingRunner()
        query = ('lsblk', '--tree', '-J', '-b', '-o', 'PATH,TYPE,PARTN,START,SIZE,PTTYPE', '/dev/vda')
        runner.responses[query] = '{"blockdevices":[{"pttype":"gpt","children":[]}]}'
        with self.assertRaises(InstallError):
            DeviceOperations(plan, runner).partition(SimpleNamespace(wipe=True, partitions=[1, 2]))
        self.assertFalse(any(command[0].startswith('mkfs') for command, _ in runner.commands))

    def test_live_plan_requires_the_unlock_screen_modules_and_font_only_when_encrypted(self):
        with tempfile.TemporaryDirectory() as temp:
            modules = Path(temp)
            self.assertEqual(missing_grub_modules(modules), list(GRUB_EARLY_MODULES))
            for name in GRUB_EARLY_MODULES:
                (modules / (name + '.mod')).write_bytes(b'')
            self.assertEqual(missing_grub_modules(modules), [])
            for name in ('png', 'tar'):
                (modules / (name + '.mod')).unlink()
            self.assertEqual(missing_grub_modules(modules), ['tar', 'png'])
        plain = make_plan(config(), inventory())
        encrypted = make_plan(dict(config(), encryption='separate', disk_password='disk-secret-for-tests'), inventory())
        rules = '! layout\n us English\n ru Russian\n'
        with patch('emaki_installer.arch_backend.offline_config'), \
                patch('emaki_installer.arch_backend.build_disk_config', return_value='disk-config'), \
                patch('emaki_installer.arch_backend.Path') as path, \
                patch('emaki_installer.arch_backend.GRUB_VISIBLE_FONT', FONT), \
                patch('emaki_installer.arch_backend.missing_grub_modules', return_value=['tar', 'png']) as missing:
            path.return_value.read_text.return_value = rules
            # Unencrypted plans never look at GRUB's modules.
            self.assertEqual(validate_live_plan(None, plain), 'disk-config')
            missing.assert_not_called()
            with self.assertRaises(InstallError) as raised:
                validate_live_plan(None, encrypted)
            self.assertEqual(raised.exception.code, Code.BOOT_VERIFY)
            self.assertIn('tar, png', raised.exception.message)
            missing.return_value = []
            self.assertEqual(validate_live_plan(None, encrypted), 'disk-config')
            with patch('emaki_installer.arch_backend.GRUB_VISIBLE_FONT', FONT.with_name('absent.pf2')):
                with self.assertRaises(InstallError) as raised:
                    validate_live_plan(None, encrypted)
            self.assertEqual(raised.exception.code, Code.BOOT_VERIFY)
            self.assertIn('font', raised.exception.message)


class Installer45:
    """The part of archinstall 4.5's Installer that touches /etc/vconsole.conf.

    minimal_installation writes the file through set_vconsole and then straps base,
    mkinitcpio and both kernels; mkinitcpio's pacman hook builds images from that file
    inside the same strap (installer.py 4.5, lines 936-965).
    """
    def __init__(self, target, disk_config, base_packages, kernels, silent):
        self.packages = base_packages + kernels

    def mount_ordered_layout(self):
        pass

    def sanity_check(self, skip_ntp, skip_wkd):
        pass

    def minimal_installation(self, mkinitcpio, hostname, locale_config):
        if locale_config:
            self.set_vconsole(locale_config)
        self.pacman.strap(self.packages)
        self.set_hostname(hostname)
        if locale_config:
            self.set_locale(locale_config)
            self.set_keyboard_language(locale_config.kb_layout)


class StrapRunner(RecordingRunner):
    """Remembers /etc/vconsole.conf as the strap's image build would read it."""
    def __init__(self, target):
        super().__init__()
        self.target, self.at_strap = target, []

    def run(self, argv, **kwargs):
        if str(argv[0]) == 'pacstrap':
            path = self.target / 'etc/vconsole.conf'
            self.at_strap.append(path.read_text() if path.exists() else None)
        return super().run(argv, **kwargs)


class ConsoleFileTests(unittest.TestCase):
    def test_minimal_install_writes_the_whole_console_file_before_the_strap(self):
        # render.vconsole_conf is the one producer of /etc/vconsole.conf; the minimal install
        # must not leave a KEYMAP-only file for the kernels' image build in its strap.
        from emaki_installer.arch_backend import Backend
        from emaki_installer.render import console_keymap, vconsole_conf
        for layouts, font in ((layouts, font) for layouts in (['cz', 'ru'], ['dvorak', 'ua'], ['us'])
                              for font in (None, 'ter-124b')):
            with self.subTest(layouts=layouts), tempfile.TemporaryDirectory() as temp:
                settings = config()
                settings['layouts'] = layouts
                plan, target = make_plan(settings, inventory()), Path(temp)
                runner = StrapRunner(target)
                handler = SimpleNamespace(perform_filesystem_operations=lambda: None)
                api = SimpleNamespace(
                    filesystem=SimpleNamespace(device_handler=None, udev_sync=None,
                                               FilesystemHandler=lambda disk_config: handler),
                    installer=SimpleNamespace(Installer=Installer45, PacmanConfig=None,
                                              accessibility_tools_in_use=None))
                with patch('emaki_installer.arch_backend.console_font', return_value=font), \
                        patch('emaki_installer.arch_backend.build_disk_config'), \
                        patch('emaki_installer.arch_backend.offline_config', return_value=Path('/offline.conf')):
                    backend = Backend(api, plan, runner, target)
                    backend.prepare()
                    backend.minimal(SimpleNamespace(kb_layout=console_keymap(layouts[0])))
                expected = vconsole_conf(layouts, font)
                self.assertEqual(runner.at_strap, [expected])
                self.assertEqual(backend.instance.packages,
                                 ['base', 'mkinitcpio', 'terminus-font', 'linux', 'linux-lts'])
                self.assertEqual((target / 'etc/vconsole.conf').read_text(), expected)


if __name__ == '__main__':
    unittest.main()
