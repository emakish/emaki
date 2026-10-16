#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fallback heartbeat generation and isolated real config-parser regressions."""
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from emaki_lock_fallback import FallbackHeartbeat


class FallbackHeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='emaki-lock-heartbeat-test-', dir='/tmp')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.original = self.root / 'hyprlock.conf'
        self.original.write_text('general {\n    hide_cursor = true\n}\n', encoding='utf-8')

    def heartbeat(self, source=None):
        heartbeat = FallbackHeartbeat(source or self.original)
        self.addCleanup(heartbeat.close)
        return heartbeat

    @staticmethod
    def command(heartbeat):
        prefix = '    text = cmd[update:500] '
        return next(line[len(prefix):] for line in heartbeat.config_path.read_text().splitlines()
                    if line.startswith(prefix))

    def test_only_command_progress_counts_and_generations_are_isolated(self):
        first = self.heartbeat()
        second = self.heartbeat()
        self.assertNotEqual(first.config_path.parent, second.config_path.parent)
        self.assertFalse(first.progressed())
        self.assertFalse(second.progressed())
        for _ in range(20):
            result = subprocess.run(['sh', '-c', self.command(first)], capture_output=True, check=True)
            self.assertEqual(result.stdout, b'.')
            self.assertEqual(result.stderr, b'')
            if first.progressed():
                break
            time.sleep(0.01)
        else:
            self.fail('the label command did not change the heartbeat timestamp')
        self.assertFalse(first.progressed())
        self.assertFalse(second.progressed())

    def test_private_files_and_idempotent_cleanup(self):
        heartbeat = self.heartbeat()
        directory = heartbeat.config_path.parent
        descriptor, = heartbeat.pass_fds
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for path in directory.iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(os.path.samefile(f'/proc/self/fd/{descriptor}', self.original))
        original = self.original.read_bytes()
        heartbeat.close()
        heartbeat.close()
        self.assertFalse(directory.exists())
        self.assertFalse(heartbeat.progressed())
        self.assertEqual(heartbeat.pass_fds, ())
        self.assertEqual(self.original.read_bytes(), original)
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_failed_constructor_removes_private_directory(self):
        from unittest.mock import patch
        real_directory = tempfile.TemporaryDirectory
        created = []

        def remember(*args, **kwargs):
            directory = real_directory(*args, **kwargs)
            created.append(Path(directory.name))
            return directory

        with patch('emaki_lock_fallback.tempfile.TemporaryDirectory', side_effect=remember):
            with self.assertRaises(FileNotFoundError):
                FallbackHeartbeat(self.root / 'absent.conf')
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].exists())

    def test_special_source_is_rejected_without_waiting_for_a_writer(self):
        fifo = self.root / 'config-fifo'
        os.mkfifo(fifo, 0o600)
        with self.assertRaisesRegex(OSError, 'not a regular file'):
            FallbackHeartbeat(fifo)

    def parse(self, heartbeat):
        env = dict(os.environ, HOME=str(self.root), XDG_CONFIG_HOME=str(self.root),
                   XDG_RUNTIME_DIR=str(self.root), WAYLAND_DISPLAY=str(self.root / 'no-display'),
                   DBUS_SESSION_BUS_ADDRESS=f'unix:path={self.root}/no-session-bus',
                   DBUS_SYSTEM_BUS_ADDRESS=f'unix:path={self.root}/no-system-bus')
        env.pop('WAYLAND_SOCKET', None)
        env.pop('WAYLAND_DEBUG', None)

        result = subprocess.run(
            ['hyprlock', '--config', str(heartbeat.config_path), '--display', env['WAYLAND_DISPLAY']],
            env=env, pass_fds=heartbeat.pass_fds, capture_output=True, timeout=5,
            preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)))
        self.assertNotEqual(result.returncode, 0)
        output = result.stdout + result.stderr
        self.assertIn(b"Couldn't connect to a wayland compositor", output)
        return output

    @unittest.skipUnless(shutil.which('hyprlock'), 'hyprlock unavailable: real parser not checked')
    def test_real_parser_preserves_special_filename_and_relative_source(self):
        original = self.root / 'original $name#[x]*?.conf'
        included = self.root / 'relative.conf'
        original.write_text('source = relative.conf\nauth {\n    fingerprint {\n'
                            '        enabled = false\n    }\n}\n', encoding='utf-8')
        included.write_text('general {\n    hide_cursor = true\n}\n', encoding='utf-8')
        original_bytes = original.read_bytes()
        heartbeat = self.heartbeat(original)
        output = self.parse(heartbeat)
        self.assertNotIn(b'Config has errors', output)
        self.assertNotIn(b'source=', output)
        # Prove the nested source was parsed, rather than merely avoiding an error.
        included.write_text('source = x\n', encoding='utf-8')
        output = self.parse(heartbeat)
        self.assertIn(b'source= path garbage', output)
        self.assertEqual(original.read_bytes(), original_bytes)
        self.assertFalse(heartbeat.progressed())

    @unittest.skipUnless(shutil.which('hyprlock'), 'hyprlock unavailable: real parser not checked')
    def test_real_parser_sources_special_directory_without_path_interpretation(self):
        special = self.root / 'space $variable # [bracket] ? * \\ newline\n'
        special.mkdir()
        original = special / 'original $name#[x]*?.conf'
        original.write_text('general {\n    hide_cursor = true\n}\n', encoding='utf-8')
        heartbeat = self.heartbeat(original)
        output = self.parse(heartbeat)
        self.assertNotIn(b'Config has errors', output)
        self.assertNotIn(b'source=', output)
        # A nested relative source under a glob-containing directory is already
        # unsupported by the original parser; this wrapper preserves that behavior.
        original.write_text('source = x\n', encoding='utf-8')
        self.assertIn(b'source= path garbage', self.parse(heartbeat))


if __name__ == '__main__':
    unittest.main()
