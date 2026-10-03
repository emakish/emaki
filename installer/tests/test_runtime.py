from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from emaki_installer.errors import InstallError
from emaki_installer.runtime import Redactor, Runner, TargetFiles, cleanup, mounts_under
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


if __name__ == '__main__':
    unittest.main()
