#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline negative and positive fixtures for rollback acceptance prerequisites."""
import importlib.util
import contextlib
import io
import json
import subprocess
from types import SimpleNamespace
from unittest import mock
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rollback_check', ROOT / 'tests/vm/rollback-check.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'arbitrary-candidate-name'
        self.base.mkdir()
        self.iso = self.root / 'candidate.iso'
        self.iso.write_bytes(b'candidate image fixture')
        for name in ('target.qcow2', 'OVMF_VARS.4m.fd'):
            (self.base / name).write_bytes(name.encode())
        self.packages = {p: '1.2.3-4' for p in ('emaki', 'emaki-config', 'emaki-desktop')}
        self.manifest = {'candidate': 'candidate-build', 'packages': self.packages,
                         'sha256': {'iso': check.digest(self.iso), **{
                             n: check.digest(self.base / n)
                             for n in ('target.qcow2', 'OVMF_VARS.4m.fd')}}}

    def validate(self):
        return check.validate_fixture(self.base, self.iso, self.manifest, 'candidate-build')

    def test_matching_fixture_passes(self):
        self.assertEqual(self.validate(), self.packages)

    def test_fresh_install_validates_image_and_package_identity(self):
        self.assertEqual(check.validate_fixture(None, self.iso, self.manifest, 'candidate-build'), self.packages)
        self.iso.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            check.validate_fixture(None, self.iso, self.manifest, 'candidate-build')

    def test_old_candidate_is_rejected(self):
        self.manifest['candidate'] = 'old-build'
        with self.assertRaisesRegex(ValueError, 'requested candidate'):
            self.validate()

    def test_changed_image_disk_or_firmware_is_rejected(self):
        for path in (self.iso, self.base / 'target.qcow2', self.base / 'OVMF_VARS.4m.fd'):
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(b'broken')
                with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                    self.validate()
                path.write_bytes(original)

    def test_missing_package_identity_is_rejected(self):
        del self.packages['emaki-config']
        with self.assertRaisesRegex(ValueError, 'required installed packages'):
            self.validate()

    def test_arbitrary_run_name_under_work_root(self):
        check.validate_area(self.root / 'release-42', self.base, self.root)

    def test_fixture_overlap_and_escape_rejected(self):
        for area in (self.root, self.base, self.base / 'child', self.root.parent / 'outside'):
            with self.subTest(area=area), self.assertRaises(ValueError):
                check.validate_area(area, self.base, self.root)

    def test_scope_does_not_claim_encrypted_or_hibernating_coverage(self):
        plan = {'fs': 'btrfs', 'encryption': 'none', 'hibernation': False}
        self.assertTrue(check.supported_plan(plan))
        for key, value in [('fs', 'ext4'), ('encryption', 'luks'), ('hibernation', True)]:
            self.assertFalse(check.supported_plan(dict(plan, **{key: value})))


class CaptureTests(unittest.TestCase):
    def test_capture_uses_host_decoder_and_assesses_written_frame(self):
        frames = mock.Mock()
        with mock.patch.object(check.subprocess, 'run') as run:
            check.capture_frame(Path('/tmp/disposable'), 'boot', 'menu', frames)
        command = run.call_args.args[0]
        self.assertIn(str(check.HERE / 'iso-shot.py'), command)
        self.assertNotIn('--monitor', command)
        frames.assess_frame.assert_called_once_with(Path('/tmp/disposable/boot.png'), 'menu')

    def test_missing_surface_capture_and_ocr_failures_retry_until_menu(self):
        capture = mock.Mock(side_effect=[OSError('no frame'),
                            subprocess.CalledProcessError(1, 'tesseract'),
                            RuntimeError('firmware screen'), {'status': 'PASS'}])
        tick = mock.Mock()
        with mock.patch.object(check.time, 'sleep'):
            result = check.wait_frame(capture, 'boot', 'menu', 90, tick)
        self.assertEqual(result, {'status': 'PASS'})
        self.assertEqual(tick.call_count, 4)

    def test_missing_screen_times_out_without_advancing(self):
        capture = mock.Mock(side_effect=subprocess.TimeoutExpired('capture', 25))
        with mock.patch.object(check.time, 'monotonic', side_effect=[0, 0, 91]), \
                mock.patch.object(check.time, 'sleep'), \
                self.assertRaisesRegex(RuntimeError, 'Timed out waiting for menu'):
            check.wait_frame(capture, 'boot', 'menu', 90)
        self.assertEqual(capture.call_count, 2)


class ShortCommandTests(unittest.TestCase):
    def test_flag_candidate_must_match_image_commit(self):
        candidate = 'a' * 40
        manifest = check.flag_provenance(candidate, 'b' * 64,
                                        ['emaki=1', 'emaki-config=1', 'emaki-desktop=1'])
        release = 'VERSION=0.3.1\nLABEL=alpha\nEMAKI_COMMIT=' + candidate + '\n'
        self.assertEqual(check.verify_candidate(release, manifest['candidate']), release)
        with self.assertRaisesRegex(ValueError, 'EMAKI_COMMIT'):
            check.verify_candidate(release.replace(candidate, 'c' * 40), manifest['candidate'])

    def test_missing_ambiguous_or_incomplete_image_commit_fails(self):
        commit = 'a' * 40
        for release in ('VERSION=0.3.1\n', 'EMAKI_COMMIT=\n', 'EMAKI_COMMIT=aaaaaaa\n',
                        'EMAKI_COMMIT=' + commit + '\nEMAKI_COMMIT=' + commit + '\n',
                        'EMAKI_COMMIT=' + commit + '\nEMAKI_COMMIT=invalid\n'):
            with self.subTest(release=release), self.assertRaisesRegex(ValueError, 'EMAKI_COMMIT'):
                check.verify_candidate(release, commit)

    def test_matching_malformed_commit_and_candidate_fail(self):
        for candidate in ('a' * 7, 'a' * 39, 'a' * 41, 'g' * 40):
            with self.subTest(candidate=candidate), self.assertRaisesRegex(ValueError, 'EMAKI_COMMIT'):
                check.verify_candidate('EMAKI_COMMIT=' + candidate + '\n', candidate)

    def test_matching_short_prefix_does_not_identify_candidate(self):
        candidate = 'a' * 40
        release = 'EMAKI_COMMIT=' + 'a' * 39 + 'b\n'
        with self.assertRaisesRegex(ValueError, 'EMAKI_COMMIT'):
            check.verify_candidate(release, candidate)

    def test_encrypted_plan_matches_fixture_overrides(self):
        plan = check.encrypted_plan()
        self.assertTrue(check.supported_plan(plan, True))
        self.assertEqual(plan['software'], 'minimal')
        self.assertFalse(plan['online_update'])
        self.assertEqual(plan['encryption'], 'account')

    def test_flags_build_provenance_without_a_generated_file(self):
        manifest = check.flag_provenance('candidate', 'A' * 64,
                     ['emaki=0.3.1-1', 'emaki-config=0.3.1-1', 'emaki-desktop=0.3.1-1'])
        self.assertEqual(manifest['sha256']['iso'], 'a' * 64)
        self.assertEqual(manifest['packages']['emaki'], '0.3.1-1')

    def test_ambiguous_flags_fail(self):
        for packages in (['emaki=1', 'emaki=2'], ['emaki'], ['emaki=']):
            with self.subTest(packages=packages), self.assertRaises(ValueError):
                check.flag_provenance('candidate', 'a' * 64, packages)
        with self.assertRaises(ValueError):
            check.flag_provenance('candidate', 'unchecked', [])


class LiveImageCandidateTests(unittest.TestCase):
    class InstallerReached(Exception):
        pass

    def run_live_image(self, commit, expected_error):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            iso = root / 'candidate.iso'
            iso.write_bytes(b'candidate image fixture')
            identity = root / 'id_vm'
            identity.write_text('disposable identity fixture')
            candidate = 'a' * 40
            argv = ['rollback-check.py', '--install-encrypted-hibernation',
                    '--work-root', str(root), '--iso', str(iso), '--candidate', candidate,
                    '--iso-sha256', check.digest(iso), '--identity', str(identity)]
            for package in ('emaki', 'emaki-config', 'emaki-desktop'):
                argv.extend(['--package', package + '=1.2.3-4'])
            calls = []

            def run(command, **kwargs):
                calls.append(command)
                if command[-1].startswith('emaki-install-cli '):
                    raise self.InstallerReached('installer reached')
                output = ('EMAKI_COMMIT=' + commit + '\n').encode() if command[-1] == 'cat /usr/lib/emaki-release' else b''
                return subprocess.CompletedProcess(command, 0, output, b'')

            frame_spec = SimpleNamespace(loader=mock.Mock())
            live = mock.Mock()
            live.poll.return_value = None
            with mock.patch.object(check.sys, 'argv', argv), \
                    mock.patch.dict(check.os.environ, VMDIR=str(root / 'runs')), \
                    mock.patch.object(check.importlib.util, 'spec_from_file_location', return_value=frame_spec), \
                    mock.patch.object(check.importlib.util, 'module_from_spec', return_value=mock.Mock()), \
                    mock.patch.object(check.subprocess, 'Popen', return_value=live) as start, \
                    mock.patch.object(check.subprocess, 'run', side_effect=run), \
                    mock.patch.object(check.monitor, 'command') as monitor, \
                    contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaisesRegex(expected_error, 'EMAKI_COMMIT|installer reached'):
                check.main()
            start.assert_called_once()
            monitor.assert_called_once()
            self.assertEqual(monitor.call_args.args[1], 'quit')
            live.wait.assert_called_once_with(timeout=30)
            return calls

    def test_wrong_live_commit_fails_before_installing(self):
        calls = self.run_live_image('a' * 39 + 'b', ValueError)
        self.assertTrue(any(command[-1] == 'cat /usr/lib/emaki-release' for command in calls))
        self.assertFalse(any(command[-1].startswith('emaki-install-cli ') for command in calls))

    def test_matching_live_commit_reaches_installer(self):
        calls = self.run_live_image('a' * 40, self.InstallerReached)
        self.assertTrue(any(command[-1].startswith('emaki-install-cli ') for command in calls))


class ResumeTests(unittest.TestCase):
    def state(self):
        return {
            'cmdline': 'root=UUID=abc rootflags=subvol=@ resume=UUID=abc resume_offset=123 '
                       'cryptdevice=UUID=1234-abcd:emaki-root '
                       'cryptkey=rootfs:/etc/cryptsetup-keys.d/emaki-root.key',
            'offset': '123', 'kernel_offset': '123', 'kernel_resume': '253:0',
            'swap_source': '/dev/mapper/emaki-root', 'swap_device': '253:0', 'luks_uuid': '1234-abcd', 'crypt_uuid': 'abc', 'swap_uuid': 'abc', 'swap_fsroot': '/@swap',
            'swap_size': 6 * 1024**3, 'swap_inode': 257,
            'swaps': 'Filename Type Size Used Priority\n/swap/swapfile file 6291452 0 10\n',
            'hooks': 'HOOKS=(base encrypt emaki-resume filesystems)\n',
            'initramfs': 'hooks/emaki-resume\nhooks/emaki-resume-upstream\n'
                         'etc/cryptsetup-keys.d/emaki-root.key\n',
            'crypt': '/dev/mapper/emaki-root is active and is in use.\n  type: LUKS2\n',
        }

    def test_probe_resolves_btrfs_source_to_real_block_device(self):
        calls = []
        def output(args, **kwargs):
            calls.append(args)
            if args[0] == 'findmnt':
                return {'SOURCE': '/dev/mapper/emaki-root[/@swap]',
                        'UUID': 'abc', 'FSROOT': '/@swap', 'MAJ:MIN': '0:28'}[args[3]]
            if args[0] == 'lsblk':
                self.assertEqual(args[-1], '/dev/mapper/emaki-root')
                return '253:0'
            return '123'
        def read(path):
            return {'/sys/power/resume': '253:0', '/sys/power/resume_offset': '123'}.get(str(path), '')
        stream = io.StringIO()
        program = check.RESUME_PROBE.split("\n", 1)[1].rsplit('\nPYPROBE', 1)[0]
        with mock.patch.object(check.subprocess, 'check_output', side_effect=output), \
                mock.patch.object(Path, 'stat', return_value=SimpleNamespace(st_size=6 * 1024**3, st_ino=257)), \
                mock.patch.object(Path, 'read_text', read), contextlib.redirect_stdout(stream):
            exec(program, {})
        state = json.loads(stream.getvalue())
        self.assertEqual(state['swap_device'], state['kernel_resume'])
        self.assertEqual(state['swap_device'], '253:0')
        self.assertIn(('btrfs', 'inspect-internal', 'map-swapfile', '-r', '/swap/swapfile'), calls)
        self.assertFalse(any(args[0] == 'findmnt' and 'MAJ:MIN' in args for args in calls))

    def test_anonymous_filesystem_device_is_not_a_resume_device(self):
        with self.assertRaisesRegex(ValueError, 'resume device'):
            check.validate_resume(dict(self.state(), swap_device='0:28', kernel_resume='0:28'))

    def test_encrypted_hibernation_requires_explicit_mode(self):
        config = {'fs': 'btrfs', 'encryption': 'account', 'hibernation': True, 'mode': 'erase'}
        self.assertFalse(check.supported_plan(config))
        self.assertTrue(check.supported_plan(config, True))
        self.assertTrue(check.supported_plan(dict(config, encryption='separate'), True))
        for key, value in [('fs', 'ext4'), ('hibernation', False), ('encryption', 'none'), ('mode', 'manual')]:
            self.assertFalse(check.supported_plan(dict(config, **{key: value}), True))

    def test_matching_cold_boot_and_rollback_pass(self):
        before = self.state()
        check.validate_resume(before)
        after = self.state()
        after['cmdline'] = after['cmdline'].replace('subvol=@', 'subvol=@snapshots/42/snapshot')
        self.assertEqual(check.validate_resume(after, before), after)

    def test_mismatched_resume_or_missing_artifacts_fail(self):
        changes = {
            'offset': '0', 'kernel_offset': '456', 'kernel_resume': '0:0',
            'swap_device': '253:1', 'luks_uuid': 'ffff-abcd', 'crypt_uuid': 'other', 'swap_uuid': 'other', 'swap_fsroot': '/@',
            'swap_size': 4096, 'swaps': 'Filename Type Size Used Priority\n',
            'hooks': 'HOOKS=(base emaki-resume encrypt filesystems)\n',
            'initramfs': 'hooks/emaki-resume\n', 'crypt': '  type: PLAIN\n',
        }
        for key, value in changes.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                check.validate_resume(dict(self.state(), **{key: value}))

    def test_ambiguous_and_missing_kernel_arguments_fail(self):
        original = self.state()
        for name in ('root', 'resume', 'resume_offset', 'cryptdevice', 'cryptkey'):
            tokens = original['cmdline'].split()
            token = next(t for t in tokens if t.startswith(name + '='))
            for line in (' '.join(t for t in tokens if t != token), original['cmdline'] + ' ' + token):
                with self.subTest(name=name, line=line), self.assertRaises(ValueError):
                    check.validate_resume(dict(original, cmdline=line))

    def test_changed_swap_inode_and_physical_offset_fail(self):
        before = self.state()
        after = dict(before, swap_inode=999)
        with self.assertRaisesRegex(ValueError, 'changed swap identity'):
            check.validate_resume(after, before)
        after = dict(before, offset='456', kernel_offset='456',
                     cmdline=before['cmdline'].replace('offset=123', 'offset=456'))
        with self.assertRaisesRegex(ValueError, 'changed swap identity'):
            check.validate_resume(after, before)

    def test_duplicate_hook_and_duplicate_active_swap_fail(self):
        for state in (dict(self.state(), hooks='HOOKS=(encrypt emaki-resume emaki-resume filesystems)'),
                      dict(self.state(), swaps=self.state()['swaps'] + '/swap/swapfile file 100 0 10\n')):
            with self.assertRaises(ValueError):
                check.validate_resume(state)


class InstalledTests(unittest.TestCase):
    def run_fixture(self, *, version='1.2.3-4', owner='emaki-config', damaged=False, override=False,
                    commit='a' * 40):
        calls = []
        def remote(command, **kwargs):
            calls.append(command)
            if command == 'cat /usr/lib/emaki-release':
                return 'EMAKI_COMMIT=' + commit + '\n'
            if command.startswith('pacman -Q '):
                return command.split()[-1] + ' ' + version + '\n'
            if command.startswith('pacman -Qkk '):
                if damaged:
                    raise RuntimeError('altered package file')
                return '0 altered files\n'
            if command.startswith('pacman -Qqo '):
                return owner + '\n'
            if command.startswith('test ! -e') and override:
                raise RuntimeError('shadowing hook exists')
            return ''
        check.verify_installed(remote, lambda *_: None, {'emaki-config': '1.2.3-4'}, 'a' * 40)
        return calls

    def test_packaged_candidate_passes_without_production_writes(self):
        calls = self.run_fixture()
        self.assertEqual(sum(c.startswith('pacman -Qqo') for c in calls), len(check.PAYLOAD))
        self.assertFalse(any('install ' in c or 'mkinitcpio -P' in c for c in calls))
        for hook in ('emaki-resume', 'emaki-snapshot-fstab'):
            for directory in ('hooks', 'install'):
                self.assertIn('test ! -e /etc/initcpio/' + directory + '/' + hook, calls)

    def test_old_installed_version_fails(self):
        with self.assertRaisesRegex(ValueError, 'version mismatch'):
            self.run_fixture(version='0.1.1-1')

    def test_wrong_installed_commit_fails_with_matching_package_versions(self):
        with self.assertRaisesRegex(ValueError, 'EMAKI_COMMIT'):
            self.run_fixture(commit='b' * 40)

    def test_wrong_owner_fails(self):
        with self.assertRaisesRegex(ValueError, 'payload owner'):
            self.run_fixture(owner='foreign-package')

    def test_altered_payload_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'altered package'):
            self.run_fixture(damaged=True)

    def test_shadowing_hook_fails(self):
        with self.assertRaisesRegex(RuntimeError, 'shadowing hook'):
            self.run_fixture(override=True)


if __name__ == '__main__':
    unittest.main()
