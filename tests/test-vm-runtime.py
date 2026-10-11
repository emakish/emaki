#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""VM socket commands and clients work with long evidence paths, without starting a VM."""
import importlib.util
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
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

    def test_monitor_accepts_shutdown_disconnect_but_not_read_disconnect(self):
        spec = importlib.util.spec_from_file_location('shutdown_monitor', ROOT / 'tests/vm/iso-monitor.py')
        monitor = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(monitor)
        for command in ('system_powerdown', 'quit', 'info status'):
            with self.subTest(command=command), patch.object(monitor.socket, 'socket') as factory:
                connection = factory.return_value.__enter__.return_value
                connection.recv.side_effect = [b'(qemu) ', b'']
                if command == 'info status':
                    with self.assertRaisesRegex(RuntimeError, 'disconnected'):
                        monitor.command(self.directory, command)
                else:
                    self.assertEqual(monitor.command(self.directory, command), '')

    def stop_fixture(self, *, owned=True, reap=True, launch_delay=False):
        binary = Path(self.owner.name) / 'stop-bin'
        binary.mkdir()
        qemu = binary / 'qemu-system-x86_64'
        shutil.copy2(shutil.which('sleep'), qemu)
        with (self.directory / 'run.lock').open('w') as lock:
            descriptors = (lock.fileno(),) if owned else ()
            if owned:
                fcntl.flock(lock, fcntl.LOCK_EX)
            command = [str(qemu), '60']
            if launch_delay:
                command = ['bash', '-c', 'sleep 0.3; exec "$@"', 'launcher', *command]
            process = subprocess.Popen(command, pass_fds=descriptors)

        def cleanup():
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)

        self.addCleanup(cleanup)
        if reap:
            threading.Thread(target=process.wait, daemon=True).start()
        runtime = socket_runtime.prepare(self.directory)
        (self.directory / 'qemu.pid').write_text(str(process.pid))
        python = binary / 'python3'
        python.write_text('''#!/bin/bash
case $1 in
    */iso-monitor.py)
        printf '%s\\n' "$4" >>"$STOP_COMMANDS"
        [[ ${STOP_FAIL:-0} == 0 ]] || exit 1
        if [[ ${STOP_STALL:-0} == 0 ]]; then kill -TERM "$STOP_PID"; fi ;;
    *) exec "$STOP_PYTHON" "$@" ;;
esac
''')
        python.chmod(0o700)
        shell_env = binary / 'shell-env'
        shell_env.write_text('''sleep() {
    if [[ ${STOP_STALL:-0} == 1 || ${STOP_FAIL:-0} == 1 ]]; then return 0; fi
    command sleep "$@"
}
''')
        commands = self.directory / 'commands'
        env = dict(os.environ, PATH=str(binary) + ':' + os.environ['PATH'],
                   STOP_PID=str(process.pid), STOP_COMMANDS=str(commands),
                   STOP_PYTHON=sys.executable, BASH_ENV=str(shell_env))
        return process, runtime, commands, env

    def run_stop(self, env, *args):
        return subprocess.run(['bash', str(ROOT / 'tests/vm/iso-stop.sh'),
                               '--dir', str(self.directory), *args],
                              env=env, text=True, capture_output=True, timeout=10)

    def test_stop_waits_for_guest_shutdown_and_cleans_runtime(self):
        process, runtime, commands, env = self.stop_fixture()
        result = self.run_stop(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNotNone(process.poll())
        self.assertEqual(commands.read_text(), 'system_powerdown\n')
        self.assertFalse(runtime.exists())
        self.assertFalse((self.directory / 'qemu.pid').exists())

    def test_force_stop_explicitly_requests_quit(self):
        _, runtime, commands, env = self.stop_fixture()
        result = self.run_stop(env, '--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(commands.read_text(), 'quit\n')
        self.assertFalse(runtime.exists())

    def test_stop_timeout_preserves_running_vm_without_quit(self):
        process, runtime, commands, env = self.stop_fixture()
        result = self.run_stop(dict(env, STOP_STALL='1'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('VM left running', result.stderr)
        self.assertIsNone(process.poll())
        self.assertEqual(commands.read_text(), 'system_powerdown\n')
        self.assertTrue(runtime.exists())
        self.assertTrue((self.directory / 'qemu.pid').exists())

    def test_stop_monitor_failure_preserves_vm_without_quit(self):
        process, runtime, commands, env = self.stop_fixture()
        result = self.run_stop(dict(env, STOP_FAIL='1'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(process.poll())
        self.assertEqual(commands.read_text(), 'system_powerdown\n' * 20)
        self.assertTrue(runtime.exists())
        self.assertTrue((self.directory / 'qemu.pid').exists())

    def test_stop_rejects_unknown_arguments_without_contacting_monitor(self):
        process, _, commands, env = self.stop_fixture()
        result = self.run_stop(env, '--force', '--unknown')
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(process.poll())
        self.assertFalse(commands.exists())

    def test_stop_ignores_stale_pidfile_and_stops_owned_vm(self):
        process, runtime, commands, env = self.stop_fixture()
        (self.directory / 'qemu.pid').write_text(str(os.getpid()))
        result = self.run_stop(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNotNone(process.poll())
        self.assertEqual(commands.read_text(), 'system_powerdown\n')
        self.assertFalse(runtime.exists())

    def test_stop_does_not_signal_unowned_qemu(self):
        process, runtime, commands, env = self.stop_fixture(owned=False)
        result = self.run_stop(env, '--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(process.poll())
        self.assertFalse(commands.exists())
        self.assertFalse(runtime.exists())

    def test_stop_waits_for_launcher_before_pidfile_exists(self):
        process, runtime, commands, env = self.stop_fixture(launch_delay=True)
        (self.directory / 'qemu.pid').unlink()
        result = self.run_stop(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNotNone(process.poll())
        self.assertEqual(commands.read_text(), 'system_powerdown\n')
        self.assertFalse(runtime.exists())

    def test_stop_accepts_unreaped_qemu_exit(self):
        process, runtime, commands, env = self.stop_fixture(reap=False)
        result = self.run_stop(env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(f'/proc/{process.pid}/stat').read_text().rsplit(')', 1)[1].split()[0], 'Z')
        self.assertEqual(commands.read_text(), 'system_powerdown\n')
        self.assertFalse(runtime.exists())

    def test_force_stop_falls_back_to_signal_when_monitor_fails(self):
        process, runtime, commands, env = self.stop_fixture()
        result = self.run_stop(dict(env, STOP_FAIL='1'), '--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNotNone(process.poll())
        self.assertEqual(commands.read_text(), 'quit\n' * 20)
        self.assertIn('sending SIGTERM', (self.directory / 'stop-vm.log').read_text())
        self.assertFalse(runtime.exists())


if __name__ == '__main__':
    unittest.main()
