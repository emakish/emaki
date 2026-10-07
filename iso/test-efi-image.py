#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline regressions for mastered GRUB assets and EFI partition evidence."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('image_check', Path(__file__).with_name('verify-image.py'))
image_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image_check)


class EfiImageTests(unittest.TestCase):
    def inspect(self, paths, reject_payload=True):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'appended_efi.img'
            image.write_bytes(b'efi-image-fixture')
            result = subprocess.CompletedProcess([], 0, stdout='\n'.join(paths))
            with patch.object(image_check.subprocess, 'run', return_value=result) as run:
                with contextlib.redirect_stdout(io.StringIO()):
                    report = image_check.inspect_efi(image, reject_payload)
                self.assertEqual(run.call_args.args[0], ['mdir', '-s', '-b', '-i', str(image), '::/'])
            return report

    def test_grub_partition_preserves_actual_size_and_entire_listing(self):
        paths = ['::/EFI/', '::/EFI/BOOT/', '::/EFI/BOOT/BOOTX64.EFI', '::/shellx64.efi']
        report = self.inspect(paths)
        self.assertEqual(report['bytes'], len(b'efi-image-fixture'))
        self.assertEqual(report['contents'], sorted(p[3:] for p in paths))

    def test_missing_firmware_fallback_binary_fails(self):
        with self.assertRaisesRegex(ValueError, 'fallback binary missing'):
            self.inspect(['::/EFI/emaki/grubx64.efi'])

    def test_old_partition_kernel_and_initramfs_copies_fail(self):
        for payload in ('vmlinuz-linux', 'initramfs-linux.img', 'VMLINUZ-LINUX-LTS'):
            paths = ['::/EFI/BOOT/BOOTX64.EFI', '::/emaki/boot/x86_64/' + payload]
            with self.subTest(payload=payload):
                with self.assertRaisesRegex(ValueError, 'duplicated'):
                    self.inspect(paths)
                # Baseline evidence must still capture the pre-migration partition.
                self.assertIn(paths[1][3:], self.inspect(paths, False)['contents'])

    def verify_fixture(self, loader, *, bad_entries=None, payload=False, missing_font=False,
                       version='0.3.0', legacy_leftovers=False, missing_payload=None,
                       extra_reference=None):
        """Run all verifier policies with fixture extraction at the tool boundary."""
        paths = ['/EFI/BOOT/BOOTX64.EFI']
        paths += (['/loader/entries/linux.conf'] if loader == 'systemd-boot'
                  else ['/boot/grub/grub.cfg'])
        paths += ['/emaki/boot/vmlinuz-linux', '/emaki/boot/initramfs-linux.img',
                  '/emaki/boot/vmlinuz-linux-lts', '/emaki/boot/initramfs-linux-lts.img']
        if missing_payload:
            paths.remove('/emaki/boot/' + missing_payload)
        efi_paths = ['::/EFI/BOOT/BOOTX64.EFI']
        if loader == 'systemd-boot':
            efi_paths += ['::/loader/entries/linux.conf']
        if payload:
            efi_paths += ['::/emaki/boot/vmlinuz-linux']
        entry = ('linux /emaki/boot/vmlinuz-linux\n'
                 'initrd /emaki/boot/initramfs-linux.img\n'
                 'options archisobasedir=emaki copytoram=n\n')

        def contents(location, text):
            return text + (extra_reference[1] if extra_reference and extra_reference[0] == location else '')

        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'fixture.iso'
            image.write_bytes(b'fixture')

            def run(command, **kwargs):
                output = ''
                if command[0] == 'xorriso':
                    if '-find' in command:
                        output = '\n'.join(repr(p) for p in paths)
                    else:
                        for i, arg in enumerate(command):
                            if arg == '-extract':
                                source, target = command[i + 1:i + 3]
                                target = Path(target)
                                if source == '/boot':
                                    grub = target / 'grub'
                                    grub.mkdir(parents=True)
                                    config = 'linux /emaki/boot/vmlinuz-linux copytoram=n\n'
                                    config += 'initrd /emaki/boot/initramfs-linux.img\n'
                                    if legacy_leftovers:
                                        config += 'background_image /boot/grub/background.png\n'
                                    if loader == 'grub':
                                        config += ('linux /emaki/boot/vmlinuz-linux-lts copytoram=n\n'
                                                   'initrd /emaki/boot/initramfs-linux-lts.img\n')
                                        config += ("menuentry 'Emaki (basic display, text console)' --id 'archlinux-safe' {\n"
                                                   'linux /emaki/boot/vmlinuz-linux copytoram=n nomodeset\n'
                                                   'initrd /emaki/boot/initramfs-linux.img\n}\n')
                                        config += 'loadfont /boot/grub/menu.pf2\n'
                                        (grub / 'grub.cfg').write_text(contents('grub', config))
                                        (grub / 'background.png').write_bytes(b'art')
                                        if not missing_font:
                                            (grub / 'menu.pf2').write_bytes(b'font')
                                    (grub / 'loopback.cfg').write_text(contents('loopback', config))
                                    syslinux = target / 'syslinux'
                                    syslinux.mkdir()
                                    (syslinux / 'live.cfg').write_text(contents('syslinux',
                                        'LINUX /emaki/boot/vmlinuz-linux\n'
                                        'INITRD /emaki/boot/initramfs-linux.img\n'
                                        'APPEND archisobasedir=emaki copytoram=n\n'))
                                elif source == '/emaki/version':
                                    target.write_text(version + '\n')
                                elif source == '/loader':
                                    target.mkdir()
                                    (target / 'linux.conf').write_text(contents('iso', entry if bad_entries != 'iso'
                                                                      else entry.replace('copytoram=n', '')))
                                else:
                                    target.write_bytes(b'fixture data')
                            elif arg == '-extract_boot_images':
                                target = Path(command[i + 1])
                                target.mkdir()
                                (target / 'appended_efi.img').write_bytes(b'efi image')
                elif command[0] == 'mdir':
                    output = '\n'.join(efi_paths)
                elif command[0] == 'mcopy':
                    (Path(command[-1]) / 'linux.conf').write_text(contents('efi', entry if bad_entries != 'efi'
                                                                 else entry.replace('copytoram=n', '')))
                elif command[0] == 'unsquashfs':
                    output = 'squashfs-root/etc/os-release\n'
                    if legacy_leftovers:
                        output += 'squashfs-root/usr/include/legacy.h\n'
                else:
                    self.fail('unexpected command: ' + str(command))
                return subprocess.CompletedProcess(command, 0, stdout=output)

            with patch.object(image_check.subprocess, 'run', side_effect=run), \
                    contextlib.redirect_stdout(io.StringIO()):
                return image_check.verify(image)

    def test_systemd_image_retains_old_loader_checks_and_allows_efi_payload(self):
        self.assertEqual(self.verify_fixture('systemd-boot', payload=True)['after']['loader'], 'systemd-boot')
        for location in ('iso', 'efi'):
            with self.subTest(location=location), self.assertRaisesRegex(ValueError, 'copytoram=n'):
                self.verify_fixture('systemd-boot', bad_entries=location)

    def test_grub_image_keeps_asset_and_efi_payload_checks(self):
        self.assertEqual(self.verify_fixture('grub')['after']['loader'], 'grub')
        with self.assertRaisesRegex(ValueError, 'font missing'):
            self.verify_fixture('grub', missing_font=True)
        with self.assertRaisesRegex(ValueError, 'duplicated'):
            self.verify_fixture('grub', payload=True)

    def test_missing_lts_payload_fails_finished_image_verification(self):
        for name in ('vmlinuz-linux-lts', 'initramfs-linux-lts.img'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'missing mastered boot payload.*' + name):
                self.verify_fixture('grub', missing_payload=name)

    def test_every_loader_surface_checks_its_own_references(self):
        for loader, location in (('grub', 'grub'), ('grub', 'loopback'), ('grub', 'syslinux'),
                                 ('systemd-boot', 'iso'), ('systemd-boot', 'efi')):
            for command in ('linux', 'initrd'):
                reference = f'{command} /emaki/boot/only-in-this-entry\n'
                with self.subTest(loader=loader, location=location, command=command):
                    with self.assertRaisesRegex(ValueError, 'missing mastered boot payload.*only-in-this-entry'):
                        self.verify_fixture(loader, extra_reference=(location, reference))

    def test_legacy_version_does_not_exempt_missing_boot_payload(self):
        with self.assertRaisesRegex(ValueError, 'missing mastered boot payload.*vmlinuz-linux'):
            self.verify_fixture('systemd-boot', version='0.2.0', missing_payload='vmlinuz-linux')

    def test_boot_reference_forms_and_exact_iso_paths(self):
        paths = {'/emaki/vmlinuz', '/emaki/early.img', '/emaki/root.img', '/boot/syslinux/local.img'}
        references = (
            'linux /emaki/vmlinuz quiet',
            'linuxefi /emaki/vmlinuz quiet',
            'linux16 /emaki/vmlinuz',
            'KERNEL /emaki/vmlinuz',
            'LINUX ::/emaki/vmlinuz',
            'initrd /emaki/early.img /emaki/root.img',
            'initrdefi /emaki/early.img /emaki/root.img',
            'initrd16 /emaki/root.img',
            'INITRD ::/emaki/early.img,::/emaki/root.img',
            'APPEND quiet initrd=/emaki/early.img,/emaki/root.img',
            'INITRD local.img',
            'initrd "/emaki/root.img" # comment',
        )
        with tempfile.TemporaryDirectory() as directory:
            boot = Path(directory)
            (boot / 'syslinux').mkdir()
            config = boot / 'syslinux/live.cfg'
            for reference in references:
                with self.subTest(reference=reference):
                    config.write_text(reference + '\n')
                    self.assertGreater(image_check.check_boot_payload(boot, paths), 0)
                    # Each reference must resolve, including later initrd operands.
                    for missing in paths:
                        if missing in reference or (missing.endswith('/local.img') and 'local.img' in reference):
                            with self.assertRaisesRegex(ValueError, 'missing mastered boot payload'):
                                image_check.check_boot_payload(boot, paths - {missing})
            for reference in ('linux /emaki/VMLINUZ', 'linux /emaki/vmlinuz-linux-lts',
                              'linux /%INSTALL_DIR%/vmlinuz', 'initrd ${prefix}/root.img',
                              'linux ' + str(config), 'linux', 'initrd # missing path'):
                with self.subTest(reference=reference), self.assertRaises(ValueError):
                    config.write_text(reference + '\n')
                    image_check.check_boot_payload(boot, paths)

    def test_internal_version_preserves_only_frozen_image_hygiene(self):
        self.assertEqual(self.verify_fixture('systemd-boot', version='0.2.0',
                                             legacy_leftovers=True)['after']['loader'], 'systemd-boot')
        with self.assertRaisesRegex(ValueError, 'loopback background'):
            self.verify_fixture('systemd-boot', version='0.2.1', legacy_leftovers=True)
        with self.assertRaisesRegex(ValueError, 'invalid mastered image version'):
            self.verify_fixture('systemd-boot', version='unknown')

    def test_loader_detection_uses_image_contents(self):
        listings = {
            'systemd-boot': ['/EFI/BOOT/BOOTx64.EFI', '/loader/entries/01-linux.conf',
                             '/boot/grub/loopback.cfg'],
            'grub': ['/EFI/BOOT/BOOTx64.EFI', '/boot/grub/grub.cfg'],
        }
        for loader, paths in listings.items():
            with self.subTest(loader=loader):
                result = subprocess.CompletedProcess([], 0, stdout='\n'.join(repr(p) for p in paths))
                with patch.object(image_check.subprocess, 'run', return_value=result):
                    self.assertEqual(image_check.detect_bootloader(Path('/candidate.iso')), loader)
        for paths in ([], ['/boot/grub/grub.cfg'],
                      listings['systemd-boot'] + listings['grub']):
            result = subprocess.CompletedProcess([], 0, stdout='\n'.join(repr(p) for p in paths))
            with patch.object(image_check.subprocess, 'run', return_value=result):
                with self.assertRaises(ValueError):
                    image_check.detect_bootloader(Path('/candidate.iso'))

    def test_loopback_background_options_require_the_referenced_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            boot = Path(directory)
            grub = boot / 'grub'
            grub.mkdir()
            config = grub / 'loopback.cfg'
            asset = grub / 'custom.png'
            for options in ('', '--mode stretch ', '--mode=stretch ', '--mode normal '):
                with self.subTest(options=options):
                    config.write_text('linux /emaki/boot/vmlinuz-linux copytoram=n\n'
                                      + 'background_image ' + options + '/boot/grub/custom.png\n')
                    with self.assertRaisesRegex(ValueError, 'background'):
                        image_check.check_loaders(boot, check_assets=True)
                    asset.write_bytes(b'')
                    with self.assertRaisesRegex(ValueError, 'background'):
                        image_check.check_loaders(boot, check_assets=True)
                    asset.write_bytes(b'background')
                    image_check.check_loaders(boot, check_assets=True)
                    asset.unlink()

    def test_each_referenced_graphics_asset_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            boot = Path(directory)
            grub = boot / 'grub'
            grub.mkdir()
            for name in ('grub.cfg', 'loopback.cfg'):
                (grub / name).write_text('loadfont /boot/grub/emaki-32.pf2\n')
            for name in ('background.png', 'emaki-32.pf2'):
                (grub / name).write_bytes(b'asset')
            image_check.check_grub_assets(boot)
            for name in ('grub.cfg', 'loopback.cfg', 'background.png', 'emaki-32.pf2'):
                path = grub / name
                original = path.read_bytes()
                path.write_bytes(b'')
                with self.subTest(asset=name), self.assertRaises(ValueError):
                    image_check.check_grub_assets(boot)
                path.write_bytes(original)
            for name in ('grub.cfg', 'loopback.cfg'):
                (grub / name).write_text('loadfont unicode\n')
            with self.assertRaisesRegex(ValueError, 'no staged font references'):
                image_check.check_grub_assets(boot)


if __name__ == '__main__':
    unittest.main()
