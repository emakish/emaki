#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mutate disposable ISO inputs and prove the first build gate rejects them."""
import io
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.iso = self.root / 'iso'
        shutil.copytree(ROOT / 'iso', self.iso, symlinks=True,
                        ignore=shutil.ignore_patterns('__pycache__', '*.png', '*.pf2'))

    def run_gate(self, *args, script='preflight.sh'):
        return subprocess.run(['bash', str(self.iso / script), *map(str, args)],
                              text=True, capture_output=True, timeout=12)

    def replace(self, relative, before, after):
        path = self.iso / relative
        text = path.read_text()
        self.assertIn(before, text)
        path.write_text(text.replace(before, after))

    def assert_rejected(self, phrase, *args, script='preflight.sh'):
        result = self.run_gate(*args, script=script)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(phrase, result.stderr)

    def make_repo(self):
        repo = self.root / 'repo'
        repo.mkdir()
        with tarfile.open(repo / 'emaki.db', 'w:gz') as archive:
            for name in (self.iso / 'emaki-packages.txt').read_text().split():
                filename = f'{name}-1-1-any.pkg.tar.zst'
                (repo / filename).write_bytes(b'package shape only')
                (repo / (filename + '.sig')).write_bytes(b'signature shape only')
                data = f'%NAME%\n{name}\n\n%FILENAME%\n{filename}\n\n'.encode()
                item = tarfile.TarInfo(f'{name}-1-1/desc')
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
        (repo / 'emaki.db.sig').write_bytes(b'database signature shape only')
        return repo

    def test_current_profile_is_offline_and_fast(self):
        started = time.monotonic()
        result = self.run_gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(time.monotonic() - started, 10)

    def test_package_list_mutations(self):
        for relative in ('packages-extra.txt', 'target-packages.txt', 'emaki-packages.txt',
                         'profile/packages.x86_64', 'profile/packages.aarch64', 'profile/bootstrap_packages'):
            with self.subTest(relative=relative):
                path = self.iso / relative
                original = path.read_text()
                path.write_text(original + original.splitlines()[0] + '\n')
                self.assert_rejected('sorted and unique')
                path.write_text(original)

    def test_kernel_provider_must_be_live(self):
        self.replace('profile/packages.x86_64', '\nlinux\n', '\n')
        self.assert_rejected('needs live package linux')

    def test_lts_kernel_provider_must_be_live(self):
        self.replace('profile/packages.x86_64', '\nlinux-lts\n', '\n')
        self.assert_rejected('needs live package linux-lts')

    def test_initrd_generator_must_be_live(self):
        self.replace('profile/packages.x86_64', '\nmkinitcpio-archiso\n', '\n')
        self.assert_rejected('needs live packages linux, mkinitcpio and mkinitcpio-archiso')

    def test_unknown_asset_is_rejected(self):
        self.replace('profile/syslinux/archiso_sys-linux.cfg', 'vmlinuz-linux', 'vmlinuz-missing')
        self.assert_rejected('no known package produces boot asset')

    def test_missing_initrd_is_rejected(self):
        self.replace('profile/grub/grub.cfg', '    initrd /%INSTALL_DIR%/boot/%ARCH%/initramfs-linux.img\n', '')
        self.assert_rejected('has no matching initrd')

    def test_other_initrd_does_not_match_kernel(self):
        path = self.iso / 'profile/packages.x86_64'
        path.write_text('\n'.join(sorted(set(path.read_text().split()) | {'linux-lts'})) + '\n')
        self.replace('profile/grub/grub.cfg', 'initramfs-linux.img', 'initramfs-linux-lts.img')
        self.assert_rejected('has no matching initrd')

    def test_permissions_must_name_existing_overlay_paths(self):
        with (self.iso / 'profile/profiledef.sh').open('a') as stream:
            stream.write('\nfile_permissions["/missing-file"]="0:0:0644"\n')
        self.assert_rejected('file_permissions entry does not exist: /missing-file')

    def test_profile_version_mismatch(self):
        (self.iso / 'profile/VERSION').write_text('99.99.99\n')
        self.assert_rejected('profile/VERSION differs')

    def test_profile_definition_version_mismatch(self):
        with (self.iso / 'profile/profiledef.sh').open('a') as stream:
            stream.write('\niso_version="99.99.99"\n')
        self.assert_rejected('profile identity/version differs')

    def test_all_loader_versions_are_checked(self):
        version = (self.iso / 'VERSION').read_text().strip()
        for relative in ('profile/grub/grub.cfg', 'profile/grub/loopback.cfg',
                         'profile/syslinux/archiso_head.cfg'):
            with self.subTest(relative=relative):
                self.replace(relative, f'Emaki {version}', 'Emaki 99.99.99')
                self.assert_rejected('boot menu version differs')
                self.replace(relative, 'Emaki 99.99.99', f'Emaki {version}')
        loader = self.iso / 'profile/efiboot/loader/entries/emaki.conf'
        loader.parent.mkdir(parents=True)
        loader.write_text('title Emaki 99.99.99\nlinux /vmlinuz-linux\ninitrd /initramfs-linux.img\n')
        self.assert_rejected('boot menu version differs')

    def test_signed_repository_shape_requires_no_keyring(self):
        repo = self.make_repo()
        result = self.run_gate('--repo', repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        (repo / 'emaki.db.sig').unlink()
        self.assert_rejected('missing or empty signed database', '--repo', repo)
        (repo / 'emaki.db.sig').touch()
        self.assert_rejected('missing or empty signed database', '--repo', repo)

    def test_test_repository_database_signature_is_optional(self):
        repo = self.make_repo()
        for signature in (b'signature', b'', None):
            with self.subTest(signature=signature):
                if signature is None:
                    (repo / 'emaki.db.sig').unlink()
                else:
                    (repo / 'emaki.db.sig').write_bytes(signature)
                result = self.run_gate('--test', '--repo', repo)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('test repository shape', result.stdout)
        next(repo.glob('*.pkg.tar.zst.sig')).unlink()
        self.assert_rejected('missing package or detached signature', '--test', '--repo', repo)

    def test_build_passes_mode_to_preflight_before_privileged_work(self):
        # Stop at the first gate, before any root/VM checks or build operations.
        (self.iso / 'preflight.sh').write_text('#!/bin/bash\nprintf "%s\\n" "$@"\nexit 42\n')
        for args in ((), ('--test',), ('--test-key', '/missing-test-key')):
            with self.subTest(args=args):
                result = self.run_gate('--repo', '/test-repo', *args, script='build.sh')
                self.assertEqual(result.returncode, 42, result.stderr)
                expected = ['--repo', '/test-repo'] + (['--test'] if args else [])
                self.assertEqual(result.stdout.splitlines(), expected)

    def test_repository_and_package_signature_missing(self):
        self.assert_rejected('repository directory does not exist', '--repo', self.root / 'absent')
        repo = self.make_repo()
        next(repo.glob('*.pkg.tar.zst.sig')).unlink()
        self.assert_rejected('missing package or detached signature', '--repo', repo)

    def test_malformed_database(self):
        repo = self.make_repo()
        (repo / 'emaki.db').write_bytes(b'not a database')
        self.assert_rejected('ISO preflight', '--repo', repo)

    def test_preflight_is_first_in_both_entry_points(self):
        (self.iso / 'profile/VERSION').write_text('99.99.99\n')
        for script in ('build.sh', 'check.sh'):
            with self.subTest(script=script):
                self.assert_rejected('profile/VERSION differs', script=script)


if __name__ == '__main__':
    unittest.main()
