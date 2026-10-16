#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline tests of the Secure Boot install medium: the GRUB module audit and iso/secureboot.sh.

secureboot.sh runs for real on a staged archiso tree; sbsign, sbverify, grub-mkstandalone, mkfs.fat
and mcopy are stand-ins that record their calls (a FAT image is a directory beside its file), and
the shim package is a fixture archive pinned by its own hash. openssl, bsdtar and the audit are real.
The signed images themselves are proven in a Secure Boot VM (tests/vm/README.md, Secure Boot firmware).
"""
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('grub_modules', HERE / 'secureboot-grub-modules.py')
grub_modules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grub_modules)
spec = importlib.util.spec_from_file_location('image_check', HERE / 'verify-image.py')
image_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image_check)

# GRUB's command.lst lines for every command our menus run (format: "[*]command: module").
COMMAND_LST = '''*background_image: gfxterm_background
*echo: echo
*menuentry: normal
*probe: probe
*regexp: regexp
*search: search
*serial: serial
[: test
chainloader: chain
configfile: configfile
export: normal
fwsetup: efifwsetup
halt: halt
initrd: linux
linux: linux
loadfont: font
reboot: reboot
terminal_input: terminal
terminal_output: terminal
test: test
'''
# What archiso writes as grub-embed.cfg (mkarchiso v91), cut to its commands.
EMBED = '''if ! [ -d "$cmdpath" ]; then
    if regexp --set=1:archiso_bootdevice '^\\(([^)]+)\\)\\/?[Ee][Ff][Ii]\\/[Bb][Oo][Oo][Tt]\\/?$' "${cmdpath}"; then
        set cmdpath="(${archiso_bootdevice})/EFI/BOOT"
    fi
fi
if search --no-floppy --set=archiso_device --file '/boot/x.uuid' --hint "${ARCHISO_HINT}"; then
    if probe --set ARCHISO_UUID --fs-uuid "${ARCHISO_HINT}"; then
        export ARCHISO_UUID
    fi
else
    echo "Could not find a volume"
fi
configfile "(${ARCHISO_HINT})/boot/grub/grub.cfg"
'''
STUBS = {
    'sbsign': r'''#!/usr/bin/env bash
while (($#)); do case $1 in --key) key=$2; shift 2 ;; --cert) cert=$2; shift 2 ;; --output) out=$2; shift 2 ;; *) in=$1; shift ;; esac; done
echo "sbsign $in" >>"$STUB_LOG"
{ cat "$in"; printf '\nSIGNED-BY %s\n' "$(sha256sum <"$cert" | cut -c1-16)"; } >"$out"
''',
    'sbverify': r'''#!/usr/bin/env bash
if [[ $1 == --list ]]; then printf '%b' "${STUB_SHIM_SIGNERS:-signature 1\nissuer: /C=US/O=Microsoft Corporation/CN=Microsoft Corporation UEFI CA 2011\nsignature 2\nissuer: /C=US/O=Microsoft Corporation/CN=Microsoft UEFI CA 2023\n}"; exit 0; fi
[[ $1 == --cert ]] && grep -q "SIGNED-BY $(sha256sum <"$2" | cut -c1-16)" "$3"
''',
    'grub-mkstandalone': r'''#!/usr/bin/env bash
echo "grub-mkstandalone $*" >>"$STUB_LOG"
while (($#)); do case $1 in -o) out=$2; shift 2 ;; --sbat=*) sbat=${1#--sbat=}; cp "$sbat" "$STUB_LOG.sbat"; shift ;; *) shift ;; esac; done
level=$(sed -n 's/^grub,\([0-9]*\),.*/\1/p' "$sbat")
[[ $level == 6 ]] || echo "grub-mkstandalone: warning: Global SBAT level $level in \`$sbat' does not match the build-time Global SBAT level 6." >&2
echo 'GRUB image' >"$out"
''',
    'mkfs.fat': r'''#!/usr/bin/env bash
echo "mkfs.fat $*" >>"$STUB_LOG"
image=${@: -2:1}
rm -rf "$image.d"; mkdir "$image.d"; : >"$image"
''',
    'mcopy': r'''#!/usr/bin/env bash
args=(); while (($#)); do case $1 in -i) image=$2; shift 2 ;; -s|-n) shift ;; *) args+=("$1"); shift ;; esac; done
if [[ ${args[0]} == ::/ ]]; then cp -a "$image.d/." "${args[1]}"; else cp -a "${args[@]:0:${#args[@]}-1}" "$image.d/"; fi
''',
}


def write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def fixture_grub_lib(directory):
    """A GRUB module directory with command.lst and every module the medium's menus can need."""
    lib = Path(directory)
    lib.mkdir(parents=True, exist_ok=True)
    (lib / 'command.lst').write_text(COMMAND_LST)
    names = {line.partition(':')[2].strip() for line in COMMAND_LST.splitlines()}
    base = (HERE / 'secureboot-grub-base.txt').read_text().splitlines()
    names |= set(' '.join(line for line in base if not line.startswith('#')).split())
    names |= {'efi_gop', 'gfxterm_background', 'png', 'all_video', 'udf', 'exfat'}
    for name in names:
        (lib / f'{name}.mod').touch()
    return lib


class ShippedShimPins(unittest.TestCase):
    def test_medium_and_installed_package_use_the_same_rpm(self):
        rows = [line.split() for line in (HERE / 'secureboot-shim.txt').read_text().splitlines()
                if line.strip() and not line.lstrip().startswith('#')]
        self.assertEqual(len(rows), 1)
        recipe = HERE.parent / 'packaging/emaki-shim-signed/PKGBUILD'
        result = subprocess.run(
            ['bash', '-c', 'source "$1"; printf "%s\\n" "${source[0]}" "${sha256sums[0]}"',
             'shim-pins', str(recipe)], check=True, text=True, capture_output=True, timeout=10)
        self.assertEqual(rows[0], result.stdout.splitlines(),
                         'The install medium and installed system must pin the same shim RPM.')


class ModuleAudit(unittest.TestCase):
    lib = staticmethod(fixture_grub_lib)

    def test_every_command_and_insmod_of_the_medium_menus_is_built_in(self):
        with tempfile.TemporaryDirectory() as directory:
            lib = self.lib(directory)
            embed = Path(directory) / 'embed.cfg'
            embed.write_text(EMBED)
            found = grub_modules.modules(lib, ['normal'], [embed, HERE / 'profile/grub/grub.cfg'])
        # Loaded today from the memdisk on demand; refused from a file under Secure Boot.
        for name in ('probe', 'regexp', 'search', 'configfile', 'test', 'terminal', 'gfxterm_background',
                     'efi_gop', 'font', 'linux', 'chain', 'efifwsetup', 'halt', 'reboot', 'serial', 'echo'):
            self.assertIn(name, found)
        self.assertEqual(found[0], 'normal')
        self.assertEqual(len(found), len(set(found)))

    def test_variables_inside_titles_and_strings_are_not_commands(self):
        text = 'menuentry "Emaki (${platform}; {x})" --id a {\n    set gfxpayload=keep\n    linux /k a=${b}\n}\n'
        self.assertEqual([c for c, _ in grub_modules.commands(text)], ['menuentry', 'set', 'linux'])
        text = "# insmod nothing\ndefault=archlinux\nif [ x == 'y;z' ]; then echo '#not a comment'; fi\n"
        self.assertEqual(list(grub_modules.commands(text)), [('[', 'x'), ('echo', "'#not a comment'")])

    def test_an_unknown_command_or_a_missing_module_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            lib = self.lib(directory)
            config = Path(directory) / 'a.cfg'
            config.write_text('frobnicate now\n')
            with self.assertRaisesRegex(ValueError, "no GRUB module provides the command 'frobnicate'"):
                grub_modules.modules(lib, [], [config])
            config.write_text('insmod nosuchmod\n')
            with self.assertRaisesRegex(ValueError, 'missing GRUB module: nosuchmod.mod'):
                grub_modules.modules(lib, [], [config])


class SecureBootMedium(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        base = self.base = Path(self.work.name)
        self.bin = base / 'bin'
        for name, text in STUBS.items():
            write(self.bin / name, text, 0o755)
        self.log = base / 'calls.log'
        self.log.touch()
        # The release key: a code-signing certificate and its key.
        self.key = base / 'key/release'
        self.key.parent.mkdir()
        self.make_key(self.key, 'codeSigning')
        # archiso's staged UEFI GRUB boot after pass one.
        mk = self.mk = base / 'mk'
        write(mk / 'iso/EFI/BOOT/BOOTx64.EFI', 'archiso grub')
        write(mk / 'iso/boot/grub/grub.cfg', (HERE / 'profile/grub/grub.cfg').read_text())
        write(mk / 'iso/boot/grub/menu.pf2', 'font')
        write(mk / 'iso/emaki/boot/x86_64/vmlinuz-linux', 'kernel')
        write(mk / 'iso/emaki/boot/x86_64/vmlinuz-linux-lts', 'kernel lts')
        write(mk / 'grub-embed.cfg', EMBED)
        write(mk / 'efiboot.img.d/EFI/BOOT/BOOTx64.EFI', 'archiso grub')
        write(mk / 'efiboot.img.d/shellx64.efi', 'shell')
        (mk / 'efiboot.img').touch()
        self.lib = fixture_grub_lib(base / 'grub')
        write(base / 'sbat.csv', 'sbat,1,SBAT Version,sbat,1,https://github.com/rhboot/shim/blob/main/SBAT.md\n'
                                 'grub,4,Free Software Foundation,grub,2:2.16-1,https//www.gnu.org/software/grub/\n')
        # Fedora's shim package, as a fixture archive pinned by its hash.
        rpm = base / 'pkg/shim-x64-0-0.x86_64.rpm'
        rpm.parent.mkdir()
        with tarfile.open(rpm, 'w') as archive:
            for name, text in (('usr/lib/efi/shim/0/EFI/BOOT/BOOTX64.EFI', 'shim'),
                               ('usr/lib/efi/shim/0/EFI/fedora/mmx64.efi', 'mokmanager')):
                source = base / 'pkg' / Path(name).name
                source.write_text(text)
                archive.add(source, name)
        self.cache = base / 'cache'
        self.cache.mkdir()
        shutil.copy(rpm, self.cache / rpm.name)
        digest = hashlib.sha256(rpm.read_bytes()).hexdigest()
        self.shim_list = base / 'shim.txt'
        self.shim_list.write_text(f'# fixture\nhttps://example.invalid/{rpm.name} {digest}\n')

    def tearDown(self):
        self.work.cleanup()

    def make_key(self, prefix, usage):
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '2', '-subj', '/CN=Test',
                        '-addext', f'extendedKeyUsage={usage}', '-keyout', f'{prefix}.key', '-out', f'{prefix}.crt'],
                       check=True, capture_output=True)

    def run_script(self, key=None, **env):
        return subprocess.run(['bash', str(HERE / 'secureboot.sh'), '--mode', 'test', '--release-cert',
                               str(self.base / 'no-pinned-release.crt'), '--key', str(key or self.key), '--mk', str(self.mk),
                               '--cache', str(self.cache), '--grub-lib', str(self.lib), '--sbat', str(self.base / 'sbat.csv'),
                               '--shim-list', str(self.shim_list)], text=True, capture_output=True,
                              env=dict(os.environ, PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}',
                                       STUB_LOG=str(self.log), EMAKI_PROC_ROOT=str(fake_proc(self.base)), **env),
                              timeout=120)

    def test_the_medium_boots_through_shim_with_signed_grub_and_kernels(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        mark = 'SIGNED-BY ' + hashlib.sha256(Path(f'{self.key}.crt').read_bytes()).hexdigest()[:16]
        for kernel in ('vmlinuz-linux', 'vmlinuz-linux-lts'):
            self.assertIn(mark, (self.mk / 'iso/emaki/boot/x86_64' / kernel).read_text())
        der = subprocess.run(['openssl', 'x509', '-in', f'{self.key}.crt', '-outform', 'DER'], capture_output=True,
                             check=True).stdout
        for root in (self.mk / 'efiboot.img.d', self.mk / 'iso'):
            with self.subTest(root=root.name):
                self.assertEqual((root / 'EFI/BOOT/BOOTx64.EFI').read_text(), 'shim')
                self.assertEqual((root / 'EFI/BOOT/mmx64.efi').read_text(), 'mokmanager')
                grub = (root / 'EFI/BOOT/grubx64.efi').read_text()
                self.assertTrue(grub.startswith('GRUB image') and mark in grub)
                self.assertEqual((root / 'emaki.cer').read_bytes(), der)
        self.assertEqual((self.mk / 'efiboot.img.d/shellx64.efi').read_text(), 'shell', 'the rest of the ESP stays')
        calls = self.log.read_text()
        build = next(line for line in calls.splitlines() if line.startswith('grub-mkstandalone'))
        self.assertNotIn('--disable-shim-lock', build)
        modules = build.split('--modules=', 1)[1].split(' --', 1)[0].split()
        for name in ('probe', 'terminal', 'test', 'gfxterm_background', 'efi_gop', 'normal', 'linux'):
            self.assertIn(name, modules)
        self.assertIn(f"--install-modules={' '.join(modules)}", build)
        # GRUB 2.16 refuses fonts from the stick under Secure Boot: the font rides in the memdisk.
        self.assertIn(f"boot/grub/menu.pf2={self.mk}/iso/boot/grub/menu.pf2", build)
        menu = (self.mk / 'iso/boot/grub/grub.cfg').read_text()
        self.assertIn('    if loadfont (memdisk)/boot/grub/menu.pf2; then\n', menu)
        self.assertNotIn('loadfont /boot/grub/menu.pf2', menu)
        sbat = Path(f'{self.log}.sbat').read_text()
        # Arch's line says 4; GRUB 2.16 reports 6, and the image declares 6 (built twice).
        self.assertIn('grub,6,Free Software Foundation,grub,2:2.16-1', sbat)
        self.assertEqual(calls.count('grub-mkstandalone'), 2)
        version = (HERE / 'VERSION').read_text().strip()
        self.assertIn(f'grub.emaki,1,Emaki,grub,{version},', sbat)
        self.assertRegex(calls, r'mkfs\.fat -C -n EMAKI ')
        record = (self.mk / 'iso/emaki/SECUREBOOT').read_text()
        self.assertIn('shim_rpm shim-x64-0-0.x86_64.rpm', record)

    def test_a_module_signing_only_key_is_refused(self):
        other = self.base / 'key/module'
        self.make_key(other, '1.3.6.1.4.1.2312.16.1.2')
        result = self.run_script(other)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('module-signing-only usage', result.stderr)
        self.assertEqual(self.log.read_text(), '', 'nothing is built or signed')

    def test_a_key_that_is_not_the_certificates_is_refused(self):
        other = self.base / 'key/other'
        self.make_key(other, 'codeSigning')
        shutil.copy(f'{other}.key', f'{self.key}.key')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not belong to', result.stderr)

    def test_a_shim_without_both_microsoft_signatures_is_refused(self):
        result = self.run_script(STUB_SHIM_SIGNERS='issuer: /CN=Microsoft Corporation UEFI CA 2011\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('shim is not signed by Microsoft UEFI CA 2023', result.stderr)
        self.assertEqual((self.mk / 'iso/EFI/BOOT/BOOTx64.EFI').read_text(), 'archiso grub', 'nothing is staged')

    def test_a_shim_package_that_is_not_the_pinned_one_is_refused(self):
        rpm = next(self.cache.glob('*.rpm'))
        rpm.write_bytes(rpm.read_bytes() + b'x')
        write(self.bin / 'curl', '#!/bin/sh\nexit 22\n', 0o755)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('cannot download', result.stderr)
        write(self.bin / 'curl', '#!/bin/sh\nwhile [ "$1" != -o ]; do shift; done; echo tampered > "$2"\n', 0o755)
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not match the pinned SHA-256', result.stderr)

    def test_a_menu_that_loads_its_font_another_way_is_refused(self):
        menu = self.mk / 'iso/boot/grub/grub.cfg'
        menu.write_text(menu.read_text().replace('if loadfont /boot/grub/menu.pf2; then', 'if loadfont $font; then'))
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('no longer loads its font', result.stderr)
        self.assertNotIn('grub-mkstandalone', self.log.read_text())

    def test_a_tree_that_is_not_archiso_uefi_grub_staging_is_refused(self):
        (self.mk / 'grub-embed.cfg').unlink()
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is not archiso's staged UEFI GRUB boot", result.stderr)


    def test_the_private_key_reaches_no_file_of_the_medium_and_no_output(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        pem = Path(f'{self.key}.key').read_text()
        der = subprocess.run(['openssl', 'pkey', '-in', f'{self.key}.key', '-outform', 'DER'], capture_output=True,
                             check=True).stdout
        needles = [b'PRIVATE KEY', der[-64:]] + [line.encode() for line in pem.splitlines() if len(line) >= 40]
        haystacks = {'stdout': result.stdout.encode(), 'stderr': result.stderr.encode()}
        for root in (self.mk, self.cache):
            for path in root.rglob('*'):
                if path.is_file():
                    haystacks[str(path)] = path.read_bytes()
        self.assertIn(str(self.mk / 'iso/EFI/BOOT/grubx64.efi'), haystacks)
        for name, data in haystacks.items():
            for needle in needles:
                self.assertNotIn(needle, data, f'private key material in {name}')


SWAPS_HEADER = 'Filename\t\t\t\tType\t\tSize\t\tUsed\t\tPriority\n'
CORE_PATTERN = '|/usr/lib/systemd/systemd-coredump %P %u %g %s %t %c %h %d %F %I\n'


def fake_proc(base, core_pattern=CORE_PATTERN):
    proc = base / 'proc'
    (proc / 'sys/kernel').mkdir(parents=True, exist_ok=True)
    (proc / 'sys/kernel/core_pattern').write_text(core_pattern)
    return proc


def limit_recorder(bin_directory, log):
    """An openssl on PATH that records its core size limits (soft hard) when it is given a .key file."""
    real = shutil.which('openssl')
    write(bin_directory / 'openssl', '#!/usr/bin/env bash\n'
                                     'for a; do case $a in *.key) '
                                     f'echo "$(ulimit -S -c) $(ulimit -H -c)" >>{log} ;; esac; done\n'
                                     f'exec {real} "$@"\n', 0o755)


class KeyRules(unittest.TestCase):
    """secureboot.sh --check-only: which key may sign which image (iso/build.sh runs it first)."""

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        base = self.base = Path(self.work.name)
        self.bin = base / 'bin'
        # `stat -f` (file system type) answers from the environment and /proc/swaps is a fixture file
        # (EMAKI_PROC_ROOT); everything else is real.
        real_stat = shutil.which('stat')
        write(self.bin / 'stat', '#!/usr/bin/env bash\n'
                                 'if [[ $1 == -f ]]; then echo "${STUB_FS:-tmpfs}"; exit 0; fi\n'
                                 f'exec {real_stat} "$@"\n', 0o755)
        self.proc = fake_proc(base)
        self.limits = base / 'openssl-limits.log'
        limit_recorder(self.bin, self.limits)
        self.release = base / 'run/emaki-release'
        self.release.parent.mkdir()
        SecureBootMedium.make_key(self, self.release, 'codeSigning')
        os.chmod(f'{self.release}.key', 0o600)
        self.pinned = base / 'pinned.crt'
        shutil.copy(f'{self.release}.crt', self.pinned)
        self.other = base / 'test/emaki-test-release'
        self.other.parent.mkdir()
        SecureBootMedium.make_key(self, self.other, 'codeSigning')

    def tearDown(self):
        self.work.cleanup()

    def check(self, mode, key=None, pinned=None, swaps=SWAPS_HEADER, **env):
        args = ['bash', str(HERE / 'secureboot.sh'), '--mode', mode, '--check-only',
                '--release-cert', str(pinned or self.pinned)]
        if key is not None:
            args += ['--key', str(key)]
        environment = dict(os.environ, PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}',
                           EMAKI_PROC_ROOT=str(self.proc))
        environment.update(env)
        swaps_file = self.proc / 'swaps'
        swaps_file.unlink(missing_ok=True)
        if swaps is not None:
            swaps_file.write_text(swaps)
        return subprocess.run(args, text=True, capture_output=True, env=environment, timeout=60)

    def assert_refused(self, result, phrase):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(phrase, result.stderr)

    def test_a_release_image_needs_the_pinned_release_key_unlocked_in_memory(self):
        result = self.check('release', self.release)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout + result.stderr, '')

    def test_a_release_image_without_a_key_fails_loudly(self):
        self.assert_refused(self.check('release'), 'a release image starts with Secure Boot on: pass --secure-boot-key')

    def test_a_release_image_without_a_pinned_certificate_fails(self):
        self.assert_refused(self.check('release', self.release, pinned=self.base / 'absent.crt'),
                            'no release certificate is pinned')

    def test_another_key_never_signs_a_release_image(self):
        self.assert_refused(self.check('release', self.other), 'is not the release certificate pinned in')

    def test_the_release_key_never_lies_on_a_disk_is_private_and_unswappable(self):
        self.assert_refused(self.check('release', self.release, STUB_FS='btrfs'),
                            'must lie on a RAM file system (/run in the build VM), never on a disk; ')
        os.chmod(f'{self.release}.key', 0o640)
        self.assert_refused(self.check('release', self.release), 'must be readable by its owner only')
        os.chmod(f'{self.release}.key', 0o600)
        self.assert_refused(self.check('release', self.release,
                                       swaps=SWAPS_HEADER + '/swap/swapfile file 524284 0 -2\n'),
                            'swap is on: the release key in memory could be written to a disk')

    def test_a_swap_inspection_that_fails_refuses_the_release_key(self):
        # No swap list at all, or one that is not /proc/swaps: never "no swap".
        for swaps in (None, '', 'garbage\n'):
            with self.subTest(swaps=swaps):
                self.assert_refused(self.check('release', self.release, swaps=swaps), 'cannot inspect the swap')

    def test_a_key_given_through_links_is_judged_by_the_files_they_reach(self):
        disk = self.base / 'disk'
        disk.mkdir()
        shutil.copy(f'{self.release}.key', disk / 'real.key')
        os.chmod(disk / 'real.key', 0o600)
        link = self.base / 'run/link'
        os.symlink(disk / 'real.key', f'{link}.key')
        os.symlink(f'{self.release}.crt', f'{link}.crt')
        # The link lies on the (stubbed) RAM file system; the key it reaches does not.
        real_stat = shutil.which('stat', path=os.environ['PATH'])
        write(self.bin / 'stat', '#!/usr/bin/env bash\n'
                                 f'if [[ $1 == -f ]]; then [[ ${{@: -1}} == {self.base}/run/* ]] && echo tmpfs || echo btrfs; exit 0; fi\n'
                                 f'exec {real_stat} "$@"\n', 0o755)
        self.assertEqual(self.check('release', self.release).returncode, 0)
        self.assert_refused(self.check('release', link), 'must lie on a RAM file system')

    def test_a_passphrase_protected_key_is_refused_before_anything_asks_for_it(self):
        encrypted = self.base / 'run/encrypted'
        shutil.copy(f'{self.release}.crt', f'{encrypted}.crt')
        subprocess.run(['openssl', 'pkcs8', '-topk8', '-v2', 'aes-256-cbc', '-in', f'{self.release}.key',
                        '-out', f'{encrypted}.key', '-passout', 'pass:throwaway'], check=True, capture_output=True)
        os.chmod(f'{encrypted}.key', 0o600)
        for mode in ('release', 'test'):
            with self.subTest(mode=mode):
                result = self.check(mode, encrypted)
                self.assert_refused(result, 'is protected by a passphrase, which the build cannot ask for')
                self.assertNotIn('pass phrase', result.stderr)

    def test_a_test_image_is_unsigned_or_signed_with_any_key_but_the_release_key(self):
        for mode in ('test', 'test-alongside'):
            with self.subTest(mode=mode):
                self.assertEqual(self.check(mode).returncode, 0)
                result = self.check(mode, self.other, STUB_FS='btrfs', swaps=SWAPS_HEADER + 'swapfile file 1 0 -2\n')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assert_refused(self.check(mode, self.release), 'a test image is never signed with the release key')
        # A new certificate for the release key is still the release key.
        reissued = self.base / 'test/reissued'
        shutil.copy(f'{self.release}.key', f'{reissued}.key')
        subprocess.run(['openssl', 'req', '-new', '-x509', '-key', f'{reissued}.key', '-subj', '/CN=Another name',
                        '-days', '3', '-addext', 'extendedKeyUsage=codeSigning', '-out', f'{reissued}.crt'],
                       check=True, capture_output=True)
        self.assert_refused(self.check('test', reissued), 'a test image is never signed with the release key')
        # Before the release key exists (nothing pinned), test keys work as tonight.
        self.assertEqual(self.check('test', self.other, pinned=self.base / 'absent.crt').returncode, 0)

    def test_every_openssl_that_reads_the_key_runs_without_core_dumps(self):
        for mode in ('release', 'test'):
            with self.subTest(mode=mode):
                self.limits.unlink(missing_ok=True)
                result = self.check(mode, self.release if mode == 'release' else self.other)
                self.assertEqual(result.returncode, 0, result.stderr)
                lines = self.limits.read_text().splitlines()
                self.assertTrue(lines)
                self.assertEqual(set(lines), {'0 0'})

    def test_a_dump_handler_that_ignores_the_limit_is_refused(self):
        fake_proc(self.base, '|/usr/local/bin/catch-dumps %p\n')
        self.assert_refused(self.check('test', self.other), "core dumps on this machine go to '/usr/local/bin/catch-dumps")

    def test_a_socket_dump_handler_is_refused(self):
        for pattern in ('@/tmp/catch-dumps.sock\n', '@@/tmp/catch-dumps.sock\n'):
            with self.subTest(pattern=pattern):
                fake_proc(self.base, pattern)
                self.assert_refused(self.check('test', self.other), 'core dumps on this machine go to the socket')

    def test_an_unknown_mode_is_refused(self):
        self.assert_refused(self.check('stable', self.other), 'usage: --mode release|test|test-alongside')


class BuildGate(unittest.TestCase):
    """iso/build.sh applies the key rules right after preflight, before root, VM or build work."""

    def test_build_refuses_a_release_without_the_key_and_a_locked_key_at_once(self):
        with tempfile.TemporaryDirectory() as directory:
            iso = Path(directory) / 'iso'
            shutil.copytree(HERE, iso, ignore=shutil.ignore_patterns('__pycache__', '*.png', '*.pf2', 'profile'))
            write(iso / 'preflight.sh', '#!/bin/bash\nexit 0\n', 0o755)
            key = Path(directory) / 'key/locked'
            key.parent.mkdir()
            SecureBootMedium.make_key(self, key, 'codeSigning')
            subprocess.run(['openssl', 'pkcs8', '-topk8', '-in', f'{key}.key', '-out', f'{key}.key.enc',
                            '-passout', 'pass:throwaway'], check=True, capture_output=True)
            os.replace(f'{key}.key.enc', f'{key}.key')
            test_key = Path(directory) / 'test.pub'
            test_key.write_text('ssh-ed25519 AAAA test\n')
            for args, phrase in (([], 'a release image starts with Secure Boot on'),
                                 (['--secure-boot-key', str(key)], 'is protected by a passphrase'),
                                 (['--test', '--test-key', str(test_key), '--secure-boot-key', str(key)],
                                  'is protected by a passphrase')):
                with self.subTest(args=args):
                    result = subprocess.run(['bash', str(iso / 'build.sh'), '--repo', directory, *args], text=True,
                                            capture_output=True, timeout=60)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(phrase, result.stderr)
                    self.assertNotIn('run as root', result.stderr)
            if os.geteuid() != 0:
                # A test image without a key passes the gate and stops at the next one.
                result = subprocess.run(['bash', str(iso / 'build.sh'), '--repo', directory, '--test', '--test-key',
                                         str(test_key)], text=True, capture_output=True, timeout=60)
                self.assertIn('run as root inside the disposable build VM', result.stderr)


    @unittest.skipUnless(shutil.which('unshare'), 'needs unshare for a root-looking user namespace')
    def test_a_key_file_inside_work_is_refused_even_through_a_link_from_outside(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            iso = base / 'src/iso'
            shutil.copytree(HERE, iso, ignore=shutil.ignore_patterns('__pycache__', '*.png', '*.pf2', 'profile'))
            write(iso / 'preflight.sh', '#!/bin/bash\nexit 0\n', 0o755)
            stubs = base / 'bin'
            for name, text in (('systemd-detect-virt', 'echo kvm'), ('pacman', 'echo "archiso 91-1"')):
                write(stubs / name, f'#!/bin/sh\n{text}\n', 0o755)
            for name in ('mkarchiso', 'pacman-key', 'repo-add', 'xorriso', 'unsquashfs', 'mcopy', 'mdir'):
                write(stubs / name, '#!/bin/sh\nexit 0\n', 0o755)
            work, out, repo = base / 'work', base / 'out', base / 'repo'
            for path in (work, out, repo, base / 'run'):
                path.mkdir()
            # The key file lies in work; PREFIX.key outside work only links to it.
            SecureBootMedium.make_key(self, work / 'inner', 'codeSigning')
            prefix = base / 'run/release'
            os.symlink(work / 'inner.key', f'{prefix}.key')
            os.symlink(work / 'inner.crt', f'{prefix}.crt')
            result = subprocess.run(
                ['unshare', '-r', 'bash', str(iso / 'build.sh'), '--repo', str(repo), '--work', str(work), '--out',
                 str(out), '--test', '--test-key', str(base / 'test.pub'), '--secure-boot-key', str(prefix)],
                text=True, capture_output=True, timeout=60,
                env=dict(os.environ, PATH=f'{stubs}{os.pathsep}{os.environ["PATH"]}'),
                input='') if (base / 'test.pub').write_text('ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl test\n') else None
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('the Secure Boot key must be outside the source checkout, work and output', result.stderr)


class MasteredMedium(unittest.TestCase):
    def record(self, *paths):
        return {'image': 'eltorito_img2_uefi.img', 'contents': list(paths)}

    def test_a_signed_medium_carries_shim_grub_mokmanager_and_the_certificate(self):
        full = ('EFI/BOOT/BOOTX64.EFI', 'EFI/BOOT/grubx64.efi', 'EFI/BOOT/mmx64.efi', 'emaki.cer')
        image_check.check_secure_boot(self.record(*full), True)
        with self.assertRaisesRegex(ValueError, 'lacks'):
            image_check.check_secure_boot(self.record(*full[:3]), True)

    def test_an_unsigned_medium_carries_none_of_them(self):
        image_check.check_secure_boot(self.record('EFI/BOOT/BOOTX64.EFI', 'shellx64.efi'), False)
        with self.assertRaisesRegex(ValueError, 'without /emaki/SECUREBOOT'):
            image_check.check_secure_boot(self.record('EFI/BOOT/BOOTX64.EFI', 'emaki.cer'), False)


if __name__ == '__main__':
    unittest.main()
