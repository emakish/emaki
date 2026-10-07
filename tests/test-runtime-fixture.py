#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Runtime socket paths stay short even when evidence and TMPDIR paths are long."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from runtime_fixture import runtime_path, short_runtime


class RuntimeFixture(unittest.TestCase):
    def test_long_temp_environment_does_not_change_runtime_parent(self):
        with tempfile.TemporaryDirectory(prefix='em-test-', dir='/tmp') as work:
            long_path = Path(work) / ('long-worktree-' * 12)
            long_path.mkdir()
            old_socket = long_path / 'profile/runtime/quickshell/by-id/fixture/ipc.sock'
            self.assertGreaterEqual(len(os.fsencode(old_socket)), 108)
            with patch.dict(os.environ, TMPDIR=str(long_path), TMP=str(long_path), TEMP=str(long_path)), \
                    patch.object(tempfile, 'tempdir', str(long_path)):
                with short_runtime() as first, short_runtime() as second:
                    directory = Path(first)
                    self.assertEqual(directory.parent, Path('/tmp'))
                    self.assertNotEqual(first, second)
                    self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
                    socket = directory / 'quickshell/by-id/fixture/ipc.sock'
                    self.assertLess(len(os.fsencode(socket)), 108)
                self.assertFalse(directory.exists())
                self.assertFalse(Path(second).exists())

    def test_profile_mapping_is_stable_and_private(self):
        first = runtime_path('fixture-one')
        self.assertEqual(first, runtime_path(Path('fixture-one')))
        self.assertNotEqual(first, runtime_path('fixture-two'))
        self.assertEqual(first.stat().st_mode & 0o777, 0o700)

    def test_cached_runtime_is_removed_at_process_exit(self):
        result = subprocess.run([sys.executable, '-c',
                                 'from runtime_fixture import runtime_path; print(runtime_path("child"))'],
                                cwd=Path(__file__).parent, text=True, capture_output=True, check=True)
        self.assertFalse(Path(result.stdout.strip()).exists())

    def test_sigterm_removes_owned_runtimes_only(self):
        parent = runtime_path('parent')
        result = subprocess.run([sys.executable, '-c', '''
import os, signal
from runtime_fixture import runtime_path
for profile in ('one', 'two'):
    path = runtime_path(profile)
    (path / 'marker').write_text('owned')
    print(path, flush=True)
os.kill(os.getpid(), signal.SIGTERM)
'''], cwd=Path(__file__).parent, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, -signal.SIGTERM, result.stderr)
        paths = result.stdout.splitlines()
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(not Path(path).exists() for path in paths))
        self.assertTrue(parent.is_dir())

    def test_sigterm_in_fork_does_not_remove_parent_runtime(self):
        parent = runtime_path('fork-parent')
        pid = os.fork()
        if pid == 0:
            os.kill(os.getpid(), signal.SIGTERM)
            os._exit(1)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), -signal.SIGTERM)
        self.assertTrue(parent.is_dir())


if __name__ == '__main__':
    unittest.main()
