#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exercise source signatures with local recipes and real makepkg, Git and GnuPG."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


collector = module('iso_sources', ROOT / 'packaging/mirror/iso_sources.py')
daemons = module('gnupg_daemons', ROOT / 'tests/gnupg-daemons.py')


@unittest.skipUnless(os.getuid() != 0 and all(shutil.which(command) for command in
                    ('makepkg', 'git', 'gpg', 'gpgconf', 'bsdtar')),
                    'requires non-root Arch user with makepkg, Git, GnuPG and bsdtar')
class Signatures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='is-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.home.mkdir()
        self.signing = self.root / 'signing'
        self.signing.mkdir(mode=0o700)
        environment = dict(HOME=str(self.home), GNUPGHOME=str(self.signing), LC_ALL='C',
                           TMPDIR=str(self.root), GIT_CONFIG_NOSYSTEM='1',
                           GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_COUNT='1',
                           GIT_CONFIG_KEY_0='protocol.file.allow', GIT_CONFIG_VALUE_0='always')
        self.environment = patch.dict(os.environ, environment)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        temporary_root = patch.object(tempfile, 'tempdir', str(self.root))
        temporary_root.start()
        self.addCleanup(temporary_root.stop)
        self.addCleanup(self.stop_keys)
        self.run_command('gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                         '--quick-generate-key', 'Source Fixture <source@example.org>',
                         'ed25519', 'sign', '0')
        listing = self.run_command('gpg', '--batch', '--with-colons', '--list-keys')
        self.fingerprint = next(line.split(':')[9] for line in listing.splitlines()
                                if line.startswith('fpr:'))
        self.repositories = self.root / 'repositories'
        self.repo = self.repositories / 'sample'
        self.repo.mkdir(parents=True)
        self.run_command('git', 'init', '-q', str(self.repo))

    def stop_keys(self):
        subprocess.run(['gpgconf', '--homedir', str(self.signing), '--kill', 'all'],
                       check=True, capture_output=True, timeout=15)
        self.assertEqual(daemons.check(self.root), [], 'temporary GnuPG daemon leaked')

    def run_command(self, *args, cwd=None):
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30)
        if result.returncode:
            self.fail(f'{args[0]} failed: {result.stdout}\n{result.stderr}')
        return result.stdout

    def commit(self, directory):
        self.run_command('git', '-C', str(directory), 'add', '.')
        self.run_command('git', '-C', str(directory), '-c', 'user.name=Fixture',
                         '-c', 'user.email=source@example.org', '-c', 'commit.gpgsign=false',
                         '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'Source fixture')

    def recipe(self, sources, sums, include_key=True, allowed_keys=None):
        if include_key:
            keys = self.repo / 'keys/pgp'
            keys.mkdir(parents=True, exist_ok=True)
            (keys / 'source.asc').write_text(self.run_command('gpg', '--batch', '--armor',
                                                            '--export', self.fingerprint))
        keys = ' '.join(allowed_keys or [self.fingerprint])
        text = ("pkgname=sample\npkgver=1\npkgrel=1\narch=('any')\nlicense=('GPL')\n"
                f"source=({sources})\nsha256sums=({sums})\n"
                f"validpgpkeys=({keys})\n")
        (self.repo / 'PKGBUILD').write_text(text)
        self.commit(self.repo)
        filename = 'sample-1-1-any.pkg.tar.zst'
        with tarfile.open(self.root / filename, 'w') as archive:
            for name, value in {
                '.PKGINFO': 'pkgname = sample\npkgbase = sample\npkgver = 1-1\nlicense = GPL\n',
                '.BUILDINFO': 'pkgbuild_sha256sum = ' + hashlib.sha256(text.encode()).hexdigest() + '\n',
            }.items():
                data = value.encode()
                item = tarfile.TarInfo(name)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))
        self.closure = self.root / 'closure.txt'
        self.closure.write_text(filename + '\n')
        return filename

    def detached(self, *, invalid=False, include_key=True):
        payload = self.repo / 'payload.txt'
        payload.write_text('signed source\n')
        self.run_command('gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                         '--detach-sign', str(payload))
        if invalid:
            payload.write_text('changed after signing\n')
        checksum = hashlib.sha256(payload.read_bytes()).hexdigest()
        return self.recipe("'payload.txt' 'payload.txt.sig'", f"'{checksum}' 'SKIP'", include_key)

    def collect(self):
        try:
            return collector.collect(self.closure, self.root, self.root / 'output',
                                     repositories=self.repositories)
        except subprocess.CalledProcessError as error:
            error.add_note(f'{error.stdout}\n{error.stderr}')
            raise

    def test_valid_detached_signature_imports_recipe_key(self):
        filename = self.detached()
        manifest = self.collect()
        self.assertGreater(manifest['packages'][filename]['size'], 0)
        self.assertFalse((self.home / '.gnupg').exists())

    def test_changed_payload_refused_despite_matching_checksum(self):
        self.detached(invalid=True)
        with self.assertRaises(subprocess.CalledProcessError) as failure:
            self.collect()
        self.assertIn('PGP', failure.exception.stdout + failure.exception.stderr)
        self.assertFalse((self.root / 'output/ARCH-SOURCES.json').exists())

    def test_missing_recipe_key_refused_despite_inherited_keyring(self):
        self.detached(include_key=False)
        # GNUPGHOME deliberately contains the signing key. The collector must isolate it.
        original = collector.run
        def offline(*args, **kwargs):
            if args[0] == 'curl':
                raise subprocess.CalledProcessError(22, args)
            return original(*args, **kwargs)
        with patch.object(collector, 'run', offline), self.assertRaisesRegex(ValueError, 'exact source key'):
            self.collect()
        self.assertFalse((self.root / 'output/ARCH-SOURCES.json').exists())

    def test_missing_recipe_key_fetched_by_full_fingerprint(self):
        filename = self.detached(include_key=False)
        public = self.run_command('gpg', '--batch', '--armor', '--export', self.fingerprint)
        original = collector.run
        def retrieve(*args, **kwargs):
            if args[0] == 'curl':
                self.assertTrue(args[-1].endswith('/' + self.fingerprint))
                Path(args[args.index('--output') + 1]).write_text(public)
                return ''
            return original(*args, **kwargs)
        with patch.object(collector, 'run', retrieve):
            record = self.collect()['packages'][filename]
        self.assertEqual(record['verification_keys'][0]['fingerprint'], self.fingerprint)
        self.assertFalse((self.home / '.gnupg').exists())

    def test_recipe_primary_and_subkey_accept_real_subkey_signature(self):
        self.run_command('gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                         '--quick-add-key', self.fingerprint, 'ed25519', 'sign', '0')
        listing = self.run_command('gpg', '--batch', '--with-colons', '--with-subkey-fingerprint',
                                   '--list-keys', self.fingerprint)
        subkey = [line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:')][-1]
        payload = self.repo / 'payload.txt'
        payload.write_text('signed by the recipe subkey\n')
        self.run_command('gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                         '--local-user', subkey + '!', '--detach-sign', str(payload))
        checksum = hashlib.sha256(payload.read_bytes()).hexdigest()
        filename = self.recipe("'payload.txt' 'payload.txt.sig'", f"'{checksum}' 'SKIP'",
                               include_key=False, allowed_keys=[self.fingerprint, subkey])
        public = self.run_command('gpg', '--batch', '--armor', '--export', self.fingerprint)
        original = collector.run
        signers = []
        def retrieve(*args, **kwargs):
            if args[0] == 'curl':
                self.assertIn(args[-1].rsplit('/', 1)[-1], (self.fingerprint, subkey))
                Path(args[args.index('--output') + 1]).write_text(public)
                return ''
            if args[0] == 'makepkg' and '--allsource' in args:
                status = original('gpg', '--batch', '--no-autostart', '--status-fd', '1',
                                  '--verify', str(payload) + '.sig', str(payload), env=kwargs['env'])
                signers.extend(line.split()[2] for line in status.splitlines()
                               if line.startswith('[GNUPG:] VALIDSIG '))
            return original(*args, **kwargs)
        with patch.object(collector, 'run', retrieve):
            record = self.collect()['packages'][filename]
        self.assertEqual(signers, [subkey])
        self.assertGreater(record['size'], 0)

    def test_bundled_primary_missing_signing_subkey_is_refreshed(self):
        filename = self.detached()
        self.run_command('gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                         '--quick-add-key', self.fingerprint, 'ed25519', 'sign', '0')
        listing = self.run_command('gpg', '--batch', '--with-colons', '--with-subkey-fingerprint',
                                   '--list-keys', self.fingerprint)
        subkey = [line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:')][-1]
        self.run_command('gpg', '--batch', '--yes', '--pinentry-mode', 'loopback', '--passphrase', '',
                         '--local-user', subkey + '!', '--detach-sign', str(self.repo / 'payload.txt'))
        # The collector selects the PKGBUILD-changing commit, so bind this fixture
        # binary to the new signature's packaging revision too.
        with (self.repo / 'PKGBUILD').open('a') as stream:
            stream.write('# Release signed with the new subkey.\n')
        self.commit(self.repo)
        with tarfile.open(self.root / filename) as archive:
            members = {m.name: archive.extractfile(m).read() for m in archive.getmembers()}
        members['.BUILDINFO'] = ('pkgbuild_sha256sum = ' +
                                hashlib.sha256((self.repo / 'PKGBUILD').read_bytes()).hexdigest() + '\n').encode()
        with tarfile.open(self.root / filename, 'w') as archive:
            for name, contents in members.items():
                member = tarfile.TarInfo(name)
                member.size = len(contents)
                archive.addfile(member, io.BytesIO(contents))
        public = self.run_command('gpg', '--batch', '--armor', '--export', self.fingerprint)
        original = collector.run
        queries = []
        def retrieve(*args, **kwargs):
            if args[0] == 'curl':
                queries.append(args[-1])
                self.assertTrue(args[-1].endswith('/' + self.fingerprint))
                Path(args[args.index('--output') + 1]).write_text(public)
                return ''
            return original(*args, **kwargs)
        with patch.object(collector, 'run', retrieve):
            record = self.collect()['packages'][filename]
        self.assertEqual(len(queries), 1)
        self.assertEqual(record['verification_keys'][0]['fingerprint'], self.fingerprint)
        self.assertFalse((self.home / '.gnupg').exists())

    def multiple_signatures(self, allowed_last):
        payload = self.repo / 'payload.txt'
        payload.write_text('source with two release signatures\n')
        self.run_command('gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                         '--quick-generate-key', 'Second Release <second@example.org>',
                         'ed25519', 'sign', '0')
        listing = self.run_command('gpg', '--batch', '--with-colons', '--list-keys',
                                   'second@example.org')
        other = next(line.split(':')[9] for line in listing.splitlines()
                     if line.startswith('fpr:'))
        order = [other, self.fingerprint] if allowed_last else [self.fingerprint, other]
        signatures = []
        for number, signer in enumerate(order):
            signature = self.root / f'signature-{number}'
            self.run_command('gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase',
                             '', '--local-user', signer, '--output', str(signature),
                             '--detach-sign', str(payload))
            signatures.append(signature.read_bytes())
        (self.repo / 'payload.txt.sig').write_bytes(b''.join(signatures))
        keys = self.repo / 'keys/pgp'
        keys.mkdir(parents=True)
        (keys / 'second.asc').write_text(self.run_command('gpg', '--batch', '--armor',
                                                         '--export', other))
        return self.recipe("'payload.txt' 'payload.txt.sig'",
                           f"'{hashlib.sha256(payload.read_bytes()).hexdigest()}' 'SKIP'")

    def test_last_detached_signer_outside_recipe_is_refused(self):
        # A later appended signature can change makepkg's selected primary signer.
        self.multiple_signatures(allowed_last=False)
        with self.assertRaises(subprocess.CalledProcessError) as failure:
            self.collect()
        self.assertIn('invalid public key', failure.exception.stdout + failure.exception.stderr)
        self.assertFalse((self.root / 'output/ARCH-SOURCES.json').exists())

    def test_last_detached_signer_inside_recipe_is_accepted(self):
        filename = self.multiple_signatures(allowed_last=True)
        self.assertGreater(self.collect()['packages'][filename]['size'], 0)

    def test_signing_subkey_uses_allowed_primary_fingerprint(self):
        self.run_command('gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                         '--quick-add-key', self.fingerprint, 'ed25519', 'sign', '0')
        listing = self.run_command('gpg', '--batch', '--with-colons', '--list-keys',
                                   self.fingerprint)
        subkey = [line.split(':')[9] for line in listing.splitlines()
                  if line.startswith('fpr:')][-1]
        payload = self.repo / 'payload.txt'
        payload.write_text('source signed with a release subkey\n')
        self.run_command('gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                         '--local-user', subkey + '!', '--detach-sign', str(payload))
        filename = self.recipe("'payload.txt' 'payload.txt.sig'",
                               f"'{hashlib.sha256(payload.read_bytes()).hexdigest()}' 'SKIP'")
        self.assertGreater(self.collect()['packages'][filename]['size'], 0)

    def test_signed_git_tag_imports_recipe_key_and_records_commit(self):
        upstream = self.root / 'upstream'
        upstream.mkdir()
        self.run_command('git', 'init', '-q', str(upstream))
        (upstream / 'source.txt').write_text('source\n')
        self.commit(upstream)
        self.run_command('git', '-C', str(upstream), '-c', 'user.name=Fixture',
                         '-c', 'user.email=source@example.org', 'tag', '-s', '-u',
                         self.fingerprint, '-m', 'Release fixture', 'v1')
        archived = subprocess.check_output(['git', '-c', 'core.abbrev=no', '-C', str(upstream),
                                            'archive', '--format', 'tar', 'v1'])
        checksum = hashlib.sha256(archived).hexdigest()
        filename = self.recipe(f"'upstream::git+{upstream.as_uri()}#tag=v1?signed'", f"'{checksum}'")
        manifest = self.collect()
        pins = manifest['packages'][filename]['git_sources']
        self.assertEqual(len(pins), 1)
        self.assertEqual(pins[0]['reference'], 'v1')
        self.assertEqual(pins[0]['commit'], self.run_command(
            'git', '-C', str(upstream), 'rev-parse', 'HEAD').strip())


if __name__ == '__main__':
    unittest.main()
