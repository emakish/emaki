#!/usr/bin/env python3
"""tests/vm/release-gate.sh with every VM script, make, bwrap, verify-image and eyes-gate
replaced by stubs in a copied tree. Never starts QEMU."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import reaper
reaper.guard()  # nothing this test starts outlives it

ROOT = Path(__file__).resolve().parent.parent
VM = ROOT / 'tests/vm'
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)

# Exit status from STUB_RC_<name>; the check scripts leave the files the real ones leave.
STUB = r'''#!/usr/bin/env bash
name=$(basename "$0")
printf '%s %s EMAKI_CHECK_ISO=%s VMDIR=%s\n' "$name" "$*" "${EMAKI_CHECK_ISO:-}" "${VMDIR:-}" >>"$STUB_LOG"
var=STUB_RC_$(printf '%s' "${name%.*}" | tr -c 'a-zA-Z0-9\n' _)
rc=${!var:-0}
case $name in
    iso-encrypt-check.sh)
        if ((rc == 0)) && [[ ${STUB_NO_PASS:-} != 1 ]]; then mkdir -p "$VMDIR/btrfs-$2"; : >"$VMDIR/btrfs-$2/PASS"; fi ;;
    iso-alongside-check.sh)
        rc=${!var:-77}
        mkdir -p "$VMDIR/btrfs-$2"
        if ((rc == 77)); then : >"$VMDIR/btrfs-$2/NOT-APPLICABLE"; elif ((rc == 0)); then : >"$VMDIR/btrfs-$2/PASS"; fi ;;
    bwrap)
        while (($#)) && [[ $1 != -- ]]; do shift; done
        shift
        exec "$@" ;;
esac
exit "$rc"
'''
# Release mode refuses an image that carries test material; --test refuses one that does not.
VERIFY = r'''#!/usr/bin/env python3
import os, sys
with open(os.environ['STUB_LOG'], 'a') as log:
    log.write('verify-image ' + ' '.join(sys.argv[1:]) + '\n')
test = '--test' in sys.argv
if (b'emaki.test=1' in open(sys.argv[1], 'rb').read()) != test:
    sys.exit('ERROR: mastered image verification failed: wrong mode')
'''
EYES_GATE = r'''#!/usr/bin/env python3
import os, sys
with open(os.environ['STUB_LOG'], 'a') as log:
    log.write('eyes-gate ' + ' '.join(sys.argv[1:]) + '\n')
rc = int(os.environ.get('STUB_RC_eyes_gate', '0'))
print({0: 'RESULT: PASSED (walk record)', 3: 'RESULT: NOT TESTED at W1/31'}.get(rc, 'RESULT: FAILED at W1/03'))
sys.exit(rc)
'''
# Leaves the PASS file the real check leaves; its own job is covered by test-graphics-fallback-check.py.
CHECK_GRAPHICS = r'''#!/usr/bin/env python3
import os, sys
with open(os.environ['STUB_LOG'], 'a') as log:
    log.write('check-graphics-fallback ' + ' '.join(sys.argv[1:]) + '\n')
out = sys.argv[sys.argv.index('--out') + 1]
os.makedirs(out)
open(os.path.join(out, 'PASS'), 'w').close()
print('RESULT: SCRIPTS PASSED')
'''


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.TemporaryDirectory(prefix='gate-', dir=CACHE)
        self.base = Path(self.work.name)
        self.repo = self.base / 'r'
        vm = self.repo / 'tests/vm'
        (vm / 'eyes').mkdir(parents=True)
        (self.repo / 'iso').mkdir()
        for name in ('release-gate.sh', 'jail.sh'):
            shutil.copy(VM / name, vm / name)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        stubs = [vm / n for n in ('run-iso.sh', 'iso-wait-ssh.sh', 'iso-install.sh', 'iso-stop.sh',
                                  'iso-boot-check.sh', 'iso-encrypt-check.sh', 'iso-alongside-check.sh')]
        for path in stubs + [self.bin / 'make', self.bin / 'bwrap']:
            path.write_text(STUB)
        for path, text in ((self.repo / 'iso/verify-image.py', VERIFY), (vm / 'eyes/eyes-gate.py', EYES_GATE),
                           (vm / 'check-graphics-fallback.py', CHECK_GRAPHICS)):
            path.write_text(text)
        for path in stubs + [self.bin / 'make', self.bin / 'bwrap', vm / 'release-gate.sh', vm / 'jail.sh']:
            path.chmod(0o755)
        self.log = self.base / 'calls.log'
        self.log.touch()
        self.release = self.image('release/emaki-0.2.0-x86_64.iso', b'release image\n')
        self.test = self.image('test/emaki-0.2.0-x86_64.iso', b'test image emaki.test=1\n')
        self.walk = self.base / 'walk'
        self.walk.mkdir()

    def tearDown(self):
        self.work.cleanup()

    def image(self, relative, content):
        path = self.base / 'isos' / relative
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        Path(str(path) + '.sha256').write_text(f'{hashlib.sha256(content).hexdigest()}  {path.name}\n')
        return path

    def sha(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def gate(self, *extra, release=None, test=None, walk=True, **rcs):
        env = dict(os.environ, HOME=str(self.base / 'home'), STUB_LOG=str(self.log),
                   PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}')
        env.update({f'STUB_RC_{k}': str(v) for k, v in rcs.items() if k != 'no_pass'})
        if rcs.get('no_pass'):
            env['STUB_NO_PASS'] = '1'
        command = ['bash', str(self.repo / 'tests/vm/release-gate.sh'), '--release-iso', str(release or self.release),
                   '--test-iso', str(test or self.test), '--out', str(self.base / 'out'), *extra]
        if walk:
            command += ['--walk', str(self.walk)]
        result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=120)
        self.assertNotRegex(result.stdout, r'(?i)\bOK\b|\bDONE\b|verified|accepted')
        self.calls = self.log.read_text().splitlines()
        return result

    def line(self, result, job):
        return next(line for line in result.stdout.splitlines() if re.match(rf'{job}\s{{2,}}', line))

    def assert_last(self, result, text, code):
        self.assertEqual(result.stdout.splitlines()[-1], text, result.stdout)
        self.assertEqual(result.returncode, code)

    def test_all_jobs_pass_and_name_their_image(self):
        result = self.gate()
        self.assert_last(result, 'RESULT: SCRIPTS PASSED', 0)
        for job in ('release-image', 'test-image', 'host-checks', 'accept-erase-btrfs', 'accept-erase-ext4',
                    'encrypt-btrfs', 'release-walk'):
            self.assertRegex(self.line(result, job), r'\bPASS\b')
        self.assertIn('NOT APPLICABLE', self.line(result, 'alongside-btrfs'))
        self.assertIn('DECISIONS.md 2026-10-04', self.line(result, 'alongside-btrfs'))
        self.assertIn(f'on release image {self.release} sha256 {self.sha(self.release)}', result.stdout)
        self.assertIn(f'on test image {self.test} sha256 {self.sha(self.test)}', result.stdout)
        # Every VM job ran on the test image; only the walk gate saw the release image.
        runs = [c for c in self.calls if c.startswith('run-iso.sh') and '--iso' in c]
        self.assertEqual(len(runs), 2)
        self.assertTrue(all(f'--iso {self.test}' in c for c in runs))
        for name in ('iso-encrypt-check.sh', 'iso-alongside-check.sh'):
            call = next(c for c in self.calls if c.startswith(name))
            self.assertIn(f'EMAKI_CHECK_ISO={self.test}', call)
        along = next(c for c in self.calls if c.startswith('iso-alongside-check.sh'))
        self.assertIn(f'VMDIR={self.base}/home/VMs/n2-along', along)
        walk = next(c for c in self.calls if c.startswith('eyes-gate'))
        self.assertIn(f'--iso {self.release}', walk)
        self.assertIn('--image-verified', walk)
        self.assertIn(f'make check-all', [c.split(' EMAKI')[0] for c in self.calls])
        self.assertTrue((self.base / 'out/gate.log').read_text().endswith('RESULT: SCRIPTS PASSED\n'))

    def test_night_of_2026_10_04_is_a_failure(self):
        """full-build.sh printed STATUS: DONE after 'encrypt btrfs: rc=1' (full.log:43-45)."""
        result = self.gate(iso_encrypt_check=1)
        self.assert_last(result, 'RESULT: FAILED at encrypt-btrfs', 1)
        self.assertIn('iso-encrypt-check exit 1', self.line(result, 'encrypt-btrfs'))
        self.assertRegex(self.line(result, 'accept-erase-btrfs'), r'\bPASS\b')

    def test_exit_zero_without_the_pass_file_is_a_failure(self):
        result = self.gate(no_pass=True)
        self.assert_last(result, 'RESULT: FAILED at encrypt-btrfs', 1)
        self.assertIn('no PASS file', result.stdout)

    def test_failed_install_or_boot_check_fails_its_job(self):
        result = self.gate(iso_boot_check=1)
        self.assert_last(result, 'RESULT: FAILED at accept-erase-btrfs', 1)
        result = self.gate('--out', str(self.base / 'out2'), iso_install=3)
        self.assertIn('iso-install.sh erase-btrfs exit 3', result.stdout)
        self.assertEqual(result.returncode, 1)

    def test_alongside_failure_is_not_hidden(self):
        result = self.gate(iso_alongside_check=1)
        self.assert_last(result, 'RESULT: FAILED at alongside-btrfs', 1)

    def test_no_walk_record_is_not_tested(self):
        result = self.gate(walk=False)
        self.assert_last(result, 'RESULT: NOT TESTED at release-walk', 3)
        self.assertFalse(any(c.startswith('eyes-gate') for c in self.calls))

    def test_walk_gate_results_are_passed_through(self):
        self.assert_last(self.gate(eyes_gate=1), 'RESULT: FAILED at release-walk', 1)
        result = self.gate('--out', str(self.base / 'out2'), eyes_gate=3)
        self.assert_last(result, 'RESULT: NOT TESTED at release-walk', 3)

    def assert_no_vm(self):
        self.assertFalse(any(c.startswith(('run-iso.sh', 'iso-encrypt', 'iso-alongside')) for c in self.calls))

    def test_release_image_that_does_not_match_its_checksum_stops_everything(self):
        Path(str(self.release) + '.sha256').write_text('0' * 64 + f'  {self.release.name}\n')
        result = self.gate()
        self.assert_last(result, 'RESULT: FAILED at release-image', 1)
        self.assert_no_vm()
        self.assertIn('not run: the release image failed its checks', self.line(result, 'release-walk'))

    def test_test_image_given_as_release_is_refused(self):
        result = self.gate(release=self.test, test=self.test)
        self.assert_last(result, 'RESULT: FAILED at release-image', 1)
        self.assert_no_vm()

    def test_release_image_given_as_test_is_refused(self):
        result = self.gate(test=self.release)
        self.assert_last(result, 'RESULT: FAILED at test-image', 1)
        self.assert_no_vm()
        self.assertIn('NOT TESTED', self.line(result, 'encrypt-btrfs'))

    def test_failed_host_checks_stop_the_vm_jobs(self):
        result = self.gate(make=2)
        self.assert_last(result, 'RESULT: FAILED at host-checks', 1)
        self.assert_no_vm()

    def test_partial_run_cannot_pass(self):
        result = self.gate('--jobs', 'release-image,release-walk')
        self.assert_last(result, 'RESULT: NOT TESTED at test-image', 3)
        self.assertIn('not selected in this run', self.line(result, 'host-checks'))
        self.assert_no_vm()

    def test_output_directory_is_never_reused(self):
        self.gate()
        result = self.gate()
        self.assertEqual(result.returncode, 2)
        self.assertIn('never reused', result.stderr)

    def test_shellcheck(self):
        if not shutil.which('shellcheck'):
            self.skipTest('shellcheck is not installed')
        subprocess.run(['shellcheck', '-x', str(VM / 'release-gate.sh'), str(VM / 'jail.sh')], check=True)


if __name__ == '__main__':
    unittest.main()
