#!/usr/bin/env python3
"""Static and stubbed checks of the ISO VM check scripts. Never starts QEMU or SSH."""
import ast
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import reaper
reaper.guard()  # nothing this test starts outlives it

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
VM = ROOT / 'tests/vm'
ISO_CHECKS = sorted(VM.glob('iso-*.py'))
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)

# External VM commands are replaced by this stub. Copying and inspecting an
# empty base succeed so the alongside check reaches run-iso.sh, which fails.
STUB = '''#!/usr/bin/env bash
printf '%s\\n' "$(basename "$0") $*" >>"$ISO_STUB_LOG"
case "$(basename "$0")" in
    cp) cat -- "$3" > "$4" ;;
    qemu-img)
        case "$1" in
            info) printf '%s\\n' '{"format":"qcow2","virtual-size":103079215104}' ;;
            map) printf '%s\\n' '[{"zero":true}]' ;;
            *) exit 1 ;;
        esac ;;
    *) exit 1 ;;
esac
'''

# Patch inside the child process: these fixture checks must not measure the
# builder's disk. Keep the real guard active so its threshold is also testable.
RUN_CHECK = '''
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

free = int(sys.argv.pop(1))
sys.argv.pop(0)
sys.path.insert(0, str(Path(sys.argv[0]).parent))
with patch('shutil.disk_usage', return_value=SimpleNamespace(free=free)):
    runpy.run_path(sys.argv[0], run_name='__main__')
'''


class NoHotPatchTests(unittest.TestCase):
    """The checks test the installer packaged in the ISO, not the checkout's copy."""

    def test_no_check_overwrites_the_guest_installer(self):
        markers = ('disk-hotpatch', 'emaki_installer.__path__', 'restart emaki-installerd', 'RELEASE_BLOCK')
        for path in ISO_CHECKS:
            text = path.read_text()
            for marker in markers:
                with self.subTest(script=path.name, marker=marker):
                    self.assertFalse(marker in text, f'{path.name} contains {marker!r}')


class PictureWordingTests(unittest.TestCase):
    """A saved picture nobody judged is reported as SHOT, never as an OK check."""

    def test_boot_check_reports_pictures_as_shots(self):
        source = (VM / 'iso-boot-check.py').read_text()
        offending = []
        for node in ast.walk(ast.parse(source)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == 'status' and len(node.args) == 2):
                continue
            words = ' '.join(part.value for part in ast.walk(node.args[1])
                             if isinstance(part, ast.Constant) and isinstance(part.value, str))
            first = node.args[0]
            if ('screenshot' in words or 'frames saved' in words) and \
                    not (isinstance(first, ast.Constant) and first.value is False):
                offending.append(node.lineno)
        self.assertEqual(offending, [], 'status() reports a picture as a passed check')
        self.assertIn('SHOT: ', source)

    def test_shot_helper_prints_no_ok_lines(self):
        source = (VM / 'iso-shot.py').read_text()
        for marker in ("'OK: ", '"OK: '):
            self.assertFalse(marker in source, f'iso-shot.py contains {marker!r}')
        self.assertEqual(source.count('(not judged)'), 2)


class CheckIsoTests(unittest.TestCase):
    """Run copies of the check scripts next to stubs; the real run-iso.sh is never reachable."""

    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='iso-env-', dir=CACHE)
        self.base = Path(self.work.name)
        self.home = self.base / 'home'
        self.bin = self.base / 'bin'
        self.copy = self.base / 'repo/tests/vm'
        self.log = self.base / 'calls.log'
        self.base_disk = self.base / 'empty.qcow2'
        self.base_disk.write_bytes(b'fixture base for stubbed qemu-img\n')
        for folder in (self.home, self.bin, self.copy, self.base / 'repo/installer'):
            folder.mkdir(parents=True)
        for name in ('iso-alongside-check.py', 'iso-encrypt-check.py', 'iso-monitor.py',
                     'frame_assessment.py'):
            shutil.copy(VM / name, self.copy / name)
        shutil.copytree(VM / 'fixtures', self.copy / 'fixtures')
        shutil.copytree(ROOT / 'installer/fixtures', self.base / 'repo/installer/fixtures')
        for folder, names in ((self.copy, ('run-iso.sh', 'iso-wait-ssh.sh', 'iso-ssh.sh', 'iso-stop.sh')),
                              (self.bin, ('cp', 'qemu-img', 'tesseract'))):
            for name in names:
                (folder / name).write_text(STUB)
                (folder / name).chmod(0o755)
        self.log.touch()

    def tearDown(self):
        self.work.cleanup()

    def execute(self, script, vmdir, *args, iso=None, free_bytes=3 * 1024**3):
        vmdir.mkdir(parents=True, exist_ok=True)
        env = {key: value for key, value in os.environ.items() if key != 'EMAKI_CHECK_ISO'}
        env.update(HOME=str(self.home), VMDIR=str(vmdir), ISO_STUB_LOG=str(self.log),
                   EMAKI_ALONGSIDE_BASE_DISK=str(self.base_disk),
                   PATH=str(self.bin) + os.pathsep + os.environ['PATH'])
        if iso is not None:
            env['EMAKI_CHECK_ISO'] = str(iso)
        result = subprocess.run([sys.executable, '-B', '-c', RUN_CHECK, str(free_bytes),
                                 str(self.copy / script), *args],
                                env=env, text=True, capture_output=True, timeout=60)
        return result, self.log.read_text().splitlines()

    def assert_refused_before_any_mutation(self, script, vmdir, iso=None):
        result, calls = self.execute(script, vmdir, 'btrfs', 'x', iso=iso)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('EMAKI_CHECK_ISO', result.stderr)
        self.assertEqual(list(vmdir.iterdir()), [])
        self.assertFalse(any(call.startswith('run-iso.sh') for call in calls), calls)

    def test_unset_iso_is_refused_before_any_mutation(self):
        self.assert_refused_before_any_mutation('iso-alongside-check.py', self.home / 'VMs/n2-along')
        self.assert_refused_before_any_mutation('iso-encrypt-check.py', self.base / 'enc')

    def test_unreadable_iso_is_refused_before_any_mutation(self):
        for missing in (self.base / 'missing.iso', self.base):
            self.assert_refused_before_any_mutation('iso-alongside-check.py', self.home / 'VMs/n2-along', missing)
            self.assert_refused_before_any_mutation('iso-encrypt-check.py', self.base / 'enc', missing)

    def test_named_iso_is_recorded_in_the_run_directory(self):
        iso = self.base / 'fixture.iso'
        iso.write_bytes(b'fixture image\n')
        digest = hashlib.sha256(iso.read_bytes()).hexdigest()
        for script, vmdir in (('iso-alongside-check.py', self.home / 'VMs/n2-along'),
                              ('iso-encrypt-check.py', self.base / 'enc')):
            with self.subTest(script=script):
                result, calls = self.execute(script, vmdir, 'btrfs', 'x', iso=iso)
                self.assertNotEqual(result.returncode, 0)
                record = (vmdir / 'btrfs-x/iso.txt').read_text()
                self.assertIn(str(iso), record)
                self.assertIn(digest, record)
                self.assertTrue(any(call.startswith('run-iso.sh') and str(iso) in call for call in calls),
                                f'{calls}\n{result.stderr}')
                self.log.write_text('')

    def test_alongside_runs_in_any_directory_under_vms_only(self):
        # The release gate passes its own run directory; nothing outside ~/VMs is accepted.
        iso = self.base / 'fixture.iso'
        iso.write_bytes(b'fixture image\n')
        outside = self.base / 'outside'
        outside.mkdir()
        (self.home / 'VMs').mkdir()
        escape = self.home / 'VMs/escape'
        escape.symlink_to(outside, target_is_directory=True)
        for vmdir in (self.home / 'VMs', self.home / 'elsewhere', outside, escape,
                      self.base / 'repo/.cache', self.base / 'repo/.cache/alongside'):
            with self.subTest(refused=vmdir):
                result, calls = self.execute('iso-alongside-check.py', vmdir, 'btrfs', 'x', iso=iso)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('VMDIR', result.stderr)
                self.assertFalse((vmdir / 'btrfs-x').exists())
                self.assertEqual(calls, [])
        for vmdir in (self.home / 'VMs/n2-along', self.home / 'VMs/release-gate/20261005-010203/alongside'):
            with self.subTest(accepted=vmdir):
                result, calls = self.execute('iso-alongside-check.py', vmdir, 'btrfs', 'x', iso=iso)
                self.assertTrue((vmdir / 'btrfs-x/iso.txt').is_file(), result.stderr)
                self.assertTrue(any(call.startswith('run-iso.sh') for call in calls),
                                f'{calls}\n{result.stderr}')
                target = vmdir / 'btrfs-x/target.qcow2'
                self.assertEqual(target.read_bytes(), self.base_disk.read_bytes())
                self.assertIn(f'cp --reflink=always -- {self.base_disk} {target}', calls)
                self.assertIn(f'qemu-img info --output=json {target}', calls)
                self.assertIn(f'qemu-img map --output=json {target}', calls)
                self.log.write_text('')

    def test_alongside_free_space_guard(self):
        iso = self.base / 'fixture.iso'
        iso.write_bytes(b'fixture image\n')
        threshold = 3 * 1024**3
        for free_bytes in (0, threshold - 1, threshold):
            with self.subTest(free_bytes=free_bytes):
                self.log.write_text('')
                vmdir = self.home / f'VMs/free-{free_bytes}'
                result, calls = self.execute('iso-alongside-check.py', vmdir, 'btrfs', 'x',
                                             iso=iso, free_bytes=free_bytes)
                # The launch stub fails even when the guard permits a start.
                self.assertNotEqual(result.returncode, 0)
                started = any(call.startswith('run-iso.sh') for call in calls)
                self.assertEqual(started, free_bytes >= threshold, f'{calls}\n{result.stderr}')
                if free_bytes < threshold:
                    self.assertIn('not run: disk (less than 3 GiB available on /home)', result.stderr)
                else:
                    self.assertNotIn('not run: disk', result.stderr)

    def test_snapshot_only_does_not_need_an_iso(self):
        vmdir = self.base / 'enc'
        result, calls = self.execute('iso-encrypt-check.py', vmdir, 'btrfs', 'x', 'erase', '--snapshot-only')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('EMAKI_CHECK_ISO', result.stderr)
        self.assertIn('Requires a previously installed test disk', result.stderr)
        self.assertFalse(any(call.startswith('run-iso.sh') for call in calls), calls)


if __name__ == '__main__':
    unittest.main()
