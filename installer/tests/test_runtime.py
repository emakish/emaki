import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from emaki_installer.errors import InstallError
from emaki_installer.runtime import Redactor, Runner, TargetFiles, cleanup, mounts_under, stop_target_processes
from support import RecordingRunner


class RuntimeTests(unittest.TestCase):
    def test_secret_stdin_and_output_never_logged(self):
        logs = []
        runner = Runner(logs.append)
        output = runner.run([sys.executable, '-c', 'import sys; print(sys.stdin.read())'],
                            input='user:secret-on-stdin\n', secret=True)
        self.assertEqual(output, '')
        self.assertNotIn('secret-on-stdin', '\n'.join(logs))

    def test_failure_does_not_expose_secret_output(self):
        logs = []
        runner = Runner(logs.append)
        with self.assertRaises(InstallError):
            runner.run([sys.executable, '-c', 'import sys; print(sys.stdin.read()); sys.exit(1)'],
                       input='user:secret-on-stdin\n', secret=True)
        self.assertNotIn('secret-on-stdin', '\n'.join(logs))

    def test_subprocess_lines_and_progress(self):
        logs, progress = [], []
        runner = Runner(logs.append, progress=progress.append)
        runner.run([sys.executable, '-c', 'print("(2/4) installing package"); print("finished")'])
        self.assertIn('(2/4) installing package', logs)
        self.assertEqual(progress, [50])

    def test_a_command_can_be_watched_line_by_line(self):
        # The update's downloads read pacman's own lines; a secret command shows nothing.
        lines = []
        runner = Runner(lambda line: None, Redactor())
        runner.run([sys.executable, '-c', 'print(" core downloading..."); print("Packages (2) a-1  b-1", end="")'],
                   watch=lines.append)
        self.assertEqual(lines, [' core downloading...', 'Packages (2) a-1  b-1'])
        runner.run([sys.executable, '-c', 'print("hidden")'], secret=True, watch=lines.append)
        self.assertEqual(len(lines), 2)

        def broken(line):
            raise ValueError(line)

        # A failing watcher never fails the command it watches.
        self.assertEqual(runner.run([sys.executable, '-c', 'print("x")'], watch=broken), 'x\n')

    def test_installed_package_lines_reach_the_callback_by_keyword(self):
        calls = []
        runner = Runner(lambda line: None, progress=lambda pct, package=None: calls.append((pct, package)))
        for line in ('Packages (2) acl-2.4.0-1  attr-2.6.0-1', 'installing acl...', '(10/16) Arming ConditionNeedsUpdate...',
                     'installing attr...', 'reinstalling attr...', 'Optional dependencies for attr'):
            runner._line(line)
        self.assertEqual(calls, [(None, 'acl'), (62.5, None), (None, 'attr')])

    def test_timeout_stops_the_whole_process_group(self):
        # arch-chroot runs pacman as a grandchild; this one also ignores SIGTERM.
        logs = []
        with patch('emaki_installer.runtime.KILL_GRACE', 0.5), \
                self.assertRaisesRegex(InstallError, 'sh timed out'):
            Runner(logs.append).run(['sh', '-c', 'trap "" TERM; sleep 60 & echo "$!"; wait'], timeout=1)
        grandchild = int(next(line for line in logs if line.isdigit()))
        self.addCleanup(self.kill_quietly, grandchild)
        with self.assertRaises(ProcessLookupError):
            os.kill(grandchild, 0)

    def test_timeout_holds_after_the_output_is_closed(self):
        started = time.monotonic()
        with self.assertRaisesRegex(InstallError, 'sh timed out'):
            Runner(lambda line: None).run(['sh', '-c', 'exec >/dev/null 2>&1; sleep 5'], timeout=1)
        self.assertLess(time.monotonic() - started, 4)

    @staticmethod
    def kill_quietly(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def test_redactor_literal_metacharacters(self):
        r = Redactor()
        r.add('p$a.*ss')
        self.assertEqual(r.text('echo p$a.*ss twice p$a.*ss'), 'echo [REDACTED] twice [REDACTED]')

    def test_long_output_cannot_split_password_across_log_chunks(self):
        logs = []
        redactor = Redactor()
        redactor.add('unique-secret-value')
        runner = Runner(logs.append, redactor)
        runner.run([sys.executable, '-c', 'print("x" * 8190 + "unique-secret-value" + "y" * 9000)'])
        self.assertNotIn('unique-secret-value', '\n'.join(logs))

    def test_confined_writes_reject_symlink_parents_and_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root, outside = Path(temp) / 'root', Path(temp) / 'outside'
            root.mkdir()
            outside.mkdir()
            (root / 'etc').symlink_to(outside)
            files = TargetFiles(root)
            with self.assertRaises(InstallError):
                files.write('/etc/hostname', 'bad')
            self.assertFalse((outside / 'hostname').exists())
            (root / 'etc').unlink()
            files.write('/etc/hostname', 'good')
            self.assertEqual((root / 'etc/hostname').read_text(), 'good')
            (root / 'etc/alias').symlink_to(outside / 'victim')
            with self.assertRaises(InstallError):
                files.write('/etc/alias', 'bad')

    def test_cleanup_is_reverse_order_and_confined(self):
        data = ('1 0 0:1 / /mnt/emaki-target rw - btrfs /dev/x rw\n'
                '2 1 0:1 / /mnt/emaki-target/home rw - btrfs /dev/x rw\n'
                '3 0 0:1 / /mnt/emaki-target-other rw - ext4 /dev/y rw\n'
                '4 1 0:1 / /mnt/emaki-target/home/user/data rw - ext4 /dev/z rw\n')
        result = mounts_under(Path('/mnt/emaki-target'), data)
        self.assertEqual([str(p) for p in result], ['/mnt/emaki-target/home/user/data', '/mnt/emaki-target/home', '/mnt/emaki-target'])
        runner = RecordingRunner()
        with patch('emaki_installer.runtime.mounts_under', return_value=result), \
                patch('emaki_installer.runtime.stop_target_processes'):
            cleanup(runner, [Path('/mnt/emaki-target')])
        unmounts = [c for c, _ in runner.commands if c[0] == 'umount']
        self.assertEqual(unmounts, [['umount', '--', str(p)] for p in result])
        self.assertEqual([c for c, _ in runner.commands[:2]], [['sync'], ['udevadm', 'settle', '--timeout=10']])

    def test_cleanup_checks_the_target_after_its_processes_stopped_and_before_unmounting(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            order, logs = [], []
            runner = RecordingRunner(logs.append)
            original = runner.run
            runner.run = lambda argv, **kwargs: order.append(argv[0]) or original(argv, **kwargs)

            def failing():
                order.append('check')
                raise OSError(13, 'Permission denied')

            with patch('emaki_installer.runtime.mounts_under', return_value=[root / 'home', root]), \
                    patch('emaki_installer.runtime.stop_target_processes', side_effect=lambda *a: order.append('stop')):
                cleanup(runner, [root], before_unmount=lambda: order.append('check'))
                self.assertEqual(order, ['stop', 'sync', 'udevadm', 'check', 'umount', 'umount'])
                # The cleanup before an installation stops nothing, so it checks nothing.
                order.clear()
                cleanup(runner, [root], lazy=True, before_unmount=lambda: order.append('check'))
                self.assertEqual(order, ['umount', 'umount'])
                # A failing check is logged and never keeps the target mounted.
                order.clear()
                cleanup(runner, [root], before_unmount=failing)
                self.assertEqual(order, ['stop', 'sync', 'udevadm', 'check', 'umount', 'umount'])
                self.assertTrue(any('Permission denied' in line for line in logs), logs)

    def test_cleanup_with_a_failing_log_still_unmounts(self):
        def full_log(line):
            raise OSError(28, 'No space left on device')

        with tempfile.TemporaryDirectory() as temp:
            tools, calls = Path(temp) / 'bin', Path(temp) / 'calls.jsonl'
            tools.mkdir()
            for name in ('sync', 'udevadm', 'umount', 'fuser'):
                tool = tools / name
                tool.write_text('#!/usr/bin/env python3\n'
                                'import json, os, sys\n'
                                'with open(os.environ["CLEANUP_CALLS"], "a") as stream:\n'
                                '    stream.write(json.dumps([os.path.basename(sys.argv[0]), *sys.argv[1:]]) + "\\n")\n')
                tool.chmod(0o755)
            root = Path(temp) / 'target'
            mounts = [root / 'home', root]
            with patch.dict(os.environ, PATH=str(tools) + ':' + os.environ['PATH'], CLEANUP_CALLS=str(calls)), \
                    patch('emaki_installer.runtime.mounts_under', return_value=mounts):
                cleanup(Runner(full_log), [root])
            recorded = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertEqual(recorded, [['sync'], ['udevadm', 'settle', '--timeout=10'],
                                    *(['umount', '--', str(mount)] for mount in mounts)])

    def test_target_processes_are_stopped_with_a_failing_log(self):
        def full_log(line):
            raise OSError(28, 'No space left on device')

        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
        try:
            with tempfile.TemporaryDirectory() as temp, \
                    patch('emaki_installer.runtime.processes_under', return_value=[(child.pid, Path(temp))]), \
                    patch('emaki_installer.runtime.time.sleep'):
                stop_target_processes(Runner(full_log), Path(temp))
            self.assertLess(child.wait(timeout=10), 0)
        finally:
            child.kill()
            child.wait()


if __name__ == '__main__':
    unittest.main()
