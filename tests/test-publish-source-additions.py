#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Published image source completion through real archives and local HTTP routes."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import types
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


p = load('publish', 'packaging/mirror/publish.py')
s = load('iso_sources', 'packaging/mirror/iso_sources.py')
server_module = load('pointer_server', 'tests/pointer-server.py')


class AddSources(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bucket = self.root / 'bucket'
        self.dl = p.LocalBackend(self.bucket)
        self.packages = p.LocalBackend(self.root / 'packages')
        self.flat = False
        if os.environ.get('EMAKI_TEST_SOURCE_HTTP') == '1':
            server = server_module.serve(self.bucket)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            self.url = f'http://127.0.0.1:{server.server_address[1]}'
        else:
            # Exercise the real pointer routing without requiring socket permission.
            self.url = 'http://127.0.0.1:19090'
            replacements = {
                'http_get': self.public_get,
                'http_sha256': lambda url: p.sha256(self.public_get(url)),
                'http_head': lambda url: 200 if self.public_get(url) is not None else 404,
            }
            for name, value in replacements.items():
                replacement = patch.object(p, name, value)
                replacement.start()
                self.addCleanup(replacement.stop)
            opener = types.SimpleNamespace(open=lambda request, **kwargs: self.public_head(request))
            replacement = patch.object(p.urllib.request, 'build_opener', return_value=opener)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.prefix = 'iso/0.2.0'
        self.arch = self.root / 'ARCH-SOURCES.json'
        self.missing = self.root / 'MISSING-SOURCES.json'
        self.publisher = object.__new__(p.Publisher)
        self.publisher.backend = self.packages
        self.publisher.dry_run = False
        self.publisher.args = types.SimpleNamespace(
            dl_backend=f'local:{self.bucket}', dl_public_url=self.url,
            arch_sources=self.arch, missing_sources=self.missing)
        self.records = {}
        self.identities = {}
        for base, names in [('old', ['old']), ('split', ['split', 'split-libs']),
                            ('later', ['later'])]:
            recipe = f'pkgname={base}\npkgver=1\npkgrel=1\n'.encode()
            srcinfo = ('pkgbase = ' + base + '\npkgver = 1\npkgrel = 1\n' +
                       ''.join(f'pkgname = {name}\n' for name in names)).encode()
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
                for name, contents in [('PKGBUILD', recipe), ('.SRCINFO', srcinfo)]:
                    entry = tarfile.TarInfo(f'{base}/{name}')
                    entry.size = len(contents)
                    archive.addfile(entry, io.BytesIO(contents))
            data = buffer.getvalue()
            archive_name = f'{base}-1-1.src.tar.gz'
            key = f'sources/sha256/{p.sha256(data)}/{archive_name}'
            source = self.root / key
            source.parent.mkdir(parents=True)
            source.write_bytes(data)
            for name in names:
                filename = f'{name}-1-1-x86_64.pkg.tar.zst'
                identity = dict(name=name, base=base, version='1-1',
                                binary_sha256=p.sha256(name.encode()),
                                recipe_sha256=p.sha256(recipe))
                self.identities[filename] = identity
                self.records[filename] = dict(identity, archive=archive_name,
                                              sha256=p.sha256(data), size=len(data))
        closure = ''.join(name + '\n' for name in self.records).encode()
        self.closure_hash = p.sha256(closure)
        self.old_arch = dict(schema=1, closure_sha256=self.closure_hash,
                             packages={name: value for name, value in self.records.items()
                                       if value['base'] == 'old'})
        self.old_missing = dict(schema=1, closure_sha256=self.closure_hash, bases=[])
        for base in ('split', 'later'):
            records = {name: record for name, record in self.identities.items()
                       if record['base'] == base}
            self.old_missing['bases'].append(dict(
                base=base, version='1-1', recipe_sha256=next(iter(records.values()))['recipe_sha256'],
                packages=records, reason='Source host unavailable',
                upstream_url=f'https://example.org/{base}', revision='v1'))
        self.dl.put_new(f'{self.prefix}/closure.txt', closure)
        self.dl.put_new(f'{self.prefix}/ARCH-SOURCES.json', self.encode(self.old_arch))
        self.dl.put_new(f'{self.prefix}/MISSING-SOURCES.json', self.encode(self.old_missing))
        missing_records = {name: dict(record, reason=group['reason'],
                                     upstream_url=group['upstream_url'], revision='v1')
                           for group in self.old_missing['bases']
                           for name, record in group['packages'].items()}
        directions = ('Emaki package source directions\n\n' +
                      s.directions(self.old_arch['packages'], self.url, partial=True) + '\n' +
                      s.missing_directions(missing_records))
        self.dl.put_new(f'{self.prefix}/SOURCES-ISO.txt', directions.encode())
        image = b'published image remains exact'
        image_name = 'emaki-0.2.0-x86_64.iso'
        for name, data in [(image_name, image), (image_name + '.sig', b'image signature'),
                           ('emaki-signing-key.asc', b'public key'),
                           (image_name + '.sha256', f'{p.sha256(image)}  {image_name}\n'.encode())]:
            self.dl.put_new(f'{self.prefix}/{name}', data)
        self.packages.put_new('released/iso/0.2.0', self.encode(dict(image_sha256=p.sha256(image))))
        self.new_arch = copy.deepcopy(self.old_arch)
        self.new_arch['packages'].update({name: record for name, record in self.records.items()
                                         if record['base'] == 'split'})
        self.new_missing = copy.deepcopy(self.old_missing)
        self.new_missing['bases'] = self.new_missing['bases'][1:]
        self.write_inputs()

    def public_head(self, request):
        if request.get_header('User-agent') != 'emaki-publish':
            raise p.urllib.error.HTTPError(request.full_url, 403, 'Forbidden user agent', {}, None)
        response = contextlib.nullcontext()
        response.headers = {} if self.flat else {'X-Emaki-Source-Pointer': '1'}
        return response

    def public_get(self, url):
        path = urlsplit(url).path
        routed = None if self.flat else server_module.route(self.bucket, path)
        if routed:
            status, destination = routed
            if status != 302:
                raise p.Refused('invalid public source pointer')
            path = destination
        return (self.bucket / path.lstrip('/')).read_bytes()

    @staticmethod
    def encode(data):
        return (json.dumps(data, sort_keys=True, indent=2) + '\n').encode()

    def write_inputs(self):
        self.arch.write_bytes(self.encode(self.new_arch))
        self.missing.write_bytes(self.encode(self.new_missing))

    def run_addition(self):
        p.iso_add_sources(self.publisher, '0.2.0')

    def snapshot(self):
        return {str(path.relative_to(self.bucket)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.bucket.rglob('*') if path.is_file()}

    def assert_unpointed(self):
        self.assertIsNone(self.dl.get(f'{self.prefix}/source-pointer')[0])
        self.assertEqual(p.http_get(f'{self.url}/{self.prefix}/ARCH-SOURCES.json'),
                         self.encode(self.old_arch))

    def test_complete_base_updates_routes_preserves_objects_and_retries_without_writes(self):
        before = self.snapshot()
        self.run_addition()
        for name, old in before.items():
            self.assertEqual(self.snapshot()[name], old, name)
        self.assertEqual(p.http_get(f'{self.url}/{self.prefix}/ARCH-SOURCES.json'), self.arch.read_bytes())
        self.assertEqual(p.http_get(f'{self.url}/{self.prefix}/MISSING-SOURCES.json'), self.missing.read_bytes())
        directions = p.http_get(f'{self.url}/{self.prefix}/SOURCES-ISO.txt').decode()
        self.assertIn('split-libs 1-1', directions)
        self.assertIn('later 1-1', directions)
        after = self.snapshot()
        self.run_addition()
        self.assertEqual(self.snapshot(), after)

    def test_second_addition_replaces_existing_pointer_and_completes_missing_list(self):
        self.run_addition()
        first = self.dl.get(f'{self.prefix}/source-pointer')[0]
        old_snapshot = {key: value for key, value in self.snapshot().items()
                        if '/source-snapshots/' in key}
        self.new_arch['packages'] = copy.deepcopy(self.records)
        self.new_missing['bases'] = []
        self.write_inputs()
        self.run_addition()
        self.assertNotEqual(self.dl.get(f'{self.prefix}/source-pointer')[0], first)
        for key, value in old_snapshot.items():
            self.assertEqual(self.snapshot()[key], value)
        self.assertEqual(p.http_get(f'{self.url}/{self.prefix}/MISSING-SOURCES.json'), self.missing.read_bytes())
        self.assertEqual(p.http_get(f'{self.url}/{self.prefix}/ARCH-SOURCES.json'), self.arch.read_bytes())
        self.assertNotIn(b'Sources not yet copied by Emaki',
                         p.http_get(f'{self.url}/{self.prefix}/SOURCES-ISO.txt'))
        final = self.snapshot()
        self.run_addition()
        self.assertEqual(self.snapshot(), final)

    def test_missing_published_image_companions_refused(self):
        for name in ('emaki-0.2.0-x86_64.iso', 'emaki-0.2.0-x86_64.iso.sig',
                     'emaki-signing-key.asc'):
            with self.subTest(name=name):
                path = self.bucket / self.prefix / name
                contents = path.read_bytes()
                path.unlink()
                with self.assertRaisesRegex(p.Refused, 'published|missing'):
                    self.run_addition()
                path.write_bytes(contents)
                self.assert_unpointed()

    def test_partial_split_base_refused(self):
        name = 'split-libs-1-1-x86_64.pkg.tar.zst'
        del self.new_arch['packages'][name]
        group = copy.deepcopy(self.old_missing['bases'][0])
        group['packages'] = {name: self.identities[name]}
        self.new_missing['bases'].insert(0, group)
        self.write_inputs()
        with self.assertRaisesRegex(p.Refused, 'entire missing source base'):
            self.run_addition()
        self.assert_unpointed()

    def test_previous_source_records_cannot_change(self):
        self.new_arch['packages']['old-1-1-x86_64.pkg.tar.zst']['annotation'] = 'changed'
        self.write_inputs()
        with self.assertRaisesRegex(p.Refused, 'previously collected'):
            self.run_addition()
        self.assert_unpointed()

    def test_wrong_image_closure_refused(self):
        self.new_arch['closure_sha256'] = '0' * 64
        self.write_inputs()
        with self.assertRaisesRegex(p.Refused, 'image package list'):
            self.run_addition()
        self.assert_unpointed()

    def test_binary_identity_changes_refused(self):
        filename = 'split-1-1-x86_64.pkg.tar.zst'
        for field, value in [('version', '2-1'), ('binary_sha256', '0' * 64),
                             ('recipe_sha256', '0' * 64)]:
            with self.subTest(field=field):
                self.new_arch['packages'][filename] = dict(self.records[filename], **{field: value})
                self.write_inputs()
                with self.assertRaisesRegex(p.Refused, 'image binary'):
                    self.run_addition()
                self.assert_unpointed()

    def test_corrupt_local_archive_refused(self):
        record = self.records['split-1-1-x86_64.pkg.tar.zst']
        (self.root / f'sources/sha256/{record["sha256"]}/{record["archive"]}').write_bytes(b'broken')
        with self.assertRaisesRegex(p.Refused, 'archive bytes differ'):
            self.run_addition()
        self.assert_unpointed()

    def test_remaining_missing_base_cannot_change(self):
        self.new_missing['bases'][0]['reason'] = 'Different reason'
        self.write_inputs()
        with self.assertRaisesRegex(p.Refused, 'remaining missing source bases'):
            self.run_addition()
        self.assert_unpointed()

    def test_missing_release_refused(self):
        self.packages._path('released/iso/0.2.0').unlink()
        with self.assertRaisesRegex(p.Refused, 'not published'):
            self.run_addition()
        self.assert_unpointed()

    def test_wrong_release_checksum_refused(self):
        self.packages.overwrite('released/iso/0.2.0', self.encode(dict(image_sha256='0' * 64)))
        with self.assertRaisesRegex(p.Refused, 'checksum differs'):
            self.run_addition()
        self.assert_unpointed()

    def test_anonymous_source_verification_failure_preserves_pointer(self):
        with patch.object(p, 'http_sha256', return_value='0' * 64):
            with self.assertRaisesRegex(p.Refused, 'differs from the image source manifest'):
                self.run_addition()
        self.assert_unpointed()

    def test_snapshot_upload_failure_resumes(self):
        original = p.LocalBackend.put_new

        def fail(backend, key, data):
            if '/source-snapshots/' in key and key.endswith('/ARCH-SOURCES.json'):
                raise OSError('interrupted snapshot upload')
            return original(backend, key, data)

        with patch.object(p.LocalBackend, 'put_new', fail):
            with self.assertRaisesRegex(OSError, 'interrupted snapshot upload'):
                self.run_addition()
        self.assert_unpointed()
        self.run_addition()
        self.assertEqual(p.http_get(f'{self.url}/{self.prefix}/ARCH-SOURCES.json'), self.arch.read_bytes())

    def test_snapshot_anonymous_failure_preserves_pointer(self):
        original = p.http_get

        def stale(url):
            return b'stale' if '/source-snapshots/' in url else original(url)

        with patch.object(p, 'http_get', stale):
            with self.assertRaisesRegex(p.Refused, 'not publicly available'):
                self.run_addition()
        self.assert_unpointed()

    def test_pointer_conflict_refused(self):
        original = p.LocalBackend.put_pointer
        rival = ('0' * 64 + '\n').encode()

        def race(backend, key, data, etag):
            original(backend, key, rival, etag)
            return original(backend, key, data, etag)

        with patch.object(p.LocalBackend, 'put_pointer', race):
            with self.assertRaisesRegex(p.Refused, 'changed during this run'):
                self.run_addition()
        self.assertEqual(self.dl.get(f'{self.prefix}/source-pointer')[0], rival)

    def test_dry_run_writes_nothing(self):
        self.publisher.dry_run = True
        before = self.snapshot()
        self.run_addition()
        self.assertEqual(self.snapshot(), before)
        self.assert_unpointed()

    def test_readiness_rejects_default_user_agent(self):
        url = f'{self.url}/{self.prefix}/SOURCES-ISO.txt'
        request = p.urllib.request.Request(url, method='HEAD')
        with self.assertRaises(p.urllib.error.HTTPError) as error:
            if os.environ.get('EMAKI_TEST_SOURCE_HTTP') == '1':
                p.urllib.request.urlopen(request)
            else:
                self.public_head(request)
        self.assertEqual(error.exception.code, 403)

    def test_missing_worker_refused_before_upload(self):
        if os.environ.get('EMAKI_TEST_SOURCE_HTTP') == '1':
            flat = server_module.serve(self.bucket, flat=True)
            self.addCleanup(flat.server_close)
            self.addCleanup(flat.shutdown)
            self.publisher.args.dl_public_url = f'http://127.0.0.1:{flat.server_address[1]}'
        else:
            self.flat = True
        before = self.snapshot()
        with self.assertRaisesRegex(p.Refused, 'deploy the download Worker'):
            self.run_addition()
        self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
