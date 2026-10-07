#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""VM socket commands and clients work with long evidence paths, without starting a VM."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests/vm'))
import socket_runtime


class VMRuntime(unittest.TestCase):
    def setUp(self):
        evidence = ROOT / '.cache/evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        self.owner = tempfile.TemporaryDirectory(prefix='vm-runtime-', dir=evidence)
        self.addCleanup(self.owner.cleanup)
        self.directory = Path(self.owner.name) / ('long-worktree-' * 12)
        self.directory.mkdir()
        self.addCleanup(socket_runtime.cleanup, self.directory)

    def test_private_runtime_and_links_are_short_and_cleaned(self):
        self.assertGreater(len(os.fsencode(self.directory / 'mon.sock')), 108)
        runtime = socket_runtime.prepare(self.directory)
        self.assertEqual(runtime.parent, Path('/tmp'))
        self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
        for name in socket_runtime.NAMES:
            self.assertTrue((self.directory / name).is_symlink())
            self.assertEqual((self.directory / name).resolve(), runtime / name)
            self.assertLess(len(os.fsencode(runtime / name)), 108)
        socket_runtime.cleanup(self.directory)
        self.assertFalse(runtime.exists())
        self.assertTrue(self.directory.is_dir())

    def test_existing_socket_name_is_never_deleted(self):
        existing = self.directory / 'mon.sock'
        existing.write_text('preserve')
        with self.assertRaises(FileExistsError):
            socket_runtime.prepare(self.directory)
        self.assertEqual(existing.read_text(), 'preserve')

    def test_missing_runtime_removes_stale_links(self):
        runtime = socket_runtime.prepare(self.directory)
        (runtime / 'owner').unlink()
        runtime.rmdir()
        socket_runtime.cleanup(self.directory)
        self.assertFalse((self.directory / '.socket-runtime').is_symlink())
        self.assertFalse(any((self.directory / name).is_symlink() for name in socket_runtime.NAMES))

    def test_qemu_launcher_passes_short_socket_paths(self):
        binary = Path(self.owner.name) / 'bin'
        binary.mkdir()
        qemu = binary / 'qemu-system-x86_64'
        qemu.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
        qemu.chmod(0o700)
        env = dict(os.environ, PATH=str(binary) + ':' + os.environ['PATH'],
                   EMAKI_VM_DIR=str(self.directory), TMPDIR=str(self.directory))
        result = subprocess.run(['bash', str(ROOT / 'tests/vm/run.sh')], env=env,
                                text=True, capture_output=True, check=True)
        arguments = result.stdout.splitlines()
        runtime = (self.directory / '.socket-runtime').resolve()
        self.assertIn('unix:' + str(runtime / 'mon.sock') + ',server,nowait', arguments)
        self.assertIn('unix:' + str(runtime / 'vnc.sock'), arguments)
        self.assertIn('file:' + str(self.directory / 'serial.log'), arguments)

    def test_launcher_stops_when_runtime_preparation_fails(self):
        existing = self.directory / 'mon.sock'
        existing.write_text('preserve')
        binary = Path(self.owner.name) / 'bin'
        binary.mkdir()
        qemu = binary / 'qemu-system-x86_64'
        marker = Path(self.owner.name) / 'launched'
        qemu.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n')
        qemu.chmod(0o700)
        env = dict(os.environ, PATH=str(binary) + ':' + os.environ['PATH'], EMAKI_VM_DIR=str(self.directory))
        result = subprocess.run(['bash', str(ROOT / 'tests/vm/run.sh')], env=env, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())
        self.assertEqual(existing.read_text(), 'preserve')

    def test_monitor_client_resolves_socket_link(self):
        runtime = socket_runtime.prepare(self.directory)
        spec = importlib.util.spec_from_file_location('runtime_monitor', ROOT / 'tests/vm/iso-monitor.py')
        monitor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(monitor)
        connection = MagicMock()
        connection.recv.return_value = b'(qemu) '
        with patch.object(monitor.socket, 'socket') as factory:
            factory.return_value.__enter__.return_value = connection
            monitor.command(self.directory, 'info status')
        connection.connect.assert_called_once_with(str(runtime / 'mon.sock'))


if __name__ == '__main__':
    unittest.main()
