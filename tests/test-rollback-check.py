#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline negative and positive fixtures for rollback acceptance prerequisites."""
import importlib.util
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


class InstalledTests(unittest.TestCase):
    def run_fixture(self, *, version='1.2.3-4', owner='emaki-config', damaged=False, override=False):
        calls = []
        def remote(command, **kwargs):
            calls.append(command)
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
        check.verify_installed(remote, lambda *_: None, {'emaki-config': '1.2.3-4'})
        return calls

    def test_packaged_candidate_passes_without_production_writes(self):
        calls = self.run_fixture()
        self.assertEqual(sum(c.startswith('pacman -Qqo') for c in calls), len(check.PAYLOAD))
        self.assertFalse(any('install ' in c or 'mkinitcpio -P' in c for c in calls))

    def test_old_installed_version_fails(self):
        with self.assertRaisesRegex(ValueError, 'version mismatch'):
            self.run_fixture(version='0.1.1-1')

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
