#!/usr/bin/python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Only migrated presets are refreshed, with retryable pending work."""
from pathlib import Path
import contextlib
import io
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'upkeep'))
from emaki_boot_defaults import legacy_preset
import emaki_initramfs as transaction

refresh = runpy.run_path(str(REPO / 'upkeep/emaki-initramfs-refresh'))['refresh']


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pending = self.root / 'var/lib/emaki/migrations/initramfs-pending'
        self.pending.parent.mkdir(parents=True)
        for kernel in ('linux', 'linux-lts'):
            self.put(f'etc/mkinitcpio.d/{kernel}.preset', f'. /usr/share/emaki/boot/{kernel}.preset\n')
            self.put(f'boot/vmlinuz-{kernel}', 'kernel')
        self.put('boot/grub/grub.cfg', 'menu')

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_no_pending_migration_does_not_rebuild(self):
        run = Mock()
        refresh(self.root, run)
        run.assert_not_called()

    def test_only_changed_preset_is_rebuilt_without_grub(self):
        self.pending.write_text('linux-lts\n')
        run = Mock()
        refresh(self.root, run)
        run.assert_called_once_with(['mkinitcpio', '-p', 'linux-lts'], check=True)
        self.assertFalse(self.pending.exists())

    def test_released_presets_rebuild_after_hooks_change(self):
        self.pending.write_text('linux\nlinux-lts\n')
        for kernel in ('linux', 'linux-lts'):
            self.put(f'etc/mkinitcpio.d/{kernel}.preset', legacy_preset(kernel))
        run = Mock()
        refresh(self.root, run)
        run.assert_called_once_with(['mkinitcpio', '-p', 'linux', '-p', 'linux-lts'], check=True)
        self.assertFalse(self.pending.exists())

    def test_failure_keeps_pending_work_for_retry(self):
        self.pending.write_text('linux\nlinux-lts\n')
        run = Mock(side_effect=subprocess.CalledProcessError(1, 'mkinitcpio'))
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), self.assertRaises(subprocess.CalledProcessError):
            refresh(self.root, run)
        self.assertIn('next kernel or initramfs transaction will retry', errors.getvalue())
        self.assertIn('Boot refresh is blocked', errors.getvalue())
        self.assertEqual(self.pending.read_text(), 'linux\nlinux-lts\n')
        run.assert_called_once_with(['mkinitcpio', '-p', 'linux', '-p', 'linux-lts'], check=True)

    def test_later_transaction_retries_and_clears_pending(self):
        self.pending.write_text('linux\n')
        run = Mock(side_effect=[subprocess.CalledProcessError(1, 'mkinitcpio'), None])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(subprocess.CalledProcessError):
            refresh(self.root, run)
        refresh(self.root, run)
        self.assertEqual(run.call_count, 2)
        self.assertFalse(self.pending.exists())

    def test_hook_retries_on_kernel_and_initramfs_transactions(self):
        hook = (REPO / 'upkeep/93-emaki-initramfs-refresh.hook').read_text()
        for target in ('emaki-config', 'mkinitcpio', 'mkinitcpio-git',
                       'usr/lib/modules/*/vmlinuz', 'usr/lib/initcpio/*',
                       'usr/lib/firmware/*', 'usr/bin/cryptsetup'):
            self.assertIn('Target = ' + target + '\n', hook)
        self.assertIn('Operation = Install', hook)
        self.assertIn('Operation = Remove', hook)

    def test_changed_local_preset_is_not_refreshed(self):
        self.pending.write_text('linux\n')
        self.put('etc/mkinitcpio.d/linux.preset', '# Local preset\n')
        run = Mock()
        refresh(self.root, run)
        run.assert_not_called()

    def test_stock_success_rebuilds_once_and_clears_pending(self):
        self.pending.write_text('linux\nlinux-lts\n')
        run = Mock()
        targets = 'usr/lib/modules/6.0/vmlinuz\n'
        transaction.stock(self.root, targets, run)
        run.assert_called_once_with(
            ['/usr/share/libalpm/scripts/mkinitcpio', 'install'],
            input=targets + 'usr/lib/initcpio/emaki-pending\n', text=True, check=True)
        self.assertFalse(self.pending.exists())
        refresh(self.root, run)
        self.assertEqual(run.call_count, 1)

    def test_stock_failure_retries_next_transaction_only(self):
        self.pending.write_text('linux\n')
        run = Mock(side_effect=subprocess.CalledProcessError(1, 'mkinitcpio'))
        with self.assertRaises(subprocess.CalledProcessError):
            transaction.stock(self.root, 'mkinitcpio\n', run)
        refresh(self.root, run, in_transaction=True)
        self.assertEqual(run.call_count, 1)
        self.assertTrue(self.pending.exists())
        transaction.begin(self.root)
        run.side_effect = None
        transaction.stock(self.root, 'mkinitcpio\n', run)
        self.assertEqual(run.call_count, 2)
        self.assertFalse(self.pending.exists())

    def test_stock_skips_quietly_while_mkinitcpio_is_removed(self):
        self.pending.write_text('linux\n')
        run = Mock(side_effect=FileNotFoundError('/usr/share/libalpm/scripts/mkinitcpio'))
        transaction.stock(self.root, 'usr/lib/initcpio/install/base\n', run)
        run.assert_called_once()
        self.assertFalse((self.root / transaction.RECEIPT).exists())
        self.assertTrue(self.pending.exists())

    def test_stock_without_pending_preserves_targets(self):
        run = Mock()
        targets = 'usr/lib/modules/6.0/vmlinuz\n'
        transaction.stock(self.root, targets, run)
        run.assert_called_once_with(
            ['/usr/share/libalpm/scripts/mkinitcpio', 'install'],
            input=targets, text=True, check=True)

    def test_hook_override_follows_stock_and_is_released(self):
        stock = '[Trigger]\nTarget = future-target\n[Action]\n' + transaction.STOCK_EXEC + 'NeedsTargets\n'
        self.put('usr/share/libalpm/hooks/90-mkinitcpio-install.hook', stock)
        transaction.install(self.root)
        hook = self.root / transaction.HOOK
        self.assertIn('Target = future-target\n', hook.read_text())
        self.assertIn(transaction.WRAPPER_EXEC, hook.read_text())
        transaction.release(self.root)
        self.assertFalse(hook.exists())

    def test_live_marker_prevents_wrapper_installation_and_transaction_refresh(self):
        stock_path = 'usr/share/libalpm/hooks/90-mkinitcpio-install.hook'
        stock = transaction.STOCK_EXEC + 'NeedsTargets\n'
        self.put(stock_path, stock)
        self.put('etc/emaki-live/greetd.toml', '# Live session\n')
        # Image construction has the marker before the runtime directory exists.
        for runtime in (False, True):
            with self.subTest(runtime=runtime):
                if runtime:
                    (self.root / 'run/archiso').mkdir(parents=True)
                for action in (transaction.install, transaction.begin):
                    action(self.root)
                    self.assertFalse((self.root / transaction.HOOK).exists())
                    self.assertFalse((self.root / transaction.LEDGER).exists())
                    self.assertEqual((self.root / stock_path).read_text(), stock)

    def test_installed_target_with_shared_live_run_keeps_wrapper(self):
        self.put('usr/share/libalpm/hooks/90-mkinitcpio-install.hook',
                 transaction.STOCK_EXEC + 'NeedsTargets\n')
        # arch-chroot exposes the live /run even for an installed target.
        (self.root / 'run/archiso').mkdir(parents=True)
        for action in (transaction.install, transaction.begin):
            action(self.root)
            self.assertIn(transaction.WRAPPER_EXEC, (self.root / transaction.HOOK).read_text())
            self.assertEqual((self.root / transaction.HOOK).read_text(),
                             (self.root / transaction.LEDGER).read_text())

    def test_live_system_releases_only_unchanged_owned_wrapper(self):
        self.put('usr/share/libalpm/hooks/90-mkinitcpio-install.hook',
                 transaction.STOCK_EXEC + 'NeedsTargets\n')
        transaction.install(self.root)
        self.put('etc/emaki-live/greetd.toml', '# Live session\n')
        transaction.begin(self.root)
        self.assertFalse((self.root / transaction.HOOK).exists())
        self.assertFalse((self.root / transaction.LEDGER).exists())
        self.put(transaction.HOOK, '# Local override\n')
        transaction.install(self.root)
        self.assertEqual((self.root / transaction.HOOK).read_text(), '# Local override\n')

    def test_local_override_and_changed_generated_override_survive(self):
        self.put('usr/share/libalpm/hooks/90-mkinitcpio-install.hook',
                 transaction.STOCK_EXEC + 'NeedsTargets\n')
        hook = self.root / transaction.HOOK
        self.put(transaction.HOOK, '# Local override\n')
        transaction.install(self.root)
        transaction.release(self.root)
        self.assertEqual(hook.read_text(), '# Local override\n')
        hook.unlink()
        transaction.install(self.root)
        hook.write_text(hook.read_text() + '# Local change\n')
        changed = hook.read_text()
        transaction.release(self.root)
        self.assertEqual(hook.read_text(), changed)

    def test_local_mask_is_preserved(self):
        self.put('usr/share/libalpm/hooks/90-mkinitcpio-install.hook',
                 transaction.STOCK_EXEC + 'NeedsTargets\n')
        hook = self.root / transaction.HOOK
        hook.parent.mkdir(parents=True)
        hook.symlink_to('/dev/null')
        transaction.install(self.root)
        transaction.release(self.root)
        self.assertTrue(hook.is_symlink())

    def test_begin_hook_covers_all_retry_triggers(self):
        begin = (REPO / 'upkeep/89-emaki-initramfs-begin.hook').read_text()
        retry = (REPO / 'upkeep/93-emaki-initramfs-refresh.hook').read_text()
        self.assertEqual(begin.split('[Action]')[0], retry.split('[Action]')[0])
        self.assertIn('emaki-initramfs-refresh --begin\n', begin)

    def test_manual_retry_ignores_failed_stock_receipt(self):
        self.pending.write_text('linux\n')
        failed = Mock(side_effect=subprocess.CalledProcessError(7, 'mkinitcpio'))
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(subprocess.CalledProcessError):
            transaction.stock(self.root, 'mkinitcpio\n', failed)
        run = Mock()
        refresh(self.root, run)
        run.assert_called_once_with(['mkinitcpio', '-p', 'linux'], check=True)
        self.assertFalse(self.pending.exists())

    def test_begin_updates_override_with_new_stock_targets(self):
        self.put('usr/share/libalpm/hooks/90-mkinitcpio-install.hook',
                 transaction.STOCK_EXEC + 'NeedsTargets\n')
        transaction.install(self.root)
        self.put('usr/share/libalpm/hooks/90-mkinitcpio-install.hook',
                 'Target = usr/lib/modprobe.d/\n' + transaction.STOCK_EXEC + 'NeedsTargets\n')
        transaction.begin(self.root)
        self.assertIn('Target = usr/lib/modprobe.d/\n',
                      (self.root / transaction.HOOK).read_text())
        self.assertEqual((self.root / transaction.HOOK).read_text(),
                         (self.root / transaction.LEDGER).read_text())

    def test_begin_releases_override_when_stock_is_unwrappable_or_missing(self):
        stock_path = 'usr/share/libalpm/hooks/90-mkinitcpio-install.hook'
        for replacement in ('Exec = /usr/bin/new-builder\nNeedsTargets\n', None):
            with self.subTest(replacement=replacement):
                self.put(stock_path, transaction.STOCK_EXEC + 'NeedsTargets\n')
                transaction.install(self.root)
                if replacement is None:
                    (self.root / stock_path).unlink()
                else:
                    self.put(stock_path, replacement)
                transaction.begin(self.root)
                self.assertFalse((self.root / transaction.HOOK).exists())
                self.assertFalse((self.root / transaction.LEDGER).exists())

    def test_begin_preserves_local_override_or_mask_when_stock_changes(self):
        stock_path = 'usr/share/libalpm/hooks/90-mkinitcpio-install.hook'
        self.put(stock_path, transaction.STOCK_EXEC + 'NeedsTargets\n')
        transaction.install(self.root)
        hook = self.root / transaction.HOOK
        hook.write_text(hook.read_text() + '# Local change\n')
        changed = hook.read_text()
        self.put(stock_path, 'Exec = /usr/bin/new-builder\n')
        transaction.begin(self.root)
        self.assertEqual(hook.read_text(), changed)
        hook.unlink()
        hook.symlink_to('/dev/null')
        transaction.begin(self.root)
        self.assertTrue(hook.is_symlink())

    def test_stock_cli_preserves_failure_status_without_traceback(self):
        errors = io.StringIO()
        with patch.object(sys, 'argv', ['emaki-initramfs-refresh', '--stock']), \
                patch.object(sys, 'stdin', io.StringIO('kernel-target\n')), \
                patch.object(transaction, 'stock', side_effect=subprocess.CalledProcessError(7, 'mkinitcpio')), \
                contextlib.redirect_stderr(errors), self.assertRaises(SystemExit) as caught:
            runpy.run_path(str(REPO / 'upkeep/emaki-initramfs-refresh'), run_name='__main__')
        self.assertEqual(caught.exception.code, 7)
        self.assertEqual(len(errors.getvalue().splitlines()), 1)
        self.assertIn('failed', errors.getvalue())
        self.assertNotIn('Traceback', errors.getvalue())

    def test_hook_uses_distinct_helper(self):
        name = '93-emaki-initramfs-refresh.hook'
        hook = (REPO / 'upkeep' / name).read_text()
        self.assertIn('When = PostTransaction', hook)
        self.assertIn('Exec = /usr/share/libalpm/scripts/emaki-initramfs-refresh --transaction\n', hook)
        loader_name = '95-emaki-boot-refresh.hook'
        loader = (REPO / 'grub' / loader_name).read_text()
        self.assertLess(name, loader_name)
        self.assertIn('When = PostTransaction', loader)
        self.assertIn('Exec = /usr/bin/emaki-boot-refresh --hook\n', loader)


if __name__ == '__main__':
    unittest.main()
