#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""packaging/mirror/r2.py: AWS SigV4 against AWS's published examples, and the R2 backend of
publish.sh against a local fake S3 endpoint that checks every request's signature.

This never talks to Cloudflare. What it proves: the signer matches AWS's documented
signatures; requests arrive with valid signatures; the conditional headers make the backend
refuse to replace objects; a whole publish and promote run through the R2 code path and a real
pacman syncs the result. What it cannot prove: R2's own behaviour, which is checked on the real
mirror before anything reaches stable.
"""
import datetime
import hashlib
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / 'packaging/mirror'))
import r2  # noqa: E402

ACCESS = 'AKIAIOSFODNN7EXAMPLE'
SECRET = 'wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'
WHEN = datetime.datetime(2013, 5, 24, tzinfo=datetime.timezone.utc)


class SignatureVectors(unittest.TestCase):
    """Examples from AWS's 'Signature Calculations for the Authorization Header' page."""

    def test_get_object_example(self):
        header = r2.sign_v4('GET', 'https://examplebucket.s3.amazonaws.com/test.txt', {'Range': 'bytes=0-9'},
                            r2.EMPTY_SHA256, ACCESS, SECRET, 'us-east-1', WHEN)
        self.assertEqual(header, 'AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/'
                                 'aws4_request, SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, '
                                 'Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41')

    def test_put_object_example_quotes_the_key(self):
        body = b'Welcome to Amazon S3.'
        header = r2.sign_v4('PUT', 'https://examplebucket.s3.amazonaws.com/' + r2._quote_path('test$file.text'),
                            {'Date': 'Fri, 24 May 2013 00:00:00 GMT', 'x-amz-storage-class': 'REDUCED_REDUNDANCY'},
                            hashlib.sha256(body).hexdigest(), ACCESS, SECRET, 'us-east-1', WHEN)
        self.assertTrue(header.endswith(
            'SignedHeaders=date;host;x-amz-content-sha256;x-amz-date;x-amz-storage-class, '
            'Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd'), header)

    def test_list_objects_example_sorts_the_query(self):
        header = r2.sign_v4('GET', 'https://examplebucket.s3.amazonaws.com/?max-keys=2&prefix=J', {},
                            r2.EMPTY_SHA256, ACCESS, SECRET, 'us-east-1', WHEN)
        self.assertTrue(header.endswith(
            'Signature=34b48302e7b5fa45bde8084f4b7868a86f0a534bc59db6670ed5711ef69dc6f7'), header)


class FakeS3(http.server.BaseHTTPRequestHandler):
    """Path-style S3 with the subset R2 documents: conditional PUT, copy, list v2, multipart."""
    root = None
    bucket = 'emaki-pkgs'
    lock = threading.Lock()
    uploads = {}
    seen = []

    def log_message(self, format, *args):
        pass

    def reply(self, status, body=b'', headers=None):
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def check_signature(self, body):
        given = self.headers.get('Authorization', '')
        signed = given.split('SignedHeaders=')[1].split(',')[0].split(';')
        headers = {name: self.headers[name] for name in signed if name not in ('host', 'x-amz-date',
                                                                              'x-amz-content-sha256')}
        payload = self.headers['x-amz-content-sha256']
        if payload != hashlib.sha256(body).hexdigest():
            return False
        when = datetime.datetime.strptime(self.headers['x-amz-date'], '%Y%m%dT%H%M%SZ').replace(
            tzinfo=datetime.timezone.utc)
        url = f'http://{self.headers["Host"]}{self.path}'
        expected = r2.sign_v4(self.command, url, headers, payload, ACCESS, SECRET, 'auto', when)
        return expected == given

    def target(self):
        parsed = urllib.parse.urlsplit(self.path)
        parts = parsed.path.lstrip('/').split('/', 1)
        if parts[0] != self.bucket:
            return None, None, {}
        key = urllib.parse.unquote(parts[1]) if len(parts) > 1 else ''
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        return key, (self.root / key) if key else None, query

    def handle_any(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        if not self.check_signature(body):
            self.reply(403, b'<Error><Code>SignatureDoesNotMatch</Code></Error>')
            return
        key, path, query = self.target()
        if key is None:
            self.reply(404)
            return
        self.seen.append((self.command, key, dict(query)))
        with self.lock:
            if self.command == 'GET' and query.get('list-type') == '2':
                prefix = query.get('prefix', '')
                keys = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob('*')
                              if p.is_file() and not p.name.startswith('.'))
                contents = ''.join(f'<Contents><Key>{k}</Key></Contents>' for k in keys if k.startswith(prefix))
                self.reply(200, ('<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                                 f'{contents}<IsTruncated>false</IsTruncated></ListBucketResult>').encode())
            elif self.command == 'POST' and 'uploads' in query:
                upload = uuid.uuid4().hex
                self.uploads[upload] = {}
                self.reply(200, ('<InitiateMultipartUploadResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                                 f'<UploadId>{upload}</UploadId></InitiateMultipartUploadResult>').encode())
            elif self.command == 'PUT' and 'partNumber' in query:
                self.uploads[query['uploadId']][int(query['partNumber'])] = body
                self.reply(200, headers={'ETag': f'"{hashlib.md5(body).hexdigest()}"'})
            elif self.command == 'POST' and 'uploadId' in query:
                if self.headers.get('If-None-Match') == '*' and path.exists():
                    self.reply(412)
                    return
                parts = self.uploads.pop(query['uploadId'])
                self.store(path, b''.join(parts[n] for n in sorted(parts)))
                self.reply(200)
            elif self.command == 'DELETE' and 'uploadId' in query:
                self.uploads.pop(query['uploadId'], None)
                self.reply(204)
            elif self.command == 'PUT' and self.headers.get('x-amz-copy-source'):
                source = urllib.parse.unquote(self.headers['x-amz-copy-source']).split('/', 2)[2]
                if self.headers.get('cf-copy-destination-if-none-match') == '*' and path.exists():
                    self.reply(412)
                    return
                self.store(path, (self.root / source).read_bytes())
                self.reply(200, b'<CopyObjectResult/>')
            elif self.command == 'PUT':
                current = self.etag(path)
                if self.headers.get('If-None-Match') == '*' and current is not None:
                    self.reply(412)
                    return
                if self.headers.get('If-Match') and self.headers['If-Match'] != current:
                    self.reply(412)
                    return
                self.store(path, body)
                self.reply(200, headers={'ETag': self.etag(path)})
            elif self.command in ('GET', 'HEAD'):
                if not path.is_file():
                    self.reply(404)
                    return
                self.reply(200, path.read_bytes(), {'ETag': self.etag(path)})
            elif self.command == 'DELETE':
                path.unlink(missing_ok=True)
                self.reply(204)
            else:
                self.reply(405)

    @staticmethod
    def etag(path):
        return f'"{hashlib.md5(path.read_bytes()).hexdigest()}"' if path.is_file() else None

    @staticmethod
    def store(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.parent / f'.tmp-{uuid.uuid4().hex}'
        temp.write_bytes(data)
        os.replace(temp, path)

    do_GET = do_PUT = do_POST = do_DELETE = do_HEAD = handle_any


def start_fake(root):
    handler = type('Fake', (FakeS3,), {'root': Path(root), 'uploads': {}, 'seen': []})
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, handler


class ClientAgainstFake(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='emaki-fake-s3-'))
        self.server, self.handler = start_fake(self.root)
        endpoint = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.client = r2.S3Client(endpoint, 'emaki-pkgs', ACCESS, SECRET)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.root)

    def test_conditional_writes(self):
        self.assertEqual(self.client.put('a/b.txt', b'one', if_none_match=True)[0], 200)
        self.assertEqual(self.client.put('a/b.txt', b'two', if_none_match=True)[0], 412)
        self.assertEqual(self.client.get('a/b.txt')[0], b'one')
        etag = self.client.head('a/b.txt')
        self.assertEqual(self.client.put('a/b.txt', b'two', if_match='"stale"')[0], 412)
        self.assertEqual(self.client.put('a/b.txt', b'two', if_match=etag)[0], 200)
        self.assertEqual(self.client.copy('a/b.txt', 'c/d.txt'), 200)
        self.assertEqual(self.client.copy('a/b.txt', 'c/d.txt'), 412)
        self.assertEqual(self.client.list('a/'), ['a/b.txt'])
        self.assertEqual(self.client.get('missing'), (None, None))
        self.client.delete('a/b.txt')
        self.assertIsNone(self.client.head('a/b.txt'))

    def test_keys_with_reserved_characters_are_signed_as_sent(self):
        key = 'testing/x86_64/emaki-config-0.1.3-1-x86_64.pkg.tar.zst'
        self.assertEqual(self.client.put(key, b'x', if_none_match=True)[0], 200)
        self.assertEqual(self.client.put('odd/a+b@c.txt', b'y', if_none_match=True)[0], 200)
        self.assertEqual(self.client.get('odd/a+b@c.txt')[0], b'y')

    def test_a_wrong_secret_is_rejected(self):
        bad = r2.S3Client(self.client.endpoint, 'emaki-pkgs', ACCESS, SECRET + 'x')
        with self.assertRaises(r2.S3Error) as caught:
            bad.put('x', b'1')
        self.assertEqual(caught.exception.status, 403)

    def test_multipart_upload_appears_whole_and_refuses_an_existing_name(self):
        big = self.root.parent / f'{self.root.name}-iso'
        big.write_bytes(os.urandom(5 * 1024 * 1024 + 123))
        try:
            self.client.upload_large('iso/0.1.3/emaki.iso', big, part_size=1024 * 1024)
            self.assertEqual(self.client.get('iso/0.1.3/emaki.iso')[0], big.read_bytes())
            parts = [q for c, k, q in self.handler.seen if 'partNumber' in q]
            self.assertEqual(len(parts), 6)
            with self.assertRaises(r2.S3Error):
                self.client.upload_large('iso/0.1.3/emaki.iso', big, part_size=1024 * 1024)
            self.assertFalse(self.handler.uploads, 'the refused upload was aborted')
        finally:
            big.unlink()

    def test_credentials_file_must_be_private(self):
        home = Path(tempfile.mkdtemp())
        try:
            path = home / '.config/emaki-signing/r2-emaki-pkgs.env'
            path.parent.mkdir(parents=True)
            path.write_text('EMAKI_R2_ACCOUNT_ID=a\nEMAKI_R2_ACCESS_KEY_ID=b\nEMAKI_R2_SECRET_ACCESS_KEY=c\n')
            path.chmod(0o644)
            with self.assertRaises(PermissionError):
                r2.load_credentials('emaki-pkgs', environ={}, home=home)
            path.chmod(0o600)
            self.assertEqual(r2.load_credentials('emaki-pkgs', environ={}, home=home)['EMAKI_R2_ACCESS_KEY_ID'], 'b')
            with self.assertRaises(KeyError):
                r2.load_credentials('emaki-dl', environ={}, home=home)
        finally:
            shutil.rmtree(home)

    def test_credentials_in_the_environment_belong_to_one_bucket(self):
        """Two buckets, two tokens: the package token in the environment must never be used for
        the ISO bucket, which has its own file."""
        home = Path(tempfile.mkdtemp())
        try:
            path = home / '.config/emaki-signing/r2-emaki-dl.env'
            path.parent.mkdir(parents=True)
            path.write_text('EMAKI_R2_ACCOUNT_ID=a\nEMAKI_R2_ACCESS_KEY_ID=dl-key\nEMAKI_R2_SECRET_ACCESS_KEY=dl\n')
            path.chmod(0o600)
            # The package token exported for a publish, unscoped: it would be used for emaki-dl too.
            unscoped = {'EMAKI_R2_ACCOUNT_ID': 'a', 'EMAKI_R2_ACCESS_KEY_ID': 'pkgs-key',
                        'EMAKI_R2_SECRET_ACCESS_KEY': 'pkgs'}
            with self.assertRaises(KeyError) as refused:
                r2.load_credentials('emaki-dl', environ=unscoped, home=home)
            self.assertIn('EMAKI_R2_EMAKI_DL_ACCESS_KEY_ID', str(refused.exception))
            scoped = {'EMAKI_R2_EMAKI_PKGS_ACCOUNT_ID': 'a', 'EMAKI_R2_EMAKI_PKGS_ACCESS_KEY_ID': 'pkgs-key',
                      'EMAKI_R2_EMAKI_PKGS_SECRET_ACCESS_KEY': 'pkgs'}
            self.assertEqual(r2.load_credentials('emaki-dl', environ=scoped, home=home)['EMAKI_R2_ACCESS_KEY_ID'],
                             'dl-key')
            self.assertEqual(r2.load_credentials('emaki-pkgs', environ=scoped, home=home)['EMAKI_R2_ACCESS_KEY_ID'],
                             'pkgs-key')
        finally:
            shutil.rmtree(home)


class PublishThroughR2Backend(unittest.TestCase):
    """The same publish/promote as tests/test-publish.py, with --backend r2: and a fake endpoint."""

    def test_publish_and_promote(self):
        spec = importlib.util.spec_from_file_location('test_publish', HERE / 'test-publish.py')
        tp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tp)
        missing = [tool for tool in tp.TOOLS if not shutil.which(tool)]
        if missing:
            self.skipTest('missing tools: ' + ', '.join(missing))
        world = tp.World()
        server, handler = start_fake(world.bucket)
        try:
            world.backend = 'r2:emaki-pkgs'
            world.extra_env = {'EMAKI_R2_ENDPOINT': f'http://127.0.0.1:{server.server_address[1]}',
                               'EMAKI_R2_EMAKI_PKGS_ACCOUNT_ID': 'test', 'EMAKI_R2_EMAKI_PKGS_ACCESS_KEY_ID': ACCESS,
                               'EMAKI_R2_EMAKI_PKGS_SECRET_ACCESS_KEY': SECRET}
            directory = world.base / 'pkgs'
            tp.release(directory, world.keys, '1-1', extra=tp.publish.PATCHED_PACKAGES)
            tp.patched_sources(directory)
            for command in (('publish', 'testing', directory), ('promote', '--first')):
                result = world.publish(*command)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            machine = world.machine('stable')
            self.assertEqual(machine.sync().returncode, 0)
            self.assertEqual({v for _, v in machine.listed()[1]}, {'1-1'})
            writes = [(c, k) for c, k, q in handler.seen if c == 'PUT']
            self.assertTrue(all(k.startswith(('testing/', 'stable/', 'snap/', 'pointers/', 'locks/',
                                              'served/', 'sources/sha256/')) for _, k in writes), writes)
            self.assertEqual(sum(1 for _, k in writes if k.startswith('pointers/')), 2)
            for channel in ('testing', 'stable'):
                snap = world.bucket / 'snap' / channel / world.pointer(channel)
                sources = (snap / 'SOURCES').read_bytes()
                for archive in directory.glob('*.sources.tar.gz'):
                    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                    key = f'sources/sha256/{digest}/{archive.name}'
                    self.assertIn(f'SHA256: {digest}'.encode(), sources)
                    self.assertIn(f'/{key}'.encode(), sources)
                    self.assertEqual((world.bucket / key).read_bytes(), archive.read_bytes())
            dry = world.publish('--dry-run', 'withdraw', 'stable', '--to', world.pointer('stable'))
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertEqual(sum(1 for c, k, q in handler.seen if c == 'PUT'), len(writes), 'dry run wrote')
        finally:
            server.shutdown()
            server.server_close()
            world.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
