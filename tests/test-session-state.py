#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise transaction liveness and root identity without touching system state."""
import importlib.util
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('session_arch', ROOT / 'scripts/emaki_session_arch.py')
arch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arch)
# The Arch transaction reader and the session-neutral identity module it builds on.
state = arch.session


class SessionState(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='session-state-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.proc = self.base / 'proc'
        self.proc.mkdir()
        boot = self.proc / 'sys/kernel/random/boot_id'
        boot.parent.mkdir(parents=True)
        boot.write_text('11111111-1111-4111-8111-111111111111\n')
        self.root = self.base / 'root'
        self.root.mkdir()
        self.lock = self.base / 'db.lck'
        for module, name, value in [(state, 'PROC', self.proc), (state, 'ROOT', self.root), (arch, 'LOCK', self.lock)]:
            mocked = patch.object(module, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def process(self, pid=201, name='pacman', args=('pacman', '-Syu'), status='S', start='101'):
        directory = self.proc / str(pid)
        directory.mkdir(exist_ok=True)
        # Linux stat fields 3 through 22, including a command with parentheses.
        fields = [status] + ['0'] * 18 + [start]
        (directory / 'stat').write_text(f'{pid} (worker (nested)) ' + ' '.join(fields))
        (directory / 'comm').write_text(name + '\n')
        (directory / 'cmdline').write_bytes(b'\0'.join(os.fsencode(arg) for arg in args) + b'\0')
        return directory

    def child(self, hold=False):
        code = ('import sys; '
                'handle = open(sys.argv[1]) if sys.argv[2] == "hold" else None; '
                'print("ready", flush=True); sys.stdin.read()')
        child = subprocess.Popen([sys.executable, '-IB', '-c', code, str(self.lock),
                                  'hold' if hold else 'command', 'emaki-boot-refresh'],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        def stop():
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
            child.stdin.close()
            child.stdout.close()
        self.addCleanup(stop)
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(timeout=5), 'Child did not become ready')
        self.assertEqual(child.stdout.readline().strip(), 'ready')
        (self.proc / str(child.pid)).symlink_to(Path('/proc') / str(child.pid), target_is_directory=True)
        return child

    def test_stale_database_lock_has_no_holder(self):
        self.lock.write_text('')
        self.assertFalse(arch.transaction_running())

    def test_live_descriptor_holder_then_stale_lock(self):
        self.lock.write_text('')
        child = self.child(hold=True)
        self.assertTrue(arch.transaction_running())
        child.terminate()
        child.wait(timeout=5)
        self.assertTrue(self.lock.exists())
        self.assertFalse(arch.transaction_running())

    def test_old_boot_token_ignores_live_holder(self):
        self.lock.write_text('emaki-boot-refresh:22222222-2222-4222-8222-222222222222')
        self.child(hold=True)
        self.assertFalse(arch.transaction_running())

    def test_current_boot_token_and_live_command(self):
        self.lock.write_text('emaki-boot-refresh:11111111-1111-4111-8111-111111111111')
        child = self.child()
        self.assertTrue(arch.transaction_running())
        child.terminate()
        child.wait(timeout=5)
        self.assertFalse(arch.transaction_running())

    def test_readonly_snapshot_does_not_inspect_processes_or_wait(self):
        self.lock.write_text('emaki-boot-refresh:11111111-1111-4111-8111-111111111111')
        self.child(hold=True)
        stat = type('Stat', (), {'f_flag': os.ST_RDONLY})()
        with patch.object(state.os, 'statvfs', return_value=stat), \
                patch.object(Path, 'iterdir', side_effect=AssertionError('Process scan on read-only root')):
            self.assertFalse(arch.transaction_running())

    def test_root_owned_descriptor_permission_falls_back_to_pacman_arguments(self):
        self.lock.write_text('')
        process = self.process()
        original = Path.iterdir
        def restricted(path):
            if path == process / 'fd':
                raise PermissionError('Root-owned descriptors')
            return original(path)
        with patch.object(Path, 'iterdir', restricted):
            self.assertTrue(arch.transaction_running())

    def test_readonly_pacman_query_is_not_a_transaction(self):
        self.lock.write_text('')
        for arguments in (('-Q',), ('-Qi',), ('-Ss', 'niri'), ('-Si', 'niri'),
                          ('-Sl',), ('-Sg',), ('-S', '--search', 'niri'),
                          ('--sync', '--info', 'niri'), ('-Sp', 'niri'),
                          ('-Rp', 'niri'), ('-Up', 'package'), ('-Sw', 'niri'),
                          ('-Sc',), ('--sync', '--print-format=%n', 'niri'),
                          ('-Q', '--', '-S'), ('--help', '-S')):
            with self.subTest(arguments=arguments):
                self.process(args=('pacman', *arguments))
                self.assertFalse(arch.transaction_running())

    def test_foreign_pacman_paths_are_not_this_system(self):
        for arguments in (('-Syu', '--root', '/target'), ('-Syu', '--root=/target'),
                          ('-Syu', '--sysroot', '/target'), ('--sysroot=/target', '-Syu'),
                          ('-U', '--dbpath', '/target/var/lib/pacman', 'package'),
                          ('-R', '--dbpath=/target/var/lib/pacman', 'niri'),
                          ('-Syr/target',), ('-r', '/target', '-Syu'),
                          ('-Syb/target/db',), ('-b', '/target/db', '-Syu')):
            with self.subTest(arguments=arguments):
                self.process(args=('pacman', *arguments))
                self.assertFalse(arch.transaction_running())

    def test_installed_package_operations_include_split_and_grouped_flags(self):
        for arguments in (('-Syu',), ('-yuS',), ('--sync', '--sysupgrade'),
                          ('-Rns', 'niri'), ('--remove', 'niri'), ('-U', 'package'),
                          ('--upgrade', 'package'), ('-S', '--root=/', 'niri'),
                          ('-S', '-b/var/lib/pacman/', '--sysroot', '/', 'niri'),
                          ('-S', '--ignore', '-Ss', 'niri')):
            with self.subTest(arguments=arguments):
                self.process(args=('pacman', *arguments))
                self.assertTrue(arch.transaction_running())

    def test_chroot_process_does_not_hold_the_host_notice(self):
        process = self.process()
        (process / 'root').symlink_to(self.base)
        self.assertFalse(arch.transaction_running())

    def test_only_exact_refresh_tokens_can_override_a_live_holder(self):
        self.lock.write_text('')
        self.child(hold=True)
        for token in ('emaki-boot-refresh:old',
                      'emaki-boot-refresh:22222222-2222-4222-8222-222222222222\n',
                      'emaki-boot-refresh:22222222-2222-4222-8222-222222222222:extra'):
            with self.subTest(token=token):
                self.lock.write_text(token)
                self.assertTrue(arch.transaction_running())

    def test_zombie_and_dead_processes_are_not_transactions(self):
        self.lock.write_text('')
        for status in ('Z', 'X'):
            with self.subTest(status=status):
                self.process(status=status)
                self.assertIsNone(state.process_start(201))
                self.assertFalse(arch.transaction_running())

    def test_pid_reuse_changes_start_identity(self):
        self.process(start='123')
        original = state.process_start(201)
        self.process(start='456')
        self.assertEqual(original, '123')
        self.assertEqual(state.process_start(201), '456')
        self.assertNotEqual(state.process_start(201), original)

    def test_missing_or_malformed_process_is_not_live(self):
        self.assertIsNone(state.process_start(201))
        directory = self.process()
        (directory / 'stat').write_text('incomplete')
        self.assertIsNone(state.process_start(201))

    def init_root(self, path):
        (self.proc / '1').mkdir()
        (self.proc / '1/root').symlink_to(path, target_is_directory=True)

    def test_matching_root_with_negative_chroot_detection(self):
        self.init_root(self.root)
        with patch.object(state.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as detect:
            self.assertFalse(state.foreign_root())
        self.assertEqual(detect.call_args.args[0], ['systemd-detect-virt', '--chroot'])

    def test_foreign_root_inode_short_circuits_detection(self):
        foreign = self.base / 'foreign'
        foreign.mkdir()
        self.init_root(foreign)
        with patch.object(state.subprocess, 'run', side_effect=AssertionError('Unexpected detector')):
            self.assertTrue(state.foreign_root())

    def test_chroot_detector_and_detector_errors_fail_closed(self):
        self.init_root(self.root)
        for code in (0, 2):
            with self.subTest(code=code), patch.object(state.subprocess, 'run',
                                                       return_value=subprocess.CompletedProcess([], code)):
                self.assertTrue(state.foreign_root())
        for error in (OSError('Unavailable'), subprocess.TimeoutExpired('detect', 2)):
            with self.subTest(error=type(error).__name__), patch.object(state.subprocess, 'run', side_effect=error):
                self.assertTrue(state.foreign_root())

    def test_missing_init_root_fails_closed(self):
        self.assertTrue(state.foreign_root())

    def test_markers_require_current_root_and_boot(self):
        marker = self.base / 'marker'
        own = {'root': state.root_id(self.root), 'boot': '11111111-1111-4111-8111-111111111111', 'transaction': 'update-one'}
        marker.write_text(json.dumps(own))
        self.assertEqual(state.marker_value(marker), 'update-one')
        for changes in ({'boot': 'old-boot'}, {'root': [0, 0]}):
            with self.subTest(changes=changes):
                marker.write_text(json.dumps(own | changes))
                self.assertIsNone(state.marker_value(marker))
        for malformed in ('not json', '{}', 'null'):
            with self.subTest(malformed=malformed):
                marker.write_text(malformed)
                self.assertIsNone(state.marker_value(marker))
        marker.unlink()
        self.assertIsNone(state.marker_value(marker))


if __name__ == '__main__':
    unittest.main()
