#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline checks for interrupted, shared and verified source collection jobs."""
import fcntl
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging/mirror'))
import collect_sources as runner


class Collection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.packages = self.root / 'packages'
        self.packages.mkdir()
        self.closure = self.root / 'closure.txt'
        self.output = self.root / 'output'
        self.calls = []
        self.fail = set()
        self.names = []
        self.package('sample', 'sample')
        self.package('sample-libs', 'sample')
        self.package('other', 'other')
        self.closure.write_text(''.join(name + '\n' for name in self.names))

    def package(self, name, base):
        filename = name + '-1-1-x86_64.pkg.tar.zst'
        self.names.append(filename)
        with tarfile.open(self.packages / filename, 'w') as archive:
            for member, text in {
                '.PKGINFO': (f'pkgname = {name}\npkgbase = {base}\npkgver = 1-1\nlicense = GPL\n'
                             f'url = https://example.org/{base}\n'),
                '.BUILDINFO': 'pkgbuild_sha256sum = ' + hashlib.sha256(base.encode()).hexdigest() + '\n',
            }.items():
                entry = tarfile.TarInfo(member)
                entry.size = len(text.encode())
                archive.addfile(entry, io.BytesIO(text.encode()))

    def fake_collect(self, command, **kwargs):
        closure = Path(command[command.index('--closure') + 1])
        output = Path(command[command.index('--output') + 1])
        inventory = runner.iso_sources.inventory(closure, self.packages)
        base = next(iter(inventory.values()))['base']
        self.calls.append(base)
        kwargs['stdout'].write(f'fixture attempt: {base}\n')
        if base in self.fail:
            return type('Result', (), {'returncode': 1})()
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as bundle:
            metadata = (f'pkgbase = {base}\npkgver = 1\npkgrel = 1\n' +
                        ''.join(f'pkgname = {item["name"]}\n' for item in inventory.values()))
            for member, data in {'PKGBUILD': base.encode(), '.SRCINFO': metadata.encode()}.items():
                entry = tarfile.TarInfo(base + '/' + member)
                entry.size = len(data)
                bundle.addfile(entry, io.BytesIO(data))
        content = buffer.getvalue()
        sha = hashlib.sha256(content).hexdigest()
        archive = base + '.src.tar.gz'
        source = output / 'sources' / 'sha256' / sha / archive
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(content)
        for record in inventory.values():
            record.update(archive=archive, sha256=sha, size=len(content), revision='a' * 40,
                          git_sources=[])
        runner.atomic_json(output / 'ARCH-SOURCES.json', {
            'schema': 1, 'closure_sha256': runner.iso_sources.digest(closure), 'packages': inventory})
        return type('Result', (), {'returncode': 0})()

    def run_collection(self, jobs=2):
        # Patch only the runner's process launcher; metadata still reads real fixture archives.
        with patch.object(runner, 'subprocess') as processes:
            processes.run.side_effect = self.fake_collect
            return runner.collect_all(self.closure, self.packages, self.output, jobs)

    def test_split_archive_resume_and_corruption(self):
        self.assertTrue(self.run_collection())
        self.assertCountEqual(self.calls, ['sample', 'other'])
        records, objects = runner.iso_sources.validate(
            self.output / 'ARCH-SOURCES.json', self.closure, self.packages)
        self.assertEqual(len(records), 3)
        self.assertEqual(len(objects), 2)
        self.calls.clear()
        self.assertTrue(self.run_collection())
        self.assertEqual(self.calls, [])
        summary = json.loads((self.output / 'summary.json').read_text())
        self.assertTrue(all(item['status'] == 'resumed' for item in summary['results']))
        # Corrupt the job's hard-linked object: neither cache nor final publication may trust it.
        next(iter(objects.values())).write_bytes(b'corruption')
        self.assertTrue(self.run_collection())
        self.assertEqual(len(self.calls), 1)
        runner.iso_sources.validate(self.output / 'ARCH-SOURCES.json', self.closure, self.packages)

    def test_vault_cooking_requires_explicit_runner_option(self):
        def collect(command, **kwargs):
            self.assertIn('--cook-swh-vault', command)
            return self.fake_collect(command, **kwargs)
        with patch.object(runner, 'subprocess') as processes:
            processes.run.side_effect = collect
            self.assertTrue(runner.collect_all(self.closure, self.packages, self.output, 1,
                                              cook_swh_vault=True))

    def test_partial_collection_writes_validated_manifest_and_missing_sources(self):
        self.fail.add('other')
        self.assertFalse(self.run_collection())
        manifest = self.output / 'ARCH-SOURCES.json'
        missing = self.output / 'MISSING-SOURCES.json'
        self.assertTrue(manifest.exists())
        self.assertTrue(missing.exists())
        with self.assertRaisesRegex(ValueError, 'missing required packages'):
            runner.iso_sources.validate(manifest, self.closure, self.packages)
        records, objects = runner.iso_sources.validate(
            manifest, self.closure, self.packages, missing_sources=missing)
        self.assertEqual(len(records), 2)
        self.assertEqual(len(objects), 1)
        data = json.loads(missing.read_text())
        self.assertEqual(data['closure_sha256'], runner.iso_sources.digest(self.closure))
        refused_base, = data['bases']
        self.assertEqual(refused_base['base'], 'other')
        self.assertEqual(refused_base['version'], '1-1')
        self.assertEqual(refused_base['recipe_sha256'], hashlib.sha256(b'other').hexdigest())
        self.assertEqual(list(refused_base['packages']), [self.names[2]])
        self.assertEqual(refused_base['upstream_url'], 'https://example.org/other')
        # The published reason is plain words; the internal log path stays in collect.log.
        self.assertEqual(refused_base['reason'], 'The source could not be fetched yet.')
        self.assertNotIn('jobs/', refused_base['reason'])
        summary = json.loads((self.output / 'summary.json').read_text())
        self.assertFalse(summary['complete'])
        refused = next(item for item in summary['results'] if item['status'] == 'refused')
        self.assertIn('fixture attempt', (self.output / refused['log']).read_text())
        self.calls.clear()
        self.fail.clear()
        self.assertTrue(self.run_collection())
        self.assertEqual(self.calls, ['other'])
        self.assertFalse(missing.exists())
        self.assertTrue(json.loads((self.output / 'summary.json').read_text())['complete'])
        runner.iso_sources.validate(manifest, self.closure, self.packages)

    def test_refused_base_with_several_upstream_pages_still_writes_partial_result(self):
        # lvm2 and samba split packages name different upstream pages in the 0.2.0 image.
        original = runner.iso_sources.package_metadata
        def metadata(path):
            data = dict(original(path))
            if path.name == self.names[2]:
                data['url'] = ['https://example.org/one', 'https://example.org/two']
            return data
        self.fail.add('other')
        with patch.object(runner.iso_sources, 'package_metadata', side_effect=metadata):
            self.assertFalse(self.run_collection())
        missing = self.output / 'MISSING-SOURCES.json'
        refused_base, = json.loads(missing.read_text())['bases']
        self.assertEqual(refused_base['upstream_url'], '')
        records, _ = runner.iso_sources.validate(
            self.output / 'ARCH-SOURCES.json', self.closure, self.packages, missing_sources=missing)
        self.assertEqual(len(records), 2)
        missing_records = runner.iso_sources.validate_missing(
            missing, self.closure, runner.iso_sources.inventory(self.closure, self.packages))
        text = runner.iso_sources.missing_directions(missing_records)
        self.assertIn('  Upstream: see the Arch recipe', text)

    def test_plain_reasons_name_the_cause_without_internal_paths(self):
        cases = {
            'ValueError: Software Heritage vault cooking timed out after 600 seconds: https://x':
                'did not deliver a copy in time',
            'ValueError: Software Heritage has no matching exact source across Savannah origin aliases':
                'has no exact copy',
            'ValueError: cannot retrieve exact source key 352B: fingerprint differs': 'signing key',
            'ValueError: primary gitlinks are absent from reviewed sources': 'submodule',
            "CalledProcessError: Command '('makepkg', '--allsource', '--force')'": 'download failed',
            'something else': 'could not be fetched yet',
        }
        self.output.mkdir(parents=True, exist_ok=True)
        for text, expected in cases.items():
            log = self.output / 'reason.log'
            log.write_text('Traceback\n' + text + '\n')
            with self.subTest(text=text):
                self.assertIn(expected, runner.plain_reason(log))
                self.assertNotIn('/', runner.plain_reason(log))

    def test_copied_pool_object_is_repaired_from_verified_job(self):
        with patch.object(runner.os, 'link', side_effect=OSError('no hard links')):
            self.assertTrue(self.run_collection())
        self.calls.clear()
        _, objects = runner.iso_sources.validate(
            self.output / 'ARCH-SOURCES.json', self.closure, self.packages)
        target = next(iter(objects.values()))
        target.write_bytes(b'interrupted copy')
        target.with_suffix(target.suffix + '.tmp').write_bytes(b'partial copy')
        self.assertTrue(self.run_collection())
        self.assertEqual(self.calls, [])
        runner.iso_sources.validate(self.output / 'ARCH-SOURCES.json', self.closure, self.packages)

    def test_all_refused_and_exact_recipe_revision_retained(self):
        self.fail.update(('sample', 'other'))
        key = hashlib.sha256(('other\0' + hashlib.sha256(b'other').hexdigest()).encode()).hexdigest()
        job = self.output / 'jobs' / key
        job.mkdir(parents=True)
        runner.atomic_json(job / 'RECIPE.json', {
            'base': 'other', 'recipe_sha256': hashlib.sha256(b'other').hexdigest(),
            'revision': 'b' * 40, 'upstream_url': 'https://example.org/other'})
        self.assertFalse(self.run_collection())
        missing = self.output / 'MISSING-SOURCES.json'
        records, objects = runner.iso_sources.validate(
            self.output / 'ARCH-SOURCES.json', self.closure, self.packages,
            missing_sources=missing)
        self.assertEqual(records, {})
        self.assertEqual(objects, {})
        bases = json.loads(missing.read_text())['bases']
        self.assertEqual(next(item for item in bases if item['base'] == 'other')['revision'],
                         'b' * 40)

    def test_partial_final_validation_rereads_refused_binary(self):
        self.fail.add('other')
        original = runner.iso_sources.validate
        def validate(path, *args, **kwargs):
            if path.name == 'ARCH-SOURCES.pending.json':
                with (self.packages / self.names[2]).open('ab') as stream:
                    stream.write(b'changed after refusal')
            return original(path, *args, **kwargs)
        with patch.object(runner.iso_sources, 'validate', side_effect=validate):
            with self.assertRaises(ValueError):
                self.run_collection()
        self.assertFalse((self.output / 'ARCH-SOURCES.json').exists())
        self.assertFalse((self.output / 'MISSING-SOURCES.json').exists())

    def test_final_validation_rereads_binaries(self):
        original = runner.iso_sources.validate
        def validate(path, *args, **kwargs):
            if path.name == 'ARCH-SOURCES.pending.json':
                with (self.packages / self.names[0]).open('ab') as stream:
                    stream.write(b'changed after collection')
            return original(path, *args, **kwargs)
        with patch.object(runner.iso_sources, 'validate', side_effect=validate):
            with self.assertRaisesRegex(ValueError, 'differs from the image binary'):
                self.run_collection()
        self.assertFalse((self.output / 'ARCH-SOURCES.json').exists())

    def test_concurrent_invocation_and_invalid_job_count_refused(self):
        self.output.mkdir()
        with (self.output / '.collection.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, 'another collection'):
                self.run_collection()
        with self.assertRaisesRegex(ValueError, 'positive'):
            self.run_collection(jobs=0)


if __name__ == '__main__':
    unittest.main()
