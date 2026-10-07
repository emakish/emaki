#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline fixtures for exact image source collection and publication validation."""
import hashlib
import importlib.util
import io
import json
import os
import shutil
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

    def test_empty_metadata_values(self):
        self.assertEqual(m.fields('\tpkgdesc = \n\tpkgname = demo\n'),
                         {'pkgdesc': [''], 'pkgname': ['demo']})

    def partial_fixture(self):
        filename = self.package()
        expected = m.inventory(self.closure, self.root)
        self.manifest = self.root / 'ARCH-SOURCES.json'
        self.manifest.write_text(json.dumps({'schema': 1, 'closure_sha256': m.digest(self.closure),
                                             'packages': {}}))
        self.missing = self.root / 'MISSING-SOURCES.json'
        self.missing_data = {'schema': 1, 'closure_sha256': m.digest(self.closure), 'bases': [
            {'base': 'demo', 'version': '1-1', 'recipe_sha256': self.sha,
             'packages': expected, 'reason': 'Download refused',
             'upstream_url': 'https://example.org/demo', 'revision': 'v1/1-1'}]}
        self.missing.write_text(json.dumps(self.missing_data))
        return filename, expected

    def test_partial_manifest_requires_explicit_exact_missing_sources(self):
        filename, expected = self.partial_fixture()
        with self.assertRaisesRegex(ValueError, 'missing required'):
            m.validate(self.manifest, self.closure, self.root)
        self.assertEqual(m.validate(self.manifest, self.closure, self.root,
                                    missing_sources=self.missing), ({}, {}))
        missing = m.validate_missing(self.missing, self.closure, expected)
        text = m.missing_directions(missing)
        self.assertIn(filename, text)
        self.assertIn('/packages/demo/-/tree/v1%2F1-1', text)
        self.assertIn('https://example.org/demo', text)
        self.assertIn("Emaki's own copy will be added.", text)

    def test_missing_sources_refuses_invalid_records(self):
        filename, expected = self.partial_fixture()
        original = json.dumps(self.missing_data)
        mutations = [
            lambda d: d['bases'][0]['packages'][filename].update(version='2-1'),
            lambda d: d['bases'][0].update(version='2-1'),
            lambda d: d['bases'].append(d['bases'][0].copy()),
            lambda d: d.update(closure_sha256='0' * 64),
            lambda d: d['bases'][0].update(upstream_url='file:///tmp/source'),
            lambda d: d['bases'][0].update(upstream_url='https://example.org/\nforged'),
            lambda d: d['bases'][0].update(revision='../main'),
            lambda d: d['bases'][0].update(reason=''),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                data = json.loads(original)
                mutate(data)
                self.missing.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    m.validate_missing(self.missing, self.closure, expected)

    def test_partial_sources_refuses_overlap_and_unaccounted_packages(self):
        filename, expected = self.partial_fixture()
        manifest = json.loads(self.manifest.read_text())
        manifest['packages'] = expected
        self.manifest.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'overlap'):
            m.validate(self.manifest, self.closure, self.root, missing_sources=self.missing)
        manifest['packages'] = {}
        self.manifest.write_text(json.dumps(manifest))
        self.missing_data['bases'] = []
        self.missing.write_text(json.dumps(self.missing_data))
        with self.assertRaisesRegex(ValueError, 'missing required'):
            m.validate(self.manifest, self.closure, self.root, missing_sources=self.missing)

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

    def test_live_closure_exactly_covers_installed_and_target_packages(self):
        target = self.package()
        live = self.package('live-tool')
        target_closure = self.root / 'target.txt'
        target_closure.write_text(target + '\n')
        pkglist = self.root / 'pkglist.txt'
        pkglist.write_text('live-tool 2:1-1\n')
        m.validate_live_closure(self.closure, pkglist, target_closure)
        original = self.closure.read_text()
        for contents in (target + '\n', live + '\n', original + 'extra-1-1-any.pkg.tar.zst\n',
                         original.replace('live-tool-1-1', 'live-tool-2-1')):
            self.closure.write_text(contents)
            with self.subTest(contents=contents), self.assertRaisesRegex(ValueError, 'differs from image'):
                m.validate_live_closure(self.closure, pkglist, target_closure)

    def test_full_inventory_preserves_live_only_source_identities(self):
        target = self.package()
        live = self.package('live-tool')
        metadata = self.root / 'live-packages.json'
        expected = m.inventory(self.closure, self.root)
        m.write_image_inventory(self.closure, self.root, metadata)
        (self.root / live).unlink()
        self.assertEqual(m.inventory(self.closure, metadata), expected)
        self.assertIn(live, expected)
        target_closure = self.root / 'target-closure.txt'
        target_closure.write_text(target + '\n')
        with self.assertRaisesRegex(ValueError, 'full image package list'):
            m.inventory(target_closure, metadata)
        data = json.loads(metadata.read_text())
        data['binaries'][live] = '0' * 64
        metadata.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'source identity differs'):
            m.inventory(self.closure, metadata)

    def test_live_closure_checks_epoch_from_full_inventory(self):
        target = self.package()
        self.package('live-tool', 'MIT')
        metadata = self.root / 'inventory.json'
        m.write_image_inventory(self.closure, self.root, metadata)
        target_closure = self.root / 'target.txt'
        target_closure.write_text(target + '\n')
        pkglist = self.root / 'pkglist.txt'
        pkglist.write_text('live-tool 1-1\n')
        m.validate_live_closure(self.closure, pkglist, target_closure, metadata)
        pkglist.write_text('live-tool 2:1-1\n')
        with self.assertRaisesRegex(ValueError, 'differs from image'):
            m.validate_live_closure(self.closure, pkglist, target_closure, metadata)
        data = json.loads(metadata.read_text())
        data['versions']['live-tool-1-1-x86_64.pkg.tar.zst'] = '2:1-1'
        metadata.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'differs from archive filename'):
            m.validate_live_closure(self.closure, pkglist, target_closure, metadata)
        data['versions']['live-tool-1-1-x86_64.pkg.tar.zst'] = '2:9-1'
        metadata.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'differs from archive filename'):
            m.validate_live_closure(self.closure, pkglist, target_closure, metadata)

    def test_live_closure_preserves_real_archive_epochs_exactly(self):
        # These filenames and versions occur in the 0.2.0 image repository.
        live = 'rtmpdump-1:2.6-2-x86_64.pkg.tar.zst'
        target = 'libasyncns-1:0.8+r3+g68cd5af-3-x86_64.pkg.tar.zst'
        versions = {live: '1:2.6-2', target: '1:0.8+r3+g68cd5af-3'}
        self.closure.write_text(live + '\n' + target + '\n')
        target_closure = self.root / 'target.txt'
        target_closure.write_text(target + '\n')
        pkglist = self.root / 'pkglist.txt'
        pkglist.write_text('rtmpdump 1:2.6-2\n')
        metadata = self.root / 'inventory.json'
        metadata.write_text(json.dumps({'versions': versions}))
        m.validate_live_closure(self.closure, pkglist, target_closure, metadata)
        for filename, exact in versions.items():
            for wrong in (exact.split(':', 1)[1], '2:' + exact.split(':', 1)[1],
                          exact.rsplit('-', 1)[0] + '-9'):
                with self.subTest(filename=filename, wrong=wrong):
                    metadata.write_text(json.dumps({'versions': {**versions, filename: wrong}}))
                    with self.assertRaisesRegex(ValueError, 'differs from archive filename'):
                        m.validate_live_closure(self.closure, pkglist, target_closure, metadata)
        metadata.write_text(json.dumps({'versions': versions}))
        for wrong in ('2.6-2', '2:2.6-2', '1:2.6-9'):
            with self.subTest(installed=wrong):
                pkglist.write_text('rtmpdump ' + wrong + '\n')
                with self.assertRaisesRegex(ValueError, 'differs from image package set'):
                    m.validate_live_closure(self.closure, pkglist, target_closure, metadata)

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

    def test_git_tags_require_recipe_checksums_even_when_signed(self):
        record = {'name': 'demo', 'version': '1-1'}
        for suffix in ('', '?signed'):
            text = ('pkgname = demo\npkgver = 1\npkgrel = 1\n'
                    'source = git+https://example.invalid/source#tag=v1' + suffix + '\n')
            for checksum in ('', 'sha256sums = SKIP\n'):
                with self.subTest(suffix=suffix, checksum=checksum), self.assertRaisesRegex(
                        ValueError, 'non-SKIP'):
                    m.check_recipe(text + checksum, record)
            m.check_recipe(text + 'sha256sums = ' + 'a' * 64 + '\n', record)

    def test_cached_dependency_archive_without_supplement_is_refused(self):
        record = {'base': 'demo', 'name': 'demo', 'version': '1-1',
                  'recipe_sha256': self.sha}
        archive = self.root / 'cargo.src.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            for name, data in {'PKGBUILD': self.recipe,
                               '.SRCINFO': b'pkgname = demo\npkgver = 1\npkgrel = 1\n'}.items():
                entry = tarfile.TarInfo('demo/' + name)
                entry.size = len(data)
                bundle.addfile(entry, io.BytesIO(data))
        for module in (m.cargo_sources, m.go_sources):
            with self.subTest(module=module.__name__):
                with patch.dict(module.RECIPES, {self.sha: ('demo', 'demo')}):
                    with self.assertRaisesRegex(ValueError, 'not archived and listed'):
                        m.validate_archive(archive, record)

    def test_pin_identity_ignores_explanations_but_checks_nested_commits(self):
        output = self.root / 'pins'
        record = {'base': 'demo', 'recipe_sha256': self.sha}
        pin = {'source': 'primary', 'kind': 'gitlink', 'reference': 'submodule',
               'commit': 'a' * 40, 'evidence': {'selection': 'old explanation'},
               'nested_sources': [{'source': 'nested', 'kind': 'gitlink', 'commit': 'b' * 40}]}
        m.remember_pins(output, record, [pin])
        pin['evidence']['selection'] = 'new explanation'
        m.remember_pins(output, record, [pin])
        self.assertEqual(len(list((output / 'git-pins').iterdir())), 1)
        pin['nested_sources'][0]['commit'] = 'c' * 40
        with self.assertRaisesRegex(ValueError, 'resolved differently'):
            m.remember_pins(output, record, [pin])

    def test_pin_write_failure_leaves_no_partial_record(self):
        output = self.root / 'pins'
        record = {'base': 'demo', 'recipe_sha256': self.sha}
        with patch.object(m.os, 'fsync', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                m.remember_pins(output, record, [])
        self.assertEqual(list((output / 'git-pins').iterdir()), [])
        m.remember_pins(output, record, [])

    def test_truncated_job_manifest_does_not_block_or_change_pins(self):
        output = self.root / 'pins'
        record = {'base': 'demo', 'recipe_sha256': self.sha}
        pin = {'source': 'primary', 'kind': 'tag', 'reference': 'v1', 'commit': 'a' * 40}
        m.remember_pins(output, record, [pin])
        for truncated in ('', '{"packages": {"demo":'):
            with self.subTest(manifest=truncated):
                (output / 'ARCH-SOURCES.json').write_text(truncated)
                m.remember_pins(output, record, [pin])
                with self.assertRaisesRegex(ValueError, 'resolved differently'):
                    m.remember_pins(output, record, [dict(pin, commit='b' * 40)])

    def test_job_manifest_write_failure_preserves_previous_json(self):
        target = self.root / 'ARCH-SOURCES.json'
        m.atomic_json(target, {'packages': {}})
        with patch.object(m.os, 'fsync', side_effect=OSError('interrupted')):
            with self.assertRaises(OSError):
                m.atomic_json(target, {'packages': {'new': {}}})
        self.assertEqual(json.loads(target.read_text()), {'packages': {}})
        self.assertEqual(list(self.root.glob('tmp*')), [])
        m.atomic_json(target, {'packages': {'new': {}}})
        self.assertEqual(json.loads(target.read_text()), {'packages': {'new': {}}})

    def test_archive_metadata_is_bound_to_binary(self):
        source = self.root / 'source.tar.gz'
        record = {'base': 'demo', 'name': 'demo', 'version': '1-1', 'recipe_sha256': self.sha}
        def archive(recipe=self.recipe, metadata=b'pkgname = demo\npkgver = 1\npkgrel = 1\n', duplicate=False):
            with tarfile.open(source, 'w:gz') as output:
                entries = [('demo/PKGBUILD', recipe), ('demo/.SRCINFO', metadata)]
                if duplicate:
                    entries.append(entries[0])
                for name, data in entries:
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    output.addfile(member, io.BytesIO(data))
        archive()
        m.validate_archive(source, record)
        archive(recipe=b'changed')
        with self.assertRaisesRegex(ValueError, 'PKGBUILD'):
            m.validate_archive(source, record)
        for metadata in (b'pkgname = other\npkgver = 1\npkgrel = 1\n',
                         b'pkgname = demo\npkgver = 2\npkgrel = 1\n'):
            archive(metadata=metadata)
            with self.assertRaisesRegex(ValueError, 'name and version'):
                m.validate_archive(source, record)
        archive(duplicate=True)
        with self.assertRaisesRegex(ValueError, 'one regular'):
            m.validate_archive(source, record)
        source.write_bytes(b'not an archive')
        with self.assertRaisesRegex(ValueError, 'invalid source archive'):
            m.validate_archive(source, record)

    def test_signature_alias_uses_downloaded_filename(self):
        text = ('pkgname = demo\npkgver = 1\npkgrel = 1\n'
                'source = https://example.invalid/source.tar.gz\n'
                'source = source.tar.gz.sig::https://example.invalid/source.tar.gz.txt\n'
                'sha256sums = ' + 'a' * 64 + '\nsha256sums = SKIP\n')
        m.check_recipe(text, {'name': 'demo', 'version': '1-1'})
        with self.assertRaisesRegex(ValueError, 'unchecked'):
            m.check_recipe(text.replace('source.tar.gz.sig::', 'payload::'),
                           {'name': 'demo', 'version': '1-1'})

    def test_arch_project_path_mapping(self):
        # devtools gitlab_project_name_to_path, including each transformation.
        for base, expected in {
                'libsigc++': 'libsigcplusplus', 'libsigc++-3.0': 'libsigcplusplus-3.0',
                'tree': 'unix-tree', 'memtest86+': 'memtest86plus',
                'gtk+': 'gtkplus', 'gtk3': 'gtk3', 'libc++': 'libcplusplus',
                'a+b': 'a-b', 'a+b+c': 'a-bplusc', 'foo@bar': 'foo-bar',
                'a__--b': 'a-b', 'foo_bar.1': 'foo_bar.1', 'Tree': 'Tree'}.items():
            with self.subTest(base=base):
                self.assertEqual(m.project_path(base), expected)

    def test_tag_resolution_and_movement_are_recorded(self):
        repo = self.root / 'upstream'
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        def commit(value):
            (repo / 'source').write_text(value)
            subprocess.run(['git', '-C', str(repo), 'add', 'source'], check=True)
            subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Fixture',
                            '-c', 'user.email=fixture@example.org', '-c', 'commit.gpgsign=false',
                            'commit', '-qm', 'Source'], check=True)
            return m.run('git', '-C', str(repo), 'rev-parse', 'HEAD').strip()
        first = commit('first')
        subprocess.run(['git', '-C', str(repo), '-c', 'tag.gpgsign=false',
                        'tag', 'v1'], check=True)
        source = 'upstream::git+https://example.invalid/source.git#tag=v1?signed'
        info = 'pkgname = demo\npkgver = 1\npkgrel = 1\nsource = ' + source + '\nsha256sums = ' + 'a' * 64 + '\n'
        record = {'name': 'demo', 'base': 'demo', 'version': '1-1', 'recipe_sha256': self.sha}
        m.check_recipe(info, record)
        pins = m.source_pins(info, self.root)
        self.assertEqual(pins, [{'source': source, 'kind': 'tag', 'reference': 'v1', 'commit': first}])
        output = self.root / 'output'
        m.remember_pins(output, record, pins)
        m.remember_pins(output, record, pins)
        second = commit('second')
        subprocess.run(['git', '-C', str(repo), '-c', 'tag.gpgsign=false',
                        'tag', '-f', 'v1'], check=True, capture_output=True)
        moved = m.source_pins(info, self.root)
        self.assertEqual(moved[0]['commit'], second)
        with self.assertRaisesRegex(ValueError, 'resolved differently'):
            m.remember_pins(output, record, moved)
        # A transferred published manifest also protects a re-run without the local lock file.
        (output / 'ARCH-SOURCES.json').write_text(json.dumps({
            'packages': {'demo.pkg.tar.zst': dict(record, git_sources=pins)}}))
        next((output / 'git-pins').iterdir()).unlink()
        with self.assertRaisesRegex(ValueError, 'resolved differently'):
            m.remember_pins(output, record, moved)

    def test_git_source_syntax(self):
        for source in ('git+https://example.invalid/source#branch=main',
                       'git://example.invalid/source',
                       'git+https://example.invalid/source#commit=abc',
                       'git+https://example.invalid/source#tag=',
                       '../upstream::git+https://example.invalid/source#tag=v1'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                m.git_source(source)
        self.assertEqual(m.git_source('git+https://example.invalid/source.git#tag=v1'),
                         ('source', 'tag', 'v1'))
        self.assertEqual(m.git_source('git://example.invalid/source.git#commit=' + 'a' * 40),
                         ('source', 'commit', 'a' * 40))
        with self.assertRaises(subprocess.CalledProcessError):
            m.git_source('git+https://example.invalid/source#tag=v1^{commit}')

    def test_raw_tag_object_and_commit_selectors(self):
        upstream = self.root / 'upstream'
        m.run('git', 'init', '-q', str(upstream))
        (upstream / 'source').write_text('source')
        m.run('git', '-C', str(upstream), 'add', 'source')
        identity = ('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.org')
        m.run('git', '-C', str(upstream), *identity, '-c', 'commit.gpgsign=false',
              'commit', '-qm', 'Source')
        commit = m.run('git', '-C', str(upstream), 'rev-parse', 'HEAD').strip()
        m.run('git', '-C', str(upstream), *identity, '-c', 'tag.gpgsign=false',
              'tag', '-am', 'Release', 'v1')
        tag = m.run('git', '-C', str(upstream), 'rev-parse', 'refs/tags/v1').strip()
        downloads = self.root / 'downloads'
        downloads.mkdir()
        for index, selector in enumerate((commit, tag)):
            source = f'raw{index}::git+file://{upstream}#tag={selector}'
            self.assertEqual(m.fetch_git_reference(source, downloads), commit)
            self.assertEqual(m.source_pins('source = ' + source, downloads), [
                {'source': source, 'kind': 'tag', 'reference': selector, 'commit': commit}])
            self.assertEqual(m.run('git', '-C', str(downloads / f'raw{index}'),
                                   'rev-parse', 'refs/emaki/objects/' + selector).strip(), selector)
        with self.assertRaises(subprocess.CalledProcessError):
            m.source_pins('source = raw0::git+file://' + str(upstream) + '#tag=' + '0' * 40,
                          downloads)

    def test_reviewed_submodule_requires_exact_recipe_and_source(self):
        sha = next(sha for sha, mapping in m.SUBMODULE_RECIPES.items() if mapping[0] == 'coreutils')
        record = {'name': 'coreutils', 'base': 'coreutils', 'version': '9.12-2',
                  'recipe_sha256': sha}
        text = ('pkgname = coreutils\npkgver = 9.12\npkgrel = 2\n'
                'source = git+https://git.savannah.gnu.org/git/coreutils.git?signed#tag=v9.12\n'
                'source = git+https://git.savannah.gnu.org/git/gnulib.git\n'
                'sha256sums = ' + 'a' * 64 + '\nsha256sums = SKIP\n')
        m.check_recipe(text, record)
        with self.assertRaisesRegex(ValueError, 'unpinned'):
            m.check_recipe(text, dict(record, recipe_sha256='0' * 64))
        with self.assertRaisesRegex(ValueError, 'mapping differs'):
            m.check_recipe(text.replace('/gnulib.git', '/other.git'), record)
        with self.assertRaisesRegex(ValueError, 'package base'):
            m.check_recipe(text, dict(record, base='other'))

    def test_exact_git_prefetch_requires_reviewed_recipe_and_selector(self):
        record = {'base': 'rtmpdump',
                  'recipe_sha256': '32be15a8f00c4231f18d3e11d90cb44b210b9aa824855a9bad86140e8cf94aa0'}
        source = 'git+https://git.ffmpeg.org/rtmpdump#tag=138fdb258d9fc26f1843fd1b891180416c9dc575'
        with patch.object(m, 'fetch_git_reference') as fetch:
            self.assertTrue(m.prepare_exact_git('source = ' + source, record, self.root))
            fetch.assert_called_once_with(source, self.root, env=None)
            self.assertFalse(m.prepare_exact_git('source = ' + source,
                dict(record, recipe_sha256='0' * 64), self.root))
            with self.assertRaisesRegex(ValueError, 'differs from recipe'):
                m.prepare_exact_git('source = ' + source + '0', record, self.root)

    def test_gitlink_fetch_is_exact_and_preserves_tag(self):
        upstream = self.root / 'upstream'
        m.run('git', 'init', '-q', str(upstream))
        (upstream / 'file').write_text('source')
        m.run('git', '-C', str(upstream), 'add', 'file')
        m.run('git', '-C', str(upstream), '-c', 'user.name=Fixture',
              '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false',
              'commit', '-qm', 'Source')
        commit = m.run('git', '-C', str(upstream), 'rev-parse', 'HEAD').strip()
        m.run('git', '-C', str(upstream), '-c', 'tag.gpgsign=false', 'tag', 'v1')
        downloads = self.root / 'downloads'
        downloads.mkdir()
        source = 'primary::git+file://' + str(upstream) + '#tag=v1'
        self.assertEqual(m.fetch_git_reference(source, downloads), commit)
        secondary = 'secondary::git+file://' + str(upstream)
        self.assertEqual(m.fetch_git_reference(secondary, downloads, commit=commit), commit)
        self.assertEqual(m.run('git', '-C', str(downloads / 'secondary'),
                               'rev-parse', 'HEAD').strip(), commit)
        # A bare mirror remains self-contained and contains the exact gitlink commit.
        self.assertFalse((downloads / 'secondary' / 'objects/info/alternates').exists())
        m.run('git', '-C', str(downloads / 'primary'), 'update-index', '--add',
              '--cacheinfo', '160000,' + commit + ',subprojects/secondary')
        tree = m.run('git', '-C', str(downloads / 'primary'), 'write-tree').strip()
        primary_commit = m.run('git', '-C', str(downloads / 'primary'),
                               '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                               'commit-tree', tree, '-m', 'Gitlink').strip()
        m.run('git', '-C', str(downloads / 'primary'), 'update-ref', 'refs/tags/v1', primary_commit)
        evidence = {secondary: {'primary_source': source, 'path': 'subprojects/secondary',
                               'recipe_sha256': self.sha}}
        info = 'source = ' + source + '\nsource = ' + secondary + '\n'
        verification_env = {'EMAKI_SWH_VAULT_COOK': '1'}
        with patch.object(m, 'recipe_submodules', return_value=evidence), \
                patch.object(m, 'fetch_git_reference') as fetch:
            result = m.prepare_submodules(info, {}, downloads, verification_env)
        self.assertEqual(fetch.call_args_list[-1].kwargs, {'commit': commit, 'env': verification_env})
        self.assertEqual(result[secondary]['evidence']['primary_commit'], primary_commit)
        self.assertEqual(m.source_pins(info, downloads, result)[1]['commit'], commit)
        # Even an explicitly pinned cherry-pick cannot silently replace the gitlink.
        evidence[secondary]['additional_primary_commits'] = [primary_commit]
        (downloads / 'primary' / 'FETCH_HEAD').write_text(primary_commit + '\n')
        original_run = m.run
        def no_remote_fetch(*args, **kwargs):
            if 'fetch' in args:
                return ''
            return original_run(*args, **kwargs)
        with patch.object(m, 'recipe_submodules', return_value=evidence), \
                patch.object(m, 'fetch_git_reference'), \
                patch.object(m, 'run', side_effect=no_remote_fetch), \
                self.assertRaisesRegex(ValueError, 'cherry-pick changes'):
            m.prepare_submodules(info, {}, downloads)
        evidence[secondary]['additional_primary_commits'] = []
        evidence[secondary]['path'] = 'missing'
        with patch.object(m, 'recipe_submodules', return_value=evidence), \
                patch.object(m, 'fetch_git_reference'), \
                self.assertRaisesRegex(ValueError, 'primary gitlinks are absent'):
            m.prepare_submodules(info, {}, downloads)

    def test_vpnc_stale_submodule_commands_do_not_pin_wiki(self):
        # The pinned primary tree has no .gitmodules or gitlinks. Its recipe's
        # stale submodule commands cannot establish the wiki revision.
        text = ('pkgname = vpnc\npkgver = 0.5.3.r557.r241\npkgrel = 1\nepoch = 1\n'
                'source = git+https://github.com/streambinder/vpnc.wiki.git\n')
        record = {'name': 'vpnc', 'base': 'vpnc', 'version': '1:0.5.3.r557.r241-1',
                  'recipe_sha256': '102d7189f3758d545c542cc1f934475da0b4eeef075d2c64905e55c2857809ee'}
        with self.assertRaisesRegex(ValueError, 'unpinned|unused-source evidence'):
            m.check_recipe(text, record)

    def test_reviewed_gitlink_transition_keeps_both_required_commits(self):
        primary = 'git+https://example.invalid/primary#tag=v1'
        secondary = 'git+https://example.invalid/secondary'
        before, after, change = '1' * 40, '2' * 40, '3' * 40
        update = {'primary_commit': change, 'before': before, 'after': after}
        evidence = {secondary: {'primary_source': primary, 'path': 'gnulib',
                    'additional_primary_commits': [change], 'gitlink_update': update}}
        def git(*args, **kwargs):
            if 'diff-tree' in args:
                return 'gnulib\n'
            if 'ls-tree' in args:
                return '160000 commit ' + (after if change in args else before) + '\tgnulib\n'
            return change if 'FETCH_HEAD^{commit}' in args else '9' * 40
        with patch.object(m, 'recipe_submodules', return_value=evidence), \
                patch.object(m, 'run', side_effect=git) as run, \
                patch.object(m, 'fetch_git_reference') as fetch:
            result = m.prepare_submodules('source = ' + primary, {}, self.root)
            self.assertEqual(result[secondary]['commit'], after)
            fetch.assert_called_with(secondary, self.root, commit=after, env=None)
            self.assertTrue(any('cat-file' in call.args and before + '^{commit}' in call.args
                                for call in run.call_args_list))
            update['before'] = '4' * 40
            with self.assertRaisesRegex(ValueError, 'differs from reviewed transition'):
                m.prepare_submodules('source = ' + primary, {}, self.root)

    def test_omitted_gitlinks_require_exact_configuration_and_objects(self):
        primary = 'git+https://example.invalid/primary#tag=v1'
        secondary = 'git+https://example.invalid/secondary'
        content = '[submodule "generator"]\n\tupdate = none\n'
        commit = '1' * 40
        omitted = {'gitmodules_sha256': hashlib.sha256(content.encode()).hexdigest(),
                   'paths': {'generator': commit}}
        evidence = {secondary: {'primary_source': primary, 'path': 'used',
                    'omitted_gitlinks': omitted}}
        extra = ''
        def git(*args, **kwargs):
            if 'show' in args:
                return content
            if 'ls-tree' in args:
                if '-r' in args:
                    return f'160000 commit {commit}\tused\n160000 commit {commit}\tgenerator\n' + extra
                return '160000 commit ' + commit + '\t' + args[-1] + '\n'
            return commit
        with patch.object(m, 'recipe_submodules', return_value=evidence), \
                patch.object(m, 'run', side_effect=git), patch.object(m, 'fetch_git_reference'):
            self.assertEqual(m.prepare_submodules('source = ' + primary, {}, self.root)[secondary]['commit'], commit)
            extra = f'160000 commit {commit}\tunknown\n'
            with self.assertRaisesRegex(ValueError, 'absent from reviewed'):
                m.prepare_submodules('source = ' + primary, {}, self.root)
            extra = ''
            omitted['paths']['generator'] = '2' * 40
            with self.assertRaisesRegex(ValueError, 'omitted gitlink differs'):
                m.prepare_submodules('source = ' + primary, {}, self.root)
            omitted['paths']['generator'] = commit
            omitted['gitmodules_sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'configuration differs'):
                m.prepare_submodules('source = ' + primary, {}, self.root)

    def test_m4_primary_bootstrap_is_retained_at_its_gitlink(self):
        source = m.GNU_GIT + 'm4.git#tag=v1.4.21?signed'
        evidence = {'recipe_sha256': '16b4384c87031c8294d9e61c918cd05d3e50698f5cef3d1c6950b2211c7f0efb'}
        (self.root / 'm4').mkdir()
        commit = 'a' * 40
        url = 'https://github.com/gnulib-modules/bootstrap.git'
        def git(*args, **kwargs):
            if 'config' in args:
                return url
            if 'ls-tree' in args and str(self.root / 'm4') in args:
                return f'160000 commit {commit}\tgnulib\n160000 commit {commit}\tgl-mod/bootstrap\n'
            return ''
        with patch.object(m, 'run', side_effect=git), patch.object(m, 'fetch_git_reference') as fetch:
            pins = m.prepare_nested_sources(source, 'b' * 40, evidence, self.root, covered={'gnulib'})
            self.assertEqual(pins[0]['path'], 'gl-mod/bootstrap')
            self.assertEqual(pins[0]['archive_directory'], 'm4/source-supplements/bootstrap')
            fetch.assert_called_once_with('git+' + url, self.root / 'm4/source-supplements',
                                          commit=commit, env=None)
            url = 'https://example.invalid/unreviewed'
            with self.assertRaisesRegex(ValueError, 'URL differs'):
                m.prepare_nested_sources(source, 'b' * 40, evidence, self.root, covered={'gnulib'})
            with self.assertRaisesRegex(ValueError, 'need exact-source'):
                m.prepare_nested_sources(source, 'b' * 40, {'recipe_sha256': '0' * 64},
                                         self.root, covered={'gnulib'})

    def test_bootstrap_revision_requires_reviewed_pinned_files(self):
        primary = 'git+https://example.invalid/primary#tag=v1'
        secondary = 'git+https://example.invalid/secondary'
        commit = '1' * 40
        content = 'GNULIB_REVISION=' + commit + '\n'
        evidence = {secondary: {'primary_source': primary, 'path': 'bootstrap.conf',
                    'recipe_sha256': self.sha, 'bootstrap': {'commit': commit,
                    'files': {'bootstrap.conf': hashlib.sha256(content.encode()).hexdigest()}}}}
        def pinned_file(*args, **kwargs):
            if 'show' in args:
                return content
            return '2' * 40
        with patch.object(m, 'recipe_submodules', return_value=evidence), \
                patch.object(m, 'run', side_effect=pinned_file), \
                patch.object(m, 'fetch_git_reference') as fetch:
            result = m.prepare_submodules('source = ' + primary, {}, self.root)
            self.assertEqual(result[secondary]['kind'], 'bootstrap-revision')
            self.assertEqual(fetch.call_args.kwargs, {'commit': commit, 'env': None})
            evidence[secondary]['bootstrap']['files']['bootstrap.conf'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'bootstrap evidence differs'):
                m.prepare_submodules('source = ' + primary, {}, self.root)

    def test_recursive_secondary_is_refused_when_nested_sources_are_absent(self):
        primary = 'git+https://example.invalid/primary#tag=v1'
        secondary = 'git+https://example.invalid/secondary'
        commit = '1' * 40
        evidence = {secondary: {'primary_source': primary, 'path': 'module',
                    'recipe_sha256': self.sha, 'recursive': True}}
        def tree(*args, **kwargs):
            if 'ls-tree' in args:
                return '160000 commit ' + commit + ('\tnested\n' if str(self.root / 'secondary') in args else '\tmodule\n')
            return '2' * 40
        with patch.object(m, 'recipe_submodules', return_value=evidence), \
                patch.object(m, 'run', side_effect=tree), \
                patch.object(m, 'fetch_git_reference'), \
                self.assertRaisesRegex(ValueError, 'recursive secondary gitlinks'):
            m.prepare_submodules('source = ' + primary, {}, self.root)

    @unittest.skipUnless(shutil.which('makepkg') and os.geteuid() != 0, 'requires non-root makepkg')
    def test_makepkg_archive_retains_nested_git_objects(self):
        upstream = self.root / 'nested-upstream'
        m.run('git', 'init', '-q', str(upstream))
        (upstream / 'source').write_text('nested source bytes\n')
        m.run('git', '-C', str(upstream), 'add', 'source')
        m.run('git', '-C', str(upstream), '-c', 'user.name=Fixture',
              '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false',
              'commit', '-qm', 'Source')
        commit = m.run('git', '-C', str(upstream), 'rev-parse', 'HEAD').strip()
        downloads = self.root / 'downloads'
        downloads.mkdir()
        source = 'parent::git+file://' + str(upstream) + '#commit=' + commit
        m.fetch_git_reference(source, downloads)
        supplements = downloads / 'parent/source-supplements'
        supplements.mkdir()
        m.fetch_git_reference('nested::git+file://' + str(upstream), supplements, commit=commit)
        recipe = self.root / 'recipe'
        recipe.mkdir()
        (recipe / 'PKGBUILD').write_text(
            '# Copyright (C) 2026 Artur Yakymenko\n# SPDX-License-Identifier: GPL-3.0-or-later\n'
            'pkgname=fixture\npkgver=1\npkgrel=1\narch=(any)\n'
            'source=("' + source + '")\nsha256sums=(SKIP)\n')
        archives = self.root / 'archives'
        archives.mkdir()
        env = dict(os.environ, SRCDEST=str(downloads), SRCPKGDEST=str(archives))
        m.run('makepkg', '--allsource', '--force', '--holdver', cwd=recipe, env=env)
        original = next(archives.glob('*.src.tar.*'))
        normalized = self.root / 'normalized.tar.gz'
        m.normalize(original, normalized)
        restored = self.root / 'restored'
        restored.mkdir()
        with tarfile.open(normalized) as archive:
            archive.extractall(restored, filter='data')
        nested = restored / 'fixture/parent/source-supplements/nested'
        self.assertEqual(m.run('git', '-C', str(nested), 'show', commit + ':source'),
                         'nested source bytes\n')
        self.assertFalse((nested / 'objects/info/alternates').exists())

    def test_collection_split_packages_and_manifest_validation(self):
        filename = self.package()
        self.package('demo-library', 'LGPL')
        calls = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            if args[:2] == ('makepkg', '--printsrcinfo'):
                return 'pkgname = demo\npkgname = demo-library\npkgver = 1\npkgrel = 1\n'
            if args[:2] == ('makepkg', '--allsource'):
                self.assertEqual(kwargs['env']['EMAKI_SWH_VAULT_COOK'], '0')
                archive = Path(kwargs['env']['SRCPKGDEST']) / 'demo.src.tar.gz'
                with tarfile.open(archive, 'w:gz') as out:
                    member = tarfile.TarInfo('demo/PKGBUILD')
                    member.size = len(self.recipe)
                    out.addfile(member, io.BytesIO(self.recipe))
                    metadata = b'pkgname = demo\npkgname = demo-library\npkgver = 1\npkgrel = 1\n'
                    member = tarfile.TarInfo('demo/.SRCINFO')
                    member.size = len(metadata)
                    out.addfile(member, io.BytesIO(metadata))
                return ''
            if args[:2] == ('git', 'clone'):
                repo = Path(args[-1])
                (repo / 'keys/pgp').mkdir(parents=True)
                (repo / 'keys/pgp/key.asc').write_text('public key fixture')
                (repo / 'PKGBUILD').write_bytes(self.recipe)
                return ''
            if args[0] == 'gpg':
                self.assertIn('--no-autostart', args)
                keyring = Path(kwargs['env']['GNUPGHOME'])
                self.assertEqual(keyring.stat().st_mode & 0o777, 0o700)
                self.assertIn('no-auto-key-retrieve', (keyring / 'gpg.conf').read_text())
                return ''
            if args[0] == 'git':
                return ''
            return original(*args, **kwargs)

        original = m.run
        output = self.root / 'output'
        with patch.object(m, 'run', side_effect=fake_run), \
                patch.object(m, 'exact_revision', return_value='a' * 40), \
                patch.dict(os.environ, EMAKI_SWH_VAULT_COOK='1'):
            m.collect(self.closure, self.root, output)
        self.assertEqual(sum(call[:2] == ('makepkg', '--allsource') for call in calls), 1)
        self.assertEqual(sum(call[0] == 'gpg' for call in calls), 1)
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
        missing = output / 'MISSING-SOURCES.json'
        missing.write_text(json.dumps({'schema': 1, 'closure_sha256': m.digest(self.closure),
            'bases': [{'base': 'demo', 'version': '1-1', 'recipe_sha256': self.sha,
                       'packages': {filename: m.inventory(self.closure, self.root)[filename]},
                       'reason': 'Download refused', 'upstream_url': 'https://example.org/demo'}]}))
        partial, partial_objects = m.validate(manifest, self.closure, self.root, missing_sources=missing)
        self.assertEqual(len(partial), 1)
        self.assertEqual(partial_objects, objects)
        source = next(iter(objects.values()))
        original_source = source.read_bytes()
        source.write_bytes(b'broken')
        with self.assertRaisesRegex(ValueError, 'bytes differ'):
            m.validate(manifest, self.closure, self.root, missing_sources=missing)
        source.write_bytes(original_source)
        data['closure_sha256'] = '0' * 64
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'does not describe'):
            m.validate(manifest, self.closure, self.root, missing_sources=missing)
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

    def test_collection_archives_refreshed_key_without_changing_recipe_key(self):
        filename = self.package()
        fingerprint = 'A' * 40
        original_key = b'original recipe key\r\n'
        refreshed_key = b'refreshed verification key\n'
        metadata = ('pkgname = demo\npkgver = 1\npkgrel = 1\nvalidpgpkeys = ' +
                    fingerprint + '\n')
        key_name = fingerprint + '.asc'
        refreshed_name = fingerprint + '.refreshed.asc'
        attempts = []
        original_run = m.run

        def fake_run(*args, **kwargs):
            if args[:2] == ('git', 'clone'):
                repo = Path(args[-1])
                (repo / 'keys/pgp').mkdir(parents=True)
                (repo / 'keys/pgp' / key_name).write_bytes(original_key)
                (repo / 'PKGBUILD').write_bytes(self.recipe)
                return ''
            if args[0] in ('git', 'gpg'):
                return ''
            if args[:2] == ('makepkg', '--printsrcinfo'):
                return metadata
            if args[:2] == ('makepkg', '--allsource'):
                attempts.append(args)
                if len(attempts) == 1:
                    raise subprocess.CalledProcessError(1, args, stderr='unknown public key')
                recipe = kwargs['cwd']
                self.assertEqual((recipe / 'keys/pgp' / key_name).read_bytes(), original_key)
                self.assertEqual((recipe / 'keys/pgp' / refreshed_name).read_bytes(), refreshed_key)
                archive = Path(kwargs['env']['SRCPKGDEST']) / 'demo.src.tar.gz'
                # Makepkg retains only the file named by validpgpkeys.
                with tarfile.open(archive, 'w:gz') as bundle:
                    for name, data in {'PKGBUILD': self.recipe, '.SRCINFO': metadata.encode(),
                                       'keys/pgp/' + key_name: original_key}.items():
                        member = tarfile.TarInfo('demo/' + name)
                        member.size = len(data)
                        bundle.addfile(member, io.BytesIO(data))
                return ''
            return original_run(*args, **kwargs)

        def prepare(fingerprints, recipe, env, run, *, refresh=False):
            self.assertEqual(fingerprints, [fingerprint])
            if not refresh:
                return []
            (recipe / 'keys/pgp' / refreshed_name).write_bytes(refreshed_key)
            return [{'fingerprint': fingerprint, 'file': refreshed_name,
                     'url': m.source_keys.SERVERS[0].format(fingerprint=fingerprint),
                     'sha256': hashlib.sha256(refreshed_key).hexdigest(),
                     'recipe_key_sha256': hashlib.sha256(original_key).hexdigest()}]

        output = self.root / 'output'
        with patch.object(m, 'run', side_effect=fake_run), \
                patch.object(m, 'exact_revision', return_value='a' * 40), \
                patch.object(m.source_keys, 'prepare', side_effect=prepare):
            m.collect(self.closure, self.root, output)
        self.assertEqual(len(attempts), 2)
        records, objects = m.validate(output / 'ARCH-SOURCES.json', self.closure, self.root)
        record = records[filename]
        self.assertEqual(record['verification_keys'][0]['sha256'], hashlib.sha256(refreshed_key).hexdigest())
        self.assertEqual(record['verification_keys'][0]['recipe_key_sha256'], hashlib.sha256(original_key).hexdigest())
        with tarfile.open(next(iter(objects.values()))) as bundle:
            self.assertEqual(bundle.extractfile('demo/keys/pgp/' + key_name).read(), original_key)
            self.assertEqual(bundle.extractfile('demo/keys/pgp/' + refreshed_name).read(), refreshed_key)
            for field in ('sha256', 'recipe_key_sha256'):
                changed = dict(record, verification_keys=[dict(record['verification_keys'][0], **{field: '0' * 64})])
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'key bytes changed'):
                    m.source_keys.validate(bundle, changed, m.fields(metadata))

    def test_fallback_runs_only_after_normal_refusal(self):
        self.package()
        metadata = 'pkgname = demo\npkgver = 1\npkgrel = 1\n'
        complete = self.root / 'complete.src.tar.gz'
        with tarfile.open(complete, 'w:gz') as bundle:
            for name, data in {'PKGBUILD': self.recipe, '.SRCINFO': metadata.encode()}.items():
                member = tarfile.TarInfo('demo/' + name)
                member.size = len(data)
                bundle.addfile(member, io.BytesIO(data))
        original = m.run
        def run(*args, **kwargs):
            if args[:2] == ('git', 'clone'):
                repo = Path(args[-1])
                repo.mkdir()
                (repo / 'PKGBUILD').write_bytes(self.recipe)
                return ''
            if args[0] == 'git':
                return ''
            if args[:2] == ('makepkg', '--printsrcinfo'):
                return metadata
            if args[:2] == ('makepkg', '--allsource'):
                if refuse:
                    raise subprocess.CalledProcessError(1, args)
                shutil.copyfile(complete, Path(kwargs['env']['SRCPKGDEST']) / complete.name)
                return ''
            return original(*args, **kwargs)
        for refuse, eligible in ((False, True), (True, False), (True, True)):
            with self.subTest(refuse=refuse, eligible=eligible), \
                    patch.object(m, 'run', side_effect=run), \
                    patch.object(m, 'exact_revision', return_value='a' * 40), \
                    patch.object(m.arch_source_fallback, 'eligible', return_value=eligible), \
                    patch.object(m.arch_source_fallback, 'collect',
                                 return_value=(complete, {'kind': 'fixture'}, [])) as fallback, \
                    patch.object(m.source_routes, 'validate_routes'):
                output = self.root / f'output-{refuse}-{eligible}'
                if refuse and not eligible:
                    with self.assertRaises(subprocess.CalledProcessError):
                        m.collect(self.closure, self.root, output)
                else:
                    m.collect(self.closure, self.root, output)
                self.assertEqual(fallback.call_count, int(refuse and eligible))

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
                data = f'# Generated by makepkg 7.1\n# date {timestamp}\npkgbase = demo\n'.encode()
                info = tarfile.TarInfo('demo/.SRCINFO')
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            target = self.root / f'{timestamp}.gz'
            m.normalize(source, target)
            results.append(target.read_bytes())
        self.assertEqual(*results)


if __name__ == '__main__':
    unittest.main()
