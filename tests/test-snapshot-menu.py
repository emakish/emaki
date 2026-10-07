#!/usr/bin/python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise snapshot menu rendering with the real v4.14 entry emitters."""

from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
loader = SourceFileLoader('snapshot_menu', str(ROOT / 'grub/emaki-snapshot-menu'))
repair = module_from_spec(spec_from_loader(loader.name, loader))
loader.exec_module(repair)
FIXTURE = ROOT / 'grub/tests/grub-btrfs-4.14.fixture'
CHECKER = os.environ.get('EMAKI_TEST_GRUB_SCRIPT_CHECK') or shutil.which('grub-script-check')


class SnapshotMenu(unittest.TestCase):
    def setUp(self):
        if not CHECKER:
            self.skipTest('grub-script-check is unavailable')
        self.enterContext(patch.object(repair, 'SCRIPT_CHECK', CHECKER, create=True))
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.generator = self.root / 'generator'
        self.menu = self.root / 'grub-btrfs.cfg'
        self.defaults = self.root / 'grub'
        self.defaults.write_text('GRUB_TIMEOUT=5\n')
        # These are the actual upstream functions, not an imitation of their
        # output. Both the column row and each snapshot's title row are emitted.
        self.generator.write_text('''#!/bin/bash
set -e
source "$1"
grub_btrfs_directory=$2
boot_dir=$2
name_kernel=(vmlinuz-linux vmlinuz-linux-lts)
name_initramfs=(initramfs-linux.img initramfs-linux-lts.img)
name_microcode=(x)
insmods=('insmod btrfs')
CLASS='--class snapshots --class gnu-linux --class gnu --class os'
boot_uuid=1111-2222
LINUX_ROOT_DEVICE=UUID=1111-2222
rootflags=rootflags=
count_warning_menuentries=0
snap_date='2026-10-06 10:00:00'
snap_type=pre
snap_description='pacman -Syu'
: > "$grub_btrfs_directory/grub-btrfs.new"
for snap_snapshot in 42 43; do
    snap_date_trim=$snap_date
    snap_dir_name_trim=@snapshots/$snap_snapshot/snapshot
    boot_dir_root_grub=/$snap_dir_name_trim/boot
    title_format
    make_menu_entries
done
header_menu
''')
        self.generator.chmod(0o751)
        for kernel in ('vmlinuz-linux', 'vmlinuz-linux-lts'):
            (self.root / kernel).touch()
        self.generate()
        self.original = self.menu.read_text()

    def generate(self):
        subprocess.run(['bash', str(self.generator), str(FIXTURE), str(self.root)], check=True)
        self.menu.write_bytes((self.root / 'grub-btrfs.new').read_bytes())

    def assert_bootable(self, text):
        self.assertNotIn('{ echo }', text)
        self.assertTrue(text.startswith("submenu '|2026"))
        for snapshot in ('42', '43'):
            self.assertIn(f'subvol="@snapshots/{snapshot}/snapshot"', text)
        entries = [line.strip() for line in text.splitlines()
                   if line.lstrip().startswith(('submenu ', 'menuentry '))]
        self.assertEqual(len(entries), 6)
        for index in (0, 3):
            self.assertTrue(entries[index].startswith('submenu '))
            self.assertTrue(entries[index + 1].startswith("menuentry '  vmlinuz-linux &"))
        before = [line for line in self.original.splitlines() if '{ echo }' not in line]
        self.assertEqual(text.splitlines(), before)

    def test_existing_menu_starts_at_bootable_rows_without_changing_generator(self):
        self.assertEqual(self.original.count('{ echo }'), 3)
        original_generator = self.generator.read_bytes()
        repair.repair(self.generator, self.menu, self.defaults)
        self.assert_bootable(self.menu.read_text())
        self.assertEqual(self.generator.read_bytes(), original_generator)
        self.assertEqual(self.generator.stat().st_mode & 0o777, 0o751)
        self.assertIn(repair.SETTING, self.defaults.read_text())
        before = self.defaults.read_bytes()
        repair.repair(self.generator, self.menu, self.defaults)
        self.assertEqual(self.defaults.read_bytes(), before)

    def test_every_new_menu_is_rendered_before_real_syntax_check(self):
        repair.repair(self.generator, self.menu, self.defaults)
        for _ in range(2):
            self.generate()
            with patch.object(sys, 'argv', ['emaki-snapshot-menu', '--check', str(self.menu)]):
                self.assertEqual(repair.main(), 0)
            self.assert_bootable(self.menu.read_text())
            self.assertEqual(subprocess.run([CHECKER, str(self.menu)]).returncode, 0)

    def test_kernel_without_matching_initramfs_keeps_title(self):
        self.generator.write_text(self.generator.read_text().replace(
            'name_initramfs=(initramfs-linux.img initramfs-linux-lts.img)',
            'name_initramfs=(initramfs-missing.img)'))
        self.generate()
        repair.repair_menu(self.menu)
        self.assertEqual(self.menu.read_text().count('{ echo }'), 2)
        self.assertEqual(subprocess.run([CHECKER, str(self.menu)]).returncode, 0)

    def test_syntax_check_failure_keeps_published_menu(self):
        self.menu.write_text(self.original + "submenu 'broken' {\n")
        before = self.menu.read_bytes()
        with self.assertRaises(ValueError):
            repair.repair_menu(self.menu)
        self.assertEqual(self.menu.read_bytes(), before)
        self.assertEqual(list(self.root.glob('grub-btrfs.cfg.emaki.*')), [])
        with patch.object(sys, 'argv', ['emaki-snapshot-menu', '--check', str(self.menu)]):
            with self.assertRaises(SystemExit) as error:
                repair.main()
        self.assertEqual(error.exception.code, 1)
        self.assertEqual(self.menu.read_bytes(), before)

    def test_custom_headings_and_boot_commands_are_unchanged(self):
        custom = "menuentry '| My recovery |' { echo }\nsubmenu '|mine|' {\n    submenu '|different|' { echo }\n}\n"
        self.assertEqual(repair.render(custom), custom)

    def test_links_are_not_followed(self):
        for path in (self.generator, self.defaults, self.menu):
            with self.subTest(path=path):
                target = self.root / 'custom'
                path.rename(target)
                path.symlink_to(target)
                before = target.read_bytes()
                with self.assertRaisesRegex(ValueError, 'regular file'):
                    repair.repair(self.generator, self.menu, self.defaults)
                self.assertEqual(target.read_bytes(), before)
                path.unlink()
                target.rename(path)

    def test_absent_package_is_noop(self):
        self.generator.unlink()
        before = self.menu.read_bytes(), self.defaults.read_bytes()
        repair.repair(self.generator, self.menu, self.defaults)
        self.assertEqual((self.menu.read_bytes(), self.defaults.read_bytes()), before)


class PacmanMenu(unittest.TestCase):
    setUp = SnapshotMenu.setUp
    generate = SnapshotMenu.generate
    assert_bootable = SnapshotMenu.assert_bootable
    def test_package_hook_renders_real_menu_without_changing_generator(self):
        transaction_loader = SourceFileLoader('menu_transactions', str(ROOT / 'tests/test-rollback-holds.py'))
        transactions = module_from_spec(spec_from_loader(transaction_loader.name, transaction_loader))
        transaction_loader.exec_module(transactions)
        transactions.Transactions.setUpClass()
        scratch = transactions.Transactions()
        self.addCleanup(scratch.doCleanups)
        scratch.setUp()
        for directory in ('usr/share/libalpm/hooks', 'etc/pacman.d/hooks'):
            for hook in (scratch.root / directory).glob('*.hook'):
                hook.unlink()
        sources = [CHECKER] + re.findall(r'(/[^\s()]+)', subprocess.run(
            ['ldd', CHECKER], text=True, capture_output=True, check=True).stdout)
        for source in sources:
            relative = 'usr/bin/grub-script-check' if source == CHECKER else source.lstrip('/')
            destination = scratch.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        helper = scratch.work / 'emaki-snapshot-menu'
        helper.write_bytes((ROOT / 'grub/emaki-snapshot-menu').read_bytes())
        helper.chmod(0o755)  # Match the package's install rule.
        payload = {
            'usr/share/libalpm/scripts/emaki-snapshot-menu': helper,
            'usr/share/libalpm/hooks/91-emaki-snapshot-menu.hook': ROOT / 'grub/91-emaki-snapshot-menu.hook',
            'usr/bin/emaki-snapshot-menu-check': ROOT / 'grub/emaki-snapshot-menu-check',
        }
        for relative in payload:
            (scratch.root / relative).unlink(missing_ok=True)
        generator = scratch.root / 'etc/grub.d/41_snapshots-btrfs'
        generator.parent.mkdir(parents=True, exist_ok=True)
        original_generator = '#!/bin/bash\n' + FIXTURE.read_text() + '\nheader_menu\n'
        generator.write_text(original_generator)
        generator.chmod(0o755)
        defaults = scratch.root / 'etc/default/grub'
        defaults.parent.mkdir(parents=True, exist_ok=True)
        defaults.write_text('GRUB_TIMEOUT=5\n')
        menu = scratch.root / 'boot/grub/grub-btrfs.cfg'
        menu.parent.mkdir(parents=True, exist_ok=True)
        menu.write_text(self.original)
        scratch.repository('menu', {'emaki-config': '3-1'}, {'emaki-config': payload})
        scratch.configure('menu')
        result = scratch.pacman('-Syy', 'emaki-config')
        scratch.success(result)
        self.assertEqual(result.stdout.count('Selecting bootable entries in the snapshot menu'), 1)
        self.assert_bootable(menu.read_text())
        self.assertEqual(generator.read_text(), original_generator)
        self.assertIn(repair.SETTING, defaults.read_text().splitlines())
        self.assertIn('emaki-config', scratch.pacman('-Qo', str(scratch.root / 'usr/bin/emaki-snapshot-menu-check')).stdout)
        # The same package hook must retain the title for snapshots without an
        # initramfs, and must never publish a failed syntax-check result.
        with self.subTest(case='no matching initramfs'):
            self.generator.write_text(self.generator.read_text().replace(
                'name_initramfs=(initramfs-linux.img initramfs-linux-lts.img)',
                'name_initramfs=(initramfs-missing.img)'))
            self.generate()
            menu.write_text(self.menu.read_text())
            result = scratch.pacman('-S', 'emaki-config')
            scratch.success(result)
            self.assertEqual(menu.read_text().count('{ echo }'), 2)
            self.assertEqual(subprocess.run([CHECKER, str(menu)]).returncode, 0)
        with self.subTest(case='invalid replacement'):
            menu.write_text(self.original + "submenu 'broken' {\n")
            before = menu.read_bytes()
            result = scratch.pacman('-S', 'emaki-config')
            self.assertIn('command failed', result.stdout + result.stderr)
            self.assertEqual(menu.read_bytes(), before)
            self.assertEqual(list(menu.parent.glob('grub-btrfs.cfg.emaki.*')), [])



if __name__ == '__main__':
    unittest.main()
