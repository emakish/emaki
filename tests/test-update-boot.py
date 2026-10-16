#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rootless update acceptance, transaction fencing and interrupted publication."""
from contextlib import ExitStack, contextmanager, nullcontext, redirect_stderr
import io
import json
import inspect
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'installer'), str(ROOT / 'grub'), str(ROOT / 'upkeep')]
from emaki_installer import boot
sys.modules['emaki_boot.boot'] = boot
from emaki_installer import secureboot
sys.modules['emaki_boot.secureboot'] = secureboot
from emaki_boot import refresh, update

FS = 'abcdef01-2345-6789-abcd-ef0123456789'
OLD = '12345678-1234-5678-9abc-123456789abc'
NEW = '12345678-1234-5678-9abc-123456789abd'


class UpdateBoot(unittest.TestCase):
    def test_zero_space_refuses_recovery_publication_and_transaction(self):
        self.stack.enter_context(patch.object(refresh, 'shared_esp', return_value=True))
        opened = self.stack.enter_context(patch.object(update.shared, 'open_writes'))
        update.prepare(self.identity)
        self.snapshot()
        empty = types.SimpleNamespace(f_bavail=0, f_frsize=4096)
        with patch.object(refresh, 'Path', side_effect=lambda path: self.esp.parent.parent / str(path).removeprefix('/efi').lstrip('/') if str(path).startswith('/efi') else Path(path)), \
                patch.object(refresh.os, 'statvfs', return_value=empty) as space, \
                patch.object(update.os, 'geteuid', return_value=0), \
                patch.object(update, 'supported', return_value=self.identity), \
                redirect_stderr(io.StringIO()):
            self.assertEqual(update.main(['mark']), 1)
        self.assertTrue(space.called)
        opened.assert_not_called()
        self.assertEqual(list(self.esp.iterdir()), [])

    def test_missing_shared_record_refuses_package_guard(self):
        (self.esp.parent / 'Microsoft').mkdir()
        with patch.object(refresh, 'SHARED_ESP', self.root / 'absent.json'), \
                patch.object(refresh, 'Path', side_effect=lambda path: self.esp.parent.parent if str(path) == '/efi' else self.root / str(path).lstrip('/')), \
                patch.object(refresh, 'esp_mount_path', return_value=self.esp.parent.parent), \
                patch.object(refresh, 'fstab_path', return_value=self.root / 'etc/fstab'), \
                patch.object(update.shared, 'MARKER', self.root / 'absent.json'), \
                patch.object(update.os, 'geteuid', return_value=0), \
                redirect_stderr(io.StringIO()):
            self.assertEqual(update.main(['guard-alongside']), 1)

    def test_own_esp_next_to_windows_has_no_shared_guard_or_sleep_skip(self):
        # Since 0.9 Emaki has its own ESP next to Windows: the package guard has nothing to
        # check and sleep counters are kept as on any Emaki-only ESP.
        record = self.root / 'alongside-boot.json'
        record.write_text(json.dumps({'version': 1, 'esp_partuuid': '14e269ee-0b88-441e-86f3-09b058e1ad76',
                                      'emaki_esp_partuuid': '5d1f0c3a-7b2e-4c19-9a6d-2e8f4b1c0a77',
                                      'windows_esp_uuid': '1234-ABCD'}) + '\n')
        record.chmod(0o644)
        inode, real_fstat = record.stat().st_ino, os.fstat

        def fstat(fd):
            # The installer writes the record as root; this test runs unprivileged.
            info = real_fstat(fd)
            return info if info.st_ino != inode else os.stat_result([*info[:4], 0, *info[5:]])

        with patch.object(refresh, 'SHARED_ESP', record), patch.object(update.shared, 'MARKER', record), \
                patch.object(update.shared.os, 'fstat', side_effect=fstat), \
                patch.object(update.os, 'geteuid', return_value=0), \
                patch.object(update.shared, 'check', side_effect=AssertionError('shared check')) as check, \
                patch.object(update, 'supported', return_value=None) as supported, \
                redirect_stderr(io.StringIO()):
            self.assertFalse(refresh.shared_esp())
            self.assertEqual(update.main(['guard-alongside']), 0)
            check.assert_not_called()
            for action in ('sleep-pre', 'sleep-post'):
                supported.reset_mock()
                self.assertEqual(update.main([action]), 0)
                supported.assert_called_once_with()
            # A stray shared-ESP hibernate drop-in never unmounts Emaki's own ESP or
            # remounts it read-only.
            with patch.object(update.subprocess, 'run') as run, \
                    patch.object(update.os.path, 'ismount', return_value=False), \
                    patch.object(refresh, 'sleep_run_lock') as lock:
                for action in ('hibernate-pre', 'hibernate-post'):
                    self.assertEqual(update.main([action]), 0)
                run.assert_not_called()
                lock.assert_not_called()

    def test_foreign_file_post_write_failure_aborts_recovery_hooks(self):
        @contextmanager
        def post_write_failure(**kwargs):
            yield
            raise update.shared.Stopped(update.shared.STOPPED)

        for action in ('prepare', 'mark'):
            with self.subTest(action=action), \
                    patch.object(update.os, 'geteuid', return_value=0), \
                    patch.object(update, 'supported', return_value=self.identity), \
                    patch.object(update, 'locked', side_effect=post_write_failure), \
                    patch.object(update, action), redirect_stderr(io.StringIO()):
                self.assertEqual(update.main([action]), 1)

    def test_shared_ext4_hibernation_preserves_loader_trial_counters(self):
        session = types.SimpleNamespace(foreign_root=lambda: False)
        counter = {'emaki_candidate': 'a' * 32, 'emaki_trial': '0'}
        saved = self.root / 'trial-sleep.json'
        with patch.dict(sys.modules, {'emaki_session_state': session}), \
                patch.object(refresh, 'Path', side_effect=lambda path: self.esp / 'trial.env' if str(path) == '/efi/EFI/Emaki/trial.env' else Path(path)), \
                patch.object(refresh, 'SLEEP_STATE', str(saved)), \
                patch.object(refresh, 'esp_writable'), \
                patch.object(refresh, 'trial_state', side_effect=lambda: dict(counter)), \
                patch.object(refresh, 'mount_info', return_value={'fstype': 'ext4', 'fsroot': '/'}), \
                patch.object(refresh, 'shared_esp', return_value=True), \
                patch.object(update.os, 'geteuid', return_value=0), \
                patch.object(update.os.path, 'ismount', return_value=True), \
                patch.object(refresh, 'sleep_run_lock', side_effect=lambda *args: nullcontext(True)), \
                patch.object(update.shared, 'session', side_effect=nullcontext), \
                patch.object(update, 'sleep_phase') as snapshots, \
                patch.object(refresh, 'sleep_trial', wraps=refresh.sleep_trial) as trial, \
                patch.object(update.subprocess, 'run') as run:
            self.assertEqual(update.main(['hibernate-pre']), 0)
            trial.assert_called_once_with('pre')
            self.assertEqual(json.loads(saved.read_text()), counter)
            counter['emaki_trial'] = '1'
            self.assertEqual(update.main(['hibernate-post']), 0)
            self.assertEqual(trial.call_args_list[-1].args, ('post',))
            snapshots.assert_not_called()
            self.assertIn(b'emaki_trial=0', (self.esp / 'trial.env').read_bytes())
            self.assertFalse(saved.exists())
            run.assert_called_once_with(['umount', '/efi'], check=True, timeout=60)

    def test_alongside_initial_install_hook_skips_before_runtime_protection(self):
        with patch.object(refresh.Path, 'exists', autospec=True,
                          side_effect=lambda path: str(path) == '/.emaki-install-incomplete'), \
                patch.object(refresh, 'shared_esp') as shared, \
                patch.object(refresh.subprocess, 'run') as run, \
                patch.object(refresh, 'refresh_attempt') as attempt:
            self.assertEqual(refresh.main(['--hook']), 0)
            shared.assert_not_called()
            run.assert_not_called()
            attempt.assert_not_called()

    def test_alongside_chroot_hook_skips_before_runtime_protection(self):
        with patch.object(refresh.Path, 'exists', return_value=False), \
                patch.object(refresh, 'shared_esp') as shared, \
                patch.object(refresh.subprocess, 'run',
                             return_value=types.SimpleNamespace(returncode=0)), \
                patch.object(refresh, 'refresh_attempt') as attempt:
            self.assertEqual(refresh.main(['--hook']), 0)
            shared.assert_not_called()
            attempt.assert_not_called()

    def test_shared_guard_recovers_only_while_holding_the_boot_lock(self):
        events = []

        @contextmanager
        def locked(**kwargs):
            events.append('locked')
            yield
            events.append('unlocked')

        with patch.object(refresh, 'shared_esp', return_value=True), \
                patch.object(update.Path, 'is_file', return_value=False), \
                patch.object(update.os, 'geteuid', return_value=0), \
                patch.object(update, 'locked', side_effect=locked), \
                patch.object(update.shared, 'check', side_effect=lambda: events.append('checked')):
            self.assertEqual(update.main(['guard-alongside']), 0)
        self.assertEqual(events, ['locked', 'checked', 'unlocked'])

    def test_shared_esp_package_guard_refuses_only_unsafe_states_before_any_disk_operation(self):
        refusal = update.shared.Refused(update.shared.HIBERNATED)
        with patch.object(refresh, 'SHARED_ESP', self.root / 'alongside.json'), \
                patch.object(update.shared, 'MARKER', self.root / 'alongside.json'), \
                patch.object(update.os, 'geteuid', return_value=0), \
                patch.object(update, 'supported') as supported, \
                patch.object(update.shared, 'run') as shared_run, \
                patch.object(refresh, 'run') as run:
            # No shared ESP: nothing to check.
            self.assertEqual(update.main(['guard-alongside']), 0)
            # A malformed record (not root-owned, not version 1) refuses before any probe.
            refresh.SHARED_ESP.write_text('{}')
            errors = io.StringIO()
            with redirect_stderr(errors):
                self.assertEqual(update.main(['guard-alongside']), 1)
            self.assertIn('record of the startup partition', errors.getvalue())
            shared_run.assert_not_called()
            # The installer owns an unfinished target: allowed without probing Windows.
            with patch.object(update.Path, 'is_file', return_value=True), \
                    patch.object(update.shared, 'check') as check:
                self.assertEqual(update.main(['guard-alongside']), 0)
                check.assert_not_called()
            with patch.object(update.shared, 'check') as check:
                self.assertEqual(update.main(['guard-alongside']), 0)
                check.assert_called_once_with()
                check.side_effect = refusal
                errors = io.StringIO()
                with redirect_stderr(errors):
                    self.assertEqual(update.main(['guard-alongside']), 1)
                self.assertEqual(errors.getvalue(), update.shared.HIBERNATED + '\n')
            refresh.SHARED_ESP.unlink()
            refresh.SHARED_ESP.symlink_to(self.root / 'missing-marker')
            with redirect_stderr(io.StringIO()):
                self.assertEqual(update.main(['guard-alongside']), 1)
            supported.assert_not_called()
            run.assert_not_called()
        hook = ROOT / 'grub/00-emaki-alongside.hook'
        text = hook.read_text()
        self.assertIn('When = PreTransaction\n', text)
        self.assertIn('AbortOnFail\n', text)
        self.assertIn('Target = *\n', text)
        self.assertIn('emaki-update-boot guard-alongside\n', text)
        for directory in ('grub', 'upkeep'):
            for other in (ROOT / directory).glob('*.hook'):
                if other != hook and 'When = PreTransaction' in other.read_text():
                    self.assertLess(hook.name, other.name)

    def test_shared_esp_skips_sleep_bookkeeping_without_probing(self):
        # Hibernation unmounts the shared ESP first; user.slice is frozen meanwhile.
        with patch.object(refresh, 'shared_esp', return_value=True), \
                patch.object(update.os, 'geteuid', return_value=0), \
                patch.object(refresh.subprocess, 'run',
                             return_value=types.SimpleNamespace(returncode=1)), \
                patch.object(update, 'supported') as supported, \
                patch.object(update.shared, 'check') as check, \
                patch.object(refresh, 'sleep_trial') as sleep_trial:
            for action in ('sleep-pre', 'sleep-post'):
                self.assertEqual(update.main([action]), 0)
            for phase in ('pre', 'post'):
                self.assertEqual(refresh.main(['--sleep-phase', phase]), 0)
            supported.assert_not_called()
            check.assert_not_called()
            sleep_trial.assert_not_called()

    def test_shared_esp_hibernation_saves_counters_around_its_unmount_barrier(self):
        events = []

        @contextmanager
        def sleep_lock(phase, saved, label):
            events.append(('lock', saved.name))
            yield True

        @contextmanager
        def session():
            events.append('session')
            yield

        def run(argv, check, timeout):
            events.append(tuple(argv))
            if failing and argv[0] in failing:
                raise update.subprocess.CalledProcessError(32, argv)
            return types.SimpleNamespace(returncode=0)

        mounted = [True]
        failing = set()
        with patch.object(refresh, 'shared_esp', return_value=True), \
                patch.object(update.os, 'geteuid', return_value=0), \
                patch.object(update.os.path, 'ismount', side_effect=lambda path: mounted[0]), \
                patch.object(update, 'supported', return_value={'uuid': FS}), \
                patch.object(refresh, 'sleep_run_lock', side_effect=sleep_lock), \
                patch.object(update.shared, 'session', side_effect=session), \
                patch.object(update, 'sleep_phase', side_effect=lambda phase: events.append('update-' + phase)), \
                patch.object(refresh, 'sleep_trial', side_effect=lambda phase: events.append('trial-' + phase)), \
                patch.object(update.subprocess, 'run', side_effect=run):
            self.assertEqual(update.main(['hibernate-pre']), 0)
            # Counters are saved while the ESP is mounted; the unmount comes last.
            self.assertEqual(events, [('lock', 'sleep.json'), 'session', 'update-pre',
                                      ('lock', 'emaki-boot-sleep.json'), 'session', 'trial-pre',
                                      ('umount', '/efi')])
            events.clear()
            mounted[0] = False
            self.assertEqual(update.main(['hibernate-post']), 0)
            self.assertEqual(events[0], ('mount', '-o', 'ro', '/efi'))
            self.assertEqual(events[1:], [('lock', 'sleep.json'), 'session', 'update-post',
                                          ('lock', 'emaki-boot-sleep.json'), 'session', 'trial-post'])
            # After a cancelled sleep the ESP is still mounted: no second mount.
            events.clear()
            mounted[0] = True
            self.assertEqual(update.main(['hibernate-post']), 0)
            self.assertNotIn(('mount', '-o', 'ro', '/efi'), events)
            # A refused write (Windows asleep) is logged; hibernation still goes on.
            events.clear()
            errors = io.StringIO()
            with patch.object(update, 'sleep_phase', side_effect=update.shared.Refused(update.shared.HIBERNATED)), \
                    redirect_stderr(errors):
                self.assertEqual(update.main(['hibernate-pre']), 0)
            self.assertEqual(events[-1], ('umount', '/efi'))
            self.assertIn('Hibernation pre bookkeeping did not finish', errors.getvalue())
            # Only a failed unmount or mount refuses: the sleep service then does not start.
            failing.add('umount')
            with redirect_stderr(io.StringIO()):
                self.assertEqual(update.main(['hibernate-pre']), 1)
            mounted[0] = False
            failing.add('mount')
            events.clear()
            with redirect_stderr(io.StringIO()):
                self.assertEqual(update.main(['hibernate-post']), 1)
            self.assertNotIn('update-post', events)
        # Without a shared ESP the barrier alone runs.
        events.clear()
        failing.clear()
        with patch.object(refresh, 'shared_esp', return_value=False), \
                patch.object(update.os, 'geteuid', return_value=0), \
                patch.object(update, 'supported') as supported, \
                patch.object(update.subprocess, 'run', side_effect=run):
            self.assertEqual(update.main(['hibernate-pre']), 0)
            supported.assert_not_called()
        self.assertEqual(events, [('umount', '/efi')])

    def test_shared_esp_runtime_work_runs_and_defers_on_refusal(self):
        refusal = update.shared.Refused(update.shared.HIBERNATED)
        with patch.object(refresh, 'shared_esp', return_value=True), \
                patch.object(update.os, 'geteuid', return_value=0), \
                patch.object(update, 'supported', return_value={'uuid': FS}) as supported, \
                patch.object(update, 'locked', return_value=nullcontext()), \
                patch.object(update, 'monitor', side_effect=refusal) as monitor, \
                patch.object(update, 'prepare', side_effect=refusal) as prepare:
            errors = io.StringIO()
            with redirect_stderr(errors):
                # Acceptance waits for a later start instead of failing the boot service.
                self.assertEqual(update.main(['accept']), 0)
                # A refusal after the guard still stops package extraction.
                self.assertEqual(update.main(['prepare']), 1)
            monitor.assert_called_once()
            prepare.assert_called_once()
            self.assertEqual(supported.call_count, 2)
            self.assertIn('package transaction was stopped', errors.getvalue())
            self.assertIn(update.shared.HIBERNATED, errors.getvalue())

    def test_shared_esp_snapshot_service_repairs_through_the_gate(self):
        with patch.object(refresh.Path, 'exists', return_value=False), \
                patch.object(refresh.subprocess, 'run',
                             return_value=types.SimpleNamespace(returncode=1)), \
                patch.object(refresh, 'shared_esp', return_value=True), \
                patch.object(refresh.os, 'geteuid', return_value=0), \
                patch.object(refresh, 'snapshot_menu_needs_repair', return_value=True), \
                patch.object(refresh, 'menu_font_needs_repair', return_value=False), \
                patch.object(refresh, 'open_run_lock'), patch.object(refresh, 'wait_run_lock'), \
                patch.object(refresh, 'BoundedLog'), \
                patch.object(refresh, 'discover', return_value={'uuid': FS}), \
                patch.object(refresh, 'remove_previous_boot_lock'), \
                patch.object(refresh, 'current_boot_id', return_value=OLD), \
                patch.object(refresh, 'pacman_lock', return_value=nullcontext()), \
                patch.object(refresh, 'repair_snapshot_menu') as repair, \
                redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(refresh.main(['--repair-snapshots']), 0)
            repair.assert_called_once_with({'uuid': FS})
            # A refused write session defers boot-time repair without failing the service.
            repair.side_effect = update.shared.Refused(update.shared.HIBERNATED)
            self.assertEqual(refresh.main(['--repair-snapshots']), 0)
            self.assertIn(update.shared.HIBERNATED, errors.getvalue())

    def test_acceptance_lock_waits_for_boot_maintenance(self):
        # Acceptance must wait on the lock: failing fast here would leave a good boot unaccepted.
        with patch.object(refresh, 'open_run_lock') as opened, patch.object(update.fcntl, 'flock') as flock:
            with self.original_locked(blocking=True):
                pass
            flock.assert_called_with(opened.return_value.__enter__.return_value, update.fcntl.LOCK_EX)

    def test_delivered_hooks_bracket_snap_pac_and_abort_failed_protection(self):
        hooks = ('04-emaki-update-boot.hook', '05-snap-pac-pre.hook', '06-emaki-update-boot.hook')
        self.assertEqual(list(hooks), sorted(hooks))
        for name, action in ((hooks[0], 'prepare'), (hooks[2], 'mark')):
            text = (ROOT / 'grub' / name).read_text()
            self.assertIn('When = PreTransaction\n', text)
            self.assertIn('AbortOnFail\n', text)
            self.assertIn('Target = *\n', text)
            for operation in ('Install', 'Upgrade', 'Remove'):
                self.assertIn('Operation = ' + operation + '\n', text)
            self.assertIn('Exec = /usr/bin/emaki-update-boot ' + action + '\n', text)
        unit = (ROOT / 'systemd/emaki-update-boot.service').read_text()
        self.assertIn('After=multi-user.target greetd.service emaki-boot-refresh.service\n', unit)
        self.assertIn('ExecStart=/usr/bin/emaki-update-boot accept\n', unit)

    def setUp(self):
        self.original_locked = update.locked
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='ub-')))
        self.esp = self.root / 'efi' / 'EFI' / 'Emaki'
        self.snapshots = self.root / 'snapshots'
        self.menu = self.root / 'boot' / 'grub' / 'grub.cfg'
        for path in (self.esp, self.snapshots, self.menu.parent):
            path.mkdir(parents=True)
        # Keep refresh.shared_esp() hermetic: the host's own /efi and /etc/fstab are no evidence here (the package
        # build VM has an /efi its build user cannot read).
        self.stack.enter_context(patch.object(refresh, 'esp_mount_path', return_value=self.root / 'host-efi-absent'))
        self.stack.enter_context(patch.object(refresh, 'fstab_path', return_value=self.root / 'host-fstab-absent'))
        self.menu.write_text("set default=0\nmenuentry 'Emaki' {\n  linux /@/boot/vmlinuz-linux root=UUID=" + FS + " rootflags=subvol=@\n  initrd /@/boot/initramfs-linux.img\n}\n")
        self.identity = {'uuid': FS, 'esp_uuid': '1234-ABCD'}
        self.boot_id = OLD
        self.parent = [123, '456', OLD]
        self.calls = []
        self.pins = []
        self.clock = 0.0
        original_lstat = Path.lstat

        def root_owner(path, *args, **kwargs):
            # Only ownership is virtualized; mode, type, bytes and atomic writes are real.
            result = list(original_lstat(path, *args, **kwargs))
            result[4] = 0
            return os.stat_result(result)

        original_read = Path.read_text

        def read_text(path, *args, **kwargs):
            if path == Path('/proc/cmdline'):
                return 'root=UUID=' + FS + ' rootflags=subvol=@'
            return original_read(path, *args, **kwargs)

        for name, value in (('ESP', self.esp), ('SNAPSHOTS', self.snapshots),
                            ('MENU', self.menu), ('BASELINE', self.root / 'baseline.json'),
                            ('SLEEP', self.root / 'sleep.json')):
            self.stack.enter_context(patch.object(update, name, value))
        self.stack.enter_context(patch.object(Path, 'lstat', root_owner))
        self.stack.enter_context(patch.object(Path, 'read_text', read_text))
        self.stack.enter_context(patch.object(update.tempfile, 'gettempdir', return_value=str(self.root)))
        self.stack.enter_context(patch.object(update, 'transaction_identity', side_effect=lambda: self.parent))
        self.stack.enter_context(patch.object(refresh, 'current_boot_id', side_effect=lambda: self.boot_id))
        self.stack.enter_context(patch.object(refresh, 'pause_snapshots', side_effect=nullcontext))
        self.stack.enter_context(patch.object(refresh, 'recover'))
        self.stack.enter_context(patch.object(update, 'locked', side_effect=lambda **kwargs: nullcontext()))
        self.stack.enter_context(patch.object(update, 'command', side_effect=self.command))
        self.stack.callback(time.tzset)
        self.stack.enter_context(patch.dict(os.environ, {'TZ': 'UTC'}))
        time.tzset()

    def command(self, argv):
        self.calls.append([str(item) for item in argv])
        if argv[0] == 'snapper':
            self.pins.append((argv[-1], argv[-2]))
        return ''

    def snapshot(self, number='10', kind='pre', cleanup='number'):
        directory = self.snapshots / number
        (directory / 'snapshot').mkdir(parents=True)
        (directory / 'info.xml').write_text(
            f'<snapshot><type>{kind}</type><num>{number}</num><date>2026-10-07 12:00:00</date>'
            f'<cleanup>{cleanup}</cleanup></snapshot>')
        prefix = '/@snapshots/' + number + '/snapshot/boot/'
        (self.menu.parent / 'grub-btrfs.cfg').write_text(
            "menuentry 'snapshot' {\n  linux " + prefix + 'vmlinuz-linux root=UUID=' + FS
            + ' rootflags=subvol=/@snapshots/' + number + '/snapshot\n  initrd '
            + prefix + 'initramfs-linux.img\n}\n')
        (self.root / 'snap-pac-pre_root').write_text(number)

    def transaction(self, number='10'):
        update.prepare(self.identity)
        self.snapshot(number)
        update.mark(self.identity)
        return update.record()

    def env(self, **changes):
        env = update.environment()
        env.update(changes)
        update.write_environment(env)
        return env

    def now(self):
        return self.clock

    def wait(self, seconds):
        self.clock += seconds

    def monitor(self, observe):
        return update.monitor(self.identity, observe, self.now, self.wait)

    def test_prepare_mark_binds_new_pre_snapshot_and_pins_it(self):
        self.snapshot('9')
        value = self.transaction()
        self.assertEqual(value['snapshot'], '10')
        self.assertEqual(value['date'], '2026-10-07')
        self.assertEqual(value['root_uuid'], FS)
        self.assertEqual(value['armed_boot'], OLD)
        self.assertEqual(self.pins, [('10', '')])
        self.assertEqual(update.environment()['emaki_attempt'], '0')
        self.assertIn('emaki-auto-recovery', (self.esp / 'update.cfg').read_text())
        self.assertIn('emaki_update_attempt', self.menu.read_text())

    def test_receipt_rejects_stale_foreign_missing_or_post_snapshot(self):
        for case in ('old-number', 'parent', 'root', 'missing-receipt', 'post', 'missing-tree'):
            with self.subTest(case=case):
                for path in self.snapshots.iterdir():
                    shutil.rmtree(path)
                if case == 'old-number':
                    self.snapshot()
                update.prepare(self.identity)
                if case != 'old-number':
                    self.snapshot(kind='post' if case == 'post' else 'pre')
                receipt = json.loads(update.BASELINE.read_text())
                if case == 'parent':
                    receipt['parent'][1] = '999'
                if case == 'root':
                    receipt['uuid'] = NEW
                update.BASELINE.write_text(json.dumps(receipt))
                if case == 'missing-receipt':
                    (self.root / 'snap-pac-pre_root').unlink()
                if case == 'missing-tree':
                    shutil.rmtree(self.snapshots / '10' / 'snapshot')
                with self.assertRaises((refresh.Refuse, OSError)):
                    update.mark(self.identity)
                self.assertFalse((self.esp / 'update.env').exists())

    def test_double_mark_preserves_first_boundary_and_counter(self):
        first = self.transaction()
        update.mark(self.identity)
        self.assertEqual(update.record(), first)
        self.boot_id = NEW
        self.parent = [124, '789', NEW]
        self.env(emaki_attempt='2')
        second = self.transaction('11')
        self.assertEqual(second['transaction'], first['transaction'])
        self.assertEqual(second['snapshot'], '10')
        self.assertEqual(second['armed_boot'], NEW)
        self.assertEqual(update.environment()['emaki_attempt'], '2')
        self.assertEqual(self.pins, [('10', '')])

    def test_deleted_pending_snapshot_disarms_and_refuses_unprotected_update(self):
        self.transaction()
        shutil.rmtree(self.snapshots / '10')
        update.prepare(self.identity)
        self.snapshot('11')
        with self.assertRaisesRegex(refresh.Refuse, 'recovery snapshot is missing'):
            update.mark(self.identity)
        self.assertIsNone(update.record())
        self.assertEqual(update.environment(), {})
        self.assertEqual(self.pins, [('10', '')])
        # Further transactions can protect a fresh boundary without repeating the warning.
        with redirect_stderr(io.StringIO()) as messages:
            self.transaction('12')
        self.assertEqual(messages.getvalue(), '')
        self.assertEqual(update.record()['snapshot'], '12')

    def test_missing_pending_snapshot_disarms_even_without_a_new_receipt(self):
        self.transaction()
        shutil.rmtree(self.snapshots / '10')
        (self.root / 'snap-pac-pre_root').unlink()
        with self.assertRaisesRegex(refresh.Refuse, 'recovery snapshot is missing'):
            update.mark(self.identity)
        self.assertIsNone(update.record())
        self.assertEqual(update.environment(), {})

    def test_snapshot_date_uses_local_calendar_day_across_utc_midnight(self):
        self.snapshot()
        info = self.snapshots / '10' / 'info.xml'
        cases = (('America/New_York', '2026-10-08 02:39:29', '2026-10-07'),
                 ('Asia/Tokyo', '2026-10-07 23:39:29', '2026-10-08'),
                 ('America/New_York', '2026-01-08 04:39:29', '2026-01-07'),
                 ('America/New_York', '2026-07-08 04:39:29', '2026-07-08'))
        for zone, stamp, expected in cases:
            with self.subTest(zone=zone, stamp=stamp), patch.dict(os.environ, {'TZ': zone}):
                time.tzset()
                info.write_text('<snapshot><type>pre</type><num>10</num><date>' + stamp + '</date></snapshot>')
                self.assertEqual(update.snapshot_info('10')[0], expected)
        time.tzset()

    def test_snapshot_pin_and_release_use_the_daemon_snapshot_list(self):
        value = self.transaction()
        self.boot_id = NEW
        self.env(emaki_attempt='1')
        self.assertTrue(update.accept(value, self.identity))
        self.assertEqual([argv for argv in self.calls if argv[0] == 'snapper'], [
            ['snapper', '-c', 'root', 'modify', '--cleanup-algorithm', '', '10'],
            ['snapper', '-c', 'root', 'modify', '--cleanup-algorithm', 'number', '10']])

    def test_shared_hook_errors_abort_package_management(self):
        self.stack.enter_context(patch.object(refresh, 'shared_esp', return_value=True))
        for action in ('prepare', 'mark'):
            with self.subTest(action=action), patch.object(update.os, 'geteuid', return_value=0), \
                    patch.object(update, 'supported', return_value=self.identity), \
                    patch.object(update, action, side_effect=OSError('unavailable')), \
                    redirect_stderr(io.StringIO()) as messages:
                self.assertEqual(update.main([action]), 1)
                self.assertIn('package transaction was stopped', messages.getvalue())

    def test_normal_missing_or_stale_receipt_warns_and_continues(self):
        for case in ('missing', 'stale'):
            with self.subTest(case=case):
                if case == 'stale':
                    self.snapshot('9')
                update.prepare(self.identity)
                if case == 'stale':
                    (self.root / 'snap-pac-pre_root').write_text('9')
                with patch.object(refresh, 'shared_esp', return_value=False), \
                        patch.object(update.os, 'geteuid', return_value=0), \
                        patch.object(update, 'supported', return_value=self.identity), \
                        redirect_stderr(io.StringIO()) as messages:
                    self.assertEqual(update.main(['mark']), 0)
                    self.assertIn('no recovery protection', messages.getvalue())
                    self.assertNotIn('transaction was stopped', messages.getvalue())
                self.assertEqual(list(self.esp.iterdir()), [])

    def test_normal_startup_and_recovery_failures_warn_and_continue(self):
        for action in ('prepare', 'mark'):
            for stage in ('supported', action):
                for error in (OSError('unavailable'), refresh.Refuse('snapshot unavailable'),
                              update.shared.Refused('startup unavailable'), TimeoutError('busy')):
                    with self.subTest(action=action, stage=stage, error=type(error).__name__), \
                            patch.object(refresh, 'shared_esp', return_value=False), \
                            patch.object(update.os, 'geteuid', return_value=0), \
                            patch.object(update, 'supported', return_value=self.identity), \
                            patch.object(update, stage, side_effect=error), \
                            redirect_stderr(io.StringIO()) as messages:
                        self.assertEqual(update.main([action]), 0)
                        self.assertIn('no recovery protection', messages.getvalue())
                        self.assertNotIn('transaction was stopped', messages.getvalue())

    def test_shared_missing_or_stale_receipt_aborts(self):
        for case in ('missing', 'stale'):
            with self.subTest(case=case):
                if case == 'stale':
                    self.snapshot('9')
                update.prepare(self.identity)
                if case == 'stale':
                    (self.root / 'snap-pac-pre_root').write_text('9')
                with patch.object(refresh, 'shared_esp', return_value=True), \
                        patch.object(update.os, 'geteuid', return_value=0), \
                        patch.object(update, 'supported', return_value=self.identity), \
                        redirect_stderr(io.StringIO()) as messages:
                    self.assertEqual(update.main(['mark']), 1)
                    self.assertIn('transaction was stopped', messages.getvalue())

    def test_failed_menu_build_releases_the_incomplete_snapshot_pin(self):
        update.prepare(self.identity)
        self.snapshot()
        with patch.object(update, 'install_menu', side_effect=OSError('unavailable menu')):
            with self.assertRaises(OSError):
                update.mark(self.identity)
        self.assertEqual(self.pins, [('10', ''), ('10', 'number')])
        self.assertIsNone(update.record())
        self.assertEqual(update.environment(), {})

    def test_hook_lock_timeout_is_bounded_and_aborts_transaction(self):
        self.stack.enter_context(patch.object(refresh, 'shared_esp', return_value=True))
        def wait(seconds):
            self.clock += seconds

        with patch.object(refresh, 'open_run_lock') as opened, \
                patch.object(update.fcntl, 'flock', side_effect=BlockingIOError), \
                patch.object(update.time, 'monotonic', side_effect=self.now), \
                patch.object(update.time, 'sleep', side_effect=wait), \
                patch.object(update, 'locked', self.original_locked), \
                patch.object(update.os, 'geteuid', return_value=0), \
                patch.object(update, 'supported', return_value=self.identity), \
                patch.object(update, 'prepare') as prepare, redirect_stderr(io.StringIO()) as messages:
            self.assertEqual(update.main(['prepare']), 1)
            prepare.assert_not_called()
            self.assertEqual(self.clock, update.HOOK_LOCK_TIMEOUT)
            self.assertIn('Boot maintenance is busy', messages.getvalue())
            opened.assert_called_once()

    def test_lock_wait_acquires_after_real_contention(self):
        path = self.root / 'maintenance.lock'
        held = threading.Event()
        release = threading.Event()

        def holder():
            with path.open('a') as lock:
                update.fcntl.flock(lock, update.fcntl.LOCK_EX)
                held.set()
                release.wait(2)

        thread = threading.Thread(target=holder)
        thread.start()
        self.assertTrue(held.wait(2))
        timer = threading.Timer(0.1, release.set)
        timer.start()
        try:
            with patch.object(refresh, 'open_run_lock', side_effect=lambda mode: path.open(mode)):
                with self.original_locked(timeout=1):
                    self.assertTrue(release.is_set())
        finally:
            release.set()
            timer.join()
            thread.join()

    def test_acceptance_and_monitor_wait_for_maintenance(self):
        self.transaction()
        self.boot_id = NEW
        with patch.object(update, 'locked', side_effect=lambda **kwargs: nullcontext()) as lock:
            self.assertTrue(self.monitor(lambda: 'ready'))
        self.assertGreaterEqual(lock.call_count, 3)
        self.assertTrue(all(call.kwargs.get('blocking') is True for call in lock.call_args_list))

    def test_unprotected_grub_boot_disarms_instead_of_rearming_in_userspace(self):
        self.transaction()
        self.boot_id = NEW
        with patch.object(Path, 'read_text', return_value='rootflags=subvol=@ emaki.update_unprotected=1'), \
                redirect_stderr(io.StringIO()) as messages:
            self.assertFalse(self.monitor(lambda: 'ready'))
        self.assertIsNone(update.record())
        self.assertEqual(update.environment(), {})
        self.assertEqual(self.pins[-1], ('10', 'number'))
        self.assertIn('GRUB could not save the boot attempt', messages.getvalue())

    def test_acceptance_fences_boot_root_snapshot_and_counter(self):
        value = self.transaction()
        env = self.env(emaki_attempt='1')
        self.assertTrue(update.may_accept(value, env, NEW, FS, 'rootflags=subvol=@'))
        for current, root, cmdline, changes in (
                (OLD, FS, '', {}), (NEW, NEW, '', {}),
                (NEW, FS, 'emaki.auto_return=abc', {}),
                (NEW, FS, 'rootflags=subvol=/@snapshots/10/snapshot', {}),
                (NEW, FS, '', {'emaki_attempt': '0'}),
                (NEW, FS, '', {'emaki_update': 'f' * 32})):
            with self.subTest(current=current, root=root, cmdline=cmdline, changes=changes):
                self.assertFalse(update.may_accept(value, {**env, **changes}, current, root, cmdline))

    def test_same_boot_never_starts_acceptance_even_with_ready_session(self):
        self.transaction()
        with patch.object(update, 'accept') as accept:
            self.assertFalse(self.monitor(lambda: 'ready'))
            accept.assert_not_called()
        self.assertEqual(update.environment()['emaki_attempt'], '0')

    def test_ready_session_requires_fifteen_unchanged_seconds(self):
        self.transaction()
        self.boot_id = NEW
        self.assertTrue(self.monitor(lambda: 'ready'))
        self.assertEqual(self.clock, 15)
        self.assertIsNone(update.record())
        self.assertNotIn('next_entry', update.environment())
        self.assertEqual(self.pins[-1], ('10', 'number'))

    def test_restarting_session_restarts_stability_window(self):
        self.transaction()
        self.boot_id = NEW
        self.assertTrue(self.monitor(lambda: 'old' if self.clock < 14 else 'replacement'))
        self.assertEqual(self.clock, 29)

    def test_deadline_leaves_armed_return_and_unaccepted_marker(self):
        value = self.transaction()
        self.boot_id = NEW
        self.assertFalse(self.monitor(lambda: None))
        self.assertEqual(self.clock, update.DEADLINE)
        self.assertEqual(update.record(), value)
        self.assertEqual(update.environment()['next_entry'], 'emaki-auto-recovery')

    def test_late_ready_session_cannot_extend_deadline(self):
        self.transaction()
        self.boot_id = NEW
        self.assertFalse(self.monitor(lambda: 'ready' if self.clock >= update.DEADLINE - 14 else None))
        self.assertEqual(self.clock, update.DEADLINE)
        self.assertIsNotNone(update.record())

    def test_observer_time_counts_against_deadline(self):
        self.transaction()
        self.boot_id = NEW

        def slow_observation():
            self.clock += update.DEADLINE
            return 'ready'

        self.assertFalse(self.monitor(slow_observation))
        self.assertIsNotNone(update.record())

    def test_accept_rechecks_replaced_marker_and_environment(self):
        value = self.transaction()
        self.boot_id = NEW
        self.env(emaki_attempt='1', emaki_update='f' * 32)
        self.assertFalse(update.accept(value, self.identity))
        self.assertEqual(update.record(), value)

    def test_sleep_suppresses_the_return_until_resume(self):
        # Without the suppress flag GRUB would pick recovery with noresume and lose a hibernated session.
        self.transaction()
        self.env(emaki_attempt='1', next_entry='')
        update.sleep_phase('pre')
        self.assertEqual(update.environment().get('emaki_suppress'), '1')
        update.sleep_phase('post')
        self.assertNotEqual(update.environment().get('emaki_suppress'), '1')

    def test_acceptance_deadline_fits_the_unit_timeout(self):
        unit = (ROOT / 'systemd/emaki-update-boot.service').read_text()
        minutes = int(unit.split('TimeoutStartSec=', 1)[1].split('min', 1)[0])
        self.assertEqual(update.DEADLINE, 300)
        self.assertLess(update.DEADLINE + update.STABLE, minutes * 60)

    def test_package_hooks_wait_at_most_ten_seconds(self):
        # A busy boot maintenance lock must never hold a package transaction for long.
        self.assertEqual(update.HOOK_LOCK_TIMEOUT, 10)

    def test_resume_restores_original_attempt_without_replenishing_spent_return(self):
        self.transaction()
        self.env(emaki_attempt='2', next_entry='')
        before = update.environment()
        update.sleep_phase('pre')
        self.env(emaki_attempt='1', next_entry='emaki-auto-recovery')
        update.sleep_phase('post')
        self.assertEqual(update.environment(), before)
        self.assertFalse(update.SLEEP.exists())
        self.assertFalse(self.monitor(lambda: 'ready'))

    def test_resume_without_volatile_receipt_is_not_a_resume(self):
        self.transaction()
        self.env(emaki_attempt='2')
        before = update.environment()
        update.sleep_phase('post')
        self.assertEqual(update.environment(), before)

    def test_resume_cannot_restore_another_transaction(self):
        self.transaction()
        update.sleep_phase('pre')
        self.env(emaki_update='e' * 32, emaki_attempt='2')
        before = update.environment()
        update.sleep_phase('post')
        self.assertEqual(update.environment(), before)

    def test_live_foreign_snapshot_and_ext4_roots_do_nothing(self):
        session = types.SimpleNamespace(foreign_root=lambda: False)
        with patch.dict(sys.modules, {'emaki_session_state': session}), \
                patch.object(Path, 'exists', return_value=False), \
                patch.object(refresh, 'discover') as discover:
            for root in ({'fstype': 'ext4', 'fsroot': '/'},
                         {'fstype': 'overlay', 'fsroot': '/'},
                         {'fstype': 'btrfs', 'fsroot': '/@snapshots/10/snapshot'}):
                with self.subTest(root=root), patch.object(refresh, 'mount_info', return_value=root):
                    self.assertIsNone(update.supported())
            discover.assert_not_called()
            session.foreign_root = lambda: True
            with patch.object(refresh, 'mount_info') as mounts:
                self.assertIsNone(update.supported())
                mounts.assert_not_called()

    def test_live_marker_prevents_mount_discovery(self):
        session = types.SimpleNamespace(foreign_root=lambda: False)
        with patch.dict(sys.modules, {'emaki_session_state': session}), \
                patch.object(Path, 'exists', side_effect=lambda: True), \
                patch.object(refresh, 'mount_info') as mounts:
            self.assertIsNone(update.supported())
            mounts.assert_not_called()

    def test_private_state_rejects_symlink_and_writable_files(self):
        target = self.root / 'safe'
        target.write_text('content')
        link = self.root / 'link'
        link.symlink_to(target)
        with self.assertRaises(refresh.Refuse):
            update.private_read(link)
        target.chmod(0o666)
        with self.assertRaises(refresh.Refuse):
            update.private_read(target)

    def publication_steps(self, function, fail_at=None, after=False):
        events = []
        atomic, command, unlink = refresh.atomic, update.command, Path.unlink

        def step(label, operation, *args):
            index = len(events)
            events.append(label)
            if index == fail_at and not after:
                raise OSError('simulated interrupted publication')
            result = operation(*args)
            if index == fail_at and after:
                raise OSError('simulated interrupted publication')
            return result

        def remove(path, *args, **kwargs):
            if path == update.ESP / 'update.json':
                return step('unlink:update.json', lambda: unlink(path, *args, **kwargs))
            return unlink(path, *args, **kwargs)

        with patch.object(refresh, 'atomic', side_effect=lambda path, data:
                          step('atomic:' + path.name, atomic, path, data)), \
                patch.object(update, 'command', side_effect=lambda argv:
                             step('command:' + str(argv[0]), command, argv)), \
                patch.object(Path, 'unlink', remove):
            function()
        return events

    def test_every_pretransaction_publication_fault_prevents_success_and_can_retry(self):
        update.prepare(self.identity)
        self.snapshot()
        steps = self.publication_steps(lambda: update.mark(self.identity))
        self.assertIn('command:snapper', steps)
        self.assertIn('atomic:grub.cfg', steps)
        self.assertEqual(steps[-1], 'atomic:update.env')
        for index, label in enumerate(steps):
            for after in (False, True):
                with self.subTest(step=label, index=index, after=after):
                    fixture = UpdateBoot()
                    fixture.setUp()
                    try:
                        update.prepare(fixture.identity)
                        fixture.snapshot()
                        with self.assertRaises(OSError):
                            fixture.publication_steps(lambda: update.mark(fixture.identity), index, after)
                        # Failed preparation aborts package extraction; retry repairs the state.
                        # Repeating its receipt may finish the same preparation.
                        update.mark(fixture.identity)
                        state = update.record()
                        self.assertEqual(state['snapshot'], '10')
                        self.assertTrue(update.matches(state, update.environment()))
                    finally:
                        fixture.doCleanups()

    def test_acceptance_clears_selector_before_record_or_unpin(self):
        value = self.transaction()
        self.boot_id = NEW
        self.env(emaki_attempt='1', next_entry='emaki-auto-recovery')
        atomic = refresh.atomic

        def interrupted(path, data):
            atomic(path, data)
            if path.name == 'update.env':
                raise OSError('simulated power loss after acceptance commit')

        with patch.object(refresh, 'atomic', side_effect=interrupted), self.assertRaises(OSError):
            update.accept(value, self.identity)
        self.assertEqual(update.record(), value)
        self.assertNotIn('next_entry', update.environment())
        self.assertEqual(self.pins, [('10', '')])
        # Interrupted acceptance must complete without offering recovery again.
        self.assertTrue(self.monitor(lambda: None))
        self.assertIsNone(update.record())
        self.assertNotIn('next_entry', update.environment())
        self.assertEqual(self.pins[-1], ('10', 'number'))

    def test_failed_acceptance_environment_write_preserves_recovery(self):
        value = self.transaction()
        self.boot_id = NEW
        before = self.env(emaki_attempt='1', next_entry='emaki-auto-recovery')
        with patch.object(update, 'write_environment', side_effect=OSError('full disk')), \
                self.assertRaises(OSError):
            update.accept(value, self.identity)
        self.assertEqual(update.record(), value)
        self.assertEqual(update.environment(), before)
        self.assertEqual(self.pins, [('10', '')])

    def test_every_acceptance_publication_fault_keeps_a_recoverable_state(self):
        value = self.transaction()
        self.boot_id = NEW
        self.env(emaki_attempt='1', next_entry='emaki-auto-recovery')
        steps = self.publication_steps(lambda: update.accept(value, self.identity))
        self.assertEqual(steps, ['atomic:update.env', 'command:snapper',
                                 'unlink:update.json', 'atomic:update.env'])
        for index, label in enumerate(steps):
            for after in (False, True):
                with self.subTest(step=label, index=index, after=after):
                    fixture = UpdateBoot()
                    fixture.setUp()
                    try:
                        candidate = fixture.transaction()
                        fixture.boot_id = NEW
                        fixture.env(emaki_attempt='1', next_entry='emaki-auto-recovery')
                        with self.assertRaises(OSError):
                            fixture.publication_steps(lambda: update.accept(candidate, fixture.identity),
                                                      index, after)
                        if index == 0 and not after:
                            self.assertEqual(update.environment()['next_entry'], 'emaki-auto-recovery')
                            self.assertEqual(update.record(), candidate)
                        else:
                            self.assertNotIn('next_entry', update.environment())
                            if update.record() is not None:
                                self.assertTrue(fixture.monitor(lambda: None))
                            self.assertIsNone(update.record())
                    finally:
                        fixture.doCleanups()

    def test_receipt_and_retained_boundary_mutants_change_the_oracle(self):
        update.prepare(self.identity)
        self.snapshot()
        baseline = json.loads(update.BASELINE.read_text())
        baseline['parent'][1] = '999'
        update.BASELINE.write_text(json.dumps(baseline))
        source = inspect.getsource(update.current_snapshot)
        needle = "baseline['parent'] == transaction_identity()"
        self.assertIn(needle, source)
        namespace = dict(update.__dict__)
        exec(compile(source.replace(needle, 'True'), '<receipt mutation>', 'exec'), namespace)
        with self.assertRaises(refresh.Refuse):
            update.current_snapshot(self.identity)
        self.assertEqual(namespace['current_snapshot'](self.identity)[0], '10')

        baseline['parent'] = self.parent
        update.BASELINE.write_text(json.dumps(baseline))
        update.mark(self.identity)
        first = update.record()
        self.boot_id = NEW
        self.parent = [124, '789', NEW]
        self.env(emaki_attempt='2')
        update.prepare(self.identity)
        self.snapshot('11')
        source = inspect.getsource(update.mark)
        needle = "previous['armed_boot'] = refresh.current_boot_id()"
        namespace = dict(update.__dict__)
        exec(compile(source.replace(needle, needle + "\n        previous['snapshot'] = number"),
                     '<snapshot boundary mutation>', 'exec'), namespace)
        namespace['mark'](self.identity)
        self.assertNotEqual(update.record()['snapshot'], first['snapshot'])

    def test_acceptance_decision_mutants_are_rejected_by_boundary_examples(self):
        value = self.transaction()
        env = self.env(emaki_attempt='1')
        source = (ROOT / 'grub/emaki_boot/update.py').read_text()
        variants = (
            ("value['armed_boot'] != boot_id", 'True', (value, env, OLD, FS, '')),
            ("value['root_uuid'] == root_uuid", 'True', (value, env, NEW, NEW, '')),
            ("env['emaki_attempt'] in ('1', '2')", 'True',
             (value, {**env, 'emaki_attempt': '0'}, NEW, FS, '')),
            ("env.get('emaki_update') == value['transaction']", 'True',
             (value, {**env, 'emaki_update': 'b' * 32}, NEW, FS, '')),
        )
        for needle, replacement, args in variants:
            with self.subTest(decision=needle):
                self.assertEqual(source.count(needle), 1)
                mutated = types.ModuleType('emaki_boot.update_mutation')
                mutated.__package__ = 'emaki_boot'
                exec(compile(source.replace(needle, replacement, 1), str(ROOT / 'grub/emaki_boot/update.py'), 'exec'),
                     mutated.__dict__)
                self.assertFalse(update.may_accept(*args))
                self.assertTrue(mutated.may_accept(*args), 'boundary must detect removed decision')

    def test_stability_mutants_fail_the_elapsed_time_oracle(self):
        source = inspect.getsource(update.monitor)
        cases = (
            ('tick - since >= STABLE', 'True', lambda tick: 'ready', 15),
            ('evidence != seen', 'since is None',
             lambda tick: 'first' if tick < 14 else 'replacement', 29),
        )
        for needle, replacement, evidence, expected in cases:
            with self.subTest(decision=needle):
                fixture = UpdateBoot()
                fixture.setUp()
                try:
                    fixture.transaction()
                    fixture.boot_id = NEW
                    namespace = dict(update.__dict__)
                    self.assertEqual(source.count(needle), 1)
                    exec(compile(source.replace(needle, replacement), '<acceptance mutation>', 'exec'), namespace)
                    self.assertTrue(namespace['monitor'](fixture.identity, lambda: evidence(fixture.clock),
                                                         fixture.now, fixture.wait))
                    self.assertNotEqual(fixture.clock, expected, 'elapsed-time oracle must reject this mutation')
                finally:
                    fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
