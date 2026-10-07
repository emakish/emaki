#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check exact archive eligibility and offline verification failures."""
import copy
import hashlib
import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging/mirror'))
import arch_source_fallback as fallback


class ArchiveFallbackTests(unittest.TestCase):
    def test_libfakekey_documentation_tracks_collected_fallback(self):
        root = Path(__file__).resolve().parents[1]
        guide = (root / 'docs/mirror.md').read_text()
        self.assertIn('libfakekey 0.3-4 is collected from the Arch sourceball', guide)
        decisions = (root / 'DECISIONS.md').read_text()
        old = decisions.split('Keep libfakekey refused without exact downloadable bytes', 1)[1].split('\n## ', 1)[0]
        self.assertIn('Superseded', old)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.payload = b'complete upstream source\n'
        self.recipe = b'pkgname=libfakekey\npkgver=0.3\npkgrel=4\n'
        self.info = ('pkgbase = libfakekey\npkgname = libfakekey\npkgver = 0.3\npkgrel = 4\n'
                     'source = https://example.invalid/source.tar.gz\nsha256sums = '
                     + hashlib.sha256(self.payload).hexdigest() + '\n')
        self.files = {'libfakekey/PKGBUILD': self.recipe,
                      'libfakekey/.SRCINFO': self.info.encode(),
                      'libfakekey/source.tar.gz': self.payload}
        self.archive = self.root / 'source.tar.gz'
        self.write_archive()
        reviewed = ('0.3-4', fallback.digest(self.recipe),
                    fallback.digest(self.archive.read_bytes()),
                    fallback.structured_digest(fallback.source_routes.fields(self.info)),
                    fallback.structured_digest({'libfakekey/source.tar.gz': fallback.digest(self.payload)}))
        self.context = patch.dict(fallback.REVIEWED, {'libfakekey': reviewed})
        self.context.start()
        self.addCleanup(self.context.stop)
        self.record = {'base': 'libfakekey', 'version': '0.3-4', 'recipe_sha256': reviewed[1],
                       'git_sources': []}
        self.record['source_routes'] = [fallback.evidence(self.record)]

    def write_archive(self, extra=None):
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name, data in self.files.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            if extra is not None:
                archive.addfile(extra)

    def test_complete_archive_and_collection(self):
        fallback.validate(self.archive, self.record)
        def run(*args, **kwargs):
            self.assertEqual(args[0], 'curl')
            Path(args[args.index('--output') + 1]).write_bytes(self.archive.read_bytes())
            return 'https://sources.example.invalid/served.src.tar.gz'
        complete, route, pins = fallback.collect(self.record, self.recipe, self.info, self.root, run)
        self.assertEqual(route['effective_url'], 'https://sources.example.invalid/served.src.tar.gz')
        self.assertEqual({k: v for k, v in route.items() if k != 'effective_url'},
                         self.record['source_routes'][0])
        fallback.validate(complete, dict(self.record, source_routes=[route]))
        self.assertEqual(pins, [])
        self.assertEqual(complete.read_bytes(), self.archive.read_bytes())

    def test_missing_source(self):
        del self.files['libfakekey/source.tar.gz']
        self.write_archive()
        with self.assertRaisesRegex(ValueError, 'contents differ'):
            fallback.validate(self.archive, self.record)

    def test_tampered_recipe(self):
        self.files['libfakekey/PKGBUILD'] += b'# changed\n'
        self.write_archive()
        with self.assertRaisesRegex(ValueError, 'recipe differs'):
            fallback.validate(self.archive, self.record)

    def test_tampered_source(self):
        self.files['libfakekey/source.tar.gz'] = b'changed'
        self.write_archive()
        with self.assertRaisesRegex(ValueError, 'contents differ'):
            fallback.validate(self.archive, self.record)

    def test_recipe_checksum_is_checked(self):
        self.files['libfakekey/source.tar.gz'] = b'changed'
        reviewed = list(fallback.REVIEWED['libfakekey'])
        reviewed[4] = fallback.structured_digest({'libfakekey/source.tar.gz': fallback.digest(b'changed')})
        fallback.REVIEWED['libfakekey'] = tuple(reviewed)
        self.record['source_routes'] = [fallback.evidence(self.record)]
        self.write_archive()
        with self.assertRaisesRegex(ValueError, 'recipe checksum'):
            fallback.validate(self.archive, self.record)

    def test_tampered_evidence(self):
        record = copy.deepcopy(self.record)
        record['source_routes'][0]['archive_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'evidence differs'):
            fallback.validate(self.archive, record)

    def test_wrong_version(self):
        record = dict(self.record, version='0.3-5')
        self.assertFalse(fallback.eligible(record))
        with self.assertRaisesRegex(ValueError, 'unreviewed'):
            fallback.validate(self.archive, record)

    def test_unsafe_members(self):
        for name in ('libfakekey/../escape', '/absolute', 'libfakekey/source.tar.gz'):
            with self.subTest(name=name):
                self.write_archive(tarfile.TarInfo(name))
                with self.assertRaisesRegex(ValueError, 'unsafe or duplicate'):
                    fallback.validate(self.archive, self.record)
        link = tarfile.TarInfo('libfakekey/link')
        link.type = tarfile.SYMTYPE
        link.linkname = '/etc/passwd'
        self.write_archive(link)
        with self.assertRaisesRegex(ValueError, 'unsafe or duplicate'):
            fallback.validate(self.archive, self.record)

    def test_tag_evidence_requires_exact_identity(self):
        version, recipe, *_ = fallback.REVIEWED['jack2']
        record = {'base': 'jack2', 'version': version, 'recipe_sha256': recipe}
        self.assertFalse(fallback.allows_tag(record, fallback.SOURCE))
        record['source_routes'] = [fallback.evidence(record)]
        self.assertTrue(fallback.allows_tag(record, fallback.SOURCE))
        record['source_routes'][0]['effective_url'] = 'https://sources.example.invalid/served.src.tar.gz'
        self.assertTrue(fallback.allows_tag(record, fallback.SOURCE))
        self.assertFalse(fallback.allows_tag(record, fallback.SOURCE + '-other'))
        record['version'] = '1.9.22-3'
        self.assertFalse(fallback.allows_tag(record, fallback.SOURCE))


if __name__ == '__main__':
    unittest.main()
