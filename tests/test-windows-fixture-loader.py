#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""iso-make-windows-disk.sh --loader EFI: the synthetic Windows ESP can carry a real boot manager.

Under Secure Boot firmware refuses the unsigned diagnostic GRUB image the fixture writes by default,
so a BootNext to "Windows" proves nothing there; --loader puts a given EFI image (the
Microsoft-signed Windows Boot Manager of an installation medium) at EFI/Microsoft/Boot/bootmgfw.efi.
These checks run before any VM contact: the option is parsed, the file must be a PE image, and the
first call (no disk yet) still only creates the disk. The upload is exercised in the Secure Boot VM.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'tests/vm/iso-make-windows-disk.sh'


class LoaderOption(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.base = Path(self.work.name)
        (self.base / 'bin').mkdir()
        stub = self.base / 'bin/qemu-img'
        stub.write_text('#!/bin/sh\necho "$@" >"$STUB_LOG"\n: >"$4"\n')
        stub.chmod(0o755)
        self.log = self.base / 'qemu-img.log'

    def tearDown(self):
        self.work.cleanup()

    def run_script(self, *args):
        return subprocess.run(['bash', str(SCRIPT), *args, '--dir', str(self.base / 'vm'), '--ssh-port', '2299'],
                              text=True, capture_output=True, timeout=60,
                              env=dict(os.environ, PATH=f'{self.base / "bin"}{os.pathsep}{os.environ["PATH"]}',
                                       STUB_LOG=str(self.log), HOME=str(self.base)))

    def test_a_missing_or_non_pe_loader_is_refused(self):
        disk = str(self.base / 'windows.qcow2')
        result = self.run_script(disk, '--loader', str(self.base / 'absent.efi'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unreadable loader', result.stderr)
        text = self.base / 'text.efi'
        text.write_text('not an image\n')
        result = self.run_script(disk, '--loader', str(text))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('the loader is not an EFI (PE) image', result.stderr)
        self.assertFalse(self.log.exists(), 'no disk is created on a refused loader')

    def test_the_first_call_with_a_loader_creates_the_disk_only(self):
        loader = self.base / 'bootmgfw.efi'
        loader.write_bytes(b'MZ' + bytes(510))
        disk = self.base / 'windows.qcow2'
        result = self.run_script(str(disk), '--loader', str(loader))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'Created {disk}', result.stdout)
        self.assertEqual(self.log.read_text().split()[:3], ['create', '-f', 'qcow2'])

    def test_the_running_target_is_recognised_with_run_iso_drive_options(self):
        # run-iso.sh passes file=DISK,if=none,id=target,format=qcow2,discard=unmap; the target check
        # must accept the options after the format (it refused every VM before).
        disk = self.base / 'windows.qcow2'
        disk.touch()
        vm = self.base / 'vm'
        vm.mkdir()
        stand_in = subprocess.Popen(['python3', '-c', 'import time; time.sleep(60)', '-drive',
                                     f'file={disk},if=none,id=target,format=qcow2,discard=unmap', '-device',
                                     'virtio-blk-pci,drive=target,serial=emaki-target'])
        try:
            (vm / 'qemu.pid').write_text(f'{stand_in.pid}\n')
            os.environ['EMAKI_ISO_SSH_KEY'] = str(self.base / 'no-key')
            result = self.run_script(str(disk))
        finally:
            os.environ.pop('EMAKI_ISO_SSH_KEY', None)
            stand_in.kill()
            stand_in.wait()
        self.assertNotIn('does not match the running target', result.stderr)
        self.assertIn('SSH key unavailable', result.stderr)  # the next step, before any guest contact

    def test_the_esp_size_is_checked_and_combines_with_a_loader(self):
        # --esp-mib 260: Windows' ESP on 4Kn disks, above the boot refresh's 256 MiB floor.
        disk = self.base / 'windows.qcow2'
        result = self.run_script(str(disk), '--esp-mib', '300')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--esp-mib must be 100, 260 or 512', result.stderr)
        self.assertFalse(self.log.exists(), 'no disk is created on a refused size')
        loader = self.base / 'bootmgfw.efi'
        loader.write_bytes(b'MZ' + bytes(510))
        result = self.run_script(str(disk), '--esp-mib', '260', '--loader', str(loader))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'Created {disk}', result.stdout)

    def test_other_extra_arguments_still_show_the_usage(self):
        result = self.run_script(str(self.base / 'windows.qcow2'), '--loaders', 'x')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Usage: iso-make-windows-disk.sh DISK.qcow2 [--loader EFI]', result.stderr)


if __name__ == '__main__':
    unittest.main()
