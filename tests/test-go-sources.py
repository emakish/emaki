#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline checks for pinned Go source supplements and restoration evidence."""
import importlib.util
import io
from pathlib import Path
import subprocess
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    'go_sources', Path(__file__).resolve().parents[1] / 'packaging/mirror/go_sources.py')
GO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GO)


class GoSources(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.record = {'base': 'cliphist', 'recipe_sha256': next(
            key for key, value in GO.RECIPES.items() if value[0] == 'cliphist')}
        self.tree = self.root / 'source'
        self.tree.mkdir()
        self.mod = b'module example.invalid/app\n\ngo 1.20\nrequire example.invalid/lib v1.0.0\n'
        self.sums = (b'example.invalid/lib v1.0.0 h1:' + b'A' * 43 + b'=\n'
                     b'example.invalid/lib v1.0.0/go.mod h1:' + b'B' * 43 + b'=\n')
        (self.tree / 'go.mod').write_bytes(self.mod)
        (self.tree / 'go.sum').write_bytes(self.sums)
        self.destination = self.root / 'supplement'

    def vendor(self, command, **kwargs):
        self.assertEqual(command[:4], ['go', 'mod', 'vendor', '-o'])
        self.assertEqual(kwargs['env']['GOFLAGS'], '-mod=readonly')
        self.assertEqual(kwargs['env']['GOWORK'], 'off')
        self.assertEqual(kwargs['env']['GOTOOLCHAIN'], 'local')
        folder = Path(command[-1])
        (folder / 'example.invalid/lib').mkdir(parents=True)
        (folder / 'example.invalid/lib/lib.go').write_bytes(b'package lib\n')
        (folder / 'modules.txt').write_bytes(b'# example.invalid/lib v1.0.0\n## explicit; go 1.20\nexample.invalid/lib\n')
        return subprocess.CompletedProcess(command, 0, stdout='')

    def build(self, upstream_inputs=None):
        with patch.object(GO.subprocess, 'run', side_effect=self.vendor):
            evidence = GO.create_supplement(self.record, self.tree, self.destination, {})
        self.record['go_supplement'] = evidence
        source = self.root / 'source.tar'
        upstream = io.BytesIO()
        with tarfile.open(fileobj=upstream, mode='w:gz') as archive:
            for name, data in (upstream_inputs or {'go.mod': self.mod, 'go.sum': self.sums}).items():
                member = tarfile.TarInfo('cliphist-0.7.0/' + name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        with tarfile.open(source, 'w') as archive:
            member = tarfile.TarInfo('cliphist/cliphist-0.7.0.tar.gz')
            member.size = len(upstream.getvalue())
            archive.addfile(member, io.BytesIO(upstream.getvalue()))
            archive.addfile(tarfile.TarInfo('cliphist/PKGBUILD'), io.BytesIO())
        target = self.root / 'combined.tar'
        GO.add_supplement(source, self.destination, target, self.record)
        return target

    def test_exact_reviewed_identities(self):
        self.assertEqual(len(GO.RECIPES), 2)
        for sha, (base, tree) in GO.RECIPES.items():
            self.assertEqual(GO.required_tree({'base': base, 'recipe_sha256': sha}), tree)
        self.record['recipe_sha256'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'reviewed recipe'):
            GO.required_tree(self.record)

    def test_complete_supplement(self):
        target = self.build()
        with tarfile.open(target) as archive:
            GO.validate_supplement(archive, self.record)
        self.assertEqual(self.record['go_supplement']['modules'][0]['name'], 'example.invalid/lib')
        self.assertIn('GOPROXY=off', (self.destination / 'README.sources').read_text())

    def test_upstream_module_inputs_must_match(self):
        for name in ('go.mod', 'go.sum'):
            shutil.rmtree(self.destination, ignore_errors=True)
            with self.subTest(name=name):
                inputs = {'go.mod': self.mod, 'go.sum': self.sums}
                inputs[name] = inputs[name].replace(b'v1.0.0', b'v2.0.0')
                target = self.build(inputs)
                with tarfile.open(target) as archive:
                    with self.assertRaisesRegex(ValueError, 'primary source'):
                        GO.validate_supplement(archive, self.record)

    def test_missing_upstream_inputs_refused(self):
        target = self.build({'go.mod': self.mod})
        with tarfile.open(target) as archive:
            with self.assertRaisesRegex(ValueError, 'missing Go primary source'):
                GO.validate_supplement(archive, self.record)

    def test_duplicate_upstream_archive_refused(self):
        target = self.build()
        with tarfile.open(target) as archive:
            payload = archive.extractfile('cliphist/cliphist-0.7.0.tar.gz').read()
        with tarfile.open(target, 'a') as archive:
            member = tarfile.TarInfo('cliphist/duplicate.tar.gz')
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
        with tarfile.open(target) as archive:
            with self.assertRaisesRegex(ValueError, 'ambiguous Go primary source'):
                GO.validate_supplement(archive, self.record)

    def test_old_archive_refused(self):
        with tarfile.open(fileobj=io.BytesIO(), mode='w') as archive:
            with self.assertRaisesRegex(ValueError, 'not archived'):
                GO.validate_supplement(archive, self.record)

    def test_mutated_module_inputs_refused(self):
        for name in ('go.mod', 'go.sum'):
            with self.subTest(name=name):
                destination = self.root / name
                def mutate(*args, **kwargs):
                    result = self.vendor(*args, **kwargs)
                    (self.tree / name).write_bytes(b'changed')
                    return result
                with patch.object(GO.subprocess, 'run', side_effect=mutate):
                    with self.assertRaisesRegex(ValueError, 'changed'):
                        GO.create_supplement(self.record, self.tree, destination, {})

    def test_missing_or_unpinned_module_refused(self):
        self.build()
        read = lambda name: (self.destination / name).read_bytes()
        names = self.record['go_supplement']['files']
        with self.assertRaisesRegex(ValueError, 'source checksum'):
            GO.inspect_vendor(self.mod, self.sums.split(b'\n', 1)[1], read, names)
        with self.assertRaisesRegex(ValueError, 'missing required'):
            GO.inspect_vendor(self.mod + b'require example.invalid/other v2.0.0\n',
                              self.sums, read, names)

    def test_replacements_refused(self):
        self.build()
        with self.assertRaisesRegex(ValueError, 'replacement'):
            GO.inspect_vendor(self.mod + b'replace example.invalid/lib => ../local\n', self.sums,
                              lambda name: (self.destination / name).read_bytes(),
                              self.record['go_supplement']['files'])

    def test_tampered_vendor_file_refused(self):
        target = self.build()
        name = 'vendor/example.invalid/lib/lib.go'
        data = (self.destination / name).read_bytes()
        (self.destination / name).write_bytes(data + b'// changed\n')
        with tarfile.open(target, 'w') as archive:
            archive.add(self.destination, arcname='cliphist/_go')
        with tarfile.open(target) as archive:
            with self.assertRaisesRegex(ValueError, 'manifest evidence'):
                GO.validate_supplement(archive, self.record)

    def test_links_and_duplicates_refused(self):
        target = self.build()
        with tarfile.open(target, 'a') as archive:
            member = tarfile.TarInfo('cliphist/_go/vendor/escape')
            member.type = tarfile.SYMTYPE
            member.linkname = '/tmp/escape'
            archive.addfile(member)
        with tarfile.open(target) as archive:
            with self.assertRaisesRegex(ValueError, 'links or duplicate'):
                GO.validate_supplement(archive, self.record)


if __name__ == '__main__':
    unittest.main()
