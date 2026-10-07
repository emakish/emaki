#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prove published package identity with separate builds of one real recipe."""

import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('publish', ROOT / 'packaging/mirror/publish.py')
publish = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publish)

RECIPE = '''
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
pkgname=emaki-immutable-test
pkgver=1.0
pkgrel=1
pkgdesc='Package identity fixture'
arch=('any')
license=('GPL-3.0-or-later')
source=('payload')
sha256sums=('SKIP')
package() {
    install -Dm644 "$srcdir/payload" "$pkgdir/usr/share/emaki-immutable-test/payload"
}
'''


@unittest.skipUnless(all(shutil.which(tool) for tool in ('makepkg', 'fakeroot', 'bsdtar', 'zstd'))
                     and os.geteuid() != 0, 'rootless makepkg and packaging tools are required')
class RealBuildIdentity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        evidence = ROOT / '.cache/evidence'
        evidence.mkdir(parents=True, exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix='immutability-', dir=evidence)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.original = cls.build('original', 'first content\n')
        cls.rebuilt = cls.build('rebuilt', 'second content\n')
        cls.bumped = cls.build('bumped', 'second content\n', release=2)

    @classmethod
    def build(cls, directory, payload, release=1):
        work = cls.root / directory
        work.mkdir()
        (work / 'PKGBUILD').write_text(RECIPE.replace('pkgrel=1', f'pkgrel={release}'))
        (work / 'payload').write_text(payload)
        config = work / 'makepkg.conf'
        config.write_text(Path('/etc/makepkg.conf').read_text() +
                          '\nPKGEXT=".pkg.tar.zst"\nOPTIONS=(!strip !debug !lto)\n')
        environment = dict(os.environ, HOME=str(work), XDG_CONFIG_HOME=str(work / 'config'),
                           PKGDEST=str(work), SRCDEST=str(work), BUILDDIR=str(work),
                           SOURCE_DATE_EPOCH='1700000000')
        result = subprocess.run(['makepkg', '--config', str(config), '--nodeps', '--nocheck',
                                 '--nosign', '--noconfirm'], cwd=work, env=environment,
                                text=True, capture_output=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        packages = list(work.glob('*.pkg.tar.zst'))
        if len(packages) != 1:
            raise AssertionError(f'Expected one built package: {packages}')
        return packages[0]

    def check_candidate(self, package):
        publisher = publish.Publisher.__new__(publish.Publisher)
        publisher.args = SimpleNamespace(registry=None)
        manifest = {f'packages/{self.original.name}':
                    hashlib.sha256(self.original.read_bytes()).hexdigest()}
        metadata = publish.read_zst_pkginfo(package)
        candidate = {package.name: {'name': metadata['pkgname'], 'version': metadata['pkgver'],
                                   'sha256': hashlib.sha256(package.read_bytes()).hexdigest()}}
        with mock.patch.object(publisher, 'all_manifests', return_value={'published': manifest}):
            publisher.refuse_burnt(candidate)

    def test_changed_content_under_same_version_is_refused(self):
        self.assertEqual(self.original.name, self.rebuilt.name)
        self.assertNotEqual(self.original.read_bytes(), self.rebuilt.read_bytes())
        for package, expected in ((self.original, 'first content\n'),
                                  (self.rebuilt, 'second content\n')):
            content = subprocess.run(['bsdtar', '-xOf', str(package),
                                      'usr/share/emaki-immutable-test/payload'],
                                     check=True, capture_output=True, text=True).stdout
            self.assertEqual(content, expected)
        with self.assertRaisesRegex(publish.Refused, 'Raise pkgrel and rebuild'):
            self.check_candidate(self.rebuilt)

    def test_identical_published_bytes_are_accepted(self):
        self.check_candidate(self.original)

    def test_rebuild_with_bumped_release_is_accepted(self):
        self.assertNotEqual(self.original.name, self.bumped.name)
        self.check_candidate(self.bumped)


class ResignedPackage(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='resigned-package-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package = self.root / 'emaki-1-1-any.pkg.tar.zst'
        self.package.write_bytes(b'identical package bytes')
        Path(str(self.package) + '.sig').write_bytes(b'new trusted signature')
        self.publisher = publish.Publisher.__new__(publish.Publisher)
        self.publisher.args = SimpleNamespace(closure='emaki')
        self.publisher.state = self.root / 'state'
        self.publisher.backend = publish.LocalBackend(self.root / 'bucket')
        self.publisher._verifier = mock.Mock()
        self.publisher.verifier.verify.return_value = True
        self.entry = {'path': str(self.package), 'sha256': publish.sha256_file(self.package),
                      'sig': publish.sha256(b'new trusted signature'), 'source': {}}
        self.key = f'testing/x86_64/{self.package.name}'
        self.publisher.backend.put_new(self.key, self.package)
        self.publisher.backend.put_new(self.key + '.sig', b'old trusted signature')

    def publish_to_anonymous_check(self, steps):
        class AnonymousCheckReached(Exception):
            pass

        journal = {'id': 'resigned', 'steps': list(steps)}
        def build_trial(channel, work, packages, sign):
            work.mkdir(parents=True, exist_ok=True)
            (work / 'emaki.db.tar.gz').write_bytes(b'trial database')
        with mock.patch.object(self.publisher, 'collect', return_value={self.package.name: self.entry}), \
                mock.patch.object(self.publisher, 'begin', return_value=journal), \
                mock.patch.object(self.publisher, 'build_db', side_effect=build_trial), \
                mock.patch.object(self.publisher, 'check_release'), \
                mock.patch.object(self.publisher, 'take_lock'), \
                mock.patch.object(self.publisher, 'done'), \
                mock.patch.object(self.publisher, 'verify_packages_anonymously',
                                  side_effect=AnonymousCheckReached) as anonymous:
            with self.assertRaises(AnonymousCheckReached):
                self.publisher.publish('testing', self.root)
            anonymous.assert_called_once_with('testing', {
                self.package.name: (self.entry['sha256'], publish.sha256(b'old trusted signature'))})

    def test_new_signature_retains_published_signature_and_database_identity(self):
        self.publish_to_anonymous_check([0, 1])
        self.assertEqual(self.publisher.backend.get(self.key + '.sig')[0], b'old trusted signature')
        self.assertEqual(Path(str(self.package) + '.sig').read_bytes(), b'new trusted signature')
        self.publisher.signer = mock.Mock()
        with mock.patch.object(self.publisher, 'previous_db'):
            pool = self.publisher.build_db('testing', self.root / 'database',
                                           {self.package.name: self.entry}, sign=True)
        self.assertEqual((pool / (self.package.name + '.sig')).read_bytes(), b'old trusted signature')

    def test_resume_reselects_published_signature_for_manifest(self):
        self.publish_to_anonymous_check([0, 1, 2])

    def test_invalid_published_signature_is_refused(self):
        self.publisher.verifier.verify.return_value = False
        with self.assertRaisesRegex(publish.Refused, 'published signature.*does not verify'):
            self.publish_to_anonymous_check([0, 1])


if __name__ == '__main__':
    unittest.main()
