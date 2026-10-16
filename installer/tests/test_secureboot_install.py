# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The installer's Secure Boot path: plan check, signed loader, shim on the ESP, MOK request."""
import io
import json
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from emaki_installer import secureboot
from emaki_installer import worker as worker_module
SECURE_BOOT_PACKAGES = secureboot.PACKAGES
from emaki_installer.errors import Code, InstallError
from emaki_installer.planner import make_plan
from emaki_installer.runtime import Redactor
from emaki_installer.worker import Worker
from support import RecordingRunner, config, inventory, manual, replaced

FIRMWARE = Path(__file__).resolve().parents[2] / 'tests/fixtures/firmware'
MAC_LISTING = (FIRMWARE / 'mac-before.txt').read_text()
MAC_AFTER_CREATE = (FIRMWARE / 'mac-created.txt').read_text()
MAC_CREATED = (FIRMWARE / 'mac-booted.txt').read_text()
MAC_ESP = '33333333-3333-4333-8333-333333333333'
SHIM_LISTING = MAC_AFTER_CREATE.replace(r'\EFI\Emaki\grubx64.efi', r'\EFI\Emaki\a\shimx64.efi')
# grub-install's entry 0003 and shim's new 0004, which efibootmgr --create puts first.
_GRUB_LINE = next(line for line in MAC_CREATED.splitlines() if line.startswith('Boot0003'))
BOTH_LISTING = MAC_CREATED.replace('BootOrder: 0003,0002', 'BootOrder: 0004,0003,0002').replace(
    _GRUB_LINE + '\n', _GRUB_LINE + '\n' + _GRUB_LINE.replace('Boot0003', 'Boot0004').replace(
        'grubx64.efi', 'a\\shimx64.efi') + '\n')
SHIM_ONLY_LISTING = '\n'.join(line for line in BOTH_LISTING.splitlines()
                              if not line.startswith('Boot0003')).replace('0004,0003,', '0004,') + '\n'
ARCH_SBAT = ('sbat,1,SBAT Version,sbat,1,https://github.com/rhboot/shim/blob/main/SBAT.md\n'
             'grub,4,Free Software Foundation,grub,2:2.16-1,https//www.gnu.org/software/grub/\n'
             'grub.arch,1,Arch Linux,grub,2:2.16-1,https://archlinux.org/packages/core/x86_64/grub/\n')


def pe(tag):
    """A PE32+ image whose certificate table holds one Authenticode entry (WIN_CERTIFICATE)."""
    data = bytearray(256)
    data[:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 64)
    data[64:68] = b'PE\0\0'
    struct.pack_into('<H', data, 88, 0x20b)
    struct.pack_into('<I', data, 88 + 108, 16)
    struct.pack_into('<II', data, 88 + 112 + 4 * 8, 256, 16)
    data[128:128 + len(tag)] = tag
    return bytes(data) + struct.pack('<IHH', 16, 0x0200, 2) + b'pkcs7sig'


SHIM_PE, MOK_PE = pe(b'shim'), pe(b'mokmanager')
LEVEL_WARNING = ("grub-mkimage: warning: Global SBAT level 4 in `sbat.csv' does not match "
                 'the build-time Global SBAT level 6.\n')


# Any real certificate stands in for the machine's: its subject name is what kmod reports
# as the signer of a module the machine key signed.
MACHINE_DER = (Path(__file__).parent / 'fixtures/secureboot/uefi2023.der').read_bytes()
MACHINE_CN = secureboot.CA_2023
DKMS_VERSION = '580.82.09'
DKMS_KERNELS = ('6.17.1-arch1-1', '6.12.50-1-lts')
NVIDIA_MODULES = ('nvidia', 'nvidia-modeset', 'nvidia-drm', 'nvidia-uvm')


def secure_inventory(cas='both'):
    data = inventory()
    data['hardware'] = {'secure_boot': True, 'secure_boot_cas': cas}
    return data


class SigningRunner(RecordingRunner):
    """Plays openssl, grub-mkimage, sbsign, sbverify and mokutil inside the target."""

    def __init__(self, root):
        super().__init__()
        self.root, self.signed = root, set()

    def run(self, argv, **kwargs):
        output = super().run(argv, **kwargs)
        argv = [str(x) for x in argv]
        if argv[0] != 'arch-chroot':
            return output
        command = argv[2:]
        if command[:3] == ['sh', '-c', '"$@" 2>&1']:
            command = command[4:]
        target = lambda name: self.root / str(name).lstrip('/')
        if command[:2] == ['openssl', 'req']:
            target(command[command.index('-keyout') + 1]).write_bytes(b'private key')
            target(command[command.index('-out') + 1]).write_bytes(b'certificate')
        elif command[:2] == ['openssl', 'x509']:
            target(command[command.index('-out') + 1]).write_bytes(b'DER certificate')
        elif command[0] == 'grub-mkimage':
            option = {part.split('=', 1)[0]: part.split('=', 1)[1] for part in command if part.startswith('--')}
            image = b'MZ' + target(option['--config']).read_bytes() + target(option['--memdisk']).read_bytes()
            target(option['--output']).write_bytes(image)
            sbat = target(option['--sbat']).read_text()
            return LEVEL_WARNING if '\ngrub,4,' in sbat else ''
        elif command[0] == 'sbsign':
            source, output = command[-1], command[command.index('--output') + 1]
            target(output).write_bytes(target(source).read_bytes() + b'+signature')
            self.signed.add(output)
        elif command[0] == 'sbverify':
            # A copy of a signed image verifies too (the slot's GRUB).
            copy = target(command[-1]).read_bytes() if target(command[-1]).is_file() else b''
            if command[-1] not in self.signed and not (copy[:2] == b'MZ' and copy.endswith(b'+signature')):
                from emaki_installer.errors import InstallError as Failure
                raise Failure(Code.COMMAND_FAILED, 'Signature verification failed')
        return output


class FirmwareRunner(SigningRunner):
    """SigningRunner plus a firmware that keeps its entries: grub-install's unsigned entry
    first, and a created entry appended last, so the installer must reorder it."""

    def __init__(self, root):
        super().__init__(root)
        self.entries = {'0002': ('Windows Boot Manager', r'\EFI\Microsoft\Boot\bootmgfw.efi'),
                        '0003': ('Emaki', r'\EFI\Emaki\grubx64.efi')}
        self.order, self.slot_at_create = ['0003', '0002'], None

    def listing(self):
        return ''.join([f"BootCurrent: 0002\nBootOrder: {','.join(self.order)}\n"] + [
            f'Boot{number}* {label}\tHD(1,GPT,{MAC_ESP},0x800,0x32000)/{loader}\n'
            for number, (label, loader) in sorted(self.entries.items())])

    def run(self, argv, **kwargs):
        output = super().run(argv, **kwargs)
        argv = [str(x) for x in argv]
        if argv[0] != 'efibootmgr':
            return output
        if argv[1:] == ['-v']:
            return self.listing()
        if argv[1] == '--create':
            self.slot_at_create = secureboot.read_slot(self.root / 'efi', 'a')
            number = f'{max(int(n, 16) for n in self.entries) + 1:04X}'
            self.entries[number] = (argv[argv.index('--label') + 1], argv[argv.index('--loader') + 1])
            self.order.append(number)
        elif argv[1] == '--bootorder':
            self.order = argv[2].split(',')
        elif argv[1] == '-b' and argv[3] == '-B':
            del self.entries[argv[2]]
            self.order.remove(argv[2])
        return ''


OTHER_ESP = '66666666-6666-4666-8666-666666666666'


class NvramRunner(SigningRunner):
    """SigningRunner plus firmware variables as efibootmgr 18 shows and changes them: entries
    on any partition, BootOrder and BootNext. --create puts the new entry first; -B also drops
    the number from BootOrder. stuck: numbers whose deletion the firmware ignores."""

    def __init__(self, root, entries, order, bootnext=None, stuck=()):
        super().__init__(root)
        self.entries, self.order, self.next, self.stuck = dict(entries), list(order), bootnext, set(stuck)
        self.esp_at_delete = {}

    def listing(self):
        head = 'BootCurrent: 0002\n' + (f'BootNext: {self.next}\n' if self.next else '')
        return head + f"BootOrder: {','.join(self.order)}\n" + ''.join(
            f"Boot{number}{'*' if active else ' '} {label}\tHD(1,GPT,{partuuid},0x800,0x32000)/{loader}\n"
            for number, (active, label, partuuid, loader) in sorted(self.entries.items()))

    def run(self, argv, **kwargs):
        output = super().run(argv, **kwargs)
        argv = [str(x) for x in argv]
        if argv[0] != 'efibootmgr':
            return output
        if argv[1:] == ['-v']:
            return self.listing()
        if argv[1] == '--create':
            number = f'{max(int(n, 16) for n in self.entries) + 1:04X}'
            self.entries[number] = (True, argv[argv.index('--label') + 1], MAC_ESP, argv[argv.index('--loader') + 1])
            self.order.insert(0, number)
        elif argv[1] == '--bootorder':
            self.order = argv[2].split(',')
        elif argv[1] == '--delete-bootnext':
            self.next = None
        elif argv[1] == '-b' and argv[3] == '-B':
            esp = self.root / 'efi'
            self.esp_at_delete[argv[2]] = (secureboot.slot_dir(esp, 'b').exists(), secureboot.read_slot(esp, 'a'))
            if argv[2] not in self.stuck:
                del self.entries[argv[2]]
                if argv[2] in self.order:
                    self.order.remove(argv[2])
        elif argv[1] == '-b' and argv[3] == '-a':
            active, *rest = self.entries[argv[2]]
            self.entries[argv[2]] = (True, *rest)
        return ''


class DkmsRunner(SigningRunner):
    """SigningRunner plus DKMS: NVIDIA built during pacstrap, signed by DKMS's own key."""

    def __init__(self, root, status_format='3', signs=True):
        super().__init__(root)
        self.status_format, self.signs = status_format, signs
        self.signer = {kernel: 'DKMS module signing key' for kernel in DKMS_KERNELS}
        self.config_at_build = []
        for kernel in DKMS_KERNELS:
            built = root / f'var/lib/dkms/nvidia/{DKMS_VERSION}/{kernel}/x86_64/module'
            built.mkdir(parents=True)
            for name in NVIDIA_MODULES:
                (built / (name + '.ko.zst')).write_bytes(b'module')
        (root / 'usr/bin').mkdir(parents=True, exist_ok=True)
        (root / 'usr/bin/dkms').write_text('#!/bin/sh\n')

    def run(self, argv, **kwargs):
        argv = [str(x) for x in argv]
        command = argv[2:] if argv[0] == 'arch-chroot' else []
        if command[:2] == ['openssl', 'x509']:
            super().run(argv, **kwargs)
            (self.root / command[command.index('-out') + 1].lstrip('/')).write_bytes(MACHINE_DER)
            return ''
        output = super().run(argv, **kwargs)
        if command == ['dkms', 'status']:
            line = ('nvidia/{v}, {k}, x86_64: installed' if self.status_format == '3'
                    else 'nvidia, {v}, {k}, x86_64: installed')
            return ''.join(line.format(v=DKMS_VERSION, k=kernel) + '\n' for kernel in DKMS_KERNELS) + \
                'nvidia/570.1, 6.1.0-old, x86_64: installed-weak from 6.1.0\n'
        if command[:3] == ['dkms', 'build', '--force']:
            self.config_at_build.append((self.root / str(secureboot.DKMS_CONFIG).lstrip('/')).is_file())
            if self.signs:
                self.signer[command[command.index('-k') + 1]] = MACHINE_CN
        if command[:1] == ['modinfo']:
            return self.signer[command[command.index('-k') + 1]] + '\n'
        return output


class PlanTests(unittest.TestCase):
    def test_each_trusted_ca_set_plans_a_signed_install_and_says_so(self):
        for cas, line in (('both', 'the Microsoft UEFI CA 2011 and 2023.'),
                          ('2011', 'the Microsoft UEFI CA 2011 only.'),
                          ('2023', 'the Microsoft UEFI CA 2023 only.')):
            with self.subTest(cas=cas):
                plan = make_plan(config(), secure_inventory(cas))
                self.assertEqual(plan.secure_boot, cas)
                self.assertIn('Secure Boot: on; the firmware trusts ' + line, plan.summary)
                self.assertTrue(any('blue screen' in row for row in plan.summary))

    def test_no_microsoft_third_party_ca_refuses_with_the_firmware_advice(self):
        with self.assertRaises(InstallError) as raised:
            make_plan(config(), secure_inventory('none'))
        self.assertEqual(raised.exception.code, Code.SECURE_BOOT)
        self.assertIn('turn on the third-party certificate option', raised.exception.message)
        self.assertIn('or turn Secure Boot off', raised.exception.message)

    def test_unreadable_state_or_db_still_refuses(self):
        for hardware in ({'secure_boot': None}, {'secure_boot': True}, {'secure_boot': True, 'secure_boot_cas': None}):
            with self.subTest(hardware=hardware):
                data = inventory()
                data['hardware'] = hardware
                with self.assertRaises(InstallError) as raised:
                    make_plan(config(), data)
                self.assertEqual(raised.exception.code, Code.SECURE_BOOT)

    def test_without_secure_boot_the_plan_is_unchanged(self):
        plain = make_plan(config(), inventory())
        data = inventory()
        data['hardware'] = {'secure_boot': False}
        off = make_plan(config(), data)
        self.assertEqual(off.secure_boot, '')
        self.assertEqual(off.summary, plain.summary)
        self.assertFalse(any('Secure Boot' in row for row in plain.summary))

    def test_shim_packages_only_with_secure_boot(self):
        self.assertFalse(set(SECURE_BOOT_PACKAGES) & set(worker_module.software_packages()))
        self.assertTrue(set(SECURE_BOOT_PACKAGES) <= set(worker_module.software_packages(secure_boot=True)))

    def test_inventory_reads_the_db_only_with_secure_boot_on(self):
        from emaki_installer import inventory as inventory_module
        source = Path(inventory_module.__file__).read_text()
        self.assertIn('if state is True:\n            # ', source)
        self.assertIn('hardware["secure_boot_cas"] = firmware_cas(self.root)', source)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def worker(self, plan_inventory, fs='btrfs', encryption='none'):
        worker = Worker(None, None, Redactor(), lambda *a, **kw: None, lambda line: None, target=self.root)
        settings = config(fs=fs)
        if encryption != 'none':
            settings['encryption'] = encryption
        worker.plan = make_plan(settings, plan_inventory)
        for part in worker.plan.partitions:
            part.path = '/dev/test' + str(part.number)
        worker.runner = SigningRunner(self.root)
        self.notices = []
        worker.notice = self.notices.append
        f = worker.files
        f.mkdir('/efi')
        esp = next(part for part in worker.plan.partitions if part.esp)
        esp.partuuid, esp.uuid = MAC_ESP, '1234-ABCD'
        worker.plan.root.uuid = '0f0e0d0c-0b0a-4908-8706-050403020100'
        worker.runner.responses[('efibootmgr', '-v')] = MAC_LISTING
        if worker.plan.btrfs:
            f.write('/etc/default/grub-btrfs/config', 'GRUB_BTRFS_SUBMENUNAME="Arch Linux snapshots"\n')
        for directory in ('hooks', 'install'):
            f.write(f'/usr/lib/initcpio/{directory}/emaki-snapshot-fstab', directory)
        for kernel in ('linux', 'linux-lts'):
            f.write('/etc/mkinitcpio.d/' + kernel + '.preset', 'preset')
            f.write('/boot/vmlinuz-' + kernel, 'kernel')
            f.write('/boot/initramfs-' + kernel + '.img', 'initramfs')
        f.write('/boot/grub/grub.cfg', 'if loadfont ($root)/@/usr/share/emaki/grub/fonts/menu-32.pf2 ; then\n'
                '  true\nfi\nlinux /@/boot/vmlinuz-linux root=x\nlinux /@/boot/vmlinuz-linux-lts root=x\n')
        f.write('/boot/grub/x86_64-efi/normal.mod', b'normal module')
        f.write('/boot/grub/fonts/unicode.pf2', b'unicode')
        f.write('/usr/share/emaki/grub/fonts/menu-32.pf2', b'menu face')
        f.write('/usr/share/emaki-shim-signed/shimx64.efi', SHIM_PE)
        f.write('/usr/share/emaki-shim-signed/mmx64.efi', MOK_PE)
        f.write('/usr/share/grub/sbat.csv', ARCH_SBAT)
        fstab = []
        for part, mp, sv in worker.plan.mounts:
            opts = part.options + (['subvol=/' + sv] if sv else [])
            fstab.append(f'UUID={part.uuid or f"uuid-{part.number}"} {mp} {part.fs} {",".join(opts)} 0 0')
        worker.runner.responses[('genfstab', '-U', '-f', str(self.root), str(self.root))] = '\n'.join(fstab)
        return worker

    def finish(self, worker):
        for name in ('/', '/etc', '/usr', '/var', '/boot'):
            worker.files.mkdir(name).chmod(0o755)
        worker.plan.config['online_update'] = False
        with patch('emaki_installer.worker.LOG') as log, patch.object(worker, 'record_installation'):
            log.read_bytes.return_value = b''
            worker.finish()

    def commands(self, worker):
        return [command[2:] if command[0] == 'arch-chroot' else command for command, _ in worker.runner.commands]

    def test_signed_loader_shim_and_mokmanager_replace_grub_installs_image(self):
        worker = self.worker(secure_inventory())
        worker.bootloader()
        f, commands = worker.files, self.commands(worker)
        # The key is made inside the target, root only.
        self.assertEqual(f.path('/var/lib/emaki/secureboot').stat().st_mode & 0o777, 0o700)
        self.assertTrue(any(c[:2] == ['openssl', 'req'] for c in commands))
        # One image built with SBAT data, rebuilt at the level grub-mkimage names, then signed.
        builds = [c for c in commands if c[:3] == ['sh', '-c', '"$@" 2>&1']]
        self.assertEqual(len(builds), 2)
        build = builds[-1][4:]
        self.assertIn('--sbat=/boot/grub/x86_64-efi/emaki-sbat.csv', build)
        self.assertIn('--compression=none', build)
        self.assertIn('--prefix=(memdisk)/boot/grub', build)
        self.assertTrue(set(secureboot.GRUB_MODULES) <= set(build))
        sbat = f.read('/boot/grub/x86_64-efi/emaki-sbat.csv')
        self.assertIn('\ngrub,6,Free Software Foundation,', sbat)
        self.assertTrue(sbat.endswith('grub.emaki,1,Emaki,grub,2:2.16-1,https://github.com/emakish/emaki\n'))
        image = f.path('/boot/grub/x86_64-efi/emaki-secureboot.efi').read_bytes()
        self.assertTrue(image.endswith(b'+signature'))
        # The memdisk carries the modules, GRUB's font and Emaki's faces at their packaged paths.
        memdisk = f.path('/boot/grub/x86_64-efi/emaki-secureboot.tar').read_bytes()
        with tarfile.open(fileobj=io.BytesIO(memdisk)) as archive:
            names = set(archive.getnames())
        self.assertTrue({'boot/grub/x86_64-efi/normal.mod', 'boot/grub/fonts/unicode.pf2',
                         'usr/share/emaki/grub/fonts/menu-32.pf2', 'start.cfg', 'boot/grub/grub.cfg'} <= names)
        start = f.read('/boot/grub/x86_64-efi/emaki-secureboot.cfg')
        self.assertIn('set prefix=(memdisk)/boot/grub', start)
        with tarfile.open(fileobj=io.BytesIO(memdisk)) as archive:
            self.assertEqual(archive.extractfile('start.cfg').read().decode(),
                             'search.fs_uuid 0f0e0d0c-0b0a-4908-8706-050403020100 root\n'
                             'set prefix=($root)/@/boot/grub\n')
        # shim is what the firmware starts; GRUB sits next to it in slot a and on the removable
        # path, with the same bytes. Slot a is complete (its manifest names every file); slot b
        # and the old top-level loaders do not exist.
        esp = {path.relative_to(self.root / 'efi').as_posix(): path.read_bytes()
               for path in (self.root / 'efi').rglob('*') if path.is_file()}
        self.assertEqual(esp['EFI/Emaki/a/shimx64.efi'], SHIM_PE)
        self.assertEqual(esp['EFI/Emaki/a/mmx64.efi'], MOK_PE)
        self.assertEqual(esp['EFI/Emaki/a/grubx64.efi'], image)
        self.assertEqual(esp['EFI/Emaki/a/emaki-machine-key.cer'], b'DER certificate')
        record = secureboot.read_slot(self.root / 'efi', 'a')
        self.assertEqual(record, json.loads(esp['EFI/Emaki/a/slot.json']))
        self.assertEqual((record['slot'], record['generation']), ('a', None))
        self.assertEqual(sorted(esp), ['EFI/BOOT/BOOTX64.EFI', 'EFI/BOOT/grubx64.efi', 'EFI/BOOT/mmx64.efi',
                                       'EFI/Emaki/a/emaki-machine-key.cer', 'EFI/Emaki/a/grubx64.efi',
                                       'EFI/Emaki/a/mmx64.efi', 'EFI/Emaki/a/shimx64.efi', 'EFI/Emaki/a/slot.json'])
        self.assertEqual(esp['EFI/BOOT/BOOTX64.EFI'], SHIM_PE)
        self.assertEqual(esp['EFI/BOOT/grubx64.efi'], image)
        self.assertEqual(esp['EFI/BOOT/mmx64.efi'], MOK_PE)
        # Both kernels are signed and proven before the first snapshot.
        for kernel in secureboot.KERNELS:
            self.assertIn(kernel, worker.runner.signed)
        # The menu loads Emaki's faces from the signed image's memdisk.
        self.assertIn('loadfont (memdisk)/usr/share/emaki/grub/fonts/menu-32.pf2',
                      f.read('/boot/grub/grub.cfg'))
        # The firmware entry names slot a's shim, labelled Emaki, and is created only after the
        # slot's GRUB was verified on the ESP.
        create = next(c for c in commands if c[:2] == ['efibootmgr', '--create'])
        self.assertEqual(create[-4:], ['--label', 'Emaki', '--loader', r'\EFI\Emaki\a\shimx64.efi'])
        verified = commands.index(['sbverify', '--cert', '/var/lib/emaki/secureboot/machine.crt',
                                   '/efi/EFI/Emaki/a/grubx64.efi'])
        self.assertLess(verified, commands.index(create))

    def test_slot_a_is_complete_before_its_entry_which_ends_first_without_the_unsigned_one(self):
        worker = self.worker(secure_inventory())
        responses = worker.runner.responses
        worker.runner = FirmwareRunner(self.root)
        worker.runner.responses = {key: value for key, value in responses.items() if key[0] != 'efibootmgr'}
        worker.bootloader()
        # The slot's manifest was there when the entry was created.
        self.assertIsNotNone(worker.runner.slot_at_create)
        self.assertEqual(worker.runner.slot_at_create, secureboot.read_slot(self.root / 'efi', 'a'))
        self.assertEqual(worker.runner.order, ['0004', '0002'])
        self.assertEqual(worker.runner.entries['0004'], ('Emaki', r'\EFI\Emaki\a\shimx64.efi'))
        self.assertNotIn('0003', worker.runner.entries)
        self.assertFalse(secureboot.slot_dir(self.root / 'efi', 'b').exists())
        self.assertEqual(self.notices, [])

    # R9S9 #5: an earlier Emaki on a kept ESP left "Emaki" -> a and "Emaki (previous)" -> b in
    # BootOrder and a BootNext to b; grub-install has just put its unsigned entry first.
    OLD_NVRAM = {
        '0000': (True, 'Fixture Linux', OTHER_ESP, r'\EFI\ubuntu\shimx64.efi'),
        '0001': (True, 'Emaki', MAC_ESP, r'\EFI\Emaki\a\shimx64.efi'),
        '0002': (True, 'Windows Boot Manager', MAC_ESP, r'\EFI\Microsoft\Boot\bootmgfw.efi'),
        '0003': (True, 'Emaki', MAC_ESP, r'\EFI\Emaki\grubx64.efi'),
        '0005': (True, 'Emaki (previous)', MAC_ESP, r'\EFI\Emaki\b\shimx64.efi'),
        # FAT ignores case: this one names slot b too, outside BootOrder.
        '0006': (False, 'Emaki (previous)', MAC_ESP, r'\EFI\EMAKI\B\SHIMX64.EFI'),
        # Slot b of an Emaki on another disk: never this installation's to touch.
        '0007': (True, 'Emaki', OTHER_ESP, r'\EFI\Emaki\b\shimx64.efi'),
    }
    OLD_ORDER = ['0003', '0005', '0001', '0002', '0007', '0000']

    def nvram_worker(self, inventory_data, **firmware):
        worker = self.worker(inventory_data)
        responses = worker.runner.responses
        worker.runner = NvramRunner(self.root, self.OLD_NVRAM, self.OLD_ORDER, '0005', **firmware)
        worker.runner.responses = {key: value for key, value in responses.items() if key[0] != 'efibootmgr'}
        esp = self.root / 'efi'
        for slot in secureboot.SLOTS:
            secureboot.write_slot(esp, slot, {name: b'old ' + slot.encode() for name in secureboot.slot_files_names()})
        return worker

    def test_an_old_slot_b_loses_every_firmware_reference_before_it_is_removed(self):
        worker = self.nvram_worker(secure_inventory())
        old_a = secureboot.read_slot(self.root / 'efi', 'a')
        worker.bootloader()
        firmware = worker.runner
        ours = {number: entry for number, entry in firmware.entries.items()
                if entry[2] == MAC_ESP and secureboot.entry_slot(entry[3])}
        # One entry names a slot on this ESP: "Emaki" -> a, active and first; the others keep
        # their old relative order, the other disk's entry for its slot b included.
        self.assertEqual(list(ours.values()), [(True, 'Emaki', MAC_ESP, r'\EFI\Emaki\a\shimx64.efi')])
        self.assertEqual(firmware.order, [*ours, '0002', '0007', '0000'])
        self.assertIsNone(firmware.next)
        self.assertEqual(firmware.entries['0007'], self.OLD_NVRAM['0007'])
        self.assertEqual(firmware.entries['0000'], self.OLD_NVRAM['0000'])
        self.assertEqual(firmware.entries['0002'], self.OLD_NVRAM['0002'])
        # The old entries went while slot b was still on the ESP and slot a still the old one.
        for number in ('0001', '0005', '0006'):
            self.assertEqual(firmware.esp_at_delete[number], (True, old_a), number)
        self.assertFalse(secureboot.slot_dir(self.root / 'efi', 'b').exists())
        self.assertIsNotNone(secureboot.read_slot(self.root / 'efi', 'a'))
        # One BootOrder write took both old slot entries out, before BootNext and the entries.
        commands = [c for c in self.commands(worker) if c[0] == 'efibootmgr' and c[1:] != ['-v']]
        self.assertEqual(commands[:5], [['efibootmgr', '--bootorder', '0003,0002,0007,0000'],
                                        ['efibootmgr', '--delete-bootnext'], ['efibootmgr', '-b', '0001', '-B'],
                                        ['efibootmgr', '-b', '0005', '-B'], ['efibootmgr', '-b', '0006', '-B']])
        self.assertEqual(self.notices, [])

    def test_firmware_that_keeps_an_old_slot_entry_stops_before_the_slot_is_rewritten(self):
        worker = self.nvram_worker(secure_inventory(), stuck={'0005'})
        old_a = secureboot.read_slot(self.root / 'efi', 'a')
        with self.assertRaises(InstallError) as caught:
            worker.bootloader()
        self.assertEqual(caught.exception.code, Code.BOOT_VERIFY)
        self.assertEqual(caught.exception.message,
                         'The firmware still starts an earlier Emaki startup copy on this disk, '
                         'so the startup copies were not replaced.')
        self.assertTrue(secureboot.slot_dir(self.root / 'efi', 'b').is_dir())
        self.assertEqual(secureboot.read_slot(self.root / 'efi', 'a'), old_a)
        self.assertFalse(any(c[:2] == ['efibootmgr', '--create'] for c in self.commands(worker)))

    def test_a_final_inventory_still_naming_slot_b_fails_the_install_step(self):
        # Firmware that brings a deleted entry back (restored variables) once the new one exists.
        worker = self.nvram_worker(secure_inventory())
        firmware, real = worker.runner, worker.runner.run

        def run(argv, **kwargs):
            output = real(argv, **kwargs)
            if [str(x) for x in argv[:2]] == ['efibootmgr', '--create']:
                firmware.entries['0005'] = self.OLD_NVRAM['0005']
                firmware.order.append('0005')
            return output
        firmware.run = run
        with self.assertRaises(InstallError) as caught:
            worker.bootloader()
        self.assertEqual(caught.exception.code, Code.BOOT_VERIFY)
        self.assertEqual(caught.exception.message,
                         'A firmware startup entry still points to a startup copy removed by this installation.')

    def test_without_secure_boot_the_firmware_entries_follow_the_old_path(self):
        worker = self.nvram_worker(inventory())
        worker.bootloader()
        # grub-install's entry is first already: nothing in NVRAM changes, the slots stay.
        self.assertEqual([c for c in self.commands(worker) if c[0] == 'efibootmgr'], [['efibootmgr', '-v']])
        self.assertEqual((worker.runner.entries, worker.runner.order, worker.runner.next),
                         (self.OLD_NVRAM, self.OLD_ORDER, '0005'))

    def test_a_slot_b_left_by_an_earlier_emaki_is_removed(self):
        worker = self.worker(secure_inventory())
        old = secureboot.slot_dir(self.root / 'efi', 'b')
        old.mkdir(parents=True)
        for name in ('shimx64.efi', 'mmx64.efi', 'grubx64.efi', 'emaki-machine-key.cer', 'slot.json'):
            (old / name).write_bytes(b'old')
        worker.bootloader()
        self.assertFalse(old.exists())
        self.assertIsNotNone(secureboot.read_slot(self.root / 'efi', 'a'))

    def test_startup_copy_write_failure_keeps_details_in_log(self):
        worker = self.worker(secure_inventory())
        lines = []
        worker.log = lines.append
        with patch.object(secureboot, 'write_slot', side_effect=OSError('write detail')):
            with self.assertRaises(InstallError) as caught:
                worker.bootloader()
        self.assertEqual(caught.exception.message,
                         'The new startup copy could not be written. Check the installation log for details.')
        self.assertIn('The boot slot a could not be written: write detail', lines)

    def test_an_unsigned_shim_or_grub_never_completes_slot_a(self):
        for name in ('shim', 'mokmanager', 'grub'):
            with self.subTest(name=name):
                self.root = Path(tempfile.mkdtemp(dir=self.temp.name))
                worker = self.worker(secure_inventory())
                if name == 'grub':
                    worker.runner.fail = lambda argv: 'sbverify' in argv and argv[-1].startswith('/efi/')
                else:
                    worker.files.write(f'/usr/share/emaki-shim-signed/{"shimx64" if name == "shim" else "mmx64"}.efi',
                                       b'MZ unsigned')
                with self.assertRaises(InstallError) as caught:
                    worker.bootloader()
                self.assertEqual(caught.exception.code, Code.BOOT_VERIFY)
                self.assertIsNone(secureboot.read_slot(self.root / 'efi', 'a'))
                self.assertFalse(any(c[:2] == ['efibootmgr', '--create'] for c in self.commands(worker)))

    def test_encrypted_root_puts_the_unlock_screen_into_the_signed_image(self):
        worker = self.worker(secure_inventory(), encryption='account')
        luks = worker.plan.root.luks_uuid = '11111111-2222-4333-8444-555555555555'
        worker.files.write('/boot/grub/x86_64-efi/load.cfg',
                           f'cryptomount -u {luks.replace("-", "")}\ncryptomount -u {luks}\n'
                           f'set root=cryptouuid/{luks.replace("-", "")}\n')
        font = Path(self.temp.name) / 'visible.pf2'
        font.write_bytes(b'FILE\x00\x00\x00\x04PFF2')
        with patch.object(worker_module, 'GRUB_VISIBLE_FONT', font), \
                patch.object(worker_module, 'grub_loader_payload',
                             return_value=('(memdisk)/boot/grub', b'source (memdisk)/unlock.cfg\n', b'tar')) as payload:
            worker.bootloader()
        self.assertEqual(payload.call_args.args[3], luks)
        commands = self.commands(worker)
        # grub-install's unsigned unlock image is not built: the signed image replaces it.
        self.assertFalse(any('--output=/boot/grub/x86_64-efi/emaki-early.efi' in c for c in commands))
        self.assertTrue(worker.files.path('/efi/EFI/Emaki/a/grubx64.efi').read_bytes().endswith(b'+signature'))

    def dkms_worker(self, **runner):
        worker = self.worker(secure_inventory())
        responses = worker.runner.responses
        worker.runner = DkmsRunner(self.root, **runner)
        worker.runner.responses = responses
        return worker

    def test_dkms_modules_built_before_the_key_are_rebuilt_and_signed_by_it(self):
        for status_format in ('3', '2'):
            with self.subTest(dkms=status_format):
                self.root = Path(tempfile.mkdtemp(dir=self.temp.name))
                worker = self.dkms_worker(status_format=status_format)
                worker.bootloader()
                commands = self.commands(worker)
                # The config is there before the rebuild, and stays for the update's DKMS hook.
                self.assertEqual(worker.runner.config_at_build, [True, True])
                self.assertIn('mok_signing_key=/var/lib/emaki/secureboot/machine.key',
                              worker.files.read(str(secureboot.DKMS_CONFIG)))
                dkms = [c for c in commands if c[0] == 'dkms' and c[1] != 'status']
                self.assertEqual(dkms, [step for kernel in DKMS_KERNELS for step in (
                    ['dkms', 'build', '--force', '-m', 'nvidia', '-v', DKMS_VERSION, '-k', kernel],
                    ['dkms', 'install', '--force', '-m', 'nvidia', '-v', DKMS_VERSION, '-k', kernel])])
                # Every module of both builds is checked against the machine certificate.
                last_install = max(i for i, c in enumerate(commands) if c[:2] == ['dkms', 'install'])
                checked = {(c[2], c[-1]) for c in commands[last_install:] if c[0] == 'modinfo'}
                self.assertTrue({(DKMS_KERNELS[-1], name) for name in NVIDIA_MODULES} <= checked)
                self.assertEqual(commands[last_install:].count(['mkinitcpio', '-p', 'linux', '-p', 'linux-lts']), 1)
                # The kernels are signed afterwards: the initramfs build does not undo them.
                first_kernel_sign = min(i for i, c in enumerate(commands) if c[0] == 'sbsign' and c[-1] in secureboot.KERNELS)
                self.assertLess(last_install, first_kernel_sign)

    def test_a_dkms_module_left_signed_by_another_key_stops_the_installation(self):
        worker = self.dkms_worker(signs=False)
        with self.assertRaises(InstallError) as raised:
            worker.bootloader()
        self.assertEqual(raised.exception.code, Code.BOOT_VERIFY)
        self.assertIn('not signed by the machine key', raised.exception.message)

    def test_finish_rebuilds_nothing_signed_by_the_machine_key(self):
        worker = self.dkms_worker()
        worker.bootloader()
        before = len(worker.runner.commands)
        self.finish(worker)
        later = self.commands(worker)[before:]
        self.assertFalse(any(c[:2] in (['dkms', 'build'], ['dkms', 'install']) for c in later))
        self.assertTrue(any(c[0] == 'modinfo' for c in later))

    def test_dkms_untouched_without_its_modules_or_without_secure_boot(self):
        worker = self.worker(secure_inventory())  # no graphics packages: no DKMS in the target
        worker.bootloader()
        self.finish(worker)
        self.assertFalse(any(c[0] in ('dkms', 'modinfo') for c in self.commands(worker)))
        self.root = Path(tempfile.mkdtemp(dir=self.temp.name))
        plain = self.worker(inventory())
        responses = plain.runner.responses
        plain.runner = DkmsRunner(self.root)
        plain.runner.responses = responses
        plain.bootloader()
        self.finish(plain)
        self.assertFalse(any(c[0] in ('dkms', 'modinfo') for c in self.commands(plain)))
        self.assertFalse(plain.files.path(str(secureboot.DKMS_CONFIG)).exists())

    def test_dkms_status_lines(self):
        text = ('nvidia/580.82.09, 6.17.1-arch1-1, x86_64: installed\n'
                'nvidia, 580, 6.12.50-1-lts, x86_64: installed (WARNING! Diff between built and installed module!)\n'
                'nvidia/570.1, 6.1.0, x86_64: installed-weak from 6.0.0\n'
                'nvidia/580.82.09, 6.18.0-arch1-1, x86_64: built\n'
                'zfs/2.3.0: added\nDeprecated feature: REMAKE_INITRD\n'
                'evil/../.., 6.17.1-arch1-1, x86_64: installed\n')
        self.assertEqual(worker_module.dkms_installed(text),
                         [('nvidia', '580.82.09', '6.17.1-arch1-1', 'x86_64'),
                          ('nvidia', '580', '6.12.50-1-lts', 'x86_64')])

    def test_finish_signs_again_writes_dkms_and_queues_the_key_last(self):
        worker = self.worker(secure_inventory())
        worker.bootloader()
        events = []
        worker.emit = lambda kind, **fields: events.append((kind, fields))
        logs = []
        worker.log = logs.append
        worker.files.write('/boot/vmlinuz-linux', 'updated kernel')  # an update replaced it
        worker.runner.signed.discard('/boot/vmlinuz-linux')
        worker.files.write('/usr/lib/fwupd/efi/fwupdx64.efi', b'MZ fwupd')
        self.finish(worker)
        self.assertIn('/boot/vmlinuz-linux', worker.runner.signed)
        self.assertIn('/usr/lib/fwupd/efi/fwupdx64.efi.signed', worker.runner.signed)
        self.assertIn('mok_signing_key=/var/lib/emaki/secureboot/machine.key',
                      worker.files.read('/etc/dkms/framework.conf.d/50-emaki-secureboot.conf'))
        commands = worker.runner.commands
        mok = [(c, kw) for c, kw in commands if c[2:4] == ['mokutil', '--import']]
        self.assertEqual(len(mok), 1)
        argv, kwargs = mok[0]
        password = worker.mok_password
        self.assertRegex(password, r'^[0-9]{8}$')
        self.assertTrue(kwargs.get('secret'))
        self.assertEqual(kwargs.get('input'), f'{password}\n{password}\n')
        self.assertNotIn(password, ' '.join(argv))
        self.assertEqual(worker.redactor.text('code ' + password), 'code [REDACTED]')
        self.assertFalse(any(password in line for line in logs))

    def test_done_carries_the_password_only_with_secure_boot(self):
        source = Path(worker_module.__file__).read_text()
        self.assertIn("warnings=list(self.warnings), **self.secure_boot_done())", source)
        kept = manual()
        own = manual()
        own['mounts'][0]['format'] = True
        # EMAKI only where the installer formatted the ESP; a kept ESP keeps its own label.
        for name, settings, plan_inventory, label in (
                ('erase', config(), secure_inventory(), 'EMAKI'),
                ('manual, ESP formatted', own, inventory(partitions=True), 'EMAKI'),
                ('manual, ESP kept', kept, inventory(partitions=True), None)):
            for password in ('12345678', None):
                with self.subTest(name, secure=bool(password)):
                    worker = Worker(None, None, Redactor(), lambda *a, **kw: None, lambda line: None, target=self.root)
                    worker.plan, worker.mok_password = make_plan(settings, plan_inventory), password
                    # A one-time password exists only on a Secure Boot install.
                    worker.plan.secure_boot = worker.plan.secure_boot or ('both' if password else '')
                    self.assertEqual(worker.secure_boot_done(),
                                     {'secure_boot': {'password': password, 'esp_label': label}} if password else {})

    def test_without_secure_boot_the_boot_path_is_unchanged(self):
        worker = self.worker(inventory())
        worker.bootloader()
        commands = self.commands(worker)
        self.assertFalse(any(c[0] in ('openssl', 'sbsign', 'sbverify', 'mokutil', 'sh') for c in commands))
        self.assertFalse(worker.files.path('/var/lib/emaki/secureboot').exists())
        esp = {path.name for path in (self.root / 'efi').rglob('*') if path.is_file()}
        self.assertFalse(esp & {'shimx64.efi', 'mmx64.efi', 'emaki-machine-key.cer'})
        self.assertNotIn('(memdisk)', worker.files.read('/boot/grub/grub.cfg'))
        worker.files.write('/usr/lib/fwupd/efi/fwupdx64.efi', b'MZ fwupd')
        self.finish(worker)
        self.assertIsNone(worker.mok_password)
        self.assertFalse(worker.files.path('/etc/dkms/framework.conf.d/50-emaki-secureboot.conf').exists())
        self.assertFalse(any('mokutil' in c for c in self.commands(worker)))

    def test_removable_path_on_emakis_own_esp_next_to_windows(self):
        # 0.9.0: next to Windows /efi is the ESP Emaki formatted in the freed space, like erase's.
        worker = self.worker(secure_inventory())
        worker.plan.config['mode'] = 'alongside'
        self.assertTrue(next(part for part in worker.plan.partitions if part.esp).format)
        with patch.object(worker, 'grub_config'), patch.object(worker, 'check_windows_esp') as windows:
            worker.bootloader()
        windows.assert_called_once_with()
        self.assertTrue(any('--removable' in c for c in self.commands(worker)))
        self.assertEqual(worker.files.path('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes(), SHIM_PE)
        self.assertTrue(worker.files.path('/efi/EFI/BOOT/mmx64.efi').exists())
        self.assertEqual(worker.files.path('/efi/EFI/Emaki/a/shimx64.efi').read_bytes(), SHIM_PE)

    def test_manual_shared_esp_preserves_all_fallback_files(self):
        cases = [(existing, unreadable) for existing in (False, True)
                 for unreadable in ({}, {'_efi_error': True}, {'_unreadable': True})]
        for existing, unreadable in cases:
            with self.subTest(existing=existing, unreadable=unreadable):
                self.root = Path(tempfile.mkdtemp(dir=self.temp.name))
                worker = self.worker(secure_inventory())
                worker.plan.config['mode'] = 'manual'
                esp = next(part for part in worker.plan.partitions if part.esp)
                esp.format, esp.create = False, False
                loader = 'EFI/Microsoft/Boot/bootmgfw.efi'
                worker.plan.disk['partitions'] = [
                    {'path': esp.path, 'fs': 'vfat',
                     '_efi_loaders': {} if unreadable else {loader: 'hash'}, **unreadable}]
                worker.files.write('/efi/' + loader, b'Windows')
                before = {}
                if existing:
                    worker.files.write('/efi/EFI/Emaki/grubx64.efi', b'old-emaki')
                    before = {'BOOTX64.EFI': b'old-emaki', 'grubx64.efi': b'old-grub', 'mmx64.efi': b'old-mok'}
                    for name, data in before.items():
                        worker.files.write('/efi/EFI/BOOT/' + name, data)
                worker.bootloader()
                after = {path.name: path.read_bytes() for path in
                         worker.files.path('/efi/EFI/BOOT').glob('*') if path.is_file()}
                self.assertEqual(after, before)
                self.assertFalse(any('--removable' in c for c in self.commands(worker)))
                self.assertEqual(worker.files.path('/efi/' + loader).read_bytes(), b'Windows')
                self.assertEqual(worker.files.path('/efi/EFI/Emaki/a/shimx64.efi').read_bytes(), SHIM_PE)

    def test_foreign_files_beside_the_fallback_keep_the_removable_path_unused(self):
        for name in ('grubx64.efi', 'mmx64.efi'):
            with self.subTest(name=name):
                self.root = Path(tempfile.mkdtemp(dir=self.temp.name))
                worker = self.worker(secure_inventory())
                worker.files.write('/efi/EFI/BOOT/' + name, b'foreign')
                worker.bootloader()
                self.assertFalse(any('--removable' in c for c in self.commands(worker)))
                self.assertEqual(worker.files.path('/efi/EFI/BOOT/' + name).read_bytes(), b'foreign')
                self.assertFalse(worker.files.path('/efi/EFI/BOOT/BOOTX64.EFI').exists())

    def test_certificate_reaches_the_esp_when_only_the_removable_path_was_written(self):
        worker = self.worker(secure_inventory())
        worker.runner.fail = lambda argv: 'grub-install' in argv and '--removable' not in argv
        worker.bootloader()
        # Slot a holds the certificate for "Enroll key from disk" on every path.
        self.assertEqual(worker.files.path('/efi/EFI/Emaki/a/emaki-machine-key.cer').read_bytes(), b'DER certificate')
        self.assertIsNotNone(secureboot.read_slot(self.root / 'efi', 'a'))
        for name in ('shimx64.efi', 'emaki-machine-key.cer'):
            self.assertFalse(worker.files.path('/efi/EFI/Emaki/' + name).exists())
        self.assertEqual(worker.files.path('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes(), SHIM_PE)

    def test_secure_boot_change_after_review_refuses(self):
        source = Path(worker_module.__file__).read_text()
        self.assertIn('require(fresh.secure_boot == plan.secure_boot, Code.SECURE_BOOT,', source)


class FirmwareEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.worker = Worker(None, None, Redactor(), lambda *a, **kw: None, lambda line: None,
                             target=Path(self.temp.name))
        self.worker.plan = make_plan(config(), secure_inventory())
        esp = next(part for part in self.worker.plan.partitions if part.esp)
        esp.path, esp.partuuid = '/dev/vda1', MAC_ESP.upper()
        self.worker.runner = RecordingRunner()
        self.notices = []
        self.worker.notice = self.notices.append

    def test_shim_entry_is_created_and_first_before_grub_installs_entry_goes(self):
        with patch.object(self.worker.runner, 'run', side_effect=[
                MAC_CREATED, '', BOTH_LISTING, BOTH_LISTING, '', SHIM_ONLY_LISTING]) as run:
            self.worker.boot_entry_first()
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ['efibootmgr', '-v'],
            ['efibootmgr', '--create', '--disk', '/dev/vda', '--part', '1', '--label', 'Emaki',
             '--loader', r'\EFI\Emaki\a\shimx64.efi'],
            ['efibootmgr', '-v'], ['efibootmgr', '-v'], ['efibootmgr', '-b', '0003', '-B'], ['efibootmgr', '-v']])
        self.assertEqual(self.notices, [])

    def test_a_failed_shim_entry_never_removes_the_only_emaki_entry(self):
        for reread in (MAC_CREATED, 'BootOrder: 0003\n'):
            with self.subTest(reread=reread[:20]):
                self.notices.clear()
                with patch.object(self.worker.runner, 'run', side_effect=[MAC_CREATED, '', reread]) as run:
                    self.worker.boot_entry_first()
                commands = [call.args[0] for call in run.call_args_list]
                self.assertFalse(any('-B' in command for command in commands))
                self.assertEqual(len(self.notices), 1)

    def test_shim_parser(self):
        slot = secureboot.slot_entry('a')
        self.assertEqual(worker_module.emaki_boot_entry(SHIM_LISTING, MAC_ESP, loader=slot), '0003')
        self.assertIsNone(worker_module.emaki_boot_entry(SHIM_LISTING, MAC_ESP))
        self.assertIsNone(worker_module.emaki_boot_entry(MAC_CREATED, MAC_ESP, loader=slot))
        # An entry for the old top-level shim is not slot a's.
        old = MAC_AFTER_CREATE.replace(r'\EFI\Emaki\grubx64.efi', r'\EFI\Emaki\shimx64.efi')
        self.assertIsNone(worker_module.emaki_boot_entry(old, MAC_ESP, loader=slot))


class FwupdTests(unittest.TestCase):
    def test_db_and_kek_plugins_join_the_disabled_list(self):
        self.assertEqual(secureboot.fwupd_config('[fwupd]\n# doc\nDisabledPlugins=test;test_ble\n\n[redfish]\nA=1\n'),
                         '[fwupd]\n# doc\nDisabledPlugins=test;test_ble;uefi_db;uefi_kek\n\n[redfish]\nA=1\n')
        self.assertEqual(secureboot.fwupd_config('[fwupd]\nUriSchemes=file;https\n'),
                         '[fwupd]\nDisabledPlugins=uefi_db;uefi_kek\nUriSchemes=file;https\n')
        once = secureboot.fwupd_config('[fwupd]\n')
        self.assertEqual(secureboot.fwupd_config(once), once)
        self.assertEqual(secureboot.fwupd_config(''), '[fwupd]\nDisabledPlugins=uefi_db;uefi_kek\n')

    def finished(self, temp, settings, machine):
        worker = Worker(None, None, Redactor(), lambda *a, **kw: None, lambda line: None, target=Path(temp))
        worker.files.path('/etc/fwupd/fwupd.conf').unlink(missing_ok=True)
        worker.files.write('/etc/fwupd/fwupd.conf', '[fwupd]\n')
        worker.plan = make_plan(settings, machine)
        worker.plan.config['online_update'] = False
        worker.machine = machine
        worker.runner = RecordingRunner()
        with patch.object(worker, 'record_installation'), patch('emaki_installer.worker.LOG') as log, \
                patch.object(worker_module.Worker, 'update_notice'):
            log.read_bytes.return_value = b''
            for name in ('/', '/etc', '/usr', '/var', '/boot'):
                worker.files.mkdir(name).chmod(0o755)
            worker.finish()
        return 'uefi_db' in worker.files.read('/etc/fwupd/fwupd.conf')

    def test_windows_left_on_any_disk_keeps_the_firmware_keys(self):
        def machine(windows_on_target=(), second_disk=None):
            data = inventory(partitions=True)
            for part in data['disks'][0]['partitions']:
                if part['number'] in windows_on_target:
                    part['os_hint'] = 'windows'
            if second_disk:
                other = inventory(partitions=True)['disks'][0]
                other.update(id='/dev/vdb', path='/dev/vdb')
                for part in other['partitions']:
                    part.update(id=part['id'].replace('vda', 'vdb'), path=part['path'].replace('vda', 'vdb'))
                part = other['partitions'][1]
                part.update(second_disk)
                data['disks'].append(other)
            return data
        own_esp = manual()
        own_esp['mounts'][0]['format'] = True
        cases = (
            ('erase, Windows on a second disk', config(), machine(second_disk={'fs': 'ntfs', 'os_hint': 'windows'}), True),
            ('erase, BitLocker on a second disk', config(), machine(second_disk={'fs': 'BitLocker'}), True),
            ('erase of the only Windows disk', config(), machine(windows_on_target=(1, 2)), False),
            ('manual keeping the Windows ESP', manual(), machine(windows_on_target=(1,)), True),
            ('manual deleting the Windows ESP',
             replaced(manual(), machine(windows_on_target=(1,))['disks'][0], 1, '/efi', 'vfat', 'esp'),
             machine(windows_on_target=(1,)), False),
            ('Emaki only', config(), machine(second_disk={'fs': 'ext4'}), False),
        )
        with tempfile.TemporaryDirectory() as temp:
            for name, settings, data, kept in cases:
                with self.subTest(name):
                    self.assertEqual(self.finished(temp, settings, data), kept)

    def test_only_alongside_windows_and_only_an_installed_fwupd(self):
        with tempfile.TemporaryDirectory() as temp:
            for mode, present, changed in (('alongside', True, True), ('erase', True, False),
                                           ('alongside', False, False)):
                with self.subTest(mode=mode, present=present):
                    worker = Worker(None, None, Redactor(), lambda *a, **kw: None, lambda line: None,
                                    target=Path(temp))
                    path = worker.files.path('/etc/fwupd/fwupd.conf')
                    path.unlink(missing_ok=True)
                    if present:
                        worker.files.write('/etc/fwupd/fwupd.conf', '[fwupd]\n')
                    worker.plan = make_plan(config(), inventory())
                    worker.plan.config['mode'] = mode
                    worker.plan.config['online_update'] = False
                    worker.runner = RecordingRunner()
                    with patch.object(worker, 'record_installation'), \
                            patch('emaki_installer.worker.LOG') as log, \
                            patch.object(worker_module.Worker, 'update_notice'):
                        log.read_bytes.return_value = b''
                        for name in ('/', '/etc', '/usr', '/var', '/boot'):
                            worker.files.mkdir(name).chmod(0o755)
                        worker.finish()
                    self.assertEqual(path.exists(), present)
                    if present:
                        self.assertEqual('uefi_db' in path.read_text(), changed)


if __name__ == '__main__':
    unittest.main()
