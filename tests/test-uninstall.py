#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""`make uninstall` into a scratch root keeps an administrator's edits.

The files the recipe compares are staged from their shipped sources, as `make install`
copies them; two of them are then edited. No build, no root: everything happens inside a
temporary directory given as DESTDIR.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
# Installed path (under DESTDIR) -> shipped source.
STAGED = {
    'etc/niri/config.kdl': 'niri/system.kdl',
    'etc/xdg/kwalletrc': 'packaging/emaki-config/kwalletrc',
    'etc/xdg/kdeglobals': 'packaging/emaki-config/kdeglobals',
    'etc/xdg/dolphinrc': 'packaging/emaki-config/dolphinrc',
    'etc/xdg/mimeapps.list': 'packaging/emaki-config/mimeapps.list',
    'etc/xdg/menus/emaki-applications.menu': 'packaging/emaki-config/emaki-applications.menu',
    'etc/xdg/xdg-desktop-portal/niri-portals.conf': 'packaging/emaki-config/niri-portals.conf',
    'etc/xdg/qt6ct/qt6ct.conf': 'etc-skel/.config/qt6ct/qt6ct.conf',
    'etc/xdg/hypr/hyprlock.conf': 'hypr/hyprlock.conf',
    'etc/xdg/fastfetch/config.jsonc': 'fetch/config.jsonc',
    'etc/skel/.config/kitty/kitty.conf': 'etc-skel/.config/kitty/kitty.conf',
    'etc/skel/.config/qt6ct/qt6ct.conf': 'etc-skel/.config/qt6ct/qt6ct.conf',
    'etc/skel/.config/wpaperd/config.toml': 'etc-skel/.config/wpaperd/config.toml',
    'usr/share/doc/emaki/ZONES.md': 'docs/ZONES.md',
    'usr/lib/initcpio/hooks/emaki-snapshot-fstab': 'initcpio/hooks/emaki-snapshot-fstab',
    'usr/lib/initcpio/install/emaki-snapshot-fstab': 'initcpio/install/emaki-snapshot-fstab',
}
EDITED = ('etc/niri/config.kdl', 'etc/xdg/kdeglobals')
LINE = '# edited by the administrator\n'
# mkinitcpio reads /etc/initcpio before /usr/lib/initcpio: copies there (0.1.2's installer
# wrote them) are the administrator's and stay, like mkinitcpio's own directories.
KEPT = {
    'etc/initcpio/hooks/emaki-snapshot-fstab': 'initcpio/hooks/emaki-snapshot-fstab',
    'etc/initcpio/install/emaki-snapshot-fstab': 'initcpio/install/emaki-snapshot-fstab',
}


def uninstall(root):
    root = Path(root)
    # Never the real system: DESTDIR is always a fresh, absolute temporary directory.
    assert root.is_absolute() and root != Path('/') and root.is_relative_to(tempfile.gettempdir())
    environment = {key: value for key, value in os.environ.items()
                   if key not in ('MAKEFLAGS', 'MFLAGS', 'MAKELEVEL', 'DESTDIR')}
    # Silent: stdout then holds only what the recipe prints, not the commands themselves.
    return subprocess.run(['make', '-s', '--no-print-directory', 'uninstall', f'DESTDIR={root}'],
                          cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120)


def kept(result):
    return sorted(line for line in result.stdout.splitlines() if line.startswith('uninstall: kept'))


class Uninstall(unittest.TestCase):
    def test_edited_defaults_stay_and_shipped_ones_go(self):
        with tempfile.TemporaryDirectory(prefix='emaki-uninstall-') as temporary:
            for installed, source in STAGED.items():
                path = Path(temporary, installed)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / source).read_bytes())
            for installed in EDITED:
                with Path(temporary, installed).open('a') as stream:
                    stream.write(LINE)
            for installed, source in KEPT.items():
                path = Path(temporary, installed)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((ROOT / source).read_bytes())
            result = uninstall(temporary)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for installed, source in STAGED.items():
                path = Path(temporary, installed)
                if installed in EDITED:
                    self.assertTrue(path.is_file(), f'{installed} was removed although it was edited')
                    self.assertEqual(path.read_bytes(), (ROOT / source).read_bytes() + LINE.encode(), installed)
                else:
                    self.assertFalse(path.exists(), installed)
            self.assertEqual(kept(result), sorted(f'uninstall: kept {Path(temporary, installed)} '
                                                  '(it differs from the shipped file)' for installed in EDITED))
            # Directories emptied by the removal go too; the ones holding an edit stay.
            self.assertFalse(Path(temporary, 'usr/share/doc/emaki').exists())
            self.assertTrue(Path(temporary, 'etc/niri').is_dir())
            for installed, source in KEPT.items():
                self.assertEqual(Path(temporary, installed).read_bytes(), (ROOT / source).read_bytes(), installed)
            for directory in ('hooks', 'install'):
                self.assertTrue(Path(temporary, 'usr/lib/initcpio', directory).is_dir())

    def test_empty_root_is_a_no_op(self):
        with tempfile.TemporaryDirectory(prefix='emaki-uninstall-') as temporary:
            result = uninstall(temporary)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(kept(result), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
