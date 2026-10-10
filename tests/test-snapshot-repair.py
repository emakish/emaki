#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise boot-time snapshot repair and its publication boundary without mounts."""
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import runpy
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = runpy.run_path(str(ROOT / 'tests/test-boot-refresh.py'))
refresh = SUPPORT['refresh']


class SnapshotRepairChecks(unittest.TestCase):
    fixture = SUPPORT['RefreshOrchestrationChecks'].fixture

    @contextmanager
    def prepared(self, missing=False, empty=False):
        with self.fixture(False, plain_fsroot='/@') as f:
            f.put(f.mapped('/etc/grub.d/41_snapshots-btrfs'), '# fixture generator\n')
            directory = f.mapped('/.snapshots')
            directory.mkdir()
            if not empty:
                (directory / '1/snapshot').mkdir(parents=True)
                f.put(directory / '1/info.xml', '<snapshot/>\n')
                os.utime(directory / '1/info.xml', ns=(1, 1))
                os.utime(directory / '1', ns=(1, 1))
            os.utime(directory, ns=(1, 1))
            menu = f.mapped('/boot/grub/grub-btrfs.cfg')
            f.generated = 'linux /@snapshots/1/snapshot/boot/vmlinuz-linux\n'
            f.originals['/boot/grub/grub-btrfs.cfg'] = f.generated.encode()
            menu.write_text(f.generated)
            if missing:
                menu.unlink()
            else:
                os.utime(menu, ns=(2, 2))
            run = refresh.run.side_effect

            def generate(arguments):
                result = run(arguments)
                if str(arguments[0]) == 'unshare':
                    (Path(arguments[-1]) / 'grub/grub-btrfs.cfg').write_text(f.generated)
                return result

            with patch.object(refresh, 'pause_snapshots') as paused, \
                    patch.object(refresh, 'run', side_effect=generate):
                f.paused = paused
                yield f

    def assert_repaired(self, f):
        self.assertTrue(f.mapped('/boot/grub/grub-btrfs.cfg').is_file())
        self.assertEqual(f.mapped('/boot/grub/grub-btrfs.cfg').read_text(), f.generated)
        self.assertEqual(f.output.getvalue(), 'The snapshot boot menu was repaired.\n')
        f.paused.assert_called_once()
        self.assertEqual(sum(command[0] == 'unshare' for command in f.commands), 1)
        self.assertIn(['sync', '-f', str(f.mapped('/boot/grub/grub-btrfs.cfg'))], f.commands)
        for path in ('/efi/EFI/Emaki/grubx64.efi', '/efi/EFI/BOOT/BOOTX64.EFI'):
            self.assertEqual(f.mapped(path).read_bytes(), f.originals[path])
        self.assertFalse(f.mapped('/efi/EFI/Emaki/boot-intent.json').exists())

    def test_missing_fragment_is_regenerated_and_durable(self):
        with self.prepared(missing=True) as f:
            run = refresh.run.side_effect

            def ordered(arguments):
                if list(map(str, arguments)) == ['sync', '-f', str(f.mapped('/boot/grub/grub-btrfs.cfg'))]:
                    self.assertEqual(f.output.getvalue(), '')
                    self.assertEqual(f.mapped('/boot/grub/grub-btrfs.cfg').read_text(), f.generated)
                return run(arguments)

            with patch.object(refresh, 'run', side_effect=ordered):
                refresh.repair_snapshot_menu(f.identity)
            self.assert_repaired(f)
            before = list(f.commands)
            refresh.repair_snapshot_menu(f.identity)
            self.assertEqual(f.commands, before)
            self.assertEqual(f.output.getvalue().count('repaired'), 1)

    def test_repair_recreates_and_syncs_live_stage_before_generation(self):
        with self.prepared(missing=True) as f:
            stage = f.mapped('/boot/grub/.emaki-snapshots')
            self.assertFalse(stage.exists())
            sync = refresh.sync_directory
            synced = []
            run = refresh.run.side_effect

            def flushed(path):
                result = sync(path)
                if path == stage.parent and stage.is_dir():
                    synced.append(path)
                return result

            def generate(arguments):
                if str(arguments[0]) == 'unshare':
                    self.assertTrue(stage.is_dir())
                    self.assertIn(stage.parent, synced)
                return run(arguments)

            with patch.object(refresh, 'sync_directory', side_effect=flushed), \
                    patch.object(refresh, 'run', side_effect=generate):
                refresh.repair_snapshot_menu(f.identity)
            self.assert_repaired(f)

    def test_live_stage_symlink_is_refused(self):
        with self.prepared(missing=True) as f:
            stage = f.mapped('/boot/grub/.emaki-snapshots')
            stage.symlink_to(f.mapped('/boot/grub'), target_is_directory=True)
            with self.assertRaisesRegex(refresh.Refuse, 'real directory'):
                refresh.repair_snapshot_menu(f.identity)
            self.assertEqual(f.commands, [])
            f.paused.assert_not_called()

    def test_fragment_referencing_deleted_snapshot_needs_repair(self):
        with self.prepared() as f:
            shutil.rmtree(f.mapped('/.snapshots/1'))
            f.generated = '# No snapshots available.\n'
            self.assertTrue(refresh.snapshot_menu_needs_repair())
            refresh.repair_snapshot_menu(f.identity)
            self.assert_repaired(f)
            self.assertFalse(refresh.snapshot_menu_needs_repair())

    def test_omitted_existing_snapshot_does_no_work(self):
        for kernel in (False, True):
            with self.subTest(kernel=kernel), self.prepared() as f:
                boot = f.mapped('/.snapshots/2/snapshot/boot')
                boot.mkdir(parents=True)
                if kernel:
                    f.put(boot / 'vmlinuz-linux', 'snapshot kernel\n')
                self.assertFalse(refresh.snapshot_menu_needs_repair())
                refresh.repair_snapshot_menu(f.identity)
                self.assertEqual(f.commands, [])
                self.assertEqual(f.output.getvalue(), '')
                f.paused.assert_not_called()

    def test_healthy_fragment_does_no_work(self):
        with self.prepared() as f:
            before = f.mapped('/boot/grub/grub-btrfs.cfg').stat()
            refresh.repair_snapshot_menu(f.identity)
            self.assertEqual(f.commands, [])
            self.assertEqual(f.output.getvalue(), '')
            f.paused.assert_not_called()
            after = f.mapped('/boot/grub/grub-btrfs.cfg').stat()
            self.assertEqual((after.st_ino, after.st_mtime_ns), (before.st_ino, before.st_mtime_ns))
            self.assertFalse(f.mapped('/boot/emaki').exists())

    def test_only_info_xml_cleanup_changed_does_no_work(self):
        with self.prepared() as f:
            menu = f.mapped('/boot/grub/grub-btrfs.cfg')
            before = menu.stat()
            f.put(f.mapped('/.snapshots/1/info.xml'),
                  '<snapshot><cleanup>number</cleanup></snapshot>\n')
            refresh.repair_snapshot_menu(f.identity)
            self.assertEqual(f.commands, [])
            self.assertEqual(f.output.getvalue(), '')
            f.paused.assert_not_called()
            self.assertEqual(menu.read_bytes(), f.originals['/boot/grub/grub-btrfs.cfg'])
            self.assertEqual(menu.stat().st_mtime_ns, before.st_mtime_ns)

    def test_upstream_selection_settings_do_not_require_all_snapshots(self):
        for limit, expected in ((None, range(3, 53)), ('2', (52, 51)), ('0', ())):
            with self.subTest(limit=limit), self.prepared() as f:
                for number in range(2, 53):
                    f.mapped(f'/.snapshots/{number}/snapshot').mkdir(parents=True)
                if limit is not None:
                    f.put(f.mapped('/etc/default/grub-btrfs/config'),
                          'GRUB_BTRFS_LIMIT="1"\nGRUB_BTRFS_IGNORE_SNAPSHOT_TYPE=("single")\n'
                          'GRUB_BTRFS_IGNORE_SNAPSHOT_DESCRIPTION=("timeline")\n')
                    defaults = f.mapped('/etc/default/grub')
                    defaults.write_text(defaults.read_text() + f'GRUB_BTRFS_LIMIT="{limit}"\n')
                menu = f.mapped('/boot/grub/grub-btrfs.cfg')
                menu.write_text('# Snapshot menu\n' + ''.join(
                    f'linux /@snapshots/{number}/snapshot/boot/vmlinuz-linux\n' for number in expected))
                refresh.repair_snapshot_menu(f.identity)
                self.assertEqual(f.commands, [])
                self.assertEqual(f.output.getvalue(), '')
                f.mapped('/.snapshots/53/snapshot').mkdir(parents=True)
                self.assertFalse(refresh.snapshot_menu_needs_repair())
                # A deleted reference requires repair regardless of selection settings.
                menu.write_text(menu.read_text() + 'linux /@snapshots/99/snapshot/boot/vmlinuz-linux\n')
                self.assertTrue(refresh.snapshot_menu_needs_repair())

    def test_empty_fragment_needs_repair(self):
        for empty_inventory in (False, True):
            for content in ('', ' \n\t'):
                with self.subTest(empty_inventory=empty_inventory, content=content), \
                        self.prepared(empty=empty_inventory) as f:
                    f.mapped('/boot/grub/grub-btrfs.cfg').write_text(content)
                    if empty_inventory:
                        f.generated = '# No snapshots available.\n'
                    self.assertTrue(refresh.snapshot_menu_needs_repair())
                    refresh.repair_snapshot_menu(f.identity)
                    self.assert_repaired(f)
                    self.assertFalse(refresh.snapshot_menu_needs_repair())

    def test_ext4_and_absent_snapshot_setup_are_quiet(self):
        for missing in ('ext4', 'directory', 'generator'):
            with self.subTest(missing=missing), self.prepared(missing=True) as f:
                if missing == 'ext4':
                    f.identity['fstype'] = 'ext4'
                elif missing == 'directory':
                    shutil.rmtree(f.mapped('/.snapshots'))
                else:
                    f.mapped('/etc/grub.d/41_snapshots-btrfs').unlink()
                refresh.repair_snapshot_menu(f.identity)
                self.assertEqual(f.commands, [])
                self.assertEqual(f.output.getvalue(), '')
                f.paused.assert_not_called()

    def test_generator_failure_keeps_both_old_menus(self):
        with self.prepared() as f:
            shutil.rmtree(f.mapped('/.snapshots/1'))
            with patch.object(refresh, 'run', side_effect=refresh.Transient('generator failed')):
                with self.assertRaises(refresh.Transient):
                    refresh.repair_snapshot_menu(f.identity)
            for path in ('/boot/grub/grub.cfg', '/boot/grub/grub-btrfs.cfg'):
                self.assertEqual(f.mapped(path).read_bytes(), f.originals[path])
            self.assertEqual(f.output.getvalue(), '')
            self.assertFalse(f.mapped('/efi/EFI/Emaki/boot-intent.json').exists())

    def test_zero_snapshots_without_generator_fragment_is_idempotent(self):
        with self.prepared(missing=True, empty=True) as f:
            run = refresh.run.side_effect

            def no_fragment(arguments):
                result = run(arguments)
                if str(arguments[0]) == 'unshare':
                    (Path(arguments[-1]) / 'grub/grub-btrfs.cfg').unlink()
                return result

            with patch.object(refresh, 'run', side_effect=no_fragment):
                refresh.repair_snapshot_menu(f.identity)
                self.assertEqual(f.mapped('/boot/grub/grub-btrfs.cfg').read_text(), '# No snapshots available.\n')
                before = list(f.commands)
                refresh.repair_snapshot_menu(f.identity)
                self.assertEqual(f.commands, before)
                self.assertEqual(f.output.getvalue().count('repaired'), 1)

    def test_missing_generated_fragment_with_snapshots_refuses_old_copy(self):
        with self.prepared() as f:
            (f.mapped('/.snapshots') / '2/snapshot').mkdir(parents=True)
            shutil.rmtree(f.mapped('/.snapshots/1'))
            run = refresh.run.side_effect

            def no_fragment(arguments):
                if str(arguments[0]) != 'unshare':
                    return run(arguments)
                # Generate only the main menu; never touch the copied fragment.
                f.commands.append(list(map(str, arguments)))
                rows = []
                for kernel in refresh.KERNELS:
                    rows += [f"menuentry 'Emaki {kernel}' {{",
                             f'linux /@/boot/vmlinuz-{kernel} root=UUID={f.identity["uuid"]}',
                             f'initrd /@/boot/initramfs-{kernel}.img', '}']
                (Path(arguments[-1]) / 'grub/grub.cfg').write_text('\n'.join(rows) + '\n')
                return ''

            with patch.object(refresh, 'run', side_effect=no_fragment):
                with self.assertRaisesRegex(refresh.Refuse, 'did not produce a menu'):
                    refresh.repair_snapshot_menu(f.identity)
            self.assertEqual(f.mapped('/boot/grub/grub-btrfs.cfg').read_bytes(), f.originals['/boot/grub/grub-btrfs.cfg'])
            self.assertEqual(f.output.getvalue(), '')

    def test_recovery_roots_skip_through_real_discovery(self):
        for kind in ('overlay', 'snapshot'):
            with self.subTest(kind=kind), self.prepared(missing=True) as f:
                f.mapped('/run').mkdir()
                mount = {'target': '/', 'uuid': None, 'fstype': 'overlay' if kind == 'overlay' else 'btrfs',
                         'source': 'overlay', 'fsroot': '/' if kind == 'overlay' else '/@snapshots/1/snapshot',
                         'options': 'rw', 'partuuid': None}
                with patch.object(refresh, 'run', return_value=json.dumps({'filesystems': [mount]})), \
                        patch.object(refresh, 'open', side_effect=lambda path, *args: open(f.mapped(path), *args), create=True), \
                        patch.object(refresh.os, 'geteuid', return_value=0), \
                        patch.object(refresh.subprocess, 'run', return_value=SimpleNamespace(returncode=1)), \
                        patch.object(refresh, 'repair_snapshot_menu') as repair:
                    self.assertEqual(refresh.main(['--repair-snapshots']), 0)
                    repair.assert_not_called()
                self.assertEqual(f.output.getvalue(), '')
                f.paused.assert_not_called()

    def test_three_healthy_boots_with_omitted_snapshot_write_nothing(self):
        with self.prepared() as f:
            f.mapped('/.snapshots/2/snapshot/boot').mkdir(parents=True)
            lock = f.mapped('/var/lib/pacman/db.lck')
            f.put(lock, 'another package operation\n')
            before = lock.stat()

            def contents():
                return {str(path.relative_to(f.root)): (
                    path.lstat().st_ino, path.lstat().st_mtime_ns, path.lstat().st_ctime_ns,
                    path.read_bytes() if path.is_file() else None)
                    for path in f.root.rglob('*')}

            original = contents()
            with patch.object(refresh, 'discover', side_effect=refresh.Refuse('identity mismatch')) as discover, \
                    patch.object(refresh, 'pacman_lock') as pacman, \
                    patch.object(refresh, 'open_run_lock') as run_lock, \
                    patch.object(refresh, 'BoundedLog') as log, \
                    patch.object(refresh.os, 'geteuid', return_value=1000) as uid, \
                    patch.object(refresh.subprocess, 'run') as process:
                for boot in range(3):
                    with self.subTest(boot=boot):
                        self.assertEqual(refresh.main(['--repair-snapshots']), 0)
                        self.assertEqual(contents(), original)
                for operation in (discover, pacman, run_lock, log, uid, process):
                    operation.assert_not_called()
            self.assertEqual(lock.read_text(), 'another package operation\n')
            self.assertEqual(lock.stat().st_mtime_ns, before.st_mtime_ns)
            self.assertEqual(f.commands, [])
            self.assertEqual(f.output.getvalue(), '')
            f.paused.assert_not_called()

    def test_busy_run_lock_defers_repair_successfully(self):
        with self.prepared(missing=True) as f:
            with patch.object(refresh, 'open_run_lock'), \
                    patch.object(refresh, 'wait_run_lock', side_effect=BlockingIOError) as wait, \
                    patch.object(refresh.os, 'geteuid', return_value=0), \
                    patch.object(refresh.subprocess, 'run', return_value=SimpleNamespace(returncode=1)), \
                    patch.object(refresh, 'discover') as discover, \
                    patch.object(refresh, 'pacman_lock') as pacman:
                self.assertEqual(refresh.main(['--repair-snapshots']), 0)
                self.assertEqual(wait.call_args.kwargs, {})
                discover.assert_not_called()
                pacman.assert_not_called()
            self.assertEqual(f.mapped(refresh.LOG_PATH).read_text(),
                             'Another boot loader operation is running; retry at the next boot.\n')
            self.assertEqual(f.output.getvalue(), '')
            self.assertFalse(f.mapped('/var/lib/pacman/db.lck').exists())

    def test_boot_dispatch_holds_package_lock_during_repair(self):
        with self.prepared(missing=True) as f:
            f.mapped('/run').mkdir()
            f.mapped('/var/lib/pacman').mkdir(parents=True)
            run = refresh.run.side_effect

            def locked_generation(arguments):
                if str(arguments[0]) == 'unshare':
                    self.assertEqual(f.mapped('/var/lib/pacman/db.lck').read_text(),
                                     'emaki-boot-refresh:' + SUPPORT['BOOT_ID'])
                return run(arguments)

            with patch.object(refresh, 'open', side_effect=lambda path, *args: open(f.mapped(path), *args), create=True), \
                    patch.object(refresh.os, 'geteuid', return_value=0), \
                    patch.object(refresh.subprocess, 'run', return_value=SimpleNamespace(returncode=1)), \
                    patch.object(refresh, 'discover', return_value=f.identity), \
                    patch.object(refresh, 'run', side_effect=locked_generation):
                self.assertEqual(refresh.main(['--repair-snapshots']), 0)
            self.assert_repaired(f)
            self.assertFalse(f.mapped('/var/lib/pacman/db.lck').exists())
            self.assertFalse(f.mapped('/var/lib/emaki/boot-refresh-pending').exists())

    def test_boot_unit_order_and_delivery(self):
        unit = (ROOT / 'systemd/emaki-snapshot-menu.service').read_text()
        self.assertIn('After=multi-user.target emaki-boot-complete.service emaki-boot-refresh.service', unit)
        self.assertIn('RequiresMountsFor=/boot /efi /.snapshots', unit)
        self.assertIn('ExecStart=/usr/bin/emaki-boot-refresh --repair-snapshots', unit)
        self.assertIn('WantedBy=multi-user.target', unit)
        makefile = (ROOT / 'Makefile').read_text()
        self.assertIn('install -Dm644 systemd/emaki-snapshot-menu.service', makefile)
        self.assertIn('ln -sfn ../emaki-snapshot-menu.service', makefile)


class RepairMutants(unittest.TestCase):
    def assert_killed(self, method, replacement):
        suite = unittest.TestSuite([SnapshotRepairChecks(method)])
        with replacement:
            result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        self.assertFalse(result.wasSuccessful(), 'repair mutant survived')
        self.assertTrue(result.failures, 'mutant must fail an assertion, not crash the fixture')

    def test_skipped_repair_is_killed(self):
        self.assert_killed('test_missing_fragment_is_regenerated_and_durable',
                           patch.object(refresh, 'repair_snapshot_menu', return_value=None))

    def test_copied_fragment_guard_is_killed(self):
        unlink = Path.unlink

        def keep_copy(path, *args, **kwargs):
            if path.name == 'grub-btrfs.cfg' and 'emaki' in path.parts:
                return
            return unlink(path, *args, **kwargs)

        self.assert_killed('test_missing_generated_fragment_with_snapshots_refuses_old_copy',
                           patch.object(Path, 'unlink', keep_copy))

    def test_unnecessary_repair_is_killed(self):
        self.assert_killed('test_healthy_fragment_does_no_work',
                           patch.object(refresh, 'snapshot_menu_needs_repair', return_value=True))


if __name__ == '__main__':
    unittest.main()
