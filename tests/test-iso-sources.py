#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline fixtures for exact image source collection and publication validation."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('iso_sources', ROOT / 'packaging/mirror/iso_sources.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Sources(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.closure = self.root / 'closure.txt'
        self.recipe = b'pkgname=demo\npkgver=1\npkgrel=1\n'
        self.sha = hashlib.sha256(self.recipe).hexdigest()

    def package(self, name='demo', licence='GPL-2.0-only', recipe=True):
        filename = name + '-1-1-x86_64.pkg.tar.zst'
        with tarfile.open(self.root / filename, 'w') as archive:
            metadata = f'pkgname = {name}\npkgbase = demo\npkgver = 1-1\nlicense = {licence}\n'
            members = {'.PKGINFO': metadata, '.BUILDINFO':
                       ('pkgbuild_sha256sum = ' + self.sha + '\n') if recipe else ''}
            for key, value in members.items():
                item = tarfile.TarInfo(key)
                item.size = len(value.encode())
                archive.addfile(item, io.BytesIO(value.encode()))
        with self.closure.open('a') as stream:
            stream.write(filename + '\n')
        return filename

    def test_inventory_licenses_and_missing_hash(self):
        selected = self.package()
        self.package('library', 'LGPL-2.1-or-later')
        self.package('permissive', 'MIT', False)
        self.assertEqual(len(m.inventory(self.closure, self.root)), 2)
        self.assertEqual(len(m.inventory(self.closure, self.root, ['demo'])), 1)
        self.package('broken', 'GPL', False)
        with self.assertRaisesRegex(ValueError, 'recipe hash'):
            m.inventory(self.closure, self.root)
        self.assertTrue(selected)

    def test_exact_revision_uses_binary_hash(self):
        repo = self.root / 'repo'
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        for content in (self.recipe, b'wrong recipe\n'):
            (repo / 'PKGBUILD').write_bytes(content)
            subprocess.run(['git', '-C', str(repo), 'add', 'PKGBUILD'], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture',
                            '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'Recipe'], check=True)
        revision = m.exact_revision(repo, self.sha)
        self.assertEqual(subprocess.check_output(['git', '-C', str(repo), 'show',
                                                 revision + ':PKGBUILD']), self.recipe)
        with self.assertRaisesRegex(ValueError, 'absent'):
            m.exact_revision(repo, '0' * 64)

    def test_recipe_rejects_wrong_version_and_moving_vcs(self):
        record = {'name': 'demo', 'version': '2:1-1'}
        text = 'pkgname = demo\npkgver = 1\npkgrel = 1\nepoch = 2\n'
        m.check_recipe(text, record)
        with self.assertRaises(ValueError):
            m.check_recipe(text.replace('pkgrel = 1', 'pkgrel = 2'), record)
        with self.assertRaisesRegex(ValueError, 'unchecked'):
            m.check_recipe(text + 'source = https://example.invalid/latest.tar.gz\nsha256sums = SKIP\n', record)
        with self.assertRaisesRegex(ValueError, 'unpinned'):
            m.check_recipe(text + 'source = git+https://example.invalid/repo#branch=main\n', record)

    def test_collection_split_packages_and_manifest_validation(self):
        filename = self.package()
        self.package('demo-library', 'LGPL')
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('makepkg', '--printsrcinfo'):
                return 'pkgname = demo\npkgname = demo-library\npkgver = 1\npkgrel = 1\n'
            if args[:2] == ('makepkg', '--allsource'):
                archive = Path(kwargs['env']['SRCPKGDEST']) / 'demo.src.tar.gz'
                with tarfile.open(archive, 'w:gz') as out:
                    member = tarfile.TarInfo('demo/PKGBUILD')
                    member.size = len(self.recipe)
                    out.addfile(member, io.BytesIO(self.recipe))
                return ''
            if args[:2] == ('git', 'clone'):
                Path(args[-1]).mkdir()
                return ''
            if args[0] == 'git':
                return ''
            return original(*args, **kwargs)

        original = m.run
        output = self.root / 'output'
        with patch.object(m, 'run', side_effect=fake_run), patch.object(m, 'exact_revision', return_value='a' * 40):
            m.collect(self.closure, self.root, output)
        self.assertEqual(sum(call[:2] == ('makepkg', '--allsource') for call in calls), 1)
        manifest = output / 'ARCH-SOURCES.json'
        records, objects = m.validate(manifest, self.closure, self.root)
        self.assertEqual(len(records), 2)
        self.assertEqual(len(objects), 1)
        self.assertIn('https://dl.emaki.sh/sources/sha256/', m.directions(records, 'https://dl.emaki.sh'))
        saved = manifest.read_text()
        data = json.loads(saved)
        del data['packages'][filename]
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'missing required'):
            m.validate(manifest, self.closure, self.root)
        data = json.loads(saved)
        data['packages'][filename]['binary_sha256'] = '0' * 64
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'differs from the image binary'):
            m.validate(manifest, self.closure, self.root)
        manifest.write_text(saved)
        next(iter(objects.values())).write_bytes(b'broken')
        with self.assertRaises(ValueError):
            m.validate(manifest, self.closure, self.root)
        self.assertIn(filename, records)

    def test_normalization_rejects_external_links(self):
        source = self.root / 'input.tar.gz'
        with tarfile.open(source, 'w:gz') as archive:
            item = tarfile.TarInfo('demo/source')
            item.type, item.linkname = tarfile.SYMTYPE, '../../external'
            archive.addfile(item)
        with self.assertRaisesRegex(ValueError, 'outside'):
            m.normalize(source, self.root / 'output.tar.gz')

    def test_normalization_removes_timestamps(self):
        results = []
        for timestamp in (1, 20000):
            source = self.root / 'input.tar.gz'
            with tarfile.open(source, 'w:gz') as archive:
                item = tarfile.TarInfo('demo/PKGBUILD')
                item.size, item.mtime, item.uid = 4, timestamp, timestamp
                archive.addfile(item, io.BytesIO(b'test'))
            target = self.root / f'{timestamp}.gz'
            m.normalize(source, target)
            results.append(target.read_bytes())
        self.assertEqual(*results)


if __name__ == '__main__':
    unittest.main()
