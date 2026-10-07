#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rootless checks for the disposable-guest acceptance assertions."""
import ast
import json
import os
from pathlib import Path
import platform
import select
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock


SOURCE = Path(__file__).parent / 'vm/boot-delivery-guest.py'


def helper(name, source=SOURCE, **bindings):
    tree = ast.parse(source.read_text(), filename=str(source))
    function = next(node for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == name)
    scope = dict(bindings)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), scope)
    return scope[name]


class NegativeTests(unittest.TestCase):
    def invoke(self, output, status=0, changed=False):
        before = {'/efi/EFI/BOOT/BOOTX64.EFI': {'sha256': 'original'}}
        after = {'changed': True} if changed else before.copy()
        run = Mock(return_value=SimpleNamespace(returncode=status, stdout=output))
        inventory = Mock(side_effect=[before, after])
        save = Mock()
        helper('negative', Path=Path, ESP=Path('/efi'), run=run,
               inventory=inventory, save=save)()
        run.assert_called_once_with(['emaki-boot-refresh', '--hook'], check=False)
        self.assertEqual(save.call_args_list[0].args, ('negative-before.json', before))
        self.assertEqual(save.call_args_list[1].args, ('negative-after.json', after))

    def test_expected_refusals_succeed_without_changes(self):
        for output in (
            'The existing boot loaders were kept because the fallback loader is not recognized as Emaki.\n',
            'The EFI system partition is smaller than 256 MiB; the existing boot loaders were kept.\n',
        ):
            with self.subTest(output=output):
                self.invoke(output)

    def test_rejects_nonzero_status(self):
        with self.assertRaises(AssertionError):
            self.invoke('The existing boot loaders were kept.\n', status=1)

    def test_rejects_changed_files(self):
        with self.assertRaises(AssertionError):
            self.invoke('The existing boot loaders were kept.\n', changed=True)

    def test_rejects_noisy_or_empty_output(self):
        for output in ('', '\n', 'first\nsecond\n', 'WARNING: kept.\n', 'Resolve this first.\n'):
            with self.subTest(output=output), self.assertRaises(AssertionError):
                self.invoke(output)


class BoundedTests(unittest.TestCase):
    def invoke(self, initial, later):
        directories = {initial['good'], later['newest']}
        path = Mock()
        path.iterdir.return_value = [SimpleNamespace(name=name, is_dir=lambda: True)
                                    for name in directories]
        refresh = Mock()
        save = Mock()
        helper('bounded', collect=Mock(), state=Mock(side_effect=[initial] + [later] * 45),
               digest=Mock(return_value='fallback'), ESP=Path('/efi'), Path=Mock(return_value=path),
               refresh=refresh, inventory=Mock(return_value={}), save=save)()
        self.assertEqual(refresh.call_count, 45)
        self.assertEqual(len(save.call_args.args[1]), 45)

    def test_reuses_confirmed_loader(self):
        self.invoke({'good': 'good', 'newest': 'good'}, {'good': 'good', 'newest': 'good'})

    def test_reuses_pending_loader(self):
        self.invoke({'good': 'good', 'newest': 'pending'}, {'good': 'good', 'newest': 'pending'})

    def test_rejects_candidate_churn(self):
        with self.assertRaises(AssertionError):
            self.invoke({'good': 'good', 'newest': 'pending'}, {'good': 'good', 'newest': 'replacement'})


class ScenarioTests(unittest.TestCase):
    def test_requires_pending_loader(self):
        output = MagicMock()
        output.__truediv__.return_value.exists.return_value = False
        with self.assertRaisesRegex(AssertionError, 'changed loader inputs'):
            helper('scenario_prepare', OUT=output, refresh=Mock(),
                   state=Mock(return_value={'good': 'good', 'newest': 'good'}),
                   boot_id=Mock(return_value='boot'), digest=Mock(return_value='fallback'),
                   ESP=Path('/efi'), trial=Mock(return_value={'emaki_candidate': 'good',
                                                           'emaki_trial': '0'}), save=Mock())('corrupt')


POWER_CUT = SOURCE.with_name('boot-delivery-powercut.py')


class PowerCutTests(unittest.TestCase):
    def test_cold_imports_leave_only_publication_rename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'cold_module.py').write_text('value = 1\n')
            fixture = root / 'fixture.py'
            fixture.write_text(
                'import json, os, pathlib, subprocess, sys\n'
                'renames = []\n'
                'sys.addaudithook(lambda name, args: renames.append(str(args[1])) '
                'if name == "os.rename" else None)\n'
                'import cold_module\n'
                'subprocess.run([sys.executable, "-c", "import cold_module"], check=True)\n'
                'pathlib.Path("pending").write_text("candidate")\n'
                'os.replace("pending", "published")\n'
                'print(json.dumps(renames))\n')
            args = SimpleNamespace(phase='enter', rename=1, guest_out=directory,
                                   guest_helper=str(fixture))
            command = helper('trace_command', source=POWER_CUT)(args)
            # Execute the exact tracee argv and environment without requiring ptrace.
            tracee = command[command.index('python3'):]
            env = dict(os.environ)
            name, value = command[1].split('=', 1)
            env[name] = value
            result = subprocess.run(tracee, cwd=root, env=env, text=True,
                                    capture_output=True, check=True)
            self.assertEqual(json.loads(result.stdout), ['published'])
            self.assertFalse(list(root.rglob('__pycache__')))
            self.assertIn('-B', tracee)

    def test_conversion_follows_exit_and_refuses_timeout(self):
        calls = []
        wait = Mock(side_effect=lambda fd: calls.append(('exit', fd)))
        run = Mock(side_effect=lambda *a, **kw: calls.append(('convert', a)))
        convert = helper('convert_after_exit', source=POWER_CUT,
                         wait_qemu_exit=wait, subprocess=SimpleNamespace(run=run))
        convert(7, 'disk', 'raw')
        self.assertEqual([event[0] for event in calls], ['exit', 'convert'])
        wait.side_effect = RuntimeError('QEMU still owns the image')
        run.reset_mock()
        with self.assertRaises(RuntimeError):
            convert(7, 'disk', 'raw')
        run.assert_not_called()

    @unittest.skipUnless(platform.machine() == 'x86_64' and hasattr(os, 'pidfd_open'),
                         'Requires Linux x86_64 process descriptors')
    def test_zombie_leader_waits_for_live_worker(self):
        wait = helper('wait_qemu_exit', source=POWER_CUT, select=select)
        with tempfile.TemporaryDirectory() as directory:
            release = Path(directory) / 'release'
            # SYS_exit ends the leader only; the worker keeps the group alive.
            program = (
                'import ctypes, os, pathlib, sys, threading, time\n'
                'release = pathlib.Path(sys.argv[1])\n'
                'def worker():\n'
                '    while not release.exists(): time.sleep(0.01)\n'
                '    os._exit(0)\n'
                'threading.Thread(target=worker).start()\n'
                'ctypes.CDLL(None).syscall(60, 0)\n')
            process = subprocess.Popen([sys.executable, '-B', '-c', program, str(release)])
            fd = os.pidfd_open(process.pid)
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    stat = Path(f'/proc/{process.pid}/stat').read_text()
                    if stat.rsplit(') ', 1)[1].startswith('Z '):
                        break
                    time.sleep(0.01)
                else:
                    self.fail('Leader did not exit')
                with self.assertRaisesRegex(RuntimeError, 'still owns'):
                    wait(fd, timeout=0.01)
                release.touch()
                wait(fd, timeout=5)
                process.wait(timeout=5)
                # Readiness survives exit/reaping: no /proc disappearance race.
                wait(fd, timeout=0)
            finally:
                process.kill()
                process.wait(timeout=5)
                os.close(fd)


if __name__ == '__main__':
    unittest.main()
