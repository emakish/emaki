#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Package lock recovery advice without touching the running package database."""
from contextlib import ExitStack
import importlib.machinery
import importlib.util
import os
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'update-manager'))
import emaki_package_lock as locks


class Advice(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.proc = self.root / 'proc'
        self.proc.mkdir()
        self.lock = self.root / 'db.lck'
        self.lock.write_text('')

    def test_previous_boot_lock_has_manual_recovery_and_is_preserved(self):
        os.utime(self.lock, (1, 1))
        self.assertEqual(locks.lock_message(self.lock, self.proc), locks.ABANDONED.format(path=json.dumps(str(self.lock))))
        self.assertIn('An administrator must check that none is running before removing',
                      locks.ABANDONED.format(path=json.dumps(str(self.lock))))
        self.assertTrue(self.lock.exists())

    def test_creator_gone_does_not_wait_forever(self):
        self.lock.write_text('99999999\n')
        self.assertEqual(locks.lock_message(self.lock, self.proc), locks.ABANDONED.format(path=json.dumps(str(self.lock))))
        self.assertEqual(self.lock.read_text(), '99999999\n')

    def test_holder_wins_even_for_old_lock_or_reused_pid(self):
        os.utime(self.lock, (1, 1))
        descriptors = self.proc / '123' / 'fd'
        descriptors.mkdir(parents=True)
        (descriptors / '4').symlink_to(self.lock)
        self.assertEqual(locks.lock_message(self.lock, self.proc), locks.LIVE)
        self.assertIn('Wait for it to finish', locks.LIVE)

    def test_unrelated_query_or_reused_pid_is_not_a_holder(self):
        descriptors = self.proc / '123' / 'fd'
        descriptors.mkdir(parents=True)
        other = self.root / 'private-db.lck'
        other.touch()
        (descriptors / '4').symlink_to(other)
        self.lock.write_text('123\n')
        self.assertEqual(locks.lock_message(self.lock, self.proc), locks.ABANDONED.format(path=json.dumps(str(self.lock))))

    def test_permission_failure_never_claims_abandoned(self):
        self.assertEqual(locks.UNKNOWN,
                         'Another program may be installing or updating software. '
                         'Do not remove the lock while software changes may be running. '
                         'Restart the computer, then try again.')
        original = Path.iterdir
        def denied(path):
            if path == self.proc:
                raise PermissionError('fixture')
            return original(path)
        with patch.object(Path, 'iterdir', denied):
            self.assertEqual(locks.lock_message(self.lock, self.proc), locks.UNKNOWN)

    def test_replacement_during_scan_never_claims_abandoned(self):
        original = Path.iterdir
        def replace(path):
            if path == self.proc:
                replacement = self.root / 'replacement'
                replacement.write_text('other')
                replacement.replace(self.lock)
            return original(path)
        with patch.object(Path, 'iterdir', replace):
            self.assertEqual(locks.lock_message(self.lock, self.proc), locks.UNKNOWN)
        self.assertEqual(self.lock.read_text(), 'other')

    def test_nonregular_lock_never_opened(self):
        self.lock.unlink()
        os.mkfifo(self.lock)
        self.assertEqual(locks.lock_message(self.lock, self.proc), locks.UNKNOWN)
        self.lock.unlink()
        self.lock.symlink_to(self.root / 'absent')
        self.assertEqual(locks.lock_message(self.lock, self.proc), locks.UNKNOWN)

    def test_custom_database_path_is_quoted_in_recovery_advice(self):
        custom = self.root / 'custom db; echo unsafe\nname.lck'
        custom.touch()
        message = locks.lock_message(custom, self.proc)
        self.assertIn(json.dumps(str(custom)), message)
        self.assertNotIn('/var/lib/pacman/db.lck', message)
        self.assertNotIn('\n', message)

    def test_unprivileged_real_proc_cannot_prove_abandonment(self):
        original = Path.iterdir
        def empty_proc(path):
            return iter(()) if path == Path('/proc') else original(path)
        with patch.object(Path, 'iterdir', empty_proc), patch.object(locks.os, 'geteuid', return_value=1000):
            self.assertEqual(locks.lock_message(self.lock), locks.UNKNOWN)

    def test_scan_deadline_is_conservative(self):
        (self.proc / '123' / 'fd').mkdir(parents=True)
        with patch.object(locks.time, 'monotonic', side_effect=[0, 3]):
            self.assertEqual(locks.lock_message(self.lock, self.proc), locks.UNKNOWN)


class Creators(unittest.TestCase):
    def test_all_creators_hold_descriptor_until_cleanup(self):
        import storage_arch
        sys.path[:0] = [str(ROOT / name) for name in ('installer', 'grub', 'upkeep')]
        from emaki_installer import boot, secureboot
        sys.modules['emaki_boot.boot'] = boot
        sys.modules['emaki_boot.secureboot'] = secureboot
        from emaki_boot import refresh
        loader = importlib.machinery.SourceFileLoader('rollback', str(ROOT / 'scripts/emaki-rollback'))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        rollback = importlib.util.module_from_spec(spec)
        loader.exec_module(rollback)
        boot_id = '12345678-1234-5678-9abc-123456789abc'

        for creator in ('storage', 'rollback', 'refresh'):
            for failure in (False, True):
                with self.subTest(creator=creator, failure=failure), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    database = root / 'var/lib/pacman'
                    database.mkdir(parents=True)
                    lock = database / 'db.lck'
                    proc = root / 'proc'
                    (proc / '1').mkdir(parents=True)
                    # Scan only this process, using its real descriptors.
                    (proc / '1/fd').symlink_to('/proc/self/fd', target_is_directory=True)
                    opened = []
                    unlinked = []
                    real_open, real_unlink = os.open, os.unlink

                    def track_open(path, flags, *args, **kwargs):
                        descriptor = real_open(path, flags, *args, **kwargs)
                        if Path(path).name == 'db.lck' and flags & os.O_CREAT:
                            opened.append(descriptor)
                        return descriptor

                    def track_unlink(path, *args, **kwargs):
                        if Path(path).name == 'db.lck':
                            # The descriptor must remain open through the final unlink.
                            info = os.fstat(opened[0])
                            self.assertEqual((info.st_dev, info.st_ino),
                                             (lock.stat().st_dev, lock.stat().st_ino))
                            unlinked.append(path)
                        return real_unlink(path, *args, **kwargs)

                    with ExitStack() as stack:
                        stack.enter_context(patch.object(os, 'open', track_open))
                        stack.enter_context(patch.object(os, 'unlink', track_unlink))
                        if creator == 'storage':
                            stack.enter_context(patch.object(storage_arch, 'DATABASE', database))
                            stack.enter_context(patch.object(storage_arch, 'LOCKS', ()))
                            stack.enter_context(patch.object(storage_arch, 'configuration'))
                            guard = storage_arch.package_guard()
                        elif creator == 'rollback':
                            # Redirect only the run lock; package operations use the temp root.
                            stack.enter_context(patch.object(
                                rollback, 'open', lambda *args: open(root / 'refresh.lock', 'a'), create=True))
                            guard = rollback.package_guard(root, boot_id)
                        else:
                            stack.enter_context(patch.object(refresh, 'Path', lambda value: lock))
                            stack.enter_context(patch.object(refresh, 'current_boot_id', return_value=boot_id))
                            guard = refresh.pacman_lock()
                        try:
                            with guard:
                                self.assertEqual(len(opened), 1)
                                self.assertEqual(locks.lock_message(lock, proc), locks.LIVE)
                                self.assertEqual(os.fstat(opened[0]).st_ino, lock.stat().st_ino)
                                if failure:
                                    raise RuntimeError('fixture transaction failed')
                        except RuntimeError as error:
                            self.assertTrue(failure)
                            self.assertEqual(str(error), 'fixture transaction failed')
                        self.assertFalse(lock.exists())
                        self.assertEqual(len(unlinked), 1)
                        with self.assertRaises(OSError):
                            os.fstat(opened[0])


if __name__ == '__main__':
    unittest.main()
