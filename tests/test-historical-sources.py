#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline refusal and archived-object checks for historical tag evidence."""
from copy import deepcopy
import importlib.util
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('historical_sources',
    Path(__file__).resolve().parents[1] / 'packaging/mirror/historical_sources.py')
historical = importlib.util.module_from_spec(spec)
spec.loader.exec_module(historical)


class HistoricalSources(unittest.TestCase):
    def record(self):
        evidence = historical.REVIEWED[0]
        result = {key: evidence[key] for key in ('base', 'version', 'recipe_sha256', 'binary_sha256')}
        result['source_routes'] = [deepcopy(evidence)]
        result['git_sources'] = [{'source': evidence['source'], 'kind': 'tag',
                                 'reference': evidence['tag'], 'commit': evidence['commit']}]
        return result

    def test_exact_binary_identity_only(self):
        record = self.record()
        source = historical.REVIEWED[0]['source']
        self.assertTrue(historical.allows_tag(record, source))
        for key in ('base', 'version', 'recipe_sha256', 'binary_sha256'):
            changed = dict(record, **{key: 'changed'})
            self.assertIsNone(historical.exception(changed))
            self.assertFalse(historical.allows_tag(changed, source))
            with self.assertRaisesRegex(ValueError, 'no reviewed'):
                historical.validate('/unused', changed)
        self.assertFalse(historical.allows_tag(record, source + 'changed'))

    def test_screen_review_is_bound_to_exact_image_binary(self):
        evidence = next(item for item in historical.REVIEWED if item['base'] == 'screen')
        record = {key: evidence[key] for key in ('base', 'version', 'recipe_sha256', 'binary_sha256')}
        self.assertTrue(historical.allows_tag(record, evidence['source']))
        for key in record:
            changed = dict(record, **{key: 'changed'})
            self.assertFalse(historical.allows_tag(changed, evidence['source']))

    def test_pre_build_visit_required(self):
        evidence = deepcopy(historical.REVIEWED[0])
        evidence['builddate'] = 1
        with patch.object(historical, 'REVIEWED', [evidence]):
            with self.assertRaisesRegex(ValueError, 'predate'):
                historical.exception(self.record())

    def test_missing_and_changed_evidence_refused(self):
        record = self.record()
        record['source_routes'] = []
        with self.assertRaisesRegex(ValueError, 'evidence differs'):
            historical.validate('/unused', record)
        for key in ('visit_date', 'snapshot', 'commit', 'origin', 'builddate', 'tag_object'):
            record = self.record()
            record['source_routes'][0][key] = 'changed'
            with self.assertRaisesRegex(ValueError, 'evidence differs'):
                historical.validate('/unused', record)
        record = self.record()
        record['git_sources'][0]['commit'] = '0' * 40
        with self.assertRaisesRegex(ValueError, 'pin differs'):
            historical.validate('/unused', record)

    def test_prepare_requires_current_tag_and_source(self):
        record = self.record()
        evidence = historical.REVIEWED[0]
        def run(*args):
            if 'rev-parse' in args:
                return evidence['commit'] if args[-1].endswith('^{commit}') else evidence['tag_object']
            return ''
        with tempfile.TemporaryDirectory() as temporary:
            downloads = Path(temporary)
            text = 'source = ' + evidence['source']
            self.assertEqual(historical.prepare(text, record, downloads, run), [evidence])
            with self.assertRaisesRegex(ValueError, 'differs'):
                historical.prepare(text, record, downloads, lambda *args: '0' * 40)
            with self.assertRaisesRegex(ValueError, 'source differs'):
                historical.prepare('source = changed', record, downloads, run)
        self.assertEqual(historical.prepare('', {}, Path('/unused'), None), [])
        historical.validate('/unused', {})

    def moved_record(self):
        evidence = historical.MOVED_TAGS[0]
        result = {key: evidence[key] for key in ('base', 'version', 'recipe_sha256', 'binary_sha256')}
        result['source_routes'] = [deepcopy(evidence)]
        result['git_sources'] = [{'source': evidence['source'], 'kind': 'tag',
                                 'reference': evidence['tag'], 'commit': evidence['commit']}]
        return result

    def test_moved_tag_is_bound_to_exact_image_binary(self):
        record = self.moved_record()
        self.assertEqual(historical.moved_tag(record)['commit'], 'd99d2e1a3b1a213b03c7dfbea5133898935fdeea')
        # The recipe checksum already pins the tag; no checksum exception is granted.
        self.assertFalse(historical.allows_tag(record, historical.MOVED_TAGS[0]['source']))
        for key in ('base', 'version', 'recipe_sha256', 'binary_sha256'):
            changed = dict(record, **{key: 'changed'})
            self.assertIsNone(historical.moved_tag(changed))
            with self.assertRaisesRegex(ValueError, 'no reviewed'):
                historical.validate('/unused', changed)

    def test_moved_tag_must_move_after_the_build(self):
        evidence = deepcopy(historical.MOVED_TAGS[0])
        evidence['builddate'] = 1900000000
        with patch.object(historical, 'MOVED_TAGS', [evidence]):
            with self.assertRaisesRegex(ValueError, 'moved before'):
                historical.moved_tag(self.moved_record())

    def test_moved_tag_evidence_and_pin_refused_when_changed(self):
        for key in ('commit', 'b2sum', 'moved_date', 'tag'):
            record = self.moved_record()
            record['source_routes'][0][key] = 'changed'
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'evidence differs'):
                historical.validate('/unused', record)
        record = self.moved_record()
        record['git_sources'][0]['commit'] = '899348d8318eb9a3c5a5b6ec43c4a1114288773a'
        with self.assertRaisesRegex(ValueError, 'pin differs'):
            historical.validate('/unused', record)

    def test_moved_tag_prepare_requires_recipe_checksum_and_commit(self):
        evidence = historical.MOVED_TAGS[0]
        record = self.moved_record()
        text = 'source = ' + evidence['source'] + '\nb2sums = ' + evidence['b2sum'] + '\n'
        calls = []
        def run(*args):
            calls.append(args)
            return evidence['commit'] if 'rev-parse' in args else ''
        with tempfile.TemporaryDirectory() as temporary:
            downloads = Path(temporary)
            self.assertEqual(historical.prepare(text, record, downloads, run), [evidence])
            self.assertIn(('git', '-C', str(downloads / 'srt'), 'update-ref', 'refs/tags/v1.5.7',
                           evidence['commit']), calls)
            self.assertIn(('git', '-C', str(downloads / 'srt'), 'fetch', '--no-tags',
                           'https://github.com/Haivision/srt', evidence['commit']), calls)
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, 'checksum-pinned commit'):
                historical.prepare(text, record, Path(temporary), lambda *args: '0' * 40)
        with self.assertRaisesRegex(ValueError, 'checksum differs'):
            historical.prepare(text.replace(evidence['b2sum'], 'SKIP'), record, Path('/unused'), run)
        with self.assertRaisesRegex(ValueError, 'source differs'):
            historical.prepare(text.replace('v1.5.7', 'v1.5.8'), record, Path('/unused'), run)

    def test_moved_tag_archive_has_tag_at_pinned_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / 'repo'
            def git(*args):
                return subprocess.run(['git', *args], check=True, capture_output=True,
                                      text=True).stdout.strip()
            git('init', '-q', str(repo))
            for content in ('built\n', 'moved\n'):
                (repo / 'source').write_text(content)
                git('-C', str(repo), 'add', 'source')
                git('-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.org',
                    '-c', 'commit.gpgsign=false', 'commit', '-qm', content)
            built = git('-C', str(repo), 'rev-parse', 'HEAD^')
            git('-C', str(repo), 'update-ref', 'refs/tags/v1.5.7', built)
            archive = root / 'srt.tar.gz'
            with tarfile.open(archive, 'w:gz') as bundle:
                bundle.add(repo / '.git', arcname='srt/srt')
            evidence = dict(deepcopy(historical.MOVED_TAGS[0]), commit=built)
            record = self.moved_record()
            record['source_routes'] = [evidence]
            record['git_sources'][0]['commit'] = built
            with patch.object(historical, 'MOVED_TAGS', [evidence]):
                historical.validate(archive, record)
                git('-C', str(repo), 'update-ref', 'refs/tags/v1.5.7', 'HEAD')
                with tarfile.open(archive, 'w:gz') as bundle:
                    bundle.add(repo / '.git', arcname='srt/srt')
                with self.assertRaisesRegex(ValueError, 'historical commit'):
                    historical.validate(archive, record)

    def test_real_git_archive_objects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / 'repo'
            def git(*args):
                return subprocess.run(['git', *args], check=True, capture_output=True,
                                      text=True).stdout.strip()
            git('init', str(repo))
            (repo / 'source').write_text('the source\n')
            git('-C', str(repo), 'add', 'source')
            git('-C', str(repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.org',
                'commit', '-m', 'Initial source')
            commit = git('-C', str(repo), 'rev-parse', 'HEAD')
            git('-C', str(repo), 'tag', 'v1')
            archive = root / 'source.tar.gz'
            with tarfile.open(archive, 'w:gz') as bundle:
                bundle.add(repo / '.git', arcname='package/repo')
            historical.validate_git_archive(archive, 'package/repo', 'v1', commit, commit)
            with self.assertRaisesRegex(ValueError, 'historical commit'):
                historical.validate_git_archive(archive, 'package/repo', 'v1', '0' * 40)
            for relative in ('objects/info/alternates', 'shallow', 'info/grafts', 'refs/replace/evil',
                             'objects/pack/pack-empty.promisor'):
                target = repo / '.git' / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('/outside/objects\n')
                with tarfile.open(archive, 'w:gz') as bundle:
                    bundle.add(repo / '.git', arcname='package/repo')
                with self.assertRaisesRegex(ValueError, 'self-contained'):
                    historical.validate_git_archive(archive, 'package/repo', 'v1', commit)
                target.unlink()
            with tarfile.open(archive, 'w:gz') as bundle:
                bundle.add(repo / '.git', arcname='package/repo')
                member = tarfile.TarInfo('package/repo/HEAD')
                member.size = 4
                bundle.addfile(member, io.BytesIO(b'evil'))
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                historical.validate_git_archive(archive, 'package/repo', 'v1', commit)
            object_path = repo / '.git/objects' / commit[:2] / commit[2:]
            object_path.unlink()
            with tarfile.open(archive, 'w:gz') as bundle:
                bundle.add(repo / '.git', arcname='package/repo')
            with self.assertRaisesRegex(ValueError, 'failed verification'):
                historical.validate_git_archive(archive, 'package/repo', 'v1', commit)


if __name__ == '__main__':
    unittest.main()
