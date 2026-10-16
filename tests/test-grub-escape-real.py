#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Escape in the menu of a self-contained loader, in real GRUB under OVMF.

The loader's memdisk hands over to the menu on disk. Escape must leave that menu where it
is, as with a loader that reads its menu from disk, never drop to a bare grub> prompt.
Runs only with EMAKI_GRUB_TOOLS set to an extracted GRUB package root (grub-mkimage and
its x86_64-efi modules), qemu-system-x86_64 and OVMF (EMAKI_ISO_OVMF_DIR or the edk2
default). The guest has 256 MiB and no disk but a read-only FAT view of a scratch tree.
"""
from pathlib import Path
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'installer'))
from emaki_installer import boot

TOOLS = os.environ.get('EMAKI_GRUB_TOOLS')
FIRMWARE = Path(os.environ.get('EMAKI_ISO_OVMF_DIR', '/usr/share/edk2/x64'))
MARKER = 'emaki-escape-test'
TITLE = 'Escape test entry'
MODULES = ('part_msdos', 'part_gpt', 'fat', 'search', 'search_fs_file', 'memdisk', 'tar', 'normal',
           'configfile', 'echo', 'test', 'true', 'sleep', 'serial', 'terminal', 'terminfo')
MENU = f'''serial --unit=0 --speed=115200
terminal_input console
terminal_output serial
set timeout=-1
menuentry '{TITLE}' {{
  echo entry-ran
}}
'''


def wait_for(path, text, after=0, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = path.read_bytes().decode('latin-1') if path.exists() else ''
        if text in data[after:]:
            return data
        time.sleep(0.2)
    return None


class EscapeInRealGrub(unittest.TestCase):
    def setUp(self):
        if not TOOLS:
            raise unittest.SkipTest('EMAKI_GRUB_TOOLS is not set')
        self.package = Path(TOOLS)
        self.mkimage = self.package / 'usr/bin/grub-mkimage'
        self.modules = self.package / 'usr/lib/grub/x86_64-efi'
        self.qemu = shutil.which('qemu-system-x86_64')
        if not (self.mkimage.is_file() and self.modules.is_dir() and self.qemu
                and (FIRMWARE / 'OVMF_CODE.4m.fd').is_file() and (FIRMWARE / 'OVMF_VARS.4m.fd').is_file()):
            raise unittest.SkipTest('grub-mkimage, its modules, qemu or OVMF is missing')
        self.scratch = Path(tempfile.mkdtemp(prefix='emaki-grub-escape-'))
        self.addCleanup(shutil.rmtree, self.scratch, True)

    def loader(self, esp):
        """The self-contained loader the boot refresh and the Secure Boot installer build."""
        grub = self.scratch / 'grub'
        shutil.copytree(self.modules, grub / 'x86_64-efi')
        load_cfg = f'search --no-floppy --file --set=root /{MARKER}\n'
        prefix, early, memdisk = boot.grub_loader_payload(grub, None, '', None, load_cfg, b'')
        (self.scratch / 'early.cfg').write_bytes(early)
        (self.scratch / 'memdisk.tar').write_bytes(memdisk)
        target = esp / 'EFI/BOOT/BOOTX64.EFI'
        target.parent.mkdir(parents=True)
        subprocess.run([self.mkimage, '--directory=' + str(self.modules), '--format=x86_64-efi',
                        '--compression=none', '--memdisk=' + str(self.scratch / 'memdisk.tar'),
                        '--prefix=' + prefix, '--config=' + str(self.scratch / 'early.cfg'),
                        '--output=' + str(target), *MODULES], check=True, capture_output=True)

    def test_escape_keeps_the_disk_menu(self):
        esp = self.scratch / 'esp'
        self.loader(esp)
        (esp / MARKER).write_text('')
        (esp / 'boot/grub').mkdir(parents=True)
        (esp / 'boot/grub/grub.cfg').write_text(MENU)
        vars_copy = self.scratch / 'OVMF_VARS.4m.fd'
        shutil.copyfile(FIRMWARE / 'OVMF_VARS.4m.fd', vars_copy)
        serial, monitor = self.scratch / 'serial.log', self.scratch / 'monitor.sock'
        argv = ['nice', '-n', '15', self.qemu, '-machine', 'q35', '-m', '256', '-display', 'none',
                '-no-reboot', '-serial', 'file:' + str(serial),
                '-monitor', f'unix:{monitor},server,nowait',
                '-drive', f'if=pflash,format=raw,readonly=on,file={FIRMWARE / "OVMF_CODE.4m.fd"}',
                '-drive', f'if=pflash,format=raw,file={vars_copy}',
                '-drive', f'file=fat:{esp},format=raw,if=virtio,readonly=on']
        if os.access('/dev/kvm', os.R_OK | os.W_OK):
            argv[4:4] = ['-enable-kvm']
        guest = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(guest.wait)
        self.addCleanup(guest.kill)
        self.assertIsNotNone(wait_for(serial, TITLE, timeout=90), 'the disk menu never appeared')
        # The menu's first frame is drawn before it reads keys.
        time.sleep(2)
        shown = len(serial.read_bytes())
        deadline = time.monotonic() + 10
        while not monitor.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        with socket.socket(socket.AF_UNIX) as control:
            control.connect(str(monitor))
            for _ in range(3):
                control.sendall(b'sendkey esc\n')
                time.sleep(0.7)
            time.sleep(2)
            after = serial.read_bytes()[shown:].decode('latin-1')
            self.assertNotIn('grub>', after, 'Escape left the menu for a bare grub> prompt')
            # The menu still answers: Enter runs its entry.
            control.sendall(b'sendkey ret\n')
            self.assertIsNotNone(wait_for(serial, 'entry-ran', after=shown, timeout=15),
                                 'the menu no longer runs its entry after Escape')


if __name__ == '__main__':
    unittest.main()
