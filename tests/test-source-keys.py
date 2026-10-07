#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline public key identity and retrieval boundary checks."""
import importlib.util
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('source_keys', Path(__file__).resolve().parents[1] /
                                             'packaging/mirror/source_keys.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
KEY = 'A' * 40
OTHER = 'B' * 40


def listing(key):
    return 'pub:::::::::\nfpr:::::::::' + key + ':\n'


class Keys(unittest.TestCase):
    def test_primary_not_subkey(self):
        self.assertEqual(m.primary_fingerprints(listing(KEY) + 'sub:::::::::\nfpr:::::::::' + OTHER + ':\n'), {KEY})
        with self.assertRaises(ValueError):
            m.primary_fingerprints('sec:::::::::\n')

    def run_prepare(self, keys, present='', downloaded=None, *, original=None, refresh=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        home = root / 'ring'
        home.mkdir(mode=0o700)
        recipe = root / 'recipe'
        self.recipe = recipe
        if original is not None:
            keyfile = recipe / 'keys/pgp' / (KEY + '.asc')
            keyfile.parent.mkdir(parents=True)
            keyfile.write_bytes(original)
        calls = []
        def run(*args, **kwargs):
            calls.append(args)
            if args[0] == 'curl':
                Path(args[args.index('--output') + 1]).write_bytes(b'public key fixture')
                return 'https://keys.example.invalid/served.asc'
            self.assertEqual(kwargs['env']['GNUPGHOME'], str(home))
            if '--list-keys' in args:
                return (downloaded or listing(KEY)) if any('--import' in c and 'show-only' not in c for c in calls) else present
            if 'show-only' in args:
                return downloaded or listing(KEY)
            return ''
        return m.prepare(keys, recipe, {'GNUPGHOME': str(home)}, run, refresh=refresh), calls

    def test_subkey_membership_is_exact(self):
        downloaded = listing(KEY) + 'sub:::::::::\nfpr:::::::::' + OTHER + ':\n'
        self.assertEqual(m.public_fingerprints(downloaded), {KEY: KEY, OTHER: KEY})
        result, calls = self.run_prepare([OTHER], downloaded=downloaded)
        self.assertEqual(result[0]['fingerprint'], OTHER)
        self.assertTrue(all('--with-subkey-fingerprint' in call for call in calls if call[0] == 'gpg'))
        with self.assertRaisesRegex(ValueError, 'subkey is absent'):
            self.run_prepare(['C' * 40], downloaded=downloaded)

    def test_present_subkey_never_downloaded(self):
        present = listing(KEY) + 'sub:::::::::\nfpr:::::::::' + OTHER + ':\n'
        result, calls = self.run_prepare([OTHER], present=present)
        self.assertEqual(result, [])
        self.assertFalse(any(call[0] == 'curl' for call in calls))

    def test_subkey_cannot_authorize_an_extra_primary(self):
        downloaded = listing(KEY) + 'sub:::::::::\nfpr:::::::::' + OTHER + ':\n' + listing('C' * 40)
        with self.assertRaisesRegex(ValueError, 'primary fingerprint differs'):
            self.run_prepare([OTHER], downloaded=downloaded)

    def test_malformed_key_hierarchy_refused(self):
        for text in ('sub:::::::::\nfpr:::::::::' + OTHER + ':\n',
                     listing(KEY) + 'sub:::::::::\n',
                     'pub:::::::::\n' + listing(OTHER),
                     listing(KEY) + 'sub:::::::::\nfpr:::::::::short:\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                m.public_fingerprints(text)

    def test_refresh_preserves_recipe_key_bytes(self):
        original = b'original recipe key\n'
        result, _ = self.run_prepare([KEY], listing(KEY), original=original, refresh=True)
        self.assertEqual((self.recipe / 'keys/pgp' / (KEY + '.asc')).read_bytes(), original)
        self.assertEqual(result[0]['file'], KEY + '.refreshed.asc')
        self.assertEqual(result[0]['recipe_key_sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual((self.recipe / 'keys/pgp' / result[0]['file']).read_bytes(), b'public key fixture')

    def test_key_serving_url_is_recorded(self):
        result, calls = self.run_prepare([KEY])
        self.assertEqual(result[0]['effective_url'], 'https://keys.example.invalid/served.asc')
        curl = next(call for call in calls if call[0] == 'curl')
        self.assertEqual(curl[curl.index('--write-out') + 1], '%{url_effective}')

    def test_validate_retains_original_and_refreshed_key_identity(self):
        original = b'original recipe key\n'
        evidence, _ = self.run_prepare([KEY], listing(KEY), original=original, refresh=True)
        for changed in (False, True):
            with self.subTest(changed=changed):
                stream = io.BytesIO()
                with tarfile.open(fileobj=stream, mode='w') as archive:
                    for path in (self.recipe / 'keys/pgp').iterdir():
                        data = b'changed' if changed and path.name == KEY + '.asc' else path.read_bytes()
                        member = tarfile.TarInfo('fixture/keys/pgp/' + path.name)
                        member.size = len(data)
                        archive.addfile(member, io.BytesIO(data))
                stream.seek(0)
                with tarfile.open(fileobj=stream) as archive:
                    if changed:
                        with self.assertRaisesRegex(ValueError, 'recipe key bytes changed'):
                            m.validate(archive, {'base': 'fixture', 'verification_keys': evidence},
                                       {'validpgpkeys': [KEY]})
                    else:
                        m.validate(archive, {'base': 'fixture', 'verification_keys': evidence},
                                   {'validpgpkeys': [KEY]})

    def test_verification_key_is_added_to_makepkg_archive(self):
        original = b'original recipe key\n'
        evidence, _ = self.run_prepare([KEY], listing(KEY), original=original, refresh=True)
        source, target = self.recipe / 'source.tar', self.recipe / 'complete.tar'
        with tarfile.open(source, 'w') as archive:
            archive.add(self.recipe / 'keys/pgp' / (KEY + '.asc'),
                        arcname='fixture/keys/pgp/' + KEY + '.asc')
        m.add_keys(source, target, self.recipe, 'fixture', evidence)
        with tarfile.open(target) as archive:
            m.validate(archive, {'base': 'fixture', 'verification_keys': evidence}, {'validpgpkeys': [KEY]})
            self.assertEqual(archive.extractfile('fixture/keys/pgp/' + KEY + '.asc').read(), original)

    def test_present_key_never_downloaded(self):
        result, calls = self.run_prepare([KEY], listing(KEY))
        self.assertEqual(result, [])
        self.assertFalse(any(call[0] == 'curl' for call in calls))

    def test_retrieve_full_fingerprint(self):
        result, calls = self.run_prepare([KEY])
        self.assertEqual(result[0]['fingerprint'], KEY)
        self.assertTrue(next(call[-1] for call in calls if call[0] == 'curl').endswith(KEY))

    def test_short_key_refused(self):
        with self.assertRaisesRegex(ValueError, 'full uppercase'):
            self.run_prepare([KEY[-16:]])

    def test_wrong_or_extra_primary_refused(self):
        for downloaded in (listing(OTHER), listing(KEY) + listing(OTHER)):
            with self.subTest(downloaded=downloaded), self.assertRaisesRegex(ValueError, 'primary fingerprint differs'):
                self.run_prepare([KEY], downloaded=downloaded)

    def test_personal_keyring_refused(self):
        with self.assertRaisesRegex(ValueError, 'disposable'):
            m.prepare([KEY], Path('/unused'), {'GNUPGHOME': str(Path.home() / '.gnupg')}, None)


if __name__ == '__main__':
    unittest.main()
