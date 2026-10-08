#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real GRUB image/menu checks; hardware discovery is explicitly separate.

Set EMAKI_GRUB_TOOLS to an extracted GRUB package root when it is not installed.
Artifacts, including the scratch ESP and FAT image, stay in .cache/evidence.
The Windows scanner fixture supplies discovery only; its menu generator, library,
filesystem probes and syntax checker are the real packaged programs.
"""
from pathlib import Path
import io
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'installer'), str(ROOT / 'grub')]
from emaki_installer import boot
sys.modules['emaki_boot.boot'] = boot
from emaki_boot import refresh

LUKS = '01234567-89ab-cdef-0123-456789abcdef'
FSUUID = 'abcdef01-2345-6789-abcd-ef0123456789'
GENERATION = Path('/boot/emaki') / ('a' * 32)
PACKAGE = Path(os.environ.get('EMAKI_GRUB_TOOLS', '/'))
if PACKAGE == Path('/') and not shutil.which('grub-mkimage'):
    PACKAGE = Path('/tmp/emaki-grub-tools')
MODULES = Path(os.environ.get('EMAKI_GRUB_MODULES', str(PACKAGE / 'usr/lib/grub/x86_64-efi')))


def tool(name):
    packaged = PACKAGE / 'usr/bin' / name
    found = str(packaged) if packaged.is_file() else shutil.which(name)
    if not found:
        raise unittest.SkipTest('Missing real executable: ' + name)
    return found


def execute(args, **kwargs):
    result = subprocess.run([str(a) for a in args], text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise AssertionError(f'{args[0]} exited {result.returncode}: {result.stderr}')
    return result.stdout


class RealGrub(unittest.TestCase):
    def setUp(self):
        evidence = ROOT / '.cache/evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='boot-refresh-real-', dir=evidence)
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)

    def check_script(self, name, content):
        path = self.work / name
        path.write_text(content)
        execute([tool('grub-script-check'), path])

    def test_real_install_to_scratch_esp(self):
        # Ordinary directories do not reproduce separate ESP/root partitions on
        # one disk. That discovery and firmware behavior require the VM matrix.
        if not MODULES.is_dir():
            self.skipTest('Missing real x86_64-efi GRUB module directory: ' + str(MODULES))
        esp, stage = self.work / 'esp', self.work / 'generation'
        esp.mkdir()
        stage.mkdir()
        # Even a no-NVRAM install needs UUID discovery on the backing filesystem.
        # Test that capability first: an ordinary user may lack block-device read
        # access, and a container may not expose the device at all.
        probe = subprocess.run([tool('grub-probe'), '--target=fs_uuid', str(stage)],
                               text=True, capture_output=True)
        if probe.returncode:
            self.skipTest('Scratch filesystem UUID discovery is unavailable '
                          f'(grub-probe exited {probe.returncode}): {probe.stderr.strip()}')
        args = [tool('grub-install'), '--target=x86_64-efi', '--directory=' + str(MODULES),
                '--efi-directory=' + str(esp), '--boot-directory=' + str(stage),
                '--bootloader-id=Emaki', '--no-nvram']
        result = subprocess.run(args, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        image = (esp / 'EFI/Emaki/grubx64.efi').read_bytes()
        self.assertTrue(image.startswith(b'MZ'))
        for path in MODULES.glob('*.mod'):
            self.assertEqual((stage / 'grub/x86_64-efi' / path.name).read_bytes(), path.read_bytes())
        load_cfg = stage / 'grub/x86_64-efi/load.cfg'
        if load_cfg.exists():
            self.check_script('installed-load.cfg', load_cfg.read_text())

    def test_real_plain_and_encrypted_images_and_menus(self):
        if not MODULES.is_dir():
            self.skipTest('Missing real x86_64-efi GRUB module directory: ' + str(MODULES))
        cases = [('plain-ext4-fixed', False, '', False),
                 ('plain-btrfs-fixed', False, '/@', False),
                 ('plain-ext4-search', False, '', True),
                 ('plain-btrfs-search', False, '/@', True),
                 ('encrypted', True, '/@', True)]
        for name, encrypted, fsroot, has_load_cfg in cases:
            with self.subTest(layout=name):
                stage = self.work / name
                platform = stage / 'grub/x86_64-efi'
                shutil.copytree(MODULES, platform)
                identity = {'uuid': FSUUID, 'luks': LUKS if encrypted else None,
                            'fsroot': fsroot}
                # Discovery is a fixture. Same-disk plain installations may
                # embed only a partition prefix and produce no load.cfg.
                load_cfg = (f'cryptomount -u {LUKS}\n' if encrypted else '')
                load_cfg += f'search.fs_uuid {FSUUID} root\n'
                load_cfg += f'set prefix=($root){identity["fsroot"]}{GENERATION}/grub\n'
                if has_load_cfg:
                    (platform / 'load.cfg').write_text(load_cfg)
                else:
                    fixed_prefix = f'(,gpt2){fsroot}{GENERATION}/grub'
                    installed = stage / 'installed.efi'
                    execute([tool('grub-mkimage'), '--directory=' + str(MODULES),
                             '--format=x86_64-efi', '--compression=none',
                             '--prefix=' + fixed_prefix, '--output=' + str(installed),
                             *refresh.IMAGE_MODULES])
                    self.assertIn(fixed_prefix.encode() + b'\0', installed.read_bytes())
                    self.assertFalse((platform / 'load.cfg').exists())
                if not encrypted:
                    load_cfg = refresh.plain_load_config(identity, GENERATION)
                font = (ROOT / 'installer/assets/grub/unlock-24.pf2').read_bytes()
                prefix, early, memdisk = refresh.loader_payload(stage / 'grub', GENERATION,
                                                               identity, load_cfg, font)
                config, disk, image = stage / 'early.cfg', stage / 'early.tar', stage / 'early.efi'
                config.write_bytes(early)
                disk.write_bytes(memdisk)
                execute([tool('grub-mkimage'), '--directory=' + str(MODULES),
                         '--format=x86_64-efi', '--compression=none', '--prefix=' + prefix,
                         '--config=' + str(config), '--memdisk=' + str(disk),
                         '--output=' + str(image), *refresh.IMAGE_MODULES])
                refresh.verify_image(image.read_bytes(), prefix, early, memdisk, identity['luks'])
                execute([tool('grub-file'), '--is-x86_64-efi', image])
                self.check_script('early.cfg', early.decode())
                with tarfile.open(fileobj=io.BytesIO(memdisk)) as archive:
                    if not encrypted:
                        self.assertEqual(archive.extractfile('start.cfg').read(),
                                         (f'search.fs_uuid {FSUUID} root\n'
                                          f'set prefix=($root){fsroot}{GENERATION}/grub\n').encode())
                    for member in archive:
                        if member.name.endswith('.cfg'):
                            self.check_script('embedded.cfg', archive.extractfile(member).read().decode())
                menu = ''
                crypt = f' cryptdevice=UUID={LUKS}:emaki-root' if encrypted else ''
                for kernel in ('linux', 'linux-lts'):
                    for suffix in ('', '-fallback'):
                        menu += (f'menuentry "Emaki {kernel}{suffix}" {{\n'
                                 f' linux {identity["fsroot"]}/boot/vmlinuz-{kernel} root=UUID={FSUUID}{crypt}\n'
                                 f' initrd {identity["fsroot"]}/boot/initramfs-{kernel}{suffix}.img\n}}\n')
                menu = refresh.rewrite_menu(menu, GENERATION, identity['fsroot'])
                refresh.verify_menu(menu, identity, GENERATION)
                self.check_script('grub.cfg', menu)
                trial = refresh.trial_menu(GENERATION.name, 'E0A1-0001', menu)
                self.assertEqual(trial.count('menuentry '), menu.count('menuentry '))
                self.assertEqual(trial.count('chainloader '), 1)
                self.check_script('trial-grub.cfg', trial)
                self.assertNotIn(str(GENERATION / 'vmlinuz-'), menu)
                self.assertNotIn(str(GENERATION / 'initramfs-'), menu)
                self.assertIn('emaki.generation=', menu)

    def test_real_windows_generator_after_defaults_migration(self):
        generator = PACKAGE / 'etc/grub.d/30_os-prober'
        library = PACKAGE / 'usr/share/grub'
        if not generator.is_file() or not (library / 'grub-mkconfig_lib').is_file():
            self.skipTest('Missing real 30_os-prober generator or grub-mkconfig_lib')
        disk = self.work / 'windows.img'
        with disk.open('wb') as output:
            output.truncate(32 * 1024 * 1024)
        execute([tool('mkfs.fat'), disk])
        uuid = execute([tool('grub-probe'), '--device', disk, '--target=fs_uuid']).strip()
        fixture = self.work / 'scanner'
        fixture.mkdir()
        scanner = fixture / 'os-prober'
        scanner.write_text('#!/bin/sh\nprintf "%s\\n" ' + shlex.quote(
            str(disk) + '@/EFI/Microsoft/Boot/bootmgfw.efi:Windows Boot Manager:Windows:efi') + '\n')
        scanner.chmod(0o755)
        linux_scanner = fixture / 'linux-boot-prober'
        linux_scanner.write_text('#!/bin/sh\nexit 0\n')
        linux_scanner.chmod(0o755)
        old = boot.grub_defaults(alongside=True, luks_uuid=LUKS)
        migrated = refresh.migrate_defaults(old, (ROOT / 'grub/defaults.cfg').read_text())
        self.assertEqual(refresh.assignments(migrated)['GRUB_DISABLE_OS_PROBER'], 'false')
        env = dict(os.environ, PATH=str(fixture) + ':' + os.environ['PATH'],
                   pkgdatadir=str(library), grub_probe=tool('grub-probe'),
                   GRUB_DISABLE_OS_PROBER=refresh.assignments(migrated)['GRUB_DISABLE_OS_PROBER'])
        menu = execute(['sh', generator], env=env)
        self.assertIn('Windows Boot Manager', menu)
        self.assertIn('chainloader /EFI/Microsoft/Boot/bootmgfw.efi', menu)
        self.assertIn(uuid, menu)
        rewritten = refresh.rewrite_menu(menu, GENERATION, '/@')
        self.assertEqual(rewritten, menu)
        self.check_script('windows.cfg', rewritten)


class InstallCapabilities(unittest.TestCase):
    def install_case(self):
        case = RealGrub('test_real_install_to_scratch_esp')
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_unavailable_filesystem_discovery_skips_before_install(self):
        for diagnostic in ('Permission denied', 'Backing device is unavailable'):
            with self.subTest(diagnostic=diagnostic):
                case = self.install_case()
                # Neither message follows a particular version's GRUB error wording.
                result = subprocess.CompletedProcess([], 1, '', diagnostic)
                with mock.patch(__name__ + '.MODULES', case.work), \
                     mock.patch(__name__ + '.tool', side_effect=lambda name: name), \
                     mock.patch.object(subprocess, 'run', return_value=result) as run:
                    with self.assertRaisesRegex(unittest.SkipTest, 'filesystem UUID discovery'):
                        case.test_real_install_to_scratch_esp()
                    self.assertEqual(run.call_count, 1)
                    self.assertEqual(run.call_args.args[0][0], 'grub-probe')

    def test_install_failure_after_successful_discovery_is_not_skipped(self):
        case = self.install_case()

        def result(args, **kwargs):
            if args[0] == 'grub-probe':
                return subprocess.CompletedProcess(args, 0, FSUUID + '\n', '')
            return subprocess.CompletedProcess(args, 1, '', 'Installation failed')

        with mock.patch(__name__ + '.MODULES', case.work), \
             mock.patch(__name__ + '.tool', side_effect=lambda name: name), \
             mock.patch.object(subprocess, 'run', side_effect=result):
            with self.assertRaisesRegex(AssertionError, 'Installation failed'):
                case.test_real_install_to_scratch_esp()


if __name__ == '__main__':
    unittest.main(verbosity=2)
