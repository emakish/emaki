# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Secure Boot core: firmware db reading, the machine key, SBAT and MOK requests."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from emaki_installer import secureboot

FIXTURES = Path(__file__).parent / 'fixtures/secureboot'
OWNER = bytes(16)
SHA256_TYPE = bytes.fromhex('2616c4c14c509240aca941f936934328')


def certificate(name):
    return (FIXTURES / f'{name}.der').read_bytes()


def signature_list(kind, entries, size=None):
    size = size or max(16 + len(entry) for entry in entries)
    body = b''.join(OWNER + entry.ljust(size - 16, b'\0') for entry in entries)
    return kind + (28 + len(body)).to_bytes(4, 'little') + bytes(4) + size.to_bytes(4, 'little') + body


def database(*names, hashes=0):
    lists = [signature_list(secureboot.X509_TYPE, [certificate(name)]) for name in names]
    if hashes:
        lists.insert(0, signature_list(SHA256_TYPE, [bytes([n]) * 32 for n in range(hashes)], 48))
    return b''.join(lists)


def tool(name):
    """sbsigntools from PATH, or from EMAKI_SBSIGNTOOLS (an extracted package root)."""
    root = os.environ.get('EMAKI_SBSIGNTOOLS')
    if root:
        path = Path(root) / 'usr/bin' / name
        return [str(path)] if path.is_file() else None
    found = shutil.which(name)
    return [found] if found else None


def local_run(argv, input=None):
    command = list(argv)
    if command[0] in ('sbsign', 'sbverify'):
        command = tool(command[0]) + command[1:]
    env = dict(os.environ)
    if os.environ.get('EMAKI_SBSIGNTOOLS'):
        env['LD_LIBRARY_PATH'] = str(Path(os.environ['EMAKI_SBSIGNTOOLS']) / 'usr/lib')
    result = subprocess.run(command, input=input, text=True, capture_output=True, env=env)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout


class DatabaseTests(unittest.TestCase):
    def test_both_third_party_cas(self):
        db = database('pca2011', 'uefi2011', 'windows2023', 'uefi2023', 'optionrom2023', hashes=3)
        self.assertEqual(secureboot.microsoft_cas(db), 'both')

    def test_single_ca(self):
        self.assertEqual(secureboot.microsoft_cas(database('pca2011', 'uefi2011')), '2011')
        self.assertEqual(secureboot.microsoft_cas(database('windows2023', 'uefi2023')), '2023')

    def test_windows_only_db_is_none(self):
        # Secured-core PCs: Windows' own CAs and the option ROM CA are not the shim's issuers.
        self.assertEqual(secureboot.microsoft_cas(database('pca2011', 'windows2023', 'optionrom2023')), 'none')
        self.assertEqual(secureboot.microsoft_cas(b''), 'none')

    def test_one_list_with_several_certificates(self):
        entries = [certificate('pca2011'), certificate('uefi2023')]
        db = signature_list(secureboot.X509_TYPE, entries)
        self.assertEqual(len(secureboot.signature_list_certificates(db)), 2)
        self.assertEqual(secureboot.microsoft_cas(db), '2023')

    def test_malformed_database(self):
        db = database('uefi2011')
        for broken in (db[:20], db[:-1], db[:16] + (5).to_bytes(4, 'little') + db[20:]):
            with self.assertRaises(secureboot.SecureBootError):
                secureboot.microsoft_cas(broken)

    def test_subject_not_issuer(self):
        # The 2011 CA is issued by the Marketplace Root; only subjects count.
        self.assertEqual(secureboot.subject_common_names(certificate('uefi2011')), (secureboot.CA_2011,))
        self.assertEqual(secureboot.subject_common_names(b'\x30\x03\x02\x01'), ())

    def test_firmware_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertIsNone(secureboot.efi_variable(root, 'db', secureboot.IMAGE_SECURITY_GUID))
            variables = root / 'sys/firmware/efi/efivars'
            variables.mkdir(parents=True)
            self.assertEqual(secureboot.firmware_cas(root), 'none')
            (variables / f'db-{secureboot.IMAGE_SECURITY_GUID}').write_bytes(
                b'\x27\0\0\0' + database('uefi2011', 'uefi2023'))
            self.assertEqual(secureboot.firmware_cas(root), 'both')
            (variables / f'db-{secureboot.IMAGE_SECURITY_GUID}').write_bytes(b'\x27\0\0\0' + b'junk')
            self.assertIsNone(secureboot.firmware_cas(root))
            (variables / f'SetupMode-{secureboot.GLOBAL_GUID}').write_bytes(b'\x06\0\0\0\x01')
            self.assertTrue(secureboot.setup_mode(root))


class SbatTests(unittest.TestCase):
    ARCH = ('sbat,1,SBAT Version,sbat,1,https://github.com/rhboot/shim/blob/main/SBAT.md\n'
            'grub,4,Free Software Foundation,grub,2:2.16-1,https//www.gnu.org/software/grub/\n'
            'grub.arch,1,Arch Linux,grub,2:2.16-1,https://archlinux.org/packages/core/x86_64/grub/\n')

    def test_appends_emaki_line(self):
        text = secureboot.sbat(self.ARCH, '2:2.16-1')
        self.assertTrue(text.startswith(self.ARCH))
        self.assertTrue(text.endswith('grub.emaki,1,Emaki,grub,2:2.16-1,https://github.com/emakish/emaki\n'))
        self.assertEqual(secureboot.sbat(text, '2:2.16-1'), text)

    def test_raises_global_generation_to_the_build_level(self):
        warning = ("grub-mkimage: warning: Global SBAT level 4 in `sbat.csv' does not match "
                   'the build-time Global SBAT level 6.\n')
        level = secureboot.sbat_generation(warning)
        self.assertEqual(level, 6)
        text = secureboot.sbat(self.ARCH, '2:2.16-1', level)
        self.assertIn('\ngrub,6,Free Software Foundation,grub,2:2.16-1,', text)
        self.assertIn('\ngrub.arch,1,Arch Linux,', text)
        self.assertIsNone(secureboot.sbat_generation('Signing Unsigned original image\n'))
        # Never lowers a generation Arch already raised.
        self.assertIn('\ngrub,7,', secureboot.sbat(self.ARCH.replace('grub,4,', 'grub,7,'), '2:2.16-1', 6))

    def test_refuses_unknown_data(self):
        for text in ('', 'grub,4,x\n', 'sbat,1,x\n'):
            with self.assertRaises(secureboot.SecureBootError):
                secureboot.sbat(text, '2:2.16-1')
        with self.assertRaises(secureboot.SecureBootError):
            secureboot.sbat(self.ARCH, '2.16,evil')


class KeyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / 'secureboot'
        self.state.mkdir(mode=0o700)

    def test_code_signing_certificate(self):
        secureboot.generate_key(local_run, self.state)
        key = self.state / 'machine.key'
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        text = subprocess.run(['openssl', 'x509', '-in', self.state / 'machine.crt', '-noout', '-text'],
                              capture_output=True, text=True, check=True).stdout
        self.assertIn('Public-Key: (2048 bit)', text)
        self.assertIn('Code Signing', text)
        self.assertIn('CA:FALSE', text)
        # shim refuses keys that carry the kernel-module-only purpose.
        self.assertNotIn('1.3.6.1.4.1.2312.16.1.2', text)
        names = secureboot.subject_common_names((self.state / 'machine.cer').read_bytes())
        self.assertRegex(names[0], r'^Emaki Secure Boot machine key [0-9a-f]{8}$')

    def test_label_is_checked(self):
        with self.assertRaises(secureboot.SecureBootError):
            secureboot.openssl_config('x\nCN = other')

    @unittest.skipUnless(tool('sbsign') and tool('sbverify'), 'sbsigntools are not available')
    def test_sign_and_verify(self):
        secureboot.generate_key(local_run, self.state)
        other = Path(self.temp.name) / 'other'
        other.mkdir()
        secureboot.generate_key(local_run, other)
        image = Path(self.temp.name) / 'image.efi'
        image.write_bytes(minimal_efi())
        self.assertFalse(secureboot.signed(local_run, image, self.state))
        secureboot.sign(local_run, image, state=self.state)
        self.assertTrue(secureboot.signed(local_run, image, self.state))
        self.assertFalse(secureboot.signed(local_run, image, other))


class SignedImageTests(unittest.TestCase):
    ARCH = SbatTests.ARCH
    WARNING = ("grub-mkimage: warning: Global SBAT level 4 in `sbat.csv' does not match "
               'the build-time Global SBAT level 6.\n')

    def build(self, outputs):
        calls, written = [], {}

        def run(argv, input=None):
            calls.append(list(argv))
            return outputs.pop(0) if argv[0] == 'sh' else ''
        path = secureboot.build_signed_grub(
            run, written.__setitem__, ['grub-mkimage', '--format=x86_64-efi', '--output=/x/grub.efi', 'normal'],
            '/x/sbat.csv', self.ARCH)
        return path, calls, written

    def test_rebuilds_at_the_level_grub_mkimage_names(self):
        path, calls, written = self.build([self.WARNING, ''])
        self.assertEqual(path, '/x/grub.efi')
        builds = [call for call in calls if call[0] == 'sh']
        self.assertEqual(len(builds), 2)
        self.assertEqual(builds[0][:6], ['sh', '-c', '"$@" 2>&1', 'sh', 'grub-mkimage', '--sbat=/x/sbat.csv'])
        self.assertIn('\ngrub,6,Free Software Foundation,', written['/x/sbat.csv'])
        self.assertTrue(written['/x/sbat.csv'].endswith('grub.emaki,1,Emaki,grub,2:2.16-1,'
                                                        'https://github.com/emakish/emaki\n'))
        # Signed with the machine key and proven with its certificate afterwards.
        self.assertEqual([call[0] for call in calls[2:]], ['sbsign', 'sbverify'])
        self.assertEqual(calls[2][-1], '/x/grub.efi')

    def test_one_build_when_the_levels_agree(self):
        _, calls, written = self.build([''])
        self.assertEqual(sum(call[0] == 'sh' for call in calls), 1)
        self.assertIn('\ngrub,4,', written['/x/sbat.csv'])

    def test_a_level_that_stays_wrong_refuses(self):
        with self.assertRaises(secureboot.SecureBootError):
            self.build([self.WARNING, self.WARNING])

    def test_modules_and_esp_files(self):
        modules = secureboot.signed_image_modules(('part_gpt', 'memdisk', 'efi_gop'))
        self.assertEqual(modules[:3], ('part_gpt', 'memdisk', 'efi_gop'))
        self.assertEqual(len(modules), len(set(modules)))
        self.assertTrue(set(secureboot.GRUB_MODULES) <= set(modules))
        with tempfile.TemporaryDirectory() as temp:
            shim = Path(temp)
            (shim / 'shimx64.efi').write_bytes(b'shim')
            (shim / 'mmx64.efi').write_bytes(b'mm')
            (shim / 'machine.cer').write_bytes(b'cer')
            self.assertEqual(secureboot.esp_files(shim_dir=shim, der=shim / 'machine.cer'),
                             {'shimx64.efi': b'shim', 'mmx64.efi': b'mm', 'emaki-machine-key.cer': b'cer'})
            self.assertEqual(secureboot.esp_files(True, shim_dir=shim, der=shim / 'machine.cer'),
                             {'BOOTX64.EFI': b'shim', 'mmx64.efi': b'mm'})
            self.assertFalse(secureboot.enabled(shim))
            (shim / 'var/lib/emaki/secureboot').mkdir(parents=True)
            (shim / 'var/lib/emaki/secureboot/machine.key').write_bytes(b'k')
            self.assertTrue(secureboot.enabled(shim))


class MokTests(unittest.TestCase):
    def test_request_passes_password_on_stdin_only(self):
        calls = []
        secureboot.request_enrollment(lambda argv, input=None: calls.append((argv, input)), '01234567')
        self.assertEqual(calls, [(['mokutil', '--import', str(secureboot.DER)], '01234567\n01234567\n')])
        for bad in ('1234567', '0123456a', '012345678'):
            with self.assertRaises(secureboot.SecureBootError):
                secureboot.request_enrollment(lambda argv, input=None: None, bad)

    def test_password_shape(self):
        for _ in range(50):
            self.assertRegex(secureboot.one_time_password(), r'^[0-9]{8}$')

    def test_enrollment_states(self):
        def answer(text, fail=False):
            def run(argv, input=None):
                self.assertIn('--ignore-keyring', argv)
                if fail:
                    error = RuntimeError('mokutil failed')
                    error.output = text
                    raise error
                return text
            return run
        der = str(secureboot.DER)
        self.assertEqual(secureboot.enrollment(answer(f'{der} is not enrolled\n')), 'missing')
        self.assertEqual(secureboot.enrollment(answer(f'SKIP: {der} is already enrolled\n', True)), 'enrolled')
        self.assertEqual(secureboot.enrollment(
            answer(f'SKIP: {der} is already in the enrollment request\n', True)), 'pending')
        with self.assertRaises(secureboot.SecureBootError):
            secureboot.enrollment(answer('EFI variables are not supported on this system\n', True))

    def test_dkms_config_names_machine_key(self):
        text = secureboot.dkms_config()
        self.assertIn('mok_signing_key=/var/lib/emaki/secureboot/machine.key\n', text)
        self.assertIn('mok_certificate=/var/lib/emaki/secureboot/machine.cer\n', text)


class MenuFontTests(unittest.TestCase):
    HEADER = ('if loadfont /@/usr/share/emaki/grub/fonts/menu-32.pf2 ; then\n'
              '  set gfxmode=1024x768,800x600,640x480,auto\n  load_video\n  insmod gfxterm\nfi\n'
              'terminal_output gfxterm\n'
              'insmod gfxmenu\n'
              'loadfont ($root)/@/usr/share/emaki/grub/theme/noto-sans-22.pf2\n'
              'loadfont ($root)/usr/share/emaki/grub/theme/arrows-24.pf2\n'
              'loadfont ($root)/@/home/me/own.pf2\n'
              'loadfont unicode\n'
              'set theme=($root)/@/usr/share/emaki/grub/theme/theme-100.txt\n')

    def test_emaki_faces_come_from_the_memdisk(self):
        from emaki_installer import boot
        text = boot.secure_boot_menu(self.HEADER)
        self.assertIn('if loadfont (memdisk)/usr/share/emaki/grub/fonts/menu-32.pf2 ; then\n', text)
        self.assertIn('loadfont (memdisk)/usr/share/emaki/grub/theme/noto-sans-22.pf2\n', text)
        self.assertIn('loadfont (memdisk)/usr/share/emaki/grub/theme/arrows-24.pf2\n', text)
        # Local faces and the stock font stay as the person or GRUB wrote them.
        self.assertIn('loadfont ($root)/@/home/me/own.pf2\n', text)
        self.assertIn('loadfont unicode\n', text)
        self.assertEqual(boot.generated_menu_font(text), boot.generated_menu_font(self.HEADER))
        self.assertEqual(boot.generated_menu_font(text), '/usr/share/emaki/grub/fonts/menu-32.pf2')
        self.assertEqual(boot.secure_boot_menu(text), text)
        fallback = boot.menu_font_fallback(text)
        self.assertIn('set gfxterm_font="DejaVu Sans Mono Regular 32"', fallback)

    def test_font_files_keep_packaged_paths(self):
        from emaki_installer import boot
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('fonts/menu-24.pf2', 'theme/noto-sans-16.pf2', 'theme/theme-100.txt'):
                path = root / 'usr/share/emaki/grub' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'PFF2 ' + name.encode())
            files = boot.secure_boot_fonts(root)
            self.assertEqual(sorted(files), ['usr/share/emaki/grub/fonts/menu-24.pf2',
                                             'usr/share/emaki/grub/theme/noto-sans-16.pf2'])
            # The names are valid memdisk entries for the loader payload.
            grub = root / 'grub'
            (grub / 'x86_64-efi').mkdir(parents=True)
            (grub / 'x86_64-efi/normal.mod').write_bytes(b'mod')
            _, _, memdisk = boot.grub_loader_payload(grub, None, '/@', None, 'search.fs_uuid x root\n',
                                                     b'', files)
            import io
            import tarfile
            with tarfile.open(fileobj=io.BytesIO(memdisk)) as archive:
                names = archive.getnames()
            self.assertIn('usr/share/emaki/grub/fonts/menu-24.pf2', names)
            self.assertIn('boot/grub/x86_64-efi/normal.mod', names)
            with tarfile.open(fileobj=io.BytesIO(memdisk)) as archive:
                menu = archive.extractfile('boot/grub/grub.cfg').read()
            self.assertEqual(menu, b'source ($root)/@/boot/grub/grub.cfg\n')


def minimal_efi():
    """A tiny valid PE32+ EFI application (one .text section returning EFI_SUCCESS)."""
    header = bytearray(0x400)
    header[0:2] = b'MZ'
    header[0x3c:0x40] = (0x80).to_bytes(4, 'little')
    pe = 0x80
    header[pe:pe + 4] = b'PE\0\0'
    coff = pe + 4
    header[coff:coff + 20] = (0x8664).to_bytes(2, 'little') + (1).to_bytes(2, 'little') + bytes(12) + \
        (240).to_bytes(2, 'little') + (0x22).to_bytes(2, 'little')
    opt = coff + 20
    fields = bytearray(240)
    fields[0:2] = (0x20b).to_bytes(2, 'little')
    fields[4:8] = (0x200).to_bytes(4, 'little')        # SizeOfCode
    fields[16:20] = (0x1000).to_bytes(4, 'little')     # AddressOfEntryPoint
    fields[20:24] = (0x1000).to_bytes(4, 'little')     # BaseOfCode
    fields[24:32] = (0x10000000).to_bytes(8, 'little')  # ImageBase
    fields[32:36] = (0x1000).to_bytes(4, 'little')     # SectionAlignment
    fields[36:40] = (0x200).to_bytes(4, 'little')      # FileAlignment
    fields[56:60] = (0x2000).to_bytes(4, 'little')     # SizeOfImage
    fields[60:64] = (0x400).to_bytes(4, 'little')      # SizeOfHeaders
    fields[68:70] = (10).to_bytes(2, 'little')         # EFI application
    fields[108:112] = (16).to_bytes(4, 'little')       # NumberOfRvaAndSizes
    header[opt:opt + 240] = fields
    section = opt + 240
    header[section:section + 40] = b'.text\0\0\0' + (0x200).to_bytes(4, 'little') + \
        (0x1000).to_bytes(4, 'little') + (0x200).to_bytes(4, 'little') + (0x400).to_bytes(4, 'little') + \
        bytes(12) + (0x60000020).to_bytes(4, 'little')
    code = b'\x31\xc0\xc3'.ljust(0x200, b'\0')  # xor eax, eax; ret
    return bytes(header) + code


if __name__ == '__main__':
    unittest.main()
