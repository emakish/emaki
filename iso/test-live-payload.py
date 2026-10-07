#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Live unpacking omits development data and retains runtime and license data."""
import fnmatch
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LivePayload(unittest.TestCase):
    def test_exclusions_apply_only_to_live_unpacking(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'profile'
            shutil.copytree(HERE / 'profile', profile, symlinks=True)
            load('prepare-profile').prepare(profile, '/offline', '0.2.0')
            config = (profile / 'pacman.conf').read_text()
            patterns = [word for line in config.splitlines() if line.startswith('NoExtract =')
                        for word in line.partition('=')[2].split()]
            for path in ('usr/include/stdio.h', 'usr/share/gir-1.0/Gio-2.0.gir',
                         'usr/share/gtk-doc/html/lib/index.html'):
                self.assertTrue(any(fnmatch.fnmatchcase(path, p) for p in patterns), path)
            for path in ('usr/lib/girepository-1.0/Gio-2.0.typelib',
                         'usr/share/doc/openvpn/COPYING', 'usr/share/licenses/xz/COPYING',
                         'usr/lib/modules/6.0/vmlinuz', 'usr/bin/emaki-installerd'):
                self.assertFalse(any(fnmatch.fnmatchcase(path, p) for p in patterns), path)
            self.assertNotIn('NoExtract', (profile / 'airootfs/etc/emaki-installer/pacman-offline.conf').read_text())

    def test_mastered_root_rejects_development_data(self):
        check = load('verify-image').check_root_listing
        check('squashfs-root/usr/lib/girepository-1.0/Gio-2.0.typelib\n'
              'squashfs-root/usr/share/doc/openvpn/COPYING\n')
        for path in ('usr/include/stdio.h', 'usr/share/gir-1.0/Gio-2.0.gir',
                     'usr/share/gtk-doc/html/lib/index.html'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                check('squashfs-root/' + path + '\n')

    def test_mastered_root_has_no_pacman_hooks_for_the_target(self):
        check = load('verify-image').check_root_listing
        check('squashfs-root/etc/pacman.d/hooks\nsquashfs-root/usr/share/libalpm/hooks/x.hook\n')
        with self.assertRaisesRegex(ValueError, '90-mkinitcpio-install.hook'):
            check('squashfs-root/etc/pacman.d/hooks\n'
                  'squashfs-root/etc/pacman.d/hooks/90-mkinitcpio-install.hook\n')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            check('-rw-r--r-- root/root 0 2026-10-07 09:00 squashfs-root/etc/pacman.d/hooks -> /opt/hooks\n')
        # The frozen 0.2.0 image predates the rule.
        check('squashfs-root/etc/pacman.d/hooks/90-mkinitcpio-install.hook\n', check_hygiene=False)

    def test_live_root_is_packed_after_its_hooks_are_released(self):
        builder = (HERE / 'build.sh').read_text()
        deferred = builder.index('touch "$work/mk/iso._build_iso_image" "$work/mk/base._prepare_airootfs_image"')
        first, second = [i for i in range(len(builder))
                         if builder.startswith('mkarchiso -v -w "$work/mk"', i)]
        release = builder.index('rm -- "$hook" "$ledger"')
        resumed = builder.index('"$work/mk/base._prepare_airootfs_image"\n', release)
        self.assertLess(deferred, first)
        self.assertLess(first, release)
        self.assertLess(release, resumed)
        self.assertLess(resumed, second)


if __name__ == '__main__':
    unittest.main()
