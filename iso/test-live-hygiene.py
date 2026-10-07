#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Live-only package policy, including rebuilt releng profiles."""
import importlib.util
from pathlib import Path
import runpy
import shutil
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LiveHygieneTests(unittest.TestCase):
    def assert_hygiene(self, profile):
        packages = (profile / 'packages.x86_64').read_text().split()
        self.assertNotIn('cloud-init', packages)
        self.assertNotIn('grim', packages)
        root = profile / 'airootfs/etc/systemd/system'
        self.assertFalse((root / 'cloud-init.target.wants').exists())
        self.assertFalse((root / 'sockets.target.wants/pcscd.socket').is_symlink())
        self.assertFalse((root / 'multi-user.target.wants/hv_fcopy_daemon.service').is_symlink())
        self.assertIn('disable pcscd.socket', (profile / 'airootfs/etc/systemd/system-preset/00-emaki-live.preset').read_text().splitlines())
        self.assertIn('archinstall', packages)
        self.assertIn('emaki-installer', packages)

    def test_checked_in_release_profile(self):
        self.assert_hygiene(HERE / 'profile')
        self.assertNotIn('grim', (HERE / 'packages-extra.txt').read_text().split())
        constants = runpy.run_path(str(HERE.parent / 'installer/emaki_installer/constants.py'))
        seeds = set((HERE / 'target-packages.txt').read_text().split())
        self.assertTrue(set(constants['PACKAGES'] + constants['TEST_PACKAGES'] + ['mkinitcpio', 'emaki-apps']) <= seeds)

    def test_import_does_not_restore_releng_services_or_packages(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'releng'
            shutil.copytree(HERE / 'profile', source, symlinks=True)
            (source / 'packages.x86_64').write_text('archinstall\ncloud-init\ngrim\nlinux\n')
            cloud = source / 'airootfs/etc/systemd/system/cloud-init.target.wants'
            cloud.mkdir(exist_ok=True)
            link = cloud / 'cloud-config.service'
            link.unlink(missing_ok=True)
            link.symlink_to('/usr/lib/systemd/system/cloud-config.service')
            socket = source / 'airootfs/etc/systemd/system/sockets.target.wants/pcscd.socket'
            socket.parent.mkdir(exist_ok=True)
            socket.unlink(missing_ok=True)
            socket.symlink_to('/usr/lib/systemd/system/pcscd.socket')
            obsolete = source / 'airootfs/etc/systemd/system/multi-user.target.wants/hv_fcopy_daemon.service'
            obsolete.symlink_to('/usr/lib/systemd/system/hv_fcopy_daemon.service')
            output = Path(temporary) / 'output'
            load('import-releng').import_profile(source, output)
            self.assert_hygiene(output)

    def test_unit_links_resolve_inside_image_and_allow_masks(self):
        entries = {
            '/usr/lib/systemd/system/current.service': None,
            '/lib': 'usr/lib',
            '/etc/systemd/system/current.service': '/lib/systemd/system/current.service',
            '/etc/systemd/system/multi-user.target.wants/current.service': '../current.service',
            '/etc/systemd/system/masked.service': '/dev/null',
        }
        load('verify-image').check_unit_links(entries)
        del entries['/usr/lib/systemd/system/current.service']
        with self.assertRaisesRegex(ValueError, 'dangling unit link'):
            load('verify-image').check_unit_links(entries)

    def test_unit_link_cycles_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'dangling unit link'):
            load('verify-image').check_unit_links({
                '/etc/systemd/system/first.service': 'second.service',
                '/etc/systemd/system/second.service': 'first.service',
            })

    @unittest.skipUnless(shutil.which('mksquashfs') and shutil.which('unsquashfs'),
                         'squashfs tools are required')
    def test_squashfs_listing_rejects_dangling_unit_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'root'
            units = root / 'usr/lib/systemd/system'
            units.mkdir(parents=True)
            (units / 'current.service').write_text('[Service]\nExecStart=/bin/true\n')
            wants = root / 'etc/systemd/system/multi-user.target.wants'
            wants.mkdir(parents=True)
            (wants / 'current.service').symlink_to('/usr/lib/systemd/system/current.service')
            for dangling in (False, True):
                if dangling:
                    (wants / 'hv_fcopy_daemon.service').symlink_to('/usr/lib/systemd/system/hv_fcopy_daemon.service')
                image = Path(temporary) / f'{dangling}.sfs'
                subprocess.run(['mksquashfs', str(root), str(image), '-noappend', '-quiet'],
                               check=True, stdout=subprocess.DEVNULL)
                listing = subprocess.run(['unsquashfs', '-ll', str(image)],
                                         check=True, text=True, capture_output=True).stdout
                if dangling:
                    with self.assertRaisesRegex(ValueError, 'dangling unit link.*hv_fcopy_daemon'):
                        load('verify-image').check_root_listing(listing)
                else:
                    load('verify-image').check_root_listing(listing)

    def test_grim_is_added_only_with_test_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            for mode in ('release', 'test'):
                profile = Path(temporary) / mode
                shutil.copytree(HERE / 'profile', profile, symlinks=True)
                key = Path(temporary) / 'test.pub'
                key.write_text('ssh-ed25519 TEST fixture\n')
                load('prepare-profile').prepare(profile, '/offline', '0.2.0', key if mode == 'test' else None)
                packages = (profile / 'packages.x86_64').read_text().split()
                self.assertEqual('grim' in packages, mode == 'test')
                self.assertEqual(packages, sorted(set(packages)))


if __name__ == '__main__':
    unittest.main()
