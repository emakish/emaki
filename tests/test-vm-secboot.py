#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Secure Boot option of the ISO harness: EMAKI_VM_SECBOOT=2011|2023|both|none.

run-iso.sh runs for real against the fake QEMU (tests/vm/fixtures/fake-qemu.py), which records its
argv: the Secure Boot firmware needs SMM and a secure flash, the variable store is the variant's,
and a directory's store never changes firmware between runs. secboot-vars.sh refuses certificates
that are not Microsoft's; with EMAKI_VIRT_FW_VARS naming a real virt-fw-vars (python-virt-firmware)
the four stores are written and their db read back.
"""
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
VM = ROOT / 'tests/vm'
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)
VARIANTS = ('2011', '2023', 'both', 'none')
STUB_QEMU_IMG = '#!/usr/bin/env bash\n: >"$4"\n'
DB = {
    '2011': ['Microsoft Windows Production PCA 2011', 'Microsoft Corporation UEFI CA 2011'],
    '2023': ['Windows UEFI CA 2023', 'Microsoft UEFI CA 2023', 'Microsoft Option ROM UEFI CA 2023'],
    'both': ['Microsoft Windows Production PCA 2011', 'Windows UEFI CA 2023', 'Microsoft Corporation UEFI CA 2011',
             'Microsoft UEFI CA 2023', 'Microsoft Option ROM UEFI CA 2023'],
    'none': ['Microsoft Windows Production PCA 2011', 'Windows UEFI CA 2023'],
}


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class RunIsoSecureBoot(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='secboot-', dir=CACHE)
        self.base = Path(self.work.name)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        for name, text in (('qemu-img', STUB_QEMU_IMG),
                           ('qemu-system-x86_64', (VM / 'fixtures/fake-qemu.py').read_text())):
            (self.bin / name).write_text(text)
            (self.bin / name).chmod(0o755)
        self.host = self.base / 'host'
        self.host.mkdir()
        for name in ('OVMF_CODE.4m.fd', 'OVMF_CODE.secboot.4m.fd', 'kvm', 'renderD128'):
            (self.host / name).touch()
        (self.host / 'OVMF_VARS.4m.fd').write_text('plain store\n')
        self.stores = self.base / 'stores'
        self.stores.mkdir()
        for variant in VARIANTS:
            (self.stores / f'OVMF_VARS.secboot-{variant}.4m.fd').write_text(f'{variant} store\n')
        self.qemu_log = self.base / 'qemu.log'
        self.qemu_log.touch()
        self.launched = []

    def tearDown(self):
        for directory, process in self.launched:
            subprocess.run(['bash', str(VM / 'iso-stop.sh'), '--dir', str(directory), '--force'],
                           env=self.env(), capture_output=True, timeout=60)
            if process.poll() is None:
                process.terminate()
                process.wait(10)
            subprocess.run(['python3', str(VM / 'socket_runtime.py'), 'cleanup', str(directory)], capture_output=True)
        self.work.cleanup()

    def env(self, **extra):
        return dict(os.environ, PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}', FAKE_QEMU_LOG=str(self.qemu_log),
                    EMAKI_ISO_OVMF_DIR=str(self.host), EMAKI_ISO_KVM_DEVICE=str(self.host / 'kvm'),
                    EMAKI_ISO_RENDER_NODE=str(self.host / 'renderD128'), EMAKI_VM_SECBOOT_VARS=str(self.stores),
                    HOME=str(self.base / 'home')) | extra

    def start(self, name, secboot=None, wait=True, args=()):
        """Starts run-iso.sh --no-cd; returns (directory, argv of the fake QEMU or None, launcher output)."""
        directory = self.base / name
        directory.mkdir(exist_ok=True)
        (directory / 'target.qcow2').touch()
        before = len(self.qemu_log.read_text().splitlines())
        extra = {'EMAKI_VM_SECBOOT': secboot} if secboot is not None else {}
        log = directory / 'run.log'
        with open(log, 'w') as output:
            process = subprocess.Popen(['bash', str(VM / 'run-iso.sh'), '--no-cd', '--dir', str(directory),
                                        '--ssh-port', str(free_port()), *args], env=self.env(**extra), stdout=output,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        self.launched.append((directory, process))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            lines = self.qemu_log.read_text().splitlines()
            if len(lines) > before:
                return directory, lines[before].split(' '), log.read_text()
            if process.poll() is not None:
                return directory, None, log.read_text()
            time.sleep(0.05)
        self.fail('run-iso.sh neither started QEMU nor exited')

    def stop(self, directory):
        result = subprocess.run(['bash', str(VM / 'iso-stop.sh'), '--dir', str(directory), '--force'],
                                env=self.env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)

    @staticmethod
    def pflash(argv):
        return [re.search(r'file=([^,]+)$', arg).group(1) for arg in argv if arg.startswith('if=pflash')]

    def test_every_variant_starts_the_secure_boot_firmware_with_its_store(self):
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                directory, argv, log = self.start(f'vm-{variant}', variant)
                self.assertIsNotNone(argv, log)
                self.assertEqual(argv[argv.index('-machine') + 1], 'q35,smm=on')
                self.assertEqual(argv[argv.index('-global') + 1], 'driver=cfi.pflash01,property=secure,value=on')
                code, store = self.pflash(argv)
                self.assertEqual(code, str(self.host / 'OVMF_CODE.secboot.4m.fd'))
                self.assertEqual(store, str(directory / 'OVMF_VARS.4m.fd'))
                self.assertEqual(Path(store).read_text(), f'{variant} store\n')
                self.assertEqual((directory / 'firmware').read_text(), f'secboot-{variant}\n')
                self.stop(directory)

    def test_without_the_option_nothing_changes(self):
        directory, argv, log = self.start('vm-plain')
        self.assertIsNotNone(argv, log)
        self.assertEqual(argv[argv.index('-machine') + 1], 'q35')
        self.assertNotIn('-global', argv)
        code, store = self.pflash(argv)
        self.assertEqual(code, str(self.host / 'OVMF_CODE.4m.fd'))
        self.assertEqual(Path(store).read_text(), 'plain store\n')
        self.assertEqual((directory / 'firmware').read_text(), 'plain\n')

    def test_a_second_disk_is_attached_after_the_target(self):
        # Windows on one disk, Emaki erase-installed on the other (the fwupd policy check).
        second = self.base / 'windows.qcow2'
        second.touch()
        directory, argv, log = self.start('vm-two', 'both', args=('--disk2', str(second)))
        self.assertIsNotNone(argv, log)
        drives = [arg for arg in argv if arg.startswith('file=')]
        self.assertEqual(drives, [f'file={directory / "target.qcow2"},if=none,id=target,format=qcow2,discard=unmap',
                                  f'file={second},if=none,id=disk2,format=qcow2,discard=unmap'])
        devices = [arg for arg in argv if arg.startswith('virtio-blk-pci')]
        self.assertEqual(devices, ['virtio-blk-pci,drive=target,serial=emaki-target',
                                   'virtio-blk-pci,drive=disk2,serial=emaki-disk2'])
        self.stop(directory)
        _, argv, log = self.start('vm-two-missing', 'both', args=('--disk2', str(self.base / 'absent.qcow2')))
        self.assertIsNone(argv)
        self.assertIn('--disk2 must name an existing disk image', log)

    def test_an_unknown_variant_or_a_missing_store_is_refused_before_qemu(self):
        _, argv, log = self.start('vm-bad', 'secure')
        self.assertIsNone(argv)
        self.assertIn('EMAKI_VM_SECBOOT must be 2011, 2023, both or none', log)
        (self.stores / 'OVMF_VARS.secboot-2023.4m.fd').unlink()
        _, argv, log = self.start('vm-missing', '2023')
        self.assertIsNone(argv)
        self.assertIn('OVMF_VARS.secboot-2023.4m.fd (write it with tests/vm/secboot-vars.sh)', log)
        self.assertEqual(self.qemu_log.read_text(), '', 'QEMU must not be started')

    def test_a_directory_keeps_the_firmware_its_store_was_made_for(self):
        directory, argv, log = self.start('vm-keep', 'both')
        self.assertIsNotNone(argv, log)
        store = directory / 'OVMF_VARS.4m.fd'
        store.write_text('both store with an enrolled MOK\n')
        self.stop(directory)
        _, argv, log = self.start('vm-keep', 'both')
        self.assertIsNotNone(argv, log)
        self.assertEqual(store.read_text(), 'both store with an enrolled MOK\n', 'the guest-written store is kept')
        self.stop(directory)
        for other in ('2023', None):
            with self.subTest(other=other):
                _, argv, log = self.start('vm-keep', other)
                self.assertIsNone(argv)
                self.assertIn('belongs to secboot-both firmware', log)
        # A directory from before the option (a store, no marker) is a plain one.
        old = self.base / 'vm-old'
        old.mkdir()
        (old / 'OVMF_VARS.4m.fd').write_text('plain store\n')
        _, argv, log = self.start('vm-old', 'both')
        self.assertIsNone(argv)
        self.assertIn('belongs to plain firmware, not secboot-both', log)


class SecureBootStores(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='secboot-vars-', dir=CACHE)
        self.base = Path(self.work.name)

    def tearDown(self):
        self.work.cleanup()

    def run_script(self, *args, **env):
        return subprocess.run(['bash', str(VM / 'secboot-vars.sh'), *args], text=True, capture_output=True,
                              env=dict(os.environ, **env), timeout=120)

    def test_certificates_that_are_not_microsofts_are_refused(self):
        certs = self.base / 'certs'
        certs.mkdir()
        for name in ('windows-2011', 'windows-2023', 'ms-uefi-2011', 'ms-uefi-2023', 'ms-uefi-rom-2023'):
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                            '-subj', f'/CN={name}', '-keyout', str(self.base / 'key.pem'),
                            '-out', str(certs / f'{name}.pem')], check=True, capture_output=True)
        template = self.base / 'OVMF_VARS.4m.fd'
        template.write_bytes(b'\0' * 64)
        tool = self.base / 'virt-fw-vars'
        tool.write_text('#!/bin/sh\necho "must not run" >&2\nexit 9\n')
        tool.chmod(0o755)
        out = self.base / 'out'
        result = self.run_script('--out', str(out), '--virt-fw-vars', str(tool), '--template', str(template),
                                 '--certs', str(certs))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match Microsoft's certificate", result.stderr)
        self.assertNotIn('must not run', result.stderr)
        self.assertFalse(out.exists())

    def test_missing_tool_and_template_are_refused(self):
        result = self.run_script('--out', str(self.base / 'out'), '--virt-fw-vars', str(self.base / 'absent'),
                                 '--template', str(self.base / 'absent.fd'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('OVMF variable template unavailable', result.stderr)

    @unittest.skipUnless(os.environ.get('EMAKI_VIRT_FW_VARS'), 'EMAKI_VIRT_FW_VARS names no virt-fw-vars')
    def test_real_stores_carry_their_db(self):
        tool = os.environ['EMAKI_VIRT_FW_VARS']
        out = self.base / 'out'
        result = self.run_script('--out', str(out), '--virt-fw-vars', tool)
        self.assertEqual(result.returncode, 0, result.stderr)
        for variant in VARIANTS:
            with self.subTest(variant=variant):
                shown = subprocess.run([tool, '-i', str(out / f'OVMF_VARS.secboot-{variant}.4m.fd'), '-p', '-v'],
                                       text=True, capture_output=True, check=True).stdout
                sections = {block.split()[0][5:]: block for block in shown.split('\n\n') if block.startswith('name=')}
                self.assertIn('bool: ON', sections['SecureBootEnable'])
                self.assertIn('Windows OEM Devices PK', sections['PK'])
                for kek in ('Microsoft Corporation KEK CA 2011', 'Microsoft Corporation KEK 2K CA 2023'):
                    self.assertIn(kek, sections['KEK'])
                subjects = re.findall(r'subject CN=(.+)', sections['db'])
                self.assertEqual(subjects, DB[variant])


if __name__ == '__main__':
    unittest.main()
