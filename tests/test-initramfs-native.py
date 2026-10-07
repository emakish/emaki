#!/usr/bin/python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build actual images without writing to host boot or configuration directories."""
import importlib.util
import itertools
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('boot_defaults', REPO / 'upkeep/emaki_boot_defaults.py')
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)


class NativeInitramfsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for executable in ('mkinitcpio', 'lsinitcpio', 'bwrap'):
            if not shutil.which(executable):
                raise unittest.SkipTest(f'{executable} is required for native image builds')
        cls.kernels = sorted(path.name for path in Path('/usr/lib/modules').glob('*')
                             if (path / 'modules.dep').is_file())
        if not cls.kernels:
            raise unittest.SkipTest('No installed kernel modules available')
        probe = subprocess.run(['bwrap', '--ro-bind', '/', '/', '--dev', '/dev',
                                '--proc', '/proc', 'true'], capture_output=True, text=True)
        if probe.returncode:
            raise unittest.SkipTest('Read-only build sandbox unavailable: ' + probe.stderr.strip())

    def test_migrated_native_configs_build_complete_images(self):
        self.build_configs(btrfs=False)

    def test_migrated_btrfs_configs_build_complete_images(self):
        missing = []
        for kind, variable in (('install', 'MKINITCPIO_INSTALL'), ('hooks', 'MKINITCPIO_HOOKS')):
            paths = os.environ.get(variable, f'/etc/initcpio/{kind}:/usr/lib/initcpio/{kind}')
            if not any((Path(directory) / 'grub-btrfs-overlayfs').is_file()
                       for directory in paths.split(':')):
                missing.append(f'{kind}/grub-btrfs-overlayfs')
        if missing:
            reason = 'Genuine upstream hooks required: ' + ', '.join(missing)
            if 'MKINITCPIO_INSTALL' in os.environ or 'MKINITCPIO_HOOKS' in os.environ:
                self.fail(reason)
            self.skipTest(reason + '; set MKINITCPIO_INSTALL and MKINITCPIO_HOOKS')
        self.build_configs(btrfs=True)

    def build_configs(self, btrfs):
        with tempfile.TemporaryDirectory(prefix='emaki-native-') as temporary:
            root = Path(temporary)
            def write(relative, content):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
                return path

            main = write('etc/mkinitcpio.conf', '')
            dropins = root / 'etc/mkinitcpio.conf.d'
            dropins.mkdir()
            # Older releases require this input for migration. New packages omit it.
            old_dropin = REPO / 'boot/defaults/90-emaki.conf'
            if old_dropin.exists():
                shutil.copyfile(old_dropin, dropins / old_dropin.name)
            for kernel in boot.KERNELS:
                write(f'usr/share/emaki/boot/{kernel}.preset',
                      (REPO / f'boot/defaults/{kernel}.preset').read_text())
            vconsole = write('vconsole.conf', 'KEYMAP=us\n')
            key = write('test-root.key', 'scratch initramfs key fixture\n')
            hook_paths = {}
            for kind, variable in (('install', 'MKINITCPIO_INSTALL'), ('hooks', 'MKINITCPIO_HOOKS')):
                staged = root / 'initcpio' / kind
                staged.mkdir(parents=True)
                for hook in ('emaki-snapshot-fstab', 'emaki-resume'):
                    shutil.copy2(REPO / 'initcpio' / kind / hook, staged)
                upstream = os.environ.get(variable, f'/etc/initcpio/{kind}:/usr/lib/initcpio/{kind}')
                hook_paths[variable] = f'{staged}:{upstream}'
            sandbox = ['bwrap', '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc',
                       '--bind', temporary, temporary,
                       '--ro-bind', str(main), '/etc/mkinitcpio.conf',
                       '--ro-bind', str(dropins), '/etc/mkinitcpio.conf.d',
                       '--ro-bind', str(vconsole), '/etc/vconsole.conf',
                       '--setenv', 'TMPDIR', temporary]
            for variable, paths in hook_paths.items():
                sandbox += ['--setenv', variable, paths]
            for encrypted in (False, True):
                released = next(line for line, choices in boot.LEGACY_CHOICES.items()
                                if choices == (btrfs, encrypted, True))
                files = str(key) if encrypted else ''
                main.write_text(f'MODULES=()\nBINARIES=()\nFILES=({files})\n' + released)
                for kernel in boot.KERNELS:
                    write(f'etc/mkinitcpio.d/{kernel}.preset', boot.legacy_preset(kernel))
                self.assertTrue(boot.apply(root), 'Migration must adopt the legacy configuration')
                self.assertIn(boot.hook_line(btrfs, encrypted, True), main.read_text())
                self.assertIn(' emaki-resume ', main.read_text())
                self.assertNotIn(' resume ', main.read_text())
                self.assertEqual((root / 'var/lib/emaki/migrations/initramfs-pending').read_text(),
                                 'linux\nlinux-lts\n')
                main.write_text(re.sub(r'^(HOOKS=\([^\n]*)(\))$', r'\1 shutdown\2',
                                       main.read_text(), flags=re.M))
                for kernel, explicit, fallback in itertools.product(self.kernels, (True, False), (False, True)):
                    with self.subTest(btrfs=btrfs, encrypted=encrypted, kernel=kernel,
                                      explicit_config=explicit, fallback=fallback):
                        image = root / 'initramfs.img'
                        # Graphics firmware is unrelated to boot-chain coverage and large.
                        skip = 'kms,autodetect' if fallback else 'kms'
                        command = sandbox + ['mkinitcpio', '--nopost', '--kernel', kernel, '-S', skip,
                                             '-g', str(image), '-z', 'cat']
                        if explicit:
                            command += ['-c', '/etc/mkinitcpio.conf']
                        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
                        output = result.stdout + result.stderr
                        self.assertEqual(result.returncode, 0, output)
                        for hook in ('base', 'udev', 'keyboard', 'keymap', 'emaki-resume', 'shutdown'):
                            self.assertIn(f'[{hook}]', output)
                        listing = subprocess.check_output(['lsinitcpio', '-l', str(image)], text=True)
                        entries = {line.removeprefix('./').rstrip('/') for line in listing.splitlines()}
                        for entry in ('init', 'usr/lib/systemd/systemd-udevd', 'hooks/keymap',
                                      'keymap.bin', 'hooks/emaki-resume', 'hooks/emaki-resume-upstream',
                                      'usr/lib/systemd/systemd-hibernate-resume', 'shutdown'):
                            self.assertIn(entry, entries)
                        self.assertNotIn('hooks/resume', entries)
                        builtins = (Path('/usr/lib/modules') / kernel / 'modules.builtin').read_text()
                        self.assertTrue(any('/usbhid.ko' in entry for entry in entries)
                                        or '/usbhid.ko' in builtins,
                                        'Keyboard USB HID support missing')
                        if btrfs:
                            for hook in ('grub-btrfs-overlayfs', 'emaki-snapshot-fstab'):
                                self.assertIn(f'[{hook}]', output)
                                self.assertIn(f'hooks/{hook}', entries)
                            for module in ('btrfs', 'overlay'):
                                self.assertTrue(any(f'/{module}.ko' in entry for entry in entries)
                                                or f'/{module}.ko' in builtins,
                                                f'{module} support missing')
                            self.assertNotIn('[fsck]', output)
                        else:
                            self.assertIn('[fsck]', output)
                            self.assertNotIn('hooks/grub-btrfs-overlayfs', entries)
                            self.assertNotIn('hooks/emaki-snapshot-fstab', entries)
                        if encrypted:
                            self.assertIn('[encrypt]', output)
                            self.assertIn('hooks/encrypt', entries)
                            self.assertIn(str(key).lstrip('/'), entries)
                        else:
                            self.assertNotIn('hooks/encrypt', entries)
                        image.unlink()


if __name__ == '__main__':
    unittest.main()
