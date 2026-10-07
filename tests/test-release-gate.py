#!/usr/bin/env python3
"""tests/vm/release-gate.sh with every VM script, make, bwrap, verify-image and eyes-gate
replaced by stubs in a copied tree. Never starts QEMU."""
import hashlib
import json
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
        [[ ${STUB_RC_second_encrypt:-0} == 0 || $2 != gate-2 ]] || rc=$STUB_RC_second_encrypt
        if [[ $2 == gate-1 ]]; then
            case ${STUB_RC_mutate:-} in
                image) printf 'changed' >>"$EMAKI_CHECK_ISO" ;;
                checkout) printf '\n# changed\n' >>"$(dirname "$0")/iso-encrypt-check.sh" ;;
            esac
        fi
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
import importlib.util, os, sys
from pathlib import Path

def detect_bootloader(image):
    spec = importlib.util.spec_from_file_location('actual_verifier', Path(__file__).with_name('actual-verify-image.py'))
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    return verifier.detect_bootloader(image)

if __name__ == '__main__':
    with open(os.environ['STUB_LOG'], 'a') as log:
        log.write('verify-image ' + ' '.join(sys.argv[1:]) + '\n')
    test = '--test' in sys.argv
    if (b'emaki.test=1' in open(sys.argv[1], 'rb').read()) != test:
        sys.exit('ERROR: mastered image verification failed: wrong mode')
'''
XORRISO = r'''#!/usr/bin/env python3
import pathlib, sys
image = pathlib.Path(sys.argv[sys.argv.index('-indev') + 1])
print("'/EFI/BOOT/BOOTX64.EFI'")
print("'/loader/entries/01-emaki.conf'" if b'systemd-boot' in image.read_bytes() else "'/boot/grub/grub.cfg'")
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
        for name in ('release-gate.sh', 'jail.sh', 'check-gate-evidence.py', 'check-boot-menu.py', 'menu_mode.py', 'iso-shot.py', 'iso-monitor.py'):
            shutil.copy(VM / name, vm / name)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        (self.bin / 'xorriso').write_text(XORRISO)
        (self.bin / 'xorriso').chmod(0o755)
        shutil.copy(ROOT / 'iso/verify-image.py', self.repo / 'iso/actual-verify-image.py')
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
        (vm / 'rollback-check.py').write_text("""import os, sys
with open(os.environ['STUB_LOG'], 'a') as log:
    log.write('rollback-check ' + ' '.join(sys.argv[1:]) + '\\n')
sys.exit(int(os.environ.get('STUB_RC_rollback', '0')))
""")
        self.fixture = self.base / 'fixture'
        self.fixture.mkdir()
        for name in ('target.qcow2', 'OVMF_VARS.4m.fd', 'plan.json', 'identity'):
            (self.fixture / name).write_text(name)
        self.provenance = self.fixture / 'provenance.json'
        self.manifest = dict(candidate='candidate-1', sha256={
            'iso': self.sha(self.test), 'target.qcow2': self.sha(self.fixture / 'target.qcow2'),
            'OVMF_VARS.4m.fd': self.sha(self.fixture / 'OVMF_VARS.4m.fd'),
            'plan': self.sha(self.fixture / 'plan.json')},
            packages={name: '1.0' for name in ('emaki', 'emaki-config', 'emaki-desktop')})
        self.provenance.write_text(json.dumps(self.manifest))
        self.menu = self.base / 'menu'
        self.menu.mkdir()
        from PIL import Image
        self.menu_record = dict(sha256=self.sha(self.release), judgment='NOT TESTED', frames=[])
        for firmware, (width, height) in [("uefi", size) for size in ((1366, 768), (1920, 1080), (3840, 2160), (1280, 800), (2560, 1600), (1024, 768))] + [("bios", (1024, 768))]:
            path = self.menu / f'{firmware}-{width}x{height}.png'
            from PIL import ImageDraw
            picture = Image.new('RGB', (width, height), 'white')
            draw = ImageDraw.Draw(picture)
            capitals = []
            for x in (10, 60, 110):
                draw.rectangle((x, 10, x + 3, 59), fill='black')
                for y in (10, 33, 57):
                    draw.rectangle((x, y, x + 14, y + 2), fill='black')
                capitals.append(dict(letter='E', box=[x - 2, 8, x + 17, 62], foreground=[0, 0, 0]))
            picture.save(path)
            config = self.menu / f'{width}x{height}.cfg'
            config.write_text(f'set gfxmode={width}x{height}\n')
            self.menu_record['frames'].append(dict(file=path.name, firmware=firmware, capitals=capitals, advertised_size=[width, height],
                menu_mode=dict(size=f'{width}x{height}', source=config.name, sha256=self.sha(config)),
                sha256=self.sha(path), judgment='PASS', reviewer='Release reviewer',
                reviewed_at='2026-10-06T12:00:00+00:00'))
        self.save_menu()
        subprocess.run(['git', 'init', '-q', str(self.repo)], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Test Fixture',
                        '-c', 'user.email=fixture@example.invalid', '-c', 'core.hooksPath=/dev/null',
                        'commit', '-qm', 'Initial fixture'], check=True)

    def tool_env(self):
        return dict(os.environ, PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}')

    def save_menu(self):
        (self.menu / 'evidence.json').write_text(json.dumps(self.menu_record))

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

    def gate(self, *extra, release=None, test=None, walk=True, evidence=True, **rcs):
        env = dict(os.environ, HOME=str(self.base / 'home'), STUB_LOG=str(self.log),
                   PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}')
        env.update({f'STUB_RC_{k}': str(v) for k, v in rcs.items() if k != 'no_pass'})
        if rcs.get('no_pass'):
            env['STUB_NO_PASS'] = '1'
        command = ['bash', str(self.repo / 'tests/vm/release-gate.sh'), '--release-iso', str(release or self.release),
                   '--test-iso', str(test or self.test), '--out', str(self.base / 'out'), *extra]
        if evidence:
            command += ['--rollback-base', str(self.fixture), '--rollback-plan', str(self.fixture / 'plan.json'),
                        '--rollback-identity', str(self.fixture / 'identity'), '--rollback-provenance', str(self.provenance),
                        '--candidate', 'candidate-1', '--boot-menu', str(self.menu)]
        if walk:
            command += ['--walk', str(self.walk)]
        result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=120)
        self.assertNotRegex(result.stdout, r'(?i)\bOK\b|\bDONE\b|verified|accepted')
        self.calls = self.log.read_text().splitlines()
        return result

    def line(self, result, job):
        return next(line for line in result.stdout.splitlines() if re.match(rf'{job}\s+', line))

    def assert_last(self, result, text, code):
        self.assertEqual(result.stdout.splitlines()[-1], text, result.stdout)
        self.assertEqual(result.returncode, code)

    def test_all_jobs_pass_and_name_their_image(self):
        result = self.gate()
        self.assert_last(result, 'RESULT: SCRIPTS PASSED', 0)
        for job in ('release-image', 'test-image', 'host-checks', 'accept-erase-btrfs', 'accept-erase-ext4',
                    'accept-erase-btrfs-minimal', 'accept-erase-ext4-rich',
                    'encrypt-btrfs', 'rollback', 'boot-menu', 'release-walk'):
            self.assertRegex(self.line(result, job), r'\bPASS\b')
        self.assertIn('command-line installation [CLI]', self.line(result, 'accept-erase-btrfs'))
        self.assertIn('NOT APPLICABLE', self.line(result, 'alongside-btrfs'))
        self.assertIn('DECISIONS.md 2026-10-04', self.line(result, 'alongside-btrfs'))
        self.assertIn(f'on release image {self.release} sha256 {self.sha(self.release)}', result.stdout)
        self.assertIn(f'on test image {self.test} sha256 {self.sha(self.test)}', result.stdout)
        # Every VM job ran on the test image; only the walk gate saw the release image.
        runs = [c for c in self.calls if c.startswith('run-iso.sh') and '--iso' in c]
        self.assertEqual(len(runs), 4)
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

    def test_acceptance_boots_usb_and_covers_both_software_sets_on_each_filesystem(self):
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout)
        runs = [c for c in self.calls if c.startswith('run-iso.sh') and '--iso' in c]
        self.assertEqual(len(runs), 4)
        self.assertTrue(all('--usb' in c.split() for c in runs), runs)
        installs = [c.split()[1] for c in self.calls if c.startswith('iso-install.sh')]
        self.assertEqual(set(installs), {'erase-btrfs', 'erase-ext4', 'erase-btrfs-minimal', 'erase-ext4-rich'})
        combinations = set()
        for name in installs:
            path = ROOT / 'installer/fixtures' / f'plan-{name}.json'
            if not path.exists():
                path = VM / 'fixtures' / f'plan-{name}.json'
            fixture = json.loads(path.read_text())
            combinations.add((fixture['fs'], fixture['software']))
            self.assertTrue(any(c.startswith(f'iso-boot-check.sh {name} ') and '--session niri-session' in c
                                for c in self.calls))
        self.assertEqual(combinations, {(fs, software) for fs in ('btrfs', 'ext4') for software in ('minimal', 'rich')})

    def test_encryption_requires_two_distinct_successful_runs(self):
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout)
        runs = [c for c in self.calls if c.startswith('iso-encrypt-check.sh')]
        self.assertEqual([c.split()[2] for c in runs], ['gate-1', 'gate-2'])
        self.assertIn('two consecutive', self.line(result, 'encrypt-btrfs'))
        identity = (self.base / 'out/encrypt/identity').read_text()
        self.assertIn(self.sha(self.test), identity)

    def test_encryption_second_failure_cannot_pass(self):
        result = self.gate(second_encrypt=1)
        self.assert_last(result, 'RESULT: FAILED at encrypt-btrfs', 1)
        self.assertIn('on run 2', self.line(result, 'encrypt-btrfs'))

    def test_encryption_first_failure_stops_repetition(self):
        result = self.gate(iso_encrypt_check=1)
        self.assert_last(result, 'RESULT: FAILED at encrypt-btrfs', 1)
        self.assertEqual(sum(c.startswith('iso-encrypt-check.sh') for c in self.calls), 1)

    def test_encryption_refuses_changed_image_or_checkout(self):
        for mutation in ('image', 'checkout'):
            with self.subTest(mutation=mutation):
                # A fresh gate output and original image isolate the two mutation controls.
                self.test.write_bytes(b'test image emaki.test=1\n')
                self.log.write_text('')
                result = self.gate('--out', str(self.base / f'out-{mutation}'), mutate=mutation)
                self.assert_last(result, 'RESULT: FAILED at encrypt-btrfs', 1)
                self.assertIn('changed during encryption run 1', self.line(result, 'encrypt-btrfs'))
                self.assertEqual(sum(c.startswith('iso-encrypt-check.sh') for c in self.calls), 1)

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

    def test_missing_new_job_inputs_cannot_pass(self):
        result = self.gate(evidence=False)
        self.assert_last(result, 'RESULT: NOT TESTED at rollback', 3)
        self.assertIn('NOT TESTED', self.line(result, 'boot-menu'))
        self.assertFalse(any(c.startswith('rollback-check') for c in self.calls))

    def test_stale_rollback_manifest_refused_before_acceptance(self):
        self.manifest['sha256']['iso'] = '0' * 64
        self.provenance.write_text(json.dumps(self.manifest))
        self.assert_last(self.gate(), 'RESULT: FAILED at rollback', 1)
        self.assertFalse(any(c.startswith('rollback-check') for c in self.calls))

    def test_malformed_provenance_refused(self):
        self.provenance.write_text('{')
        self.assert_last(self.gate(), 'RESULT: FAILED at rollback', 1)

    def test_rollback_exit_status_is_not_hidden(self):
        self.assert_last(self.gate(rollback=77), 'RESULT: NOT TESTED at rollback', 3)
        self.assert_last(self.gate('--out', str(self.base / 'out2'), rollback=1),
                         'RESULT: FAILED at rollback', 1)

    def test_unjudged_boot_menu_cannot_pass(self):
        self.menu_record['frames'][0]['judgment'] = 'NOT TESTED'
        self.save_menu()
        self.assert_last(self.gate(), 'RESULT: NOT TESTED at boot-menu', 3)

    def test_stale_boot_menu_iso_refused(self):
        self.menu_record['sha256'] = '0' * 64
        self.save_menu()
        self.assert_last(self.gate(), 'RESULT: FAILED at boot-menu', 1)

    def test_native_mode_must_match_advertised_size(self):
        self.menu_record['frames'][0]['advertised_size'] = [1920, 1080]
        self.save_menu()
        self.assert_last(self.gate(), 'RESULT: FAILED at boot-menu', 1)

    def test_reduced_uefi_mode_cannot_pass_native_display_checks(self):
        from PIL import Image
        config = self.menu / 'grub.cfg'
        config.write_text('set gfxmode="1024x768,800x600,auto"\n')
        for frame in self.menu_record['frames']:
            path = self.menu / frame['file']
            Image.new('RGB', (1024, 768), 'white').save(path)
            frame.update(sha256=self.sha(path), menu_mode=dict(size='1024x768',
                         source=config.name, sha256=self.sha(config)))
        self.save_menu()
        self.assert_last(self.gate(), 'RESULT: FAILED at boot-menu', 1)

    def test_every_uefi_size_requires_requested_mode(self):
        from PIL import Image
        config = self.menu / 'fallback.cfg'
        config.write_text('set gfxmode=1024x768,auto\n')
        for size in ([1366, 768], [3840, 2160], [1280, 800]):
            with self.subTest(size=size):
                frame = next(f for f in self.menu_record['frames'] if f['advertised_size'] == size)
                original = dict(frame)
                path = self.menu / frame['file']
                pixels = path.read_bytes()
                with Image.open(path) as picture:
                    picture.crop((0, 0, 1024, 768)).save(path)
                frame.update(sha256=self.sha(path), menu_mode=dict(size='1024x768',
                             source=config.name, sha256=self.sha(config)))
                self.save_menu()
                result = self.gate('--out', str(self.base / f'out-{size[0]}'))
                frame.clear()
                frame.update(original)
                path.write_bytes(pixels)
                self.assert_last(result, 'RESULT: FAILED at boot-menu', 1)

    def test_missing_menu_configuration_is_not_tested(self):
        del self.menu_record['frames'][0]['menu_mode']
        self.save_menu()
        self.assert_last(self.gate(), 'RESULT: NOT TESTED at boot-menu', 3)

    def test_menu_pixels_must_match_configured_mode(self):
        mode = self.menu_record['frames'][0]['menu_mode']
        (self.menu / mode['source']).write_text('set gfxmode=1024x768\n')
        mode.update(size='1024x768', sha256=self.sha(self.menu / mode['source']))
        self.save_menu()
        self.assert_last(self.gate(), 'RESULT: FAILED at boot-menu', 1)

    def test_menu_mode_must_match_retained_configuration(self):
        self.menu_record['frames'][0]['menu_mode']['size'] = '1024x768'
        self.save_menu()
        self.assert_last(self.gate(), 'RESULT: FAILED at boot-menu', 1)

    def test_menu_configuration_hash_is_checked(self):
        self.menu_record['frames'][0]['menu_mode']['sha256'] = '0' * 64
        self.save_menu()
        self.assert_last(self.gate(), 'RESULT: FAILED at boot-menu', 1)

    def test_reviewed_pixels_cannot_change(self):
        self.menu_record['frames'][0]['sha256'] = '0' * 64
        self.save_menu()
        self.assert_last(self.gate(), 'RESULT: FAILED at boot-menu', 1)

    def test_blank_boot_menu_frame_refused(self):
        from PIL import Image
        Image.new('RGB', (1366, 768), 'black').save(self.menu / self.menu_record['frames'][0]['file'])
        self.assert_last(self.gate(), 'RESULT: FAILED at boot-menu', 1)

    def test_confusing_frame_requires_recorded_waiver(self):
        self.menu_record['frames'][0]['judgment'] = 'CONFUSING'
        self.save_menu()
        self.assert_last(self.gate(), 'RESULT: FAILED at boot-menu', 1)
        (self.menu / 'waiver.txt').write_text('Accepted readability limitation for this candidate.')
        self.menu_record['frames'][0]['waiver'] = 'waiver.txt'
        self.save_menu()
        self.assert_last(self.gate('--out', str(self.base / 'out2')), 'RESULT: SCRIPTS PASSED', 0)

    def test_tiny_capitals_fail_even_with_waiver(self):
        from PIL import Image, ImageDraw
        frame = next(f for f in self.menu_record['frames'] if f['advertised_size'] == [2560, 1600])
        path = self.menu / frame['file']
        picture = Image.new('RGB', (2560, 1600), 'white')
        draw = ImageDraw.Draw(picture)
        for x in (10, 60, 110):
            draw.rectangle((x, 10, x + 3, 21), fill='black')
        picture.save(path)
        frame.update(sha256=self.sha(path), judgment='CONFUSING', waiver='waiver.txt')
        (self.menu / 'waiver.txt').write_text('Accept this appearance.')
        self.save_menu()
        self.assert_last(self.gate(), 'RESULT: FAILED at boot-menu', 1)

    def legacy_menu(self):
        self.menu_record['frames'] = [frame for frame in self.menu_record['frames']
                                      if frame['firmware'] == 'uefi' and frame['advertised_size'] != [1024, 768]]
        for frame in self.menu_record['frames']:
            frame.pop('firmware')
            frame.pop('capitals')
            path = self.menu / frame['file']
            shutil.copyfile(self.menu / 'bios-1024x768.png', path)
            config = self.menu / '1024x768.cfg'
            frame.update(sha256=self.sha(path), menu_mode=dict(size='1024x768',
                         source=config.name, sha256=self.sha(config)))
        self.save_menu()

    def test_systemd_boot_keeps_legacy_evidence_requirements(self):
        self.release.write_bytes(b'systemd-boot image\n')
        Path(str(self.release) + '.sha256').write_text(f'{self.sha(self.release)}  {self.release.name}\n')
        self.menu_record['sha256'] = self.sha(self.release)
        self.legacy_menu()
        self.assert_last(self.gate(), 'RESULT: SCRIPTS PASSED', 0)

    def test_manifest_cannot_downgrade_grub_to_systemd_boot(self):
        self.menu_record['bootloader'] = 'systemd-boot'
        self.legacy_menu()
        self.assert_last(self.gate(), 'RESULT: FAILED at boot-menu', 1)

    def test_missing_bios_is_not_tested(self):
        self.menu_record['frames'] = [f for f in self.menu_record['frames'] if f['firmware'] != 'bios']
        self.save_menu()
        self.assert_last(self.gate(), 'RESULT: NOT TESTED at boot-menu', 3)

    def test_missing_cap_measurements_is_not_tested(self):
        del self.menu_record['frames'][0]['capitals']
        self.save_menu()
        self.assert_last(self.gate(), 'RESULT: NOT TESTED at boot-menu', 3)

    def test_missing_required_resolution_is_not_tested(self):
        self.menu_record['frames'].pop(1)  # 1920x1080 is required by the walk matrix.
        self.save_menu()
        result = subprocess.run(['python3', str(VM / 'check-gate-evidence.py'), 'boot-menu',
                                 '--iso', str(self.release), '--directory', str(self.menu)],
                                text=True, capture_output=True, env=self.tool_env())
        self.assertEqual(result.returncode, 3, result.stdout)
        self.assertIn('1920x1080', result.stdout)

    def test_png_signature_alone_is_not_evidence(self):
        (self.menu / self.menu_record['frames'][0]['file']).write_bytes(b'\x89PNG\r\n\x1a\n')
        result = subprocess.run(['python3', str(VM / 'check-gate-evidence.py'), 'boot-menu',
                                 '--iso', str(self.release), '--directory', str(self.menu)],
                                text=True, capture_output=True, env=self.tool_env())
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_bad_verdict_cannot_hide_behind_another_frame_or_wrong_mode(self):
        for verdict in ('FAIL', 'BROKEN', 'BLACK', 'unknown'):
            with self.subTest(verdict=verdict):
                bad = dict(self.menu_record['frames'][0], judgment=verdict, advertised_size=[1, 1])
                self.menu_record['frames'].append(bad)
                self.save_menu()
                result = subprocess.run(['python3', str(VM / 'check-gate-evidence.py'), 'boot-menu',
                                         '--iso', str(self.release), '--directory', str(self.menu)],
                                        text=True, capture_output=True, env=self.tool_env())
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(verdict, result.stdout)
                self.menu_record['frames'].pop()

    def test_shellcheck(self):
        if not shutil.which('shellcheck'):
            self.skipTest('shellcheck is not installed')
        subprocess.run(['shellcheck', '-x', str(VM / 'release-gate.sh'), str(VM / 'jail.sh')], check=True)


class BootArtifactClaimTests(unittest.TestCase):
    def test_unused_bitmap_check_does_not_claim_grub_appearance(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tests/test-boot-splash.py')],
                                text=True, capture_output=True, check=True)
        self.assertIn('unused BMP artifact only; GRUB appearance is NOT TESTED', result.stdout)


if __name__ == '__main__':
    unittest.main()
