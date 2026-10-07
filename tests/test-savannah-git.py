#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline Savannah mirror, archive, identity and signature fixtures."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('savannah_git', Path(__file__).resolve().parents[1] /
                                             'packaging/mirror/savannah_git.py')
routes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(routes)


def run(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs).stdout


class SavannahGit(unittest.TestCase):
    def test_origin_allowlist_and_unsigned_refusal(self):
        self.assertIsNone(routes.parse('git+https://example.org/project#commit=' + 'a' * 40))
        self.assertIsNone(routes.official_mirror('https://git.savannah.nongnu.org/git/coreutils.git'))
        self.assertIsNone(routes.official_mirror('https://git.savannah.gnu.org/other/coreutils.git'))
        with self.assertRaisesRegex(ValueError, 'unsigned'):
            routes.parse('git+https://git.savannah.gnu.org/git/example.git#tag=v1')
        with self.assertRaises(ValueError):
            routes.parse('../bad::git+https://git.savannah.gnu.org/git/example.git#commit=' + 'a' * 40)

    def test_cross_host_archive_alias_preserves_recipe_origin(self):
        origin = 'https://git.savannah.nongnu.org/git/attr.git'
        alias = 'https://git.savannah.gnu.org/git/attr.git'
        self.assertIn(alias, routes.origin_aliases(origin))
        def lookup(candidate, kind, reference, run):
            if candidate != alias:
                raise ValueError('absent')
            return {'origin': alias, 'snapshot': 'a' * 40, 'commit': 'b' * 40}
        with patch.object(routes, 'resolve_origin', side_effect=lookup):
            evidence = routes.resolve(origin, 'commit', 'b' * 40, run)
        self.assertEqual(evidence['origin'], origin)
        self.assertEqual(evidence['archive_origin'], alias)

    def test_signature_requires_exact_primary(self):
        primary = 'A' * 40
        status = '[GNUPG:] VALIDSIG ' + 'B' * 40 + ' 2026-01-01 1 0 4 0 1 10 00 ' + primary
        self.assertEqual(routes.signature_primary(status, {primary}), primary)
        for bad in (primary[-16:], 'C' * 40):
            with self.assertRaises(ValueError):
                routes.signature_primary(status, {bad})
        for token in ('BADSIG', 'EXPSIG', 'REVKEYSIG', 'ERRSIG'):
            with self.assertRaises(ValueError):
                routes.signature_primary(status + '\n[GNUPG:] ' + token, {primary})

    def test_snapshot_tag_binding(self):
        origin = 'https://git.savannah.gnu.org/git/example.git'
        snapshot, tag, commit = 'a' * 40, 'b' * 40, 'c' * 40
        responses = [{'origin': origin, 'status': 'full', 'snapshot': snapshot},
                     {'id': snapshot, 'branches': {'refs/tags/v1': {'target': tag, 'target_type': 'release'}}},
                     {'id': tag, 'target': commit, 'target_type': 'revision'}]
        with patch.object(routes, 'request_json', side_effect=responses), patch.object(routes, 'release_manifest', return_value=b'exact tag'):
            result = routes.resolve_origin(origin, 'tag', 'v1', run)
        self.assertEqual(result['commit'], commit)
        self.assertEqual(result['tag_object'], tag)
        responses[1]['id'] = 'd' * 40
        with patch.object(routes, 'request_json', side_effect=responses), self.assertRaisesRegex(ValueError, 'snapshot identifier'):
            routes.resolve_origin(origin, 'tag', 'v1', run)

    def repository(self, root):
        repo = root / 'upstream'
        run('git', 'init', '--quiet', str(repo))
        run('git', '-C', str(repo), 'config', 'user.name', 'Fixture')
        run('git', '-C', str(repo), 'config', 'user.email', 'fixture@example.invalid')
        (repo / 'file').write_text('complete source\n')
        run('git', '-C', str(repo), 'add', 'file')
        run('git', '-C', str(repo), 'commit', '--quiet', '-m', 'Source')
        return repo, run('git', '-C', str(repo), 'rev-parse', 'HEAD').strip()

    def test_official_commit_mirror_contains_full_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, commit = self.repository(root)
            downloads = root / 'downloads'
            downloads.mkdir()
            source = 'git+https://git.savannah.gnu.org/git/coreutils.git#commit=' + commit
            with patch.object(routes, 'official_mirror', return_value={'mirror': str(repo), 'authority': 'fixture'}):
                evidence = routes.fetch(source, downloads, None, [], run)
            self.assertEqual(evidence['commit'], commit)
            self.assertEqual(evidence['kind'], 'savannah-official-git')
            self.assertEqual(run('git', '-C', str(downloads / 'coreutils'), 'show', commit + ':file'), 'complete source\n')
            self.assertEqual(json.loads((downloads / 'coreutils/emaki-source-route.json').read_text()), evidence)
            with self.assertRaises(ValueError):
                routes.verify(downloads / 'coreutils', 'commit', commit, None, [], run, {'commit': 'a' * 40})

    def test_exact_recipe_origin_is_tried_before_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, commit = self.repository(root)
            downloads = root / 'downloads'
            downloads.mkdir()
            origin = 'https://git.savannah.gnu.org/git/example.git'
            source = 'git+' + origin + '#commit=' + commit
            def origin_run(*args, **kwargs):
                if args[0] == 'git' and 'fetch' in args:
                    self.assertIn(origin, args)
                    args = tuple(str(repo) if arg == origin else arg for arg in args)
                return run(*args, **kwargs)
            with patch.object(routes, 'resolve') as resolve:
                evidence = routes.fetch(source, downloads, None, [], origin_run)
            resolve.assert_not_called()
            self.assertEqual(evidence['kind'], 'savannah-origin-git')
            self.assertEqual(evidence['origin'], origin)
            self.assertEqual(evidence['commit'], commit)
            metadata = root / '.SRCINFO'
            metadata.write_text('source = ' + source + '\n')
            archive = root / 'source.tar'
            with tarfile.open(archive, 'w') as bundle:
                bundle.add(downloads / 'example', arcname='example/example')
                bundle.add(metadata, arcname='example/.SRCINFO')
            record = {'base': 'example', 'source_routes': [evidence],
                      'git_sources': [{'source': source, 'commit': commit}]}
            routes.validate_routes(archive, record)
            evidence['origin'] = 'https://example.invalid/changed.git'
            with self.assertRaisesRegex(ValueError, 'identity differs'):
                routes.validate_routes(archive, record)

    def test_vault_commit_bundle_and_no_network_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, commit = self.repository(root)
            bare = root / 'bare'
            run('git', 'clone', '--quiet', '--bare', str(repo), str(bare))
            archive = root / 'fixture.tar'
            with tarfile.open(archive, 'w') as bundle:
                bundle.add(bare, arcname='swh.git')
            downloads = root / 'downloads'
            downloads.mkdir()
            source = 'git+https://git.savannah.nongnu.org/git/example.git#commit=' + commit
            def fixture_run(*args, **kwargs):
                if args[0] == 'git' and 'fetch' in args:
                    raise subprocess.CalledProcessError(1, args)
                if args[0] == 'curl':
                    self.assertNotIn('--request', args)
                    self.assertNotIn('POST', args)
                    Path(args[args.index('--output') + 1]).write_bytes(archive.read_bytes())
                    return 'https://archive.example.invalid/served-vault.tar'
                return run(*args, **kwargs)
            resolved = {'origin': source[4:].split('#')[0], 'snapshot': 'a' * 40, 'commit': commit}
            with patch.object(routes, 'resolve', return_value=resolved), patch.object(routes, 'request_json', return_value={'status': 'done'}):
                evidence = routes.fetch(source, downloads, None, [], fixture_run)
            self.assertEqual(evidence['kind'], 'savannah-software-heritage')
            self.assertEqual(evidence['requested_url'], evidence['vault_url'] + 'raw/')
            self.assertEqual(evidence['effective_url'], 'https://archive.example.invalid/served-vault.tar')
            self.assertEqual(evidence['commit'], commit)
            self.assertEqual(run('git', '-C', str(downloads / 'example'), 'show', 'HEAD:file'), 'complete source\n')
            with patch.object(routes, 'resolve', return_value=resolved), patch.object(routes, 'request_json', return_value={'status': 'new'}), self.assertRaisesRegex(ValueError, 'read-only'):
                routes.fetch(source, downloads, None, [], fixture_run)

    def test_signed_mirror_tag_verifies_recipe_primary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, commit = self.repository(root)
            keyring = root / 'gnupg'
            keyring.mkdir(mode=0o700)
            env = dict(os.environ, GNUPGHOME=str(keyring))
            run('gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                '--quick-generate-key', 'Fixture <fixture@example.invalid>', 'ed25519', 'sign', '0', env=env)
            keys = run('gpg', '--batch', '--with-colons', '--list-keys', env=env)
            primary = next(line.split(':')[9] for line in keys.splitlines() if line.startswith('fpr:'))
            run('git', '-C', str(repo), '-c', 'user.signingkey=' + primary,
                'tag', '-s', 'v1', '-m', 'Release', env=env)
            downloads = root / 'downloads'
            downloads.mkdir()
            source = 'git+https://git.savannah.gnu.org/git/coreutils.git?signed#tag=v1'
            with patch.object(routes, 'official_mirror', return_value={'mirror': str(repo), 'authority': 'fixture'}):
                evidence = routes.fetch(source, downloads, env, [primary], run)
            self.assertEqual(evidence['signer'], primary)
            self.assertEqual(evidence['commit'], commit)
            with self.assertRaises(ValueError):
                routes.verify(downloads / 'coreutils', 'tag', 'v1', env, ['F' * 40], run)
            bare = root / 'bare'
            run('git', 'clone', '--quiet', '--bare', str(repo), str(bare))
            archive = root / 'vault.tar'
            with tarfile.open(archive, 'w') as bundle:
                bundle.add(bare, arcname='swh.git')
            archived_downloads = root / 'archived-downloads'
            archived_downloads.mkdir()
            archived = {'origin': source[4:].split('?')[0], 'snapshot': 'a' * 40,
                        'commit': commit, 'tag_object': evidence['tag_object'],
                        'tag_manifest': run('git', '-C', str(repo), 'cat-file', 'tag', 'v1')}
            def fixture_run(*args, **kwargs):
                if args[0] == 'git' and 'fetch' in args:
                    raise subprocess.CalledProcessError(1, args)
                if args[0] == 'curl':
                    Path(args[args.index('--output') + 1]).write_bytes(archive.read_bytes())
                    return 'https://archive.example.invalid/served-vault.tar'
                return run(*args, **kwargs)
            with patch.object(routes, 'official_mirror', return_value=None), patch.object(routes, 'resolve', return_value=archived), patch.object(routes, 'request_json', return_value={'status': 'done'}):
                restored = routes.fetch(source, archived_downloads, env, [primary], fixture_run)
            self.assertEqual(restored['tag_object'], evidence['tag_object'])
            self.assertEqual(restored['signer'], primary)
            self.assertIn('swh:1:rev:' + commit, restored['vault_url'])
            packaged = root / 'source.tar'
            with tarfile.open(packaged, 'w') as bundle:
                bundle.add(archived_downloads / 'coreutils', arcname='coreutils/coreutils')
                srcinfo = ('source = ' + source + '\nvalidpgpkeys = ' + primary + '\n').encode()
                metadata = tarfile.TarInfo('coreutils/.SRCINFO')
                metadata.size = len(srcinfo)
                bundle.addfile(metadata, io.BytesIO(srcinfo))
            record = {'base': 'coreutils', 'git_sources': [{'source': source, 'commit': commit}],
                      'source_routes': [restored]}
            routes.validate_routes(packaged, record, run)
            restored['signer'] = 'F' * 40
            with self.assertRaisesRegex(ValueError, 'validpgpkeys'):
                routes.validate_routes(packaged, record, run)
            restored['signer'] = primary
            restored['source'] = source.replace('coreutils.git', 'other.git')
            restored['origin'] = restored['source'][4:].split('?')[0]
            with self.assertRaisesRegex(ValueError, 'source differs'):
                routes.validate_routes(packaged, record, run)
            restored['source'] = source
            restored['origin'] = source[4:].split('?')[0]
            restored['tag_object'] = 'b' * 40
            with self.assertRaisesRegex(ValueError, 'tag differs'):
                routes.validate_routes(packaged, record, run)
            run('gpgconf', '--kill', 'gpg-agent', env=env)

    def test_signed_tag_name_binding_collection_and_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, commit = self.repository(root)
            run('git', '-C', str(repo), 'tag', '-a', 'v1', '-m', 'Release')
            tag = run('git', '-C', str(repo), 'rev-parse', 'v1').strip()
            primary = 'A' * 40
            status = '[GNUPG:] VALIDSIG ' + primary + ' 2026-01-01 1 0 4 0 1 10 00 ' + primary
            subprocess_run = subprocess.run
            def signature_fixture(args, **kwargs):
                if 'verify-tag' in args:
                    return subprocess.CompletedProcess(args, 0, '', status)
                return subprocess_run(args, **kwargs)
            for reference, valid in [('v2', False), (tag[:7], True), ('d' * 7, False)]:
                with self.subTest(reference=reference):
                    run('git', '-C', str(repo), 'update-ref', 'refs/tags/' + reference, tag)
                    with self.subTest(stage='collection'), patch.object(routes.subprocess, 'run', side_effect=signature_fixture):
                        if valid:
                            evidence = routes.verify(repo, 'tag', reference, {'GNUPGHOME': str(root)}, [primary], run)
                            self.assertEqual(evidence['tag_object'], tag)
                        else:
                            with self.assertRaisesRegex(ValueError, 'tag name'):
                                routes.verify(repo, 'tag', reference, {'GNUPGHOME': str(root)}, [primary], run)
                    source = 'git+https://git.savannah.gnu.org/git/coreutils.git?signed#tag=' + reference
                    route = dict(source=source, origin=source[4:].split('?')[0], reference=reference,
                                 reference_kind='tag', commit=commit, tag_object=tag, signer=primary,
                                 kind='savannah-official-git', **routes.official_mirror(source[4:].split('?')[0]))
                    bare = root / ('bare-' + reference)
                    run('git', 'clone', '--quiet', '--bare', str(repo), str(bare))
                    archive = root / 'source.tar'
                    with tarfile.open(archive, 'w') as bundle:
                        bundle.add(bare, arcname='coreutils/coreutils')
                        data = ('source = ' + source + '\nvalidpgpkeys = ' + primary + '\n').encode()
                        metadata = tarfile.TarInfo('coreutils/.SRCINFO')
                        metadata.size = len(data)
                        bundle.addfile(metadata, io.BytesIO(data))
                    record = {'base': 'coreutils', 'source_routes': [route],
                              'git_sources': [{'source': source, 'commit': commit}]}
                    if valid:
                        routes.validate_routes(archive, record, run)
                    else:
                        with self.assertRaisesRegex(ValueError, 'tag name'):
                            routes.validate_routes(archive, record, run)

    def test_expired_key_valid_signature_records_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, _ = self.repository(root)
            keyring = root / 'gnupg'
            keyring.mkdir(mode=0o700)
            env = dict(os.environ, GNUPGHOME=str(keyring))
            try:
                run('gpg', '--faked-system-time', '1577836800', '--batch', '--pinentry-mode',
                    'loopback', '--passphrase', '', '--quick-generate-key',
                    'Fixture <fixture@example.invalid>', 'ed25519', 'sign', '1d', env=env)
                keys = run('gpg', '--batch', '--with-colons', '--list-keys', env=env)
                primary = next(line.split(':')[9] for line in keys.splitlines() if line.startswith('fpr:'))
                signer = root / 'sign-at-release'
                signer.write_text('#!/bin/sh\nexec gpg --faked-system-time 1577836801 "$@"\n')
                signer.chmod(0o700)
                run('git', '-C', str(repo), '-c', 'user.signingkey=' + primary,
                    '-c', 'gpg.program=' + str(signer), 'tag', '-s', 'v1', '-m', 'Release', env=env)
                evidence = routes.verify(repo, 'tag', 'v1', env, [primary], run)
                self.assertEqual(evidence['signer'], primary)
                self.assertIn('expired', ' '.join(evidence['warnings']).lower())
                self.assertIn('EXPKEYSIG', ' '.join(evidence['warnings']))
            finally:
                run('gpgconf', '--kill', 'gpg-agent', env=env)

    def test_expired_key_nonzero_verifier_still_requires_valid_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo, _ = self.repository(root)
            run('git', '-C', str(repo), 'tag', '-a', 'v1', '-m', 'Release')
            primary = 'A' * 40
            valid = '[GNUPG:] VALIDSIG ' + primary + ' 2026-01-01 1 0 4 0 1 10 00 ' + primary
            subprocess_run = subprocess.run
            for suffix, accepted in [(valid, True), ('', False), (valid + '\n[GNUPG:] BADSIG broken', False)]:
                with self.subTest(status=suffix):
                    def signature_fixture(args, **kwargs):
                        if 'verify-tag' in args:
                            return subprocess.CompletedProcess(args, 1, '', '[GNUPG:] EXPKEYSIG expired\n' + suffix)
                        return subprocess_run(args, **kwargs)
                    with patch.object(routes.subprocess, 'run', side_effect=signature_fixture):
                        if accepted:
                            evidence = routes.verify(repo, 'tag', 'v1', {'GNUPGHOME': str(root)}, [primary], run)
                            self.assertEqual(evidence['signer'], primary)
                            self.assertIn('EXPKEYSIG', evidence['warnings'][0])
                        else:
                            with self.assertRaises(ValueError):
                                routes.verify(repo, 'tag', 'v1', {'GNUPGHOME': str(root)}, [primary], run)

    def test_release_serialization_requires_exact_tag_hash(self):
        import hashlib
        release = {'target': 'a' * 40, 'name': 'v1', 'author': {'fullname': 'Fixture <fixture@example.invalid>'},
                   'date': '2024-01-01T00:00:00+00:00', 'message': 'Release\n'}
        data = ('object ' + 'a' * 40 + '\ntype commit\ntag v1\ntagger Fixture <fixture@example.invalid> 1704067200 +0000\n\nRelease\n').encode()
        release['id'] = hashlib.sha1(b'tag ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        self.assertEqual(routes.release_manifest(release), data)
        release['message'] = 'Changed\n'
        with self.assertRaisesRegex(ValueError, 'exact Git object'):
            routes.release_manifest(release)

    def test_opted_in_vault_cooking_and_bounded_poll(self):
        vault = routes.API + 'vault/git-bare/swh:1:rev:' + 'a' * 40 + '/'
        env = {'EMAKI_SWH_VAULT_COOK': '1'}
        with patch.object(routes, 'request_json', side_effect=[{'status': 'pending'}, {'status': 'new'}, {'status': 'done'}]) as request, patch.object(routes.time, 'sleep') as sleep:
            routes.ready_vault(vault, env, run)
        self.assertEqual([call.kwargs.get('method', 'GET') for call in request.call_args_list], ['GET', 'POST', 'GET'])
        self.assertEqual(request.call_args_list[1].args[0], vault)
        sleep.assert_called_once_with(10)
        with patch.object(routes, 'request_json', side_effect=[{'status': 'new'}, {'status': 'failed'}]), self.assertRaisesRegex(ValueError, 'cooking failed'):
            routes.ready_vault(vault, env, run)
        with patch.object(routes, 'request_json', return_value={'status': 'pending'}), patch.object(routes.time, 'monotonic', side_effect=[0, routes.VAULT_COOK_TIMEOUT + 1]), self.assertRaisesRegex(ValueError, 'timed out'):
            routes.ready_vault(vault, env, run)

    def test_historical_source_skip_requires_exact_binary(self):
        import sys
        sys.path.insert(0, str(Path(routes.__file__).parent))
        import historical_sources
        evidence = next(item for item in historical_sources.REVIEWED if item['base'] == 'screen')
        record = {key: evidence[key] for key in ('base', 'version', 'recipe_sha256', 'binary_sha256')}
        metadata = 'source = ' + evidence['source']
        with patch.object(routes, 'fetch') as fetch:
            self.assertEqual(routes.prepare(metadata, Path('/unused'), None, run, record), [])
            fetch.assert_not_called()
        record['binary_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'unsigned'):
            routes.prepare(metadata, Path('/unused'), None, run, record)

    def test_vault_cooking_can_finish_after_old_limit(self):
        vault = routes.API + 'vault/git-bare/swh:1:rev:' + 'a' * 40 + '/'
        with patch.object(routes, 'request_json', side_effect=[{'status': 'pending'}, {'status': 'pending'}, {'status': 'done'}]), patch.object(routes.time, 'monotonic', side_effect=[0, 601, 611]), patch.object(routes.time, 'sleep'):
            routes.ready_vault(vault, {'EMAKI_SWH_VAULT_COOK': '1'}, run)
        self.assertEqual(routes.VAULT_COOK_TIMEOUT, 1800)

    def test_read_only_vault_never_posts(self):
        vault = routes.API + 'vault/git-bare/swh:1:rev:' + 'a' * 40 + '/'
        for env in (None, {}, {'EMAKI_SWH_VAULT_COOK': '0'}):
            with self.subTest(env=env), patch.object(routes, 'request_json', return_value={'status': 'pending'}) as request, self.assertRaisesRegex(ValueError, 'read-only'):
                routes.ready_vault(vault, env, run)
            request.assert_called_once_with(vault, run)
        with patch.object(routes, 'request_json', return_value={'status': 'done'}) as request:
            routes.ready_vault(vault, {'EMAKI_SWH_VAULT_COOK': '1'}, run)
        request.assert_called_once_with(vault, run)
        with patch.object(routes, 'request_json') as request, self.assertRaises(ValueError):
            routes.ready_vault('https://example.invalid/vault/', {'EMAKI_SWH_VAULT_COOK': '1'}, run)
        request.assert_not_called()

    def test_unsafe_vault_refused(self):
        for name, typecode in [('repo/../outside', tarfile.REGTYPE), ('repo/objects/info/alternates', tarfile.REGTYPE), ('repo/link', tarfile.SYMTYPE)]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / 'bad.tar'
                with tarfile.open(archive, 'w') as bundle:
                    member = tarfile.TarInfo(name)
                    member.type = typecode
                    bundle.addfile(member, io.BytesIO(b''))
                destination = root / 'destination'
                destination.mkdir()
                with self.assertRaises(ValueError):
                    routes.extract_repository(archive, destination)


if __name__ == '__main__':
    unittest.main()
