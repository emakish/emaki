#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The boot refresh on a machine that signs its boot files, and proof that others are unchanged.

A machine signs when its Secure Boot key exists: the refresh then builds a signed GRUB with
every menu module built in and the menu faces in its memdisk, and publishes it in an A/B slot
with shim, MokManager and the certificate (tests/test-boot-slots.py). Without the key every
command, file and loader stays as it was.

The real-tool test (real grub-mkimage and sbsigntools, published in a slot) is
RealSignedSlot in tests/test-boot-slots.py; tool_root below finds the tools for it.
"""
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tarfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('boot_fixture', ROOT / 'tests/test-boot-refresh.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
refresh, boot, secureboot = base.refresh, base.boot, base.secureboot
# Borrow the orchestration fixture's helpers without collecting its tests a second time.
Checks = SimpleNamespace(**{name: getattr(base.RefreshOrchestrationChecks, name)
                           for name in ('fixture', 'shared_esp', 'complete_boot')})

ARCH_SBAT = (
    'sbat,1,SBAT Version,sbat,1,https://github.com/rhboot/shim/blob/main/SBAT.md\n'
    'grub,4,Free Software Foundation,grub,2:2.16-1,https//www.gnu.org/software/grub/\n'
    'grub.arch,1,Arch Linux,grub,2:2.16-1,https://archlinux.org/packages/core/x86_64/grub/\n')
SIGNATURE = b'EMAKI-TEST-SIGNATURE'
LEVEL_WARNING = ('grub-mkimage: warning: Global SBAT level 4 in the SBAT data does not match '
                 'the build-time Global SBAT level 6.')
FACE = '/usr/share/emaki/grub/fonts/menu-32.pf2'
THEME_FACE = '/usr/share/emaki/grub/theme/title-24.pf2'


def machine(f, key=True, removable=True, shim=b'shim 16.1-7 ' * 4096, manager=b'mokmanager ' * 4096):
    """The files a signing machine has besides the ordinary fixture: package, key, faces."""
    f.put(f.mapped(str(secureboot.SHIM_DIR / secureboot.SHIM)), shim)
    f.put(f.mapped(str(secureboot.SHIM_DIR / secureboot.MOK_MANAGER)), manager)
    f.put(f.mapped(str(secureboot.SBAT_SOURCE)), ARCH_SBAT)
    f.put(f.mapped(FACE), b'menu face 32')
    f.put(f.mapped(THEME_FACE), b'theme face 24')
    for name in secureboot.GRUB_MODULES:
        path = f.mapped('/usr/lib/grub/x86_64-efi') / (name + '.mod')
        if not path.exists():
            f.put(path, b'module ' + name.encode())
    if key:
        f.put(f.mapped(str(secureboot.KEY)), b'private test key\n')
        f.put(f.mapped(str(secureboot.CERT)), b'certificate\n')
        f.put(f.mapped(str(secureboot.DER)), b'certificate in DER\n')
    if key and removable:
        # The installer's layout: shim as BOOTX64.EFI, the signed GRUB next to it.
        f.put(f.mapped('/efi/EFI/BOOT/grubx64.efi'), f.originals['/efi/EFI/Emaki/grubx64.efi'])
        f.originals['/efi/EFI/BOOT/grubx64.efi'] = f.originals['/efi/EFI/Emaki/grubx64.efi']


@contextmanager
def signing_tools(f, warn_once=True):
    """Fake sbsign/sbverify and the sh wrapper build_signed_grub puts around grub-mkimage."""
    fake = refresh.run
    warned = []

    def run(arguments):
        args = list(map(str, arguments))
        if args[0] == 'sh':
            f.commands.append(args)
            assert args[1:4] == ['-c', '"$@" 2>&1', 'sh'], args
            fake(args[4:])
            if warn_once and not warned:
                warned.append(True)
                return LEVEL_WARNING
            return ''
        if args[0] == 'sbsign':
            f.commands.append(args)
            output = Path(args[args.index('--output') + 1])
            output.write_bytes(Path(args[-1]).read_bytes() + SIGNATURE)
            return ''
        if args[0] == 'sbverify':
            f.commands.append(args)
            if not Path(args[-1]).read_bytes().endswith(SIGNATURE):
                raise refresh.Transient('sbverify failed: Signature verification failed')
            return 'Signature verification OK'
        return fake(arguments)

    with patch.object(refresh, 'run', side_effect=run):
        yield


ESP_PARTUUID = '0f1e2d3c-4b5a-4968-8776-655443322110'


def node(kind, subtype, body):
    return bytes((kind, subtype)) + (4 + len(body)).to_bytes(2, 'little') + body


def disk_node(partuuid=ESP_PARTUUID, kind=(2, 2)):
    """Hard drive node of partition 1 by its GUID; kind is the partition format and signature type."""
    return node(4, 1, (1).to_bytes(4, 'little') + (2048).to_bytes(8, 'little')
                + (204800).to_bytes(8, 'little') + uuid.UUID(partuuid).bytes_le + bytes(kind))


def file_node(path):
    return node(4, 4, (path + '\0').encode('utf-16-le'))


def raw_option(paths, description='Emaki', optional=b''):
    return ((1).to_bytes(4, 'little') + len(paths).to_bytes(2, 'little')
            + (description + '\0').encode('utf-16-le') + paths + optional)


def load_option(path, partuuid=ESP_PARTUUID, description='Emaki', before=b'', optional=b''):
    """An EFI_LOAD_OPTION as efibootmgr writes it: the hard drive node, then the file node."""
    return raw_option(before + disk_node(partuuid) + file_node(path) + node(0x7f, 0xff, b''),
                      description, optional)


def booted_from(f, path, number=1, option=None):
    """BootCurrent and its Boot#### entry, as efivarfs shows them (4 attribute bytes first)."""
    attributes = (7).to_bytes(4, 'little')
    efivars = f.mapped('/sys/firmware/efi/efivars')
    f.put(efivars / f'BootCurrent-{secureboot.GLOBAL_GUID}', attributes + number.to_bytes(2, 'little'))
    f.put(efivars / f'Boot{number:04X}-{secureboot.GLOBAL_GUID}',
          attributes + (load_option(path) if option is None else option))


def pe_image(label, size=12000, signed=True):
    """A minimal x86-64 PE32+ image: headers, one section and, when signed, a certificate table."""
    header = bytearray(1024)
    header[:2] = b'MZ'
    header[0x3c:0x40] = (64).to_bytes(4, 'little')
    header[64:68] = b'PE\0\0'
    struct.pack_into('<HH', header, 68, 0x8664, 1)
    struct.pack_into('<H', header, 84, 240)  # SizeOfOptionalHeader
    optional = 88
    struct.pack_into('<H', header, optional, 0x20b)
    struct.pack_into('<I', header, optional + 60, len(header))  # SizeOfHeaders
    struct.pack_into('<I', header, optional + 108, 16)  # NumberOfRvaAndSizes
    struct.pack_into('<II', header, optional + 240 + 16, size, len(header))  # section raw data
    # One WIN_CERTIFICATE: dwLength, revision 0x0200, type PKCS_SIGNED_DATA, then the
    # signature, padded to 8 bytes as the certificate table requires.
    payload = b'Authenticode ' * 10
    signature = struct.pack('<IHH', 8 + len(payload), 0x0200, 2) + payload
    signature += bytes(-len(signature) % 8)
    if signed:
        struct.pack_into('<II', header, optional + 112 + 32, len(header) + size, len(signature))
    return bytes(header) + (label * size)[:size] + (signature if signed else b'')


def reboot(f):
    """A new boot: the kernel's boot id changes."""
    f.put(f.mapped('/proc/sys/kernel/random/boot_id'), str(uuid.uuid4()) + '\n')


class Stop(BaseException):
    """A power loss or a killed process: nothing after it runs."""


@contextmanager
def stop_when(condition):
    """Stop the refresh once condition() holds after a durable write or a directory sync.

    Every later write raises too, so not even the transaction's own rollback runs.
    """
    stopped = []
    atomic, sync = refresh.atomic, refresh.sync_directory

    def check():
        if stopped or condition():
            stopped.append(True)
            raise Stop()

    def cut_atomic(path, data):
        if stopped:
            raise Stop()
        atomic(path, data)
        check()

    def cut_sync(path):
        if stopped:
            raise Stop()
        sync(path)
        check()

    with patch.object(refresh, 'atomic', side_effect=cut_atomic), \
            patch.object(refresh, 'sync_directory', side_effect=cut_sync):
        try:
            yield
        except Stop:
            pass
    assert stopped, 'the stop condition never held'


def boot_without_generation(f):
    """mark-good after a start of a loader that names no generation (the fixture's original)."""
    f.put(f.mapped('/proc/cmdline'), f'root=UUID={f.identity["uuid"]}\n')
    refresh.mark_good(f.identity)


def mkimage_calls(f):
    """Every grub-mkimage run; the fixture records the wrapped command of each sh too."""
    return [c for c in f.commands if c[0] == 'grub-mkimage']


def esp_tree(f):
    esp = f.mapped('/efi')
    return {str(p.relative_to(esp)): p.read_bytes() for p in esp.rglob('*') if p.is_file()}


class SigningMachine(unittest.TestCase):
    fixture = Checks.fixture
    shared_esp = Checks.shared_esp
    complete_boot = Checks.complete_boot

    def setUp(self):
        # findmnt's PARTUUID of /efi; the fixture runs no external mount query.
        patcher = patch.object(refresh, 'esp_partuuid', return_value=ESP_PARTUUID)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_load_option_device_path(self):
        shim_text = '\\EFI\\Emaki\\shimx64.efi'
        pci = node(2, 1, bytes.fromhex('d041030a00000000')) + node(1, 1, bytes((0, 0x1f)))
        end = node(0x7f, 0xff, b'')
        disk = load_option(shim_text)
        cases = {
            'efibootmgr': (load_option(shim_text), (ESP_PARTUUID, shim_text)),
            'controller nodes first': (load_option(shim_text, before=pci), (ESP_PARTUUID, shim_text)),
            'optional data': (load_option(shim_text, optional=b'\x01\x02\x03'), (ESP_PARTUUID, shim_text)),
            'split file nodes': (raw_option(disk_node() + file_node('\\EFI') + file_node('Emaki\\shimx64.efi')
                                            + end), (ESP_PARTUUID, shim_text)),
            'file without partition': (raw_option(file_node(shim_text) + end), None),
            'MBR partition': (raw_option(disk_node(kind=(1, 1)) + file_node(shim_text) + end), None),
            'two partitions': (raw_option(disk_node() + disk_node() + file_node(shim_text) + end), None),
            'node after the file': (raw_option(disk_node() + file_node(shim_text) + pci + end), None),
            'no end node': (raw_option(disk_node() + file_node(shim_text)), None),
            'truncated': (disk[:-6], None),
            'no description end': (disk[:6] + 'Emaki'.encode('utf-16-le'), None),
            'empty': (b'', None),
        }
        for name, (option, expected) in cases.items():
            with self.subTest(option=name):
                self.assertEqual(refresh.load_option_path(option), expected)

    CLUSTER = 4096

    @contextmanager
    def test_pe_image_headers(self):
        # The check reads headers only: a whole signed image passes, any cut or unsigned one fails.
        # (Fedora's 16.1-7 shimx64.efi and mmx64.efi pass and their halves fail; checked by hand.)
        image = pe_image(b'shim ')
        self.assertTrue(refresh.signed_pe_image(image))
        for index, damaged in enumerate((b'', b'MZ', image[:64], image[:1024], image[:-1],
                                         pe_image(b'shim ', signed=False), b'\0' + image[1:],
                                         image.replace(b'PE\0\0', b'PX\0\0', 1))):
            with self.subTest(index=index):
                self.assertFalse(refresh.signed_pe_image(damaged))

    def test_shim_package_update_runs_the_refresh_hook(self):
        hook = (ROOT / 'grub/95-emaki-boot-refresh.hook').read_text()
        install = hook.split('[Trigger]')[1]
        self.assertIn('Operation = Install\n', install)
        self.assertIn('Operation = Upgrade\n', install)
        self.assertIn('Target = emaki-shim-signed\n', install)

    def test_generated_menu_loads_faces_from_the_memdisk_only_with_the_key(self):
        menu = (f'if loadfont ($root)/@{FACE} ; then\n  set gfxmode=auto\nfi\n'
                f'loadfont ($root)/@{THEME_FACE}\nset theme=($root)/@/usr/share/emaki/grub/theme/theme.txt\n')
        for key in (False, True):
            with self.subTest(key=key), self.fixture(False) as f:
                stage = f.mapped('/boot/emaki/' + 'b' * 32)
                (stage / 'grub').mkdir(parents=True)
                f.put(f.mapped('/boot/grub/grub.cfg'), menu)
                if key:
                    f.put(f.mapped(str(secureboot.KEY)), b'private test key\n')
                with patch.object(refresh, 'run'), patch.object(refresh, 'shared_esp', return_value=False), \
                        patch.object(refresh.boot, 'boot_framebuffer', return_value=None):
                    refresh.generate(stage)
                text = f.mapped('/boot/grub/grub.cfg').read_text()
                if key:
                    self.assertIn(f'if loadfont (memdisk){FACE} ; then\n', text)
                    self.assertIn(f'loadfont (memdisk){THEME_FACE}\n', text)
                    self.assertIn('set theme=($root)/@/usr/share/emaki/grub/theme/theme.txt\n', text)
                else:
                    self.assertEqual(text, boot.menu_layout(boot.menu_font_fallback(menu)))
                    self.assertNotIn('(memdisk)', text)

    def test_generated_kernel_entries_print_nothing(self):
        # B9: the stock "Loading ..." lines showed in GRUB's black terminal window until the logo.
        menu = (ROOT / 'tests/fixtures/grub-2.16-encrypted.cfg').read_text()
        self.assertIn("\techo\t'Loading Linux linux ...'\n", menu)
        for key in (False, True):
            with self.subTest(key=key), self.fixture(False) as f:
                stage = f.mapped('/boot/emaki/' + 'b' * 32)
                (stage / 'grub').mkdir(parents=True)
                f.put(f.mapped('/boot/grub/grub.cfg'), menu)
                if key:
                    f.put(f.mapped(str(secureboot.KEY)), b'private test key\n')
                with patch.object(refresh, 'run'), patch.object(refresh, 'shared_esp', return_value=False), \
                        patch.object(refresh.boot, 'boot_framebuffer', return_value=None):
                    refresh.generate(stage)
                text = f.mapped('/boot/grub/grub.cfg').read_text()
                self.assertNotIn("'Loading ", text)
                self.assertEqual(re.findall(r"^menuentry 'Emaki[^\n]*\{\n([^\n]*)\n", text, re.M),
                                 ['\tclear'] * 6)  # two kernels, the two moved entries in both orders
                boot.verify_grub(text)


class EspIdentity(unittest.TestCase):
    def test_esp_partition_guid_comes_from_the_exact_efi_mount(self):
        cases = (({'partuuid': ESP_PARTUUID.upper()}, ESP_PARTUUID),
                 ({'partuuid': ESP_PARTUUID}, ESP_PARTUUID),
                 ({'partuuid': None}, None),
                 ({}, None),
                 ({'partuuid': '1234abcd-01'}, None),  # an MBR disk has no partition GUID
                 (refresh.Refuse('Cannot identify one mounted filesystem at /efi.'), None))
        for answer, expected in cases:
            with self.subTest(answer=answer), patch.object(
                    refresh, 'mount_info',
                    **({'side_effect': answer} if isinstance(answer, Exception) else {'return_value': answer})) as query:
                self.assertEqual(refresh.esp_partuuid(), expected)
                query.assert_called_once_with('/efi', True)


class MachineWithoutKey(unittest.TestCase):
    """Proof that a machine without the key keeps exactly the old boot path."""
    fixture = Checks.fixture
    complete_boot = Checks.complete_boot

    def test_same_image_command_loaders_and_no_shim_files(self):
        for encrypted in (False, True):
            with self.subTest(encrypted=encrypted), self.fixture(encrypted) as f:
                # Everything a signing machine has except the key: none of it is used.
                machine(f, key=False)
                before = esp_tree(f)
                self.assertEqual(refresh.fallback_loader(), f.mapped('/efi/EFI/BOOT/BOOTX64.EFI'))
                self.assertFalse(f.mapped('/efi/EFI/Emaki/a').exists())
                self.assertFalse(f.mapped('/efi/EFI/Emaki/b').exists())
                refresh.refresh(f.identity)
                generation, = f.mapped('/boot/emaki').iterdir()
                platform = generation / 'grub/x86_64-efi'
                builds = mkimage_calls(f)
                self.assertEqual(builds, [[
                    'grub-mkimage', '--directory=' + str(f.mapped('/usr/lib/grub/x86_64-efi')),
                    '--format=x86_64-efi', '--compression=none',
                    '--memdisk=' + str(platform / 'emaki-early.tar'), '--prefix=(memdisk)/boot/grub',
                    '--config=' + str(platform / 'emaki-early.cfg'),
                    '--output=' + str(platform / 'emaki-early.efi'), *refresh.IMAGE_MODULES]])
                self.assertEqual(refresh.IMAGE_MODULES, (*boot.GRUB_EARLY_MODULES, 'search_fs_uuid'))
                self.assertFalse([c for c in f.commands if c[0] in ('sh', 'sbsign', 'sbverify')])
                self.assertFalse((platform / refresh.SBAT_DATA).exists())
                with tarfile.open(platform / 'emaki-early.tar') as archive:
                    self.assertFalse([n for n in archive.getnames() if n.startswith('usr/')])
                after = esp_tree(f)
                added = sorted(set(after) - set(before))
                self.assertEqual(added, ['EFI/Emaki/boot-state.json', f'EFI/Emaki/loader-{generation.name}.efi',
                                         'EFI/Emaki/trial.env'])
                self.complete_boot(f, generation)
                image = (platform / 'emaki-early.efi').read_bytes()
                self.assertEqual(f.mapped('/efi/EFI/BOOT/BOOTX64.EFI').read_bytes(), image)
                self.assertEqual(f.mapped('/efi/EFI/BOOT/grubx64.efi').exists(), False)
                self.assertFalse([n for n in esp_tree(f) if 'shim' in n or 'mmx64' in n or n.endswith('.cer')])

    def test_input_signature_ignores_shim_package_without_the_key(self):
        with self.fixture(False) as f:
            defaults = f.mapped('/etc/default/grub').read_text()
            plain = refresh.input_signature(defaults)
            machine(f, key=False)
            for name in secureboot.GRUB_MODULES:
                path = f.mapped('/usr/lib/grub/x86_64-efi') / (name + '.mod')
                if name not in refresh.IMAGE_MODULES:
                    path.unlink()
            f.mapped(FACE).unlink()
            f.mapped(THEME_FACE).unlink()
            self.assertEqual(refresh.input_signature(defaults), plain)
            f.put(f.mapped(str(secureboot.KEY)), b'private test key\n')
            f.put(f.mapped(str(secureboot.DER)), b'certificate in DER\n')
            self.assertNotEqual(refresh.input_signature(defaults), plain)


def tool_root(variable):
    value = os.environ.get(variable)
    return Path(value) if value else None


if __name__ == '__main__':
    unittest.main()
