#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline checks for retained preparation-time Cargo inputs."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging/mirror'))

SPEC = importlib.util.spec_from_file_location(
    'cargo_sources', Path(__file__).resolve().parents[1] / 'packaging/mirror/cargo_sources.py')
CARGO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CARGO)


class CargoSources(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.record = {'base': 'niri', 'recipe_sha256': next(
            key for key, value in CARGO.RECIPES.items() if value[0] == 'niri')}
        self.tree = self.root / 'source'
        self.tree.mkdir()
        self.lock = (b'version = 3\n[[package]]\nname="crate"\nversion="1.0.0"\n'
                     b'source="registry+https://example.invalid/index"\nchecksum="' + b'a' * 64 + b'"\n')
        (self.tree / 'Cargo.lock').write_bytes(self.lock)
        self.destination = self.root / 'supplement'

    def vendor(self, command, **kwargs):
        self.assertIn('--locked', command)
        self.assertEqual(kwargs['env']['CARGO_HTTP_USER_AGENT'], 'emaki-publish')
        folder = Path(command[-1]) / 'crate-1.0.0'
        folder.mkdir(parents=True)
        manifest = b'[package]\nname="crate"\nversion="1.0.0"\n'
        (folder / 'Cargo.toml').write_bytes(manifest)
        (folder / '.cargo-checksum.json').write_text(json.dumps(
            {'package': 'a' * 64, 'files': {'Cargo.toml': CARGO.sha256(manifest)}}))
        return subprocess.CompletedProcess(command, 0, stdout='[source.vendored-sources]\ndirectory = "'
                                           + command[-1] + '"\n')

    def build(self):
        with patch.object(CARGO.subprocess, 'run', side_effect=self.vendor):
            evidence = CARGO.create_supplement(self.record, self.tree, self.destination, {})
        self.record['cargo_supplement'] = evidence
        source = self.root / 'source.tar'
        with tarfile.open(source, 'w') as archive:
            member = tarfile.TarInfo('niri/PKGBUILD')
            archive.addfile(member, io.BytesIO())
        target = self.root / 'combined.tar'
        CARGO.add_supplement(source, self.destination, target, self.record)
        return target

    def test_eleven_reviewed_recipes(self):
        self.assertEqual(len(CARGO.RECIPES), 11)
        for sha, (base, tree) in CARGO.RECIPES.items():
            self.assertEqual(CARGO.required_tree({'base': base, 'recipe_sha256': sha}), tree)

    def test_changed_recipe_refused(self):
        self.record['recipe_sha256'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'reviewed recipe'):
            CARGO.required_tree(self.record)

    def test_unreviewed_fetch_refused(self):
        with self.assertRaisesRegex(ValueError, 'reviewed recipe'):
            CARGO.required_tree({'base': 'other', 'recipe_sha256': 'f' * 64},
                                'prepare() { cargo fetch --locked; }')

    def test_all_functions_and_fetch_spellings_refused(self):
        commands = ['cargo --locked fetch', 'cargo +stable fetch', '"$CARGO" fetch',
                    '"${CARGO}" vendor', 'cargo vendor', 'cargo cbuild',
                    'go build -mod=readonly', 'go mod download', 'npm ci',
                    'npm install', 'pip install -r requirements.txt',
                    'python -m pip download foo', 'uv sync', 'yarn install',
                    'curl https://example.invalid/input', 'wget https://example.invalid/input',
                    'git clone https://example.invalid/repo', 'export GOFLAGS="-mod=readonly"']
        for function in ['prepare', 'build', 'check', 'package_foo', 'custom_helper']:
            for command in commands:
                with self.subTest(function=function, command=command):
                    with self.assertRaisesRegex(ValueError, 'reviewed recipe'):
                        CARGO.required_tree({'base': 'other', 'recipe_sha256': 'f' * 64},
                                            function + '() { ' + command + '; }')

    def test_shell_command_prefixes(self):
        for command in ['if cargo fetch; then :; fi', 'true; then cargo fetch',
                        'while cargo fetch; do :; done', '! cargo fetch',
                        'env -u HOME cargo fetch', 'X="hello world" cargo fetch',
                        '/usr/bin/cargo fetch', '"cargo" fetch']:
            with self.subTest(command=command):
                self.assertTrue(CARGO.dependency_fetches(command))

    def test_review_download_gate_spellings(self):
        commands = ['git -C repo fetch', 'git -c protocol.version=2 clone URL',
                    'timeout 30 cargo fetch', 'xargs -r cargo fetch',
                    'eval "cargo fetch"', "sh -c 'cargo fetch'",
                    './cargo fetch', '"$srcdir"/cargo fetch', '${CARGO_BIN} fetch',
                    'result=`cargo fetch`', 'pip3.12 install thing', 'pipx install thing',
                    'svn checkout URL', 'rsync host:source .', 'cpanm Thing',
                    'meson subprojects download', 'cp dependency.wrap subprojects/']
        for command in commands:
            with self.subTest(command=command):
                self.assertTrue(CARGO.dependency_fetches(command))

    def test_wrap_file_requires_review(self):
        with self.assertRaisesRegex(ValueError, 'reviewed recipe'):
            CARGO.required_tree({'base': 'other', 'recipe_sha256': 'f' * 64},
                                'build() { meson setup build; }',
                                source_names=['subprojects/library.wrap'])

    def test_metadata_is_not_a_download_command(self):
        for recipe in ['depends=(curl libcurl.so)', 'makedepends=(\n  wget\n)',
                       'pkgdesc="QEMU curl block driver"',
                       'package() { _pick gcc-go usr/bin/go; }',
                       '# cargo fetch\npackage() { install source dest; }']:
            with self.subTest(recipe=recipe):
                self.assertFalse(CARGO.dependency_fetches(recipe))

    def test_long_metadata_assignment_is_not_a_command(self):
        self.assertFalse(CARGO.dependency_fetches('checksum=' + 'a' * 512))

    def test_in_tree_dependency_reviews_are_hash_bound(self):
        for sha, base in CARGO.IN_TREE_RECIPES.items():
            record = {'base': base, 'recipe_sha256': sha}
            self.assertIsNone(CARGO.required_tree(record, 'build() { go build; }'))
            record['recipe_sha256'] = 'f' * 64
            with self.assertRaisesRegex(ValueError, 'reviewed recipe'):
                CARGO.required_tree(record)

    def test_rsync_version_check_requires_exact_review(self):
        recipe = 'check() { if ./rsync -V | grep -q \'no IPv6\'; then exit 1; fi; make test; }'
        self.assertTrue(CARGO.dependency_fetches(recipe))
        record = {'base': 'rsync', 'recipe_sha256': 'f02c414fb424eb95513d3111a17dce252f1a0fa31a2eb09c0e0f75ca25753327'}
        self.assertIsNone(CARGO.required_tree(record, recipe))
        record['recipe_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'reviewed recipe'):
            CARGO.required_tree(record, recipe)

    def test_missing_build_lock_refused(self):
        (self.tree / 'Cargo.lock').unlink()
        with self.assertRaisesRegex(ValueError, 'binary build lock'):
            CARGO.prepare_tree(self.record, self.tree, self.root, lambda *args: '')

    def test_reviewed_date_reconstruction(self):
        evidence = json.loads((Path(CARGO.__file__).with_name('cargo-locks') /
                               'libimagequant-4.4.1-2.json').read_text())
        record = {key: evidence[key] for key in
                  ('base', 'version', 'recipe_sha256', 'binary_sha256')}
        lock, proof = CARGO.reconstructed_lock(record)
        versions = {item['name']: item['version'] for item in CARGO.locked_packages(lock)}
        self.assertEqual(versions['arrayvec'], '0.7.6')
        self.assertEqual(versions['rayon'], '1.12.0')
        self.assertEqual(proof['builddate'], 1776785686)
        for key in ('version', 'recipe_sha256', 'binary_sha256'):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'reviewed image binary'):
                CARGO.reconstructed_lock(dict(record, **{key: 'wrong'}))
        with self.assertRaisesRegex(ValueError, 'manifest mismatch'):
            with patch.object(Path, 'read_bytes', return_value=b'changed manifest'):
                with patch.object(CARGO, 'reconstructed_lock', return_value=(lock, proof)):
                    CARGO.prepare_tree(record, self.tree, self.root, lambda *args: '')

    def test_reconstruction_publication_and_binary_evidence(self):
        directory = Path(CARGO.__file__).with_name('cargo-locks')
        original = json.loads((directory / 'libimagequant-4.4.1-2.json').read_text())
        record = {key: original[key] for key in
                  ('base', 'version', 'recipe_sha256', 'binary_sha256')}
        for kind in ('binary', 'date', 'checksum'):
            proof = json.loads(json.dumps(original))
            if kind == 'binary':
                proof['binary_versions']['arrayvec'] = '99.0.0'
            elif kind == 'date':
                proof['packages'][0]['selected_entry']['pubtime'] = '2099-01-01T00:00:00Z'
            else:
                proof['packages'][0]['selected_entry']['cksum'] = 'f' * 64
            with self.subTest(kind=kind), patch.object(Path, 'read_text', return_value=json.dumps(proof)):
                with self.assertRaisesRegex(ValueError, 'binary crate versions|publication evidence'):
                    CARGO.reconstructed_lock(record)

    def test_old_archive_refused(self):
        with tarfile.open(fileobj=io.BytesIO(), mode='w') as archive:
            with self.assertRaisesRegex(ValueError, 'not archived'):
                CARGO.validate_supplement(archive, self.record)

    def test_complete_supplement(self):
        target = self.build()
        with tarfile.open(target) as archive:
            CARGO.validate_supplement(archive, self.record)
        self.assertNotIn(str(self.root), (self.destination / 'config.toml').read_text())

    def test_tampered_vendor(self):
        target = self.build()
        with tarfile.open(target, 'a') as archive:
            member = tarfile.TarInfo('niri/_cargo/vendor/crate-1.0.0/extra')
            member.size = 1
            archive.addfile(member, io.BytesIO(b'x'))
        with tarfile.open(target) as archive:
            with self.assertRaisesRegex(ValueError, 'inventory'):
                CARGO.validate_supplement(archive, self.record)

    def test_missing_dependency(self):
        with self.assertRaisesRegex(ValueError, 'missing locked'):
            CARGO.inspect_vendor(self.lock, lambda name: b'', [])

    def test_unpinned_registry_and_git_refused(self):
        with self.assertRaisesRegex(ValueError, 'checksum'):
            CARGO.locked_packages(self.lock.replace(b'a' * 64, b'SKIP'))
        git = self.lock.split(b'checksum=')[0].replace(
            b'registry+https://example.invalid/index', b'git+https://example.invalid/repo#tag')
        with self.assertRaisesRegex(ValueError, 'full commit'):
            CARGO.locked_packages(git)

    def test_glycin_preparation_replays_exact_commits(self):
        self.record = {'base': 'glycin', 'recipe_sha256': next(
            key for key, value in CARGO.RECIPES.items() if value[0] == 'glycin')}
        calls = []
        def run(*args):
            calls.append(args)
            if args[-1].endswith('^{commit}'):
                return args[-1].split('^')[0]
            if args[-1] == CARGO.GLYCIN_COMMITS[-1]:
                (self.tree / 'Cargo.lock').write_bytes(self.lock + b'# prepared\n')
            return ''
        proof = CARGO.prepare_tree(self.record, self.tree, self.root, run)
        self.assertEqual(proof['commits'], list(CARGO.GLYCIN_COMMITS))
        self.assertNotEqual(proof['original_lock_sha256'], proof['prepared_lock_sha256'])
        self.assertEqual(sum('cherry-pick' in call for call in calls), 2)

    def test_glycin_without_preparation_refused(self):
        self.record = {'base': 'glycin', 'recipe_sha256': next(
            key for key, value in CARGO.RECIPES.items() if value[0] == 'glycin')}
        with self.assertRaisesRegex(ValueError, 'reviewed preparation'):
            CARGO.create_supplement(self.record, self.tree, self.destination)

    def test_different_primary_commit_refused(self):
        self.record['git_sources'] = [{'source': 'git+https://example.invalid/source.git',
                                      'commit': 'a' * 40}]
        (self.tree / '.git').mkdir()
        with self.assertRaisesRegex(ValueError, 'verified primary'):
            CARGO.prepare_tree(self.record, self.tree, self.root, lambda *args: 'b' * 40)

    def test_preparation_lock_cannot_change_before_vendor(self):
        proof = {'original_lock_sha256': 'a' * 64,
                 'prepared_lock_sha256': 'b' * 64, 'commits': []}
        with self.assertRaisesRegex(ValueError, 'after reviewed preparation'):
            CARGO.create_supplement(self.record, self.tree, self.destination,
                                    preparation=proof)

    def test_vendor_may_not_update_lock(self):
        def mutate(*args, **kwargs):
            result = self.vendor(*args, **kwargs)
            (self.tree / 'Cargo.lock').write_bytes(b'changed')
            return result
        with patch.object(CARGO.subprocess, 'run', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'changed'):
                CARGO.create_supplement(self.record, self.tree, self.destination, {})


if __name__ == '__main__':
    unittest.main()
