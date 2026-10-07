#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline checks for exact-source retrieval exceptions."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import tarfile
import io
import subprocess
import unittest

spec = importlib.util.spec_from_file_location('source_routes', Path(__file__).resolve().parents[1] /
                                             'packaging/mirror/source_routes.py')
routes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(routes)


class SourceRoutes(unittest.TestCase):
    def test_reviewed_git_mirrors_require_exact_commit(self):
        for source, commit in (
                ('https://git.0pointer.net/clone/libasyncns.git',
                 '68cd5aff1467638c086f1bedcc750e34917168e4'),
                ('https://git.0pointer.net/clone/libcanberra.git',
                 'c0620e432650e81062c1967cc669829dbd29b310')):
            self.assertEqual(routes.git_mirror(source, commit)['kind'], 'reviewed-commit-mirror')
            self.assertIsNone(routes.git_mirror(source, '0' * 40))
            self.assertIsNone(routes.git_mirror(source + '/other', commit))

    def recovery(self, base, content, run, directory):
        key = next(key for key in routes.RECOVERY_ROUTES if key[0] == base)
        url = routes.RECOVERY_ROUTES[key][0]
        return routes.prepare_routes('source = ' + url + '\n' + content,
            {'base': base, 'recipe_sha256': key[1]}, Path(directory), {}, run)

    def test_alternative_requires_strong_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            for content in ('sha256sums = SKIP', 'md5sums = ' + 'a' * 32):
                with self.assertRaisesRegex(ValueError, 'strong recipe checksum'):
                    self.recovery('os-prober', content,
                        lambda *a, **k: self.fail('unchecked download'), directory)

    def test_alternative_mismatch_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            def run(*args, **kwargs):
                if 'deb.debian.org' in args[-1]:
                    raise subprocess.CalledProcessError(22, args)
                Path(args[args.index('--output') + 1]).write_bytes(b'wrong')
                return args[-1]
            with self.assertRaisesRegex(ValueError, 'differs'):
                self.recovery('os-prober', 'sha512sums = ' + 'a' * 128, run, directory)
            self.assertFalse((Path(directory) / 'os-prober_1.84.tar.xz').exists())

    def test_relaxed_tls_scoped_no_redirects_and_checksum_required(self):
        with tempfile.TemporaryDirectory() as directory:
            attempts = []
            def run(*args, **kwargs):
                attempts.append(args)
                if '--insecure' not in args:
                    raise subprocess.CalledProcessError(60, args)
                self.assertEqual(args[args.index('--max-redirs') + 1], '0')
                Path(args[args.index('--output') + 1]).write_bytes(b'fixed')
                return args[-1]
            result = self.recovery('sg3_utils',
                'sha512sums = ' + hashlib.sha512(b'fixed').hexdigest(), run, directory)
            self.assertEqual(len(attempts), 2)
            self.assertEqual(attempts[0][-1], attempts[1][-1])
            self.assertIn('tls_verification', result['evidence'][0])
            with self.assertRaisesRegex(ValueError, 'differs'):
                self.recovery('sg3_utils', 'sha512sums = ' + 'a' * 128, run, directory)
            self.assertFalse((Path(directory) / 'sg3_utils-1.49.tar.xz').exists())

    def test_savannah_routes_delegate_archive_validation(self):
        import sys
        from unittest.mock import patch
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging/mirror'))
        import savannah_git
        record = {'base': 'example', 'source_routes': [{'kind': 'savannah-official-git'}]}
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'source.tar'
            with tarfile.open(archive, 'w'):
                pass
            with patch.object(savannah_git, 'validate_routes') as validator:
                routes.validate_routes(archive, record)
            validator.assert_called_once_with(archive, record)
            with self.assertRaises(ValueError):
                routes.validate_routes(archive, record)

    def test_redirect_effective_url_is_recorded(self):
        payload = b'fixed'
        original = 'https://ftp.gnu.org/gnu/example/source.tar.xz'
        effective = 'http://ftp.wayne.edu/gnu/example/source.tar.xz'
        with tempfile.TemporaryDirectory() as directory:
            def run(*args, **kwargs):
                self.assertIn('--write-out', args)
                self.assertEqual(args[args.index('--write-out') + 1], '%{url_effective}')
                Path(args[args.index('--output') + 1]).write_bytes(payload)
                return effective
            result = routes.prepare_routes(
                f'source = {original}\nsha256sums = {hashlib.sha256(payload).hexdigest()}',
                {}, Path(directory), {}, run)
            self.assertEqual(result['evidence'][0]['mirror'], original)
            self.assertEqual(result['evidence'][0]['effective_url'], effective)

    def test_corrupt_payload_and_signature_retry_and_preserve_evidence(self):
        payload = b'fixed'
        original = 'https://ftp.gnu.org/gnu/example/source.tar.xz'
        key = 'A' * 40
        with tempfile.TemporaryDirectory() as directory:
            downloads = Path(directory)
            attempts = []
            def run(*args, **kwargs):
                if args[0] == 'gpg':
                    if Path(args[-2]).read_bytes() == b'bad signature':
                        raise subprocess.CalledProcessError(1, args, '[GNUPG:] BADSIG bad')
                    return self.signatures([key])
                url = args[-1]
                attempts.append(url)
                if url.endswith('.sig'):
                    data = b'bad signature' if url.startswith(routes.GNU_MIRROR) else b'good signature'
                else:
                    data = b'wrong bytes' if url == original else payload
                Path(args[args.index('--output') + 1]).write_bytes(data)
                return url + '?served=1'
            result = routes.prepare_routes(
                f'source = {original}\nsource = {original}.sig\n'
                f'sha256sums = {hashlib.sha256(payload).hexdigest()}\nsha256sums = SKIP\n'
                f'validpgpkeys = {key}', {}, downloads, {}, run)
            self.assertEqual((downloads / 'source.tar.xz').read_bytes(), payload)
            self.assertEqual((downloads / 'source.tar.xz.sig').read_bytes(), b'good signature')
            rejected = list((downloads / '.rejected').glob('*.bytes'))
            self.assertEqual({path.read_bytes() for path in rejected}, {b'wrong bytes', b'bad signature'})
            self.assertEqual(result['evidence'][0]['mirror'], routes.GNU_MIRROR + 'example/source.tar.xz')
            self.assertEqual(result['evidence'][1]['mirror'], original + '.sig')
            self.assertEqual(len(list((downloads / '.rejected').glob('*.json'))), 2)

    def test_all_mismatches_leave_only_retained_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'source'
            retained = root / 'persistent-evidence'
            def run(*args, **kwargs):
                Path(args[args.index('--output') + 1]).write_bytes(b'wrong')
                return 'https://serving.invalid/source'
            with self.assertRaisesRegex(ValueError, 'differs'):
                routes.download_routes(['https://one.invalid/source', 'https://two.invalid/source'],
                    target, run, verify=lambda path: routes.verify_checksum(path, 'sha256', '0' * 64),
                    rejected_dir=retained)
            self.assertFalse(target.exists())
            self.assertEqual(len(list(retained.glob('*.bytes'))), 2)
            import json
            metadata = [json.loads(path.read_text()) for path in retained.glob('*.json')]
            self.assertEqual({item['requested_url'] for item in metadata},
                             {'https://one.invalid/source', 'https://two.invalid/source'})
            self.assertEqual({item['effective_url'] for item in metadata},
                             {'https://serving.invalid/source'})

    def test_signature_candidates_require_valid_recipe_primary(self):
        key = 'A' * 40
        for status in ('', self.signatures(['B' * 40]),
                       self.signatures([key]) + '\n[GNUPG:] BADSIG invalid'):
            with self.subTest(status=status), self.assertRaises(ValueError):
                routes.verify_detached(Path('sig'), Path('payload'), [key], {},
                                       lambda *a, **k: status)

    def test_missing_subkey_refreshes_once_before_retrying_same_signature(self):
        original = 'https://ftp.gnu.org/gnu/example/source.tar.xz'
        key = 'A' * 40
        for refreshed in (True, False):
            with self.subTest(refreshed=refreshed), tempfile.TemporaryDirectory() as directory:
                downloads = Path(directory)
                calls = []
                refreshes = []
                def refresh():
                    refreshes.append(True)
                    return refreshed
                def run(*args, **kwargs):
                    calls.append(args)
                    if args[0] == 'gpg':
                        if not refreshes or not refreshed:
                            raise subprocess.CalledProcessError(2, args,
                                '[GNUPG:] ERRSIG subkey 1 2 3 4 5\n[GNUPG:] NO_PUBKEY subkey')
                        return self.signatures([key])
                    Path(args[args.index('--output') + 1]).write_bytes(b'fixed')
                    return args[-1]
                info = (f'source = {original}\nsource = {original}.sig\n'
                        f'sha256sums = {hashlib.sha256(b"fixed").hexdigest()}\nsha256sums = SKIP\n'
                        f'validpgpkeys = {key}')
                if refreshed:
                    routes.prepare_routes(info, {}, downloads, {}, run, refresh_keys=refresh)
                else:
                    with self.assertRaisesRegex(ValueError, 'public key'):
                        routes.prepare_routes(info, {}, downloads, {}, run, refresh_keys=refresh)
                self.assertEqual(len(refreshes), 1)
                self.assertEqual(len([args for args in calls if args[0] == 'curl']), 2)
                self.assertFalse((downloads / '.rejected').exists())
                self.assertEqual((downloads / 'source.tar.xz.sig').read_bytes(), b'fixed')

    def test_expired_valid_signature_nonzero_status_is_accepted_with_warning(self):
        key = 'A' * 40
        def run(*args, **kwargs):
            raise subprocess.CalledProcessError(1, args,
                '[GNUPG:] EXPKEYSIG key identity\n' + self.signatures([key]))
        evidence = {}
        routes.verify_detached(Path('sig'), Path('payload'), [key], {}, run, evidence=evidence)
        self.assertEqual(evidence['warnings'], ['The signing key has expired.'])

    def test_blake2_recipe_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'source'
            path.write_bytes(b'pinned source')
            routes.verify_checksum(path, 'b2', hashlib.blake2b(path.read_bytes()).hexdigest())
            with self.assertRaisesRegex(ValueError, 'differs'):
                routes.verify_checksum(path, 'b2', '0' * 128)

    def signatures(self, signers):
        return '\n'.join(f'[GNUPG:] VALIDSIG {key} 2026-09-11 1789125717 0 4 0 22 10 00 {key}'
                         for key in signers)

    def test_two_signers_in_either_order(self):
        keys = sorted(routes.LIBGCRYPT_SIGNERS)
        for order in (keys, keys[::-1]):
            result = routes.signature_evidence(self.signatures(order), {keys[0]})
            self.assertEqual([s['primary'] for s in result], order)
            self.assertEqual(sum(s['allowed'] for s in result), 1)

    def test_unlisted_or_invalid_signers_refused(self):
        keys = sorted(routes.LIBGCRYPT_SIGNERS)
        with self.assertRaisesRegex(ValueError, 'no valid signature'):
            routes.signature_evidence(self.signatures(keys), {'0' * 40})
        for status in ('BADSIG', 'ERRSIG', 'EXPSIG', 'EXPKEYSIG', 'REVKEYSIG', 'NO_PUBKEY'):
            with self.assertRaises(ValueError):
                routes.signature_evidence(self.signatures(keys) + '\n[GNUPG:] ' + status, set(keys))
        with self.assertRaises(ValueError):
            routes.signature_evidence(self.signatures(keys[:1]), set(keys))

    def test_subkey_uses_primary(self):
        keys = sorted(routes.LIBGCRYPT_SIGNERS)
        status = self.signatures(keys).replace('VALIDSIG ' + keys[0], 'VALIDSIG ' + 'F' * 40)
        self.assertTrue(routes.signature_evidence(status, {keys[0]})[0]['allowed'])

    def test_unknown_recipe_does_not_bypass(self):
        with tempfile.TemporaryDirectory() as directory:
            result = routes.prepare_routes('', {'base': 'libgcrypt', 'recipe_sha256': 'changed'},
                                           Path(directory), {}, lambda *a, **k: self.fail('network'))
            self.assertEqual(result, {'makepkg_flags': [], 'evidence': []})

    def test_changed_exception_metadata_refused(self):
        with self.assertRaises(ValueError):
            routes.prepare_libgcrypt({}, {'base': 'libgcrypt', 'recipe_sha256': routes.LIBGCRYPT_RECIPE},
                                     Path('/unused'), {}, None)

    def test_checksum_mirror_and_corruption(self):
        data = b'checksum-pinned tarball'
        checksum = hashlib.sha256(data).hexdigest()
        info = ('source = https://ftp.gnu.org/gnu/example/example.tar.xz\n'
                'sha256sums = ' + checksum)
        with tempfile.TemporaryDirectory() as directory:
            def download(*args, **kwargs):
                if args[0] == 'gpg':
                    return self.signatures(['A' * 40])
                Path(args[args.index('--output') + 1]).write_bytes(data)
                return ''
            result = routes.prepare_routes(info, {}, Path(directory), {}, download)
            self.assertEqual(result['evidence'][0]['checksums'], {'sha256': checksum})
            self.assertEqual(result['makepkg_flags'], [])
            with self.assertRaises(ValueError):
                routes.prepare_routes(info.replace(checksum, '0' * 64), {}, Path(directory), {}, download)
            result = routes.prepare_routes(info.replace(checksum, 'SKIP'), {}, Path(directory), {},
                                           lambda *a, **k: self.fail('unpinned mirror'))
            self.assertEqual(result['evidence'], [])
            # Weak pins alone never qualify a mirror copy.
            weak = ('source = https://ftp.gnu.org/gnu/example/example.tar.xz\n'
                    'md5sums = ' + hashlib.md5(data).hexdigest() + '\n'
                    'sha1sums = ' + hashlib.sha1(data).hexdigest())
            result = routes.prepare_routes(weak, {}, Path(directory), {},
                                           lambda *a, **k: self.fail('weakly pinned mirror'))
            self.assertEqual(result['evidence'], [])

    def test_origin_failure_falls_back_and_records_route(self):
        data = b'fixed'
        info = ('source = https://ftp.gnu.org/gnu/example/example.tar.xz\n'
                'sha256sums = ' + hashlib.sha256(data).hexdigest())
        with tempfile.TemporaryDirectory() as directory:
            def download(*args, **kwargs):
                if args[0] == 'gpg':
                    return self.signatures(['A' * 40])
                if args[-1].startswith('https://ftp.gnu.org/'):
                    raise subprocess.CalledProcessError(28, args)
                Path(args[args.index('--output') + 1]).write_bytes(data)
                return ''
            result = routes.prepare_routes(info, {}, Path(directory), {}, download)
            self.assertTrue(result['evidence'][0]['mirror'].startswith(routes.GNU_MIRROR))

    def test_archive_route_bytes_are_verified(self):
        data = b'fixed'
        item = {'kind': 'official-checksum-mirror',
                'source': 'https://ftp.gnu.org/gnu/example/source.tar.gz',
                'mirror': routes.GNU_MIRROR + 'example/source.tar.gz',
                'checksums': {'sha256': hashlib.sha256(data).hexdigest()}}
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'source.tar.gz'
            with tarfile.open(archive, 'w:gz') as bundle:
                member = tarfile.TarInfo('example/source.tar.gz')
                member.size = len(data)
                bundle.addfile(member, io.BytesIO(data))
            record = {'base': 'example', 'source_routes': [item]}
            routes.validate_routes(archive, record)
            item['checksums']['sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'differs'):
                routes.validate_routes(archive, record)
            with self.assertRaisesRegex(ValueError, 'lacks reviewed'):
                routes.validate_routes(archive, {'base': 'libgcrypt',
                    'recipe_sha256': routes.LIBGCRYPT_RECIPE})

    def test_gnu_pub_and_savannah_fallback_preserve_pins_and_signatures(self):
        payload = b'fixed source'
        checksum = hashlib.sha256(payload).hexdigest()
        for original, expected, authority in (
                ('https://ftp.gnu.org/pub/gnu/sed/sed.tar.xz',
                 routes.GNU_SECONDARY + 'sed/sed.tar.xz', 'https://www.gnu.org/prep/ftp.html'),
                ('https://download.savannah.gnu.org/releases/man-db/man-db.tar.xz',
                 routes.SAVANNAH_MIRRORS[-1] + 'man-db/man-db.tar.xz', routes.SAVANNAH_AUTHORITY),
                ('https://download-mirror.savannah.gnu.org/releases/man-db/man-db-2.13.1.tar.xz',
                 routes.SAVANNAH_MIRRORS[-1] + 'man-db/man-db-2.13.1.tar.xz', routes.SAVANNAH_AUTHORITY),
                ('https://download.savannah.nongnu.org/releases/dmidecode/dmidecode.tar.xz',
                 routes.SAVANNAH_MIRRORS[-1] + 'dmidecode/dmidecode.tar.xz', routes.SAVANNAH_AUTHORITY)):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as directory:
                info = (f'source = payload.tar.xz::{original}\n'
                        f'source = payload.tar.xz.sig::{original}.sig\n'
                        f'sha256sums = {checksum}\nsha256sums = SKIP\n')
                attempts = []

                def download(*args, **kwargs):
                    if args[0] == 'gpg':
                        return self.signatures(['A' * 40])
                    url = args[-1]
                    attempts.append(url)
                    if url not in (expected, expected + '.sig'):
                        raise subprocess.CalledProcessError(28, args)
                    Path(args[args.index('--output') + 1]).write_bytes(
                        b'unchanged signature' if url.endswith('.sig') else payload)
                    return ''

                result = routes.prepare_routes(info, {}, Path(directory), {}, download)
                self.assertEqual(result['makepkg_flags'], [])
                self.assertEqual(result['evidence'][0]['mirror'], expected)
                self.assertEqual(result['evidence'][0]['authority'], authority)
                self.assertEqual(attempts[0], original)
                self.assertEqual(attempts[-1], expected + '.sig')
                self.assertEqual((Path(directory) / 'payload.tar.xz.sig').read_bytes(),
                                 b'unchanged signature')
                archive = Path(directory) / 'sources.tar.gz'
                with tarfile.open(archive, 'w:gz') as bundle:
                    for name in ('payload.tar.xz', 'payload.tar.xz.sig'):
                        bundle.add(Path(directory) / name, arcname='example/' + name)
                record = {'base': 'example', 'source_routes': result['evidence']}
                routes.validate_routes(archive, record)
                result['evidence'][0]['mirror'] = 'https://unknown.invalid/payload.tar.xz'
                with self.assertRaisesRegex(ValueError, 'unreviewed'):
                    routes.validate_routes(archive, record)
                with self.assertRaisesRegex(ValueError, 'differs'):
                    routes.prepare_routes(info.replace(checksum, '0' * 64), {},
                                          Path(directory), {}, download)
                self.assertEqual(routes.prepare_routes(info.replace(checksum, 'SKIP'), {},
                    Path(directory), {}, lambda *a, **k: self.fail('unpinned download'))['evidence'], [])

    def test_image_gnu_and_savannah_bases_have_official_routes(self):
        sources = {
            'sed': 'https://ftp.gnu.org/pub/gnu/sed/sed-4.9.tar.xz',
            'gawk': 'https://ftp.gnu.org/pub/gnu/gawk/gawk-5.3.2.tar.xz',
            'gettext': 'https://ftp.gnu.org/pub/gnu/gettext/gettext-0.26.tar.xz',
            'gnulib-l10n': 'https://ftp.gnu.org/pub/gnu/gnulib/gnulib-l10n-20241209.tar.gz',
            'dmidecode': 'https://download.savannah.gnu.org/releases/dmidecode/dmidecode-3.6.tar.xz',
            'libpipeline': 'https://download.savannah.gnu.org/releases/libpipeline/libpipeline-1.5.8.tar.gz',
            'man-db': 'https://download-mirror.savannah.gnu.org/releases/man-db/man-db-2.13.1.tar.xz',
        }
        for base, source in sources.items():
            with self.subTest(base=base):
                mirrors, authority = routes.checksum_mirrors(source)
                self.assertGreaterEqual(len(mirrors), 2)
                self.assertTrue(authority)
                if base == 'man-db':
                    self.assertEqual(mirrors[0], routes.SAVANNAH_MIRRORS[0] +
                                     'man-db/man-db-2.13.1.tar.xz')
                    self.assertEqual(routes.checksum_mirrors(source + '.asc')[0][0],
                                     mirrors[0] + '.asc')
                    self.assertEqual(routes.checksum_mirrors(source.replace('.gnu.', '.nongnu.')),
                                     (mirrors, authority))

    def test_freetype_sourceforge_release_family_and_signatures(self):
        payload = b'fixed release'
        checksum = hashlib.sha256(payload).hexdigest()
        for stem, section in (('freetype', 'freetype2'), ('freetype-doc', 'freetype-docs'),
                              ('ft2demos', 'freetype-demos')):
            original = ('https://download-mirror.savannah.gnu.org/releases/freetype/' +
                        stem + '-2.14.3.tar.xz')
            expected = ('https://downloads.sourceforge.net/project/freetype/' + section +
                        '/2.14.3/' + stem + '-2.14.3.tar.xz')
            with self.subTest(stem=stem), tempfile.TemporaryDirectory() as directory:
                def download(*args, **kwargs):
                    if args[0] == 'gpg':
                        return self.signatures(['A' * 40])
                    if args[-1] not in (expected, expected + '.sig'):
                        raise subprocess.CalledProcessError(28, args)
                    Path(args[args.index('--output') + 1]).write_bytes(
                        b'signature' if args[-1].endswith('.sig') else payload)
                    return ''
                info = (f'source = {original}\nsource = {original}.sig\n'
                        f'sha256sums = {checksum}\nsha256sums = SKIP\n')
                result = routes.prepare_routes(info, {}, Path(directory), {}, download)
                self.assertEqual(result['makepkg_flags'], [])
                self.assertEqual(result['evidence'][0]['mirror'], expected)
                self.assertEqual(result['evidence'][0]['authority'], routes.FREETYPE_AUTHORITY)
                self.assertEqual(result['evidence'][1]['mirror'], expected + '.sig')
                with self.assertRaisesRegex(ValueError, 'differs'):
                    routes.prepare_routes(info.replace(checksum, '0' * 64), {},
                                          Path(directory), {}, download)
        self.assertFalse(any('sourceforge' in u for u in routes.checksum_mirrors(
            original.replace('freetype/', 'other/'))[0]))

    def test_missing_signature_falls_back_without_skipping_verification(self):
        payload = b'fixed release'
        original = 'https://ftp.gnu.org/gnu/example/source.tar.xz'
        expected = routes.GNU_SECONDARY + 'example/source.tar.xz.sig'
        with tempfile.TemporaryDirectory() as directory:
            def download(*args, **kwargs):
                if args[0] == 'gpg':
                    return self.signatures(['A' * 40])
                if args[-1] not in (original, expected):
                    raise subprocess.CalledProcessError(22, args)
                Path(args[args.index('--output') + 1]).write_bytes(
                    b'signature' if args[-1].endswith('.sig') else payload)
                return ''
            info = (f'source = {original}\nsource = {original}.sig\n'
                    f'sha256sums = {hashlib.sha256(payload).hexdigest()}\nsha256sums = SKIP\n')
            result = routes.prepare_routes(info, {}, Path(directory), {}, download)
            self.assertEqual(result['makepkg_flags'], [])
            self.assertEqual(result['evidence'][1]['mirror'], expected)
            with self.assertRaises(subprocess.CalledProcessError):
                routes.prepare_routes(info, {}, Path(directory), {},
                    lambda *a, **k: (_ for _ in ()).throw(subprocess.CalledProcessError(22, a)))

    def test_savannah_requires_exact_host_release_path_and_plain_url(self):
        for source in ('https://download.savannah.gnu.org.evil.invalid/releases/foo.tar.gz',
                       'https://download.savannah.gnu.org/other/foo.tar.gz',
                       'https://download.savannah.gnu.org/releases/foo.tar.gz?change=1',
                       'https://download.savannah.gnu.org/releases/foo.tar.gz#change',
                       'git+https://download.savannah.gnu.org/releases/foo.tar.gz'):
            self.assertEqual(routes.checksum_mirrors(source), ([], None))

    def test_git_mirror_requires_known_repository_and_commit(self):
        source = 'git+https://git.savannah.gnu.org/git/coreutils.git'
        self.assertIsNone(routes.git_mirror(source, 'v9.12'))
        self.assertIsNone(routes.git_mirror(source.replace('coreutils', 'unknown'), 'a' * 40))
        self.assertEqual(routes.git_mirror(source, 'a' * 40)['commit'], 'a' * 40)

    def test_unreviewed_hosts_and_git_tags_not_substituted(self):
        for url in ('https://example.invalid/gnu/foo.tar.gz', 'git+https://ftp.gnu.org/gnu/foo#tag=v1',
                    'https://ftp.gnu.org/gnu/foo?change=1'):
            self.assertIsNone(routes.gnu_mirror_url(url))


if __name__ == '__main__':
    unittest.main()
