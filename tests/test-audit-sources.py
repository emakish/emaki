#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline recipe classification contracts."""
from pathlib import Path
import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging' / 'mirror'))
import audit_sources


class RecipeAudit(unittest.TestCase):
    record = {'name': 'example', 'base': 'example', 'version': '1-1'}
    prefix = 'pkgname = example\npkgver = 1\npkgrel = 1\n'

    def test_checksummed_github_archive_is_not_a_gitlink(self):
        text = self.prefix + 'source = https://github.com/example/example/v1.tar.gz\nsha256sums = abc\n'
        self.assertEqual(audit_sources.classify(text, [self.record]), {'class': 'recipe_pass'})

    def test_unpinned_git_is_refused(self):
        text = self.prefix + 'source = git+https://example.invalid/repository.git\n'
        self.assertEqual(audit_sources.classify(text, [self.record])['class'], 'unpinned_vcs')

    def test_unchecked_remote_is_refused(self):
        text = self.prefix + 'source = https://example.invalid/source.tar.gz\nsha256sums = SKIP\n'
        self.assertEqual(audit_sources.classify(text, [self.record])['class'], 'unchecked_source')

    def test_every_split_binary_identity_is_checked(self):
        other = dict(self.record, name='other')
        self.assertEqual(audit_sources.classify(self.prefix, [self.record, other])['class'],
                         'identity_mismatch')

    def test_server_long_retry_window_and_client_identity(self):
        error = urllib.error.HTTPError('https://example.invalid/repository.git', 429,
                                       'Retry later', {'Retry-After': '505'}, None)
        with patch.object(audit_sources.urllib.request, 'urlopen', side_effect=error) as request:
            self.assertEqual(audit_sources.retry_delay('https://example.invalid/repository.git'), 505)
        self.assertEqual(request.call_args.args[0].get_header('User-agent'), 'emaki-publish')

    def archive(self, recipe=b'pkgver=1\n'):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as archive:
            for name, data in [('PKGBUILD', recipe), ('.SRCINFO', self.prefix.encode())]:
                member = tarfile.TarInfo('example/' + name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        return output.getvalue()

    def test_archive_requires_binary_recipe_hash_and_retains_commit(self):
        record = dict(self.record, recipe_sha256=hashlib.sha256(b'pkgver=1\n').hexdigest())
        revision = 'a' * 40
        responses = [json.dumps({'commit': {'id': revision}}).encode(), self.archive()]
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(audit_sources, 'download', side_effect=responses):
                metadata, text, recipe = audit_sources.archive_recipe(record, Path(directory))
            self.assertEqual(metadata['revision'], revision)
            self.assertIn('/archive/' + revision + '/', metadata['recipe_archive_url'])
            self.assertEqual(text, self.prefix)
            with patch.object(audit_sources, 'download', side_effect=AssertionError('network')):
                self.assertEqual(audit_sources.archive_recipe(record, Path(directory), offline=True),
                                 (metadata, text, recipe))

    def test_build_dependency_downloads_are_refused_from_archive_and_repository(self):
        for command in ('cargo +stable build --locked', 'go build -mod=readonly'):
            recipe = f'pkgver=1\nbuild() {{ {command}; }}\n'
            record = dict(self.record, recipe_sha256=hashlib.sha256(recipe.encode()).hexdigest(),
                          **{'class': 'copyleft'})
            for storage in ('archive', 'repository'):
                with self.subTest(command=command, storage=storage), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    inventory = root / 'inventory.json'
                    inventory.write_text(json.dumps([record]))
                    output = root / 'audit.json'
                    repositories = root / 'repositories'
                    if storage == 'repository':
                        (repositories / 'example').mkdir(parents=True)
                    args = ['audit-sources', '--inventory', str(inventory), '--repositories',
                            str(repositories), '--archives', str(root / 'archives'),
                            '--output', str(output)]
                    revision = 'a' * 40
                    responses = [json.dumps({'commit': {'id': revision}}).encode(),
                                 self.archive(recipe.encode())]

                    def git(*args):
                        self.assertEqual(args[:3], ('-C', repositories / 'example', 'show'))
                        return {revision + ':PKGBUILD': recipe,
                                revision + ':.SRCINFO': self.prefix}[args[3]]

                    with patch.object(sys, 'argv', args), \
                            patch.object(audit_sources, 'download', side_effect=responses), \
                            patch.object(audit_sources, 'git', side_effect=git), \
                            patch.object(audit_sources.iso_sources, 'exact_revision', return_value=revision):
                        audit_sources.main()
                    result = json.loads(output.read_text())['bases'][0]
                    self.assertEqual(result['class'], 'recipe_refused')
                    self.assertIn('dependency inputs require a reviewed recipe', result['reason'])

    def test_historical_checker_keeps_its_own_policy(self):
        from types import SimpleNamespace
        checker = SimpleNamespace(check_recipe=lambda text, record: None,
                                  fields=audit_sources.iso_sources.fields)
        record = dict(self.record, recipe_sha256='0' * 64)
        result = audit_sources.classify(self.prefix, [record], checker,
                                       recipe_text='build() { cargo build; }')
        self.assertEqual(result, {'class': 'recipe_pass'})

    def test_matching_version_tag_does_not_override_wrong_recipe(self):
        record = dict(self.record, recipe_sha256='0' * 64)
        responses = [json.dumps({'commit': {'id': 'a' * 40}}).encode(), self.archive()]
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(audit_sources, 'download', side_effect=responses):
                with self.assertRaisesRegex(ValueError, 'tag recipe hash differs'):
                    audit_sources.archive_recipe(record, Path(directory))


if __name__ == '__main__':
    unittest.main()
