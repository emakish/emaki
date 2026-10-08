#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""packaging/publish.sh against a local bucket served like pkgs.emaki.sh, with the real pacman.

The client is the real pacman run as an ordinary user under fakeroot with a scratch root,
database and keyring; the signing key is a throwaway key made in a temporary GNUPGHOME. No
Cloudflare, no GitHub and no real key are touched. The flat-layout self-check shows that the
interruption cases can fail: the old in-place procedure must break a syncing machine.
"""
import base64
import datetime
import email.message
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import unittest
from unittest import mock
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PUBLISH = ROOT / 'packaging/mirror/publish.py'
TOOLS = ('fakeroot', 'pacman', 'pacman-key', 'repo-add', 'gpg', 'gpgconf', 'bsdtar', 'git')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pointer_server = load('pointer_server', HERE / 'pointer-server.py')
publish = load('publish', PUBLISH)
gnupg_daemons = load('gnupg_daemons', HERE / 'gnupg-daemons.py')
SUITE_ROOT = SUITE_STORAGE = SUITE_ALIAS = OUTER_TMPDIR = None


def setUpModule():
    # Every temporary directory of the suite, publish.py's and the drill's included, lies under
    # one root: what GnuPG left running is counted after each test, and the directories of
    # runs killed on purpose go with the root. The prefix is short because pacman-key homes
    # hold their sockets themselves (sun_path is 108 bytes).
    global SUITE_ROOT, SUITE_STORAGE, SUITE_ALIAS, OUTER_TMPDIR
    OUTER_TMPDIR = os.environ.get('TMPDIR')
    SUITE_ROOT = Path(tempfile.mkdtemp(prefix='emaki-t-'))
    SUITE_STORAGE = SUITE_ROOT
    if len(str(SUITE_ROOT)) > 24:
        # Keep payloads under the requested evidence directory while giving GnuPG sockets
        # a short pathname. Only this directory and symlink occupy /tmp.
        SUITE_ALIAS = Path(tempfile.mkdtemp(prefix='et-', dir='/tmp'))
        SUITE_ROOT = SUITE_ALIAS / 't'
        SUITE_ROOT.symlink_to(SUITE_STORAGE, target_is_directory=True)
    os.environ['TMPDIR'] = tempfile.tempdir = str(SUITE_ROOT)


def tearDownModule():
    os.environ.pop('TMPDIR')
    if OUTER_TMPDIR is not None:
        os.environ['TMPDIR'] = OUTER_TMPDIR
    tempfile.tempdir = None
    left = suite_gnupg_daemons()
    shutil.rmtree(SUITE_STORAGE, ignore_errors=True)
    if SUITE_ALIAS is not None:
        SUITE_ROOT.unlink()
        SUITE_ALIAS.rmdir()
    if left:
        raise AssertionError('GnuPG daemons left by the suite:\n' + gnupg_daemons.describe(left))


def suite_gnupg_daemons():
    # GnuPG may retain either the supplied alias or the canonical home pathname.
    return [process for root in {SUITE_ROOT, SUITE_STORAGE}
            for process in gnupg_daemons.check(root)]


def no_gnupg_daemons_left(test):
    """Registered by every test: nothing GnuPG started for it outlives it."""
    left = suite_gnupg_daemons()
    test.assertEqual(left, [], 'GnuPG daemons outlived the test:\n' + gnupg_daemons.describe(left))


def run(command, **kwargs):
    kwargs.setdefault('capture_output', True)
    kwargs.setdefault('text', True)
    return subprocess.run([str(c) for c in command], **kwargs)


class Keys:
    """A throwaway signing key in its own GNUPGHOME, plus the public files publish.sh reads."""

    def __init__(self, base, name='Emaki Test Signing <test@emaki.invalid>', passphrase=''):
        self.home = Path(base) / 'signing-gnupg'
        self.home.mkdir(mode=0o700, parents=True)
        env = dict(os.environ, GNUPGHOME=str(self.home))
        run(['gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', passphrase, '--quick-gen-key',
             name, 'ed25519', 'sign', '2d'], env=env, check=True)
        listing = run(['gpg', '--with-colons', '--list-secret-keys'], env=env, check=True).stdout
        self.fpr = next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr'))
        self.keyring = Path(base) / 'keyring.gpg'
        self.keyring.write_bytes(run(['gpg', '--export', self.fpr], env=env, check=True, text=False).stdout)
        self.trusted = Path(base) / 'trusted'
        self.trusted.write_text(f'{self.fpr}:4:\n')
        self.env, self.passphrase = env, passphrase

    def sign(self, path):
        typed = ['--pinentry-mode', 'loopback', '--passphrase', self.passphrase] if self.passphrase else []
        run(['gpg', '--batch', '--yes', *typed, '--local-user', self.fpr, '--detach-sign', '--no-armor',
             '--output', f'{path}.sig', path], env=self.env, check=True)

    def close(self):
        run(['gpgconf', '--homedir', self.home, '--kill', 'all'])


def fixture_recipe(name, version):
    upstream, release = version.rsplit('-', 1)
    epoch, upstream = upstream.split(':', 1) if ':' in upstream else ('0', upstream)
    return (f'pkgname={name}\npkgver={upstream}\npkgrel={release}\nepoch={epoch}\n').encode()


def make_package(directory, name, version, depends=(), payload=None, arch='any'):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(dir=directory.parent, prefix='stage-'))
    (stage / 'usr/share' / name).mkdir(parents=True)
    (stage / 'usr/share' / name / 'marker').write_text(payload or f'{name} {version}\n')
    lines = [f'pkgname = {name}', f'pkgbase = {name}', f'pkgver = {version}', 'pkgdesc = test package',
             'url = https://example.invalid', 'builddate = 1700000000', 'packager = Test', 'size = 16',
             f'arch = {arch}', 'license = GPL-3.0-or-later'] + [f'depend = {d}' for d in depends]
    (stage / '.PKGINFO').write_text('\n'.join(lines) + '\n')
    (stage / '.BUILDINFO').write_text('pkgbuild_sha256sum = ' +
                                    publish.sha256(fixture_recipe(name, version)) + '\n')
    path = directory / f'{name}-{version}-{arch}.pkg.tar.zst'
    run(['bsdtar', '--zstd', '-cf', path, '-C', stage, '.PKGINFO', '.BUILDINFO', 'usr'], check=True)
    shutil.rmtree(stage)
    patched_sources(directory)
    return path


def buildinfo(directory, commit='a' * 40):
    directory = Path(directory)
    paths = sorted(directory.glob('*.pkg.tar.zst')) + sorted(directory.glob('*.sources.tar.gz'))
    if (directory / 'SOURCES.json').exists():
        paths.append(directory / 'SOURCES.json')
    lines = [f'commit {commit}'] + [f'{publish.sha256_file(p)}  {p.name}' for p in paths]
    (directory / 'BUILDINFO').write_text('\n'.join(lines) + '\n')


def patched_sources(directory, names=None):
    directory = Path(directory)
    records = {}
    for path in sorted(directory.glob('*.pkg.tar.zst')):
        name, version = publish.package_version(path.name)
        if names is not None and name not in names:
            continue
        archive = directory / f'{name}-{version}.sources.tar.gz'
        archive.write_bytes(f'prepared sources for {name} {version}\n'.encode())
        records[name] = {'version': version, 'archive': archive.name, 'sha256': publish.sha256_file(archive)}
    (directory / 'SOURCES.json').write_text(json.dumps({'format': 1, 'commit': 'a' * 40, 'packages': records}))
    buildinfo(directory)


def release(directory, keys, version, config_payload=None, extra=()):
    """One candidate: the marker pins emaki-config exactly, like packaging/emaki/PKGBUILD."""
    paths = [
        make_package(directory, 'emaki-config', version, payload=config_payload),
        make_package(directory, 'emaki-mirrorlist', version),
        make_package(directory, 'emaki', version, depends=(f'emaki-config={version}', 'emaki-mirrorlist')),
        make_package(directory, 'emaki-apps', version, depends=('emaki-config',)),
    ] + [make_package(directory, name, version) for name in extra]
    for path in paths:
        keys.sign(path)
    return paths


class Machine:
    """An installed system as far as [emaki] is concerned: its own sync db, kept between syncs."""
    _template = {}

    def __init__(self, base, keys, server, siglevel='Required'):
        self.dir = Path(tempfile.mkdtemp(dir=base, prefix='machine-'))
        for name in ('root', 'db', 'cache', 'hooks'):
            (self.dir / name).mkdir()
        self.config = self.dir / 'pacman.conf'
        self.set_server(server, siglevel)
        template = self._template.get(keys.fpr)
        if template is None:
            template = Path(base) / f'pacman-keyring-{keys.fpr[-8:]}'
            prefix = ['fakeroot', '--', 'pacman-key', '--config', self.config, '--gpgdir', template]
            try:
                run(prefix + ['--init'], check=True)
                run(prefix + ['--add', keys.keyring], check=True)
                run(prefix + ['--lsign-key', keys.fpr], check=True)
            finally:
                # Under fakeroot like pacman-key: its sockets are in the home itself.
                run(['fakeroot', '--', 'gpgconf', '--homedir', template, '--kill', 'all'])
            self._template[keys.fpr] = template
        shutil.copytree(template, self.dir / 'gnupg', ignore=shutil.ignore_patterns('S.*', '*.lock'))

    def set_server(self, server, siglevel='Required'):
        self.config.write_text('\n'.join([
            '[options]', f'RootDir = {self.dir}/root', f'DBPath = {self.dir}/db', f'CacheDir = {self.dir}/cache',
            f'LogFile = {self.dir}/pacman.log', f'GPGDir = {self.dir}/gnupg', f'HookDir = {self.dir}/hooks',
            'Architecture = x86_64', f'SigLevel = {siglevel}', '[emaki]', f'Server = {server}']) + '\n')

    def pacman(self, *arguments):
        return run(['fakeroot', '--', 'pacman', '--config', self.config, '--noconfirm', *arguments],
                   env=dict(os.environ, LC_ALL='C'))

    def sync(self):
        return self.pacman('-Sy')

    def listed(self):
        result = self.pacman('-Sl', 'emaki')
        return result.returncode, {tuple(line.split()[1:3]) for line in result.stdout.splitlines()}


class World:
    """Bucket directory + pointer server + keys + a clean source tree + publish.sh arguments."""

    def __init__(self, flat=False):
        self.base = Path(tempfile.mkdtemp(prefix='emaki-publish-test-'))
        self.keys = Keys(self.base)
        self.bucket = self.base / 'bucket'
        self.bucket.mkdir()
        self.log = self.base / 'server.log'
        self.server = pointer_server.serve(self.bucket, flat=flat, log=self.log)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.home = self.base / 'home'
        self.home.mkdir()
        self.state = self.base / 'state'
        self.tree = self.base / 'tree'
        self.tree.mkdir()
        run(['git', '-C', self.tree, 'init', '-q'], check=True)
        run(['git', '-C', self.tree, '-c', 'user.name=t', '-c', 'user.email=t@example.invalid',
             'commit', '-q', '--allow-empty', '-m', 'init'], check=True)
        self.registry = self.base / 'released-packages.sha256'
        self.registry.write_text('')
        self.github = self.base / 'github'
        self.flat = flat
        self.backend = f'local:{self.bucket}'
        self.extra_env = {}
        self.extra_args = []

    def args(self, state=None):
        args = ['--backend', self.backend, '--public-url', self.url, '--state-dir', state or self.state,
                '--stamp', self.base / 'stamp.json', '--key', self.keys.fpr, '--signing-home', self.keys.home,
                '--keyring', self.keys.keyring, '--trusted', self.keys.trusted, '--registry', self.registry,
                '--source-tree', self.tree, '--closure', 'emaki emaki-apps', '--arch-dbs', 'none',
                '--github', f'local:{self.github}']
        args += self.extra_args
        if self.flat:
            args += ['--layout', 'flat']
        return args

    def publish(self, *command, kill=None, state=None):
        env = dict(os.environ, HOME=str(self.home), GNUPGHOME=str(self.keys.home), **self.extra_env)
        env.pop('EMAKI_PUBLISH_TEST_KILL_AFTER', None)
        if kill is not None:
            env['EMAKI_PUBLISH_TEST_KILL_AFTER'] = str(kill)
        if 'iso' in command and '--arch-sources' not in command:
            image = Path(command[command.index('iso') + 1])
            command = (*command, '--arch-sources', self.arch_sources(image))
        return run([sys.executable, PUBLISH, *self.args(state), *command], env=env)

    def arch_sources(self, image):
        """Prepare a local source fixture bound to the exact packages of this test image."""
        module = load('iso_source_fixture', ROOT / 'packaging/mirror/iso_sources.py')
        output = image.parent / 'arch-sources'
        output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.base) as temporary:
            run(['bsdtar', '-xf', image, '-C', temporary, 'emaki'], check=True)
            packages = Path(temporary) / 'emaki/repo'
            closure = packages.parent / 'live-closure.txt'
            records = module.inventory(closure, packages.parent / 'live-packages.json', publish.EMAKI_PACKAGES)
            for record in records.values():
                tree = Path(temporary) / 'source' / record['base']
                tree.mkdir(parents=True, exist_ok=True)
                recipe = fixture_recipe(record['name'], record['version'])
                (tree / 'PKGBUILD').write_bytes(recipe)
                (tree / '.SRCINFO').write_text('pkgbase = ' + record['base'] + '\n' +
                                              recipe.decode().replace('=', ' = '))
                raw = Path(temporary) / 'source.tar'
                normalized = Path(temporary) / 'source.tar.gz'
                run(['bsdtar', '-cf', raw, '-C', tree.parent, tree.name], check=True)
                module.normalize(raw, normalized)
                data = normalized.read_bytes()
                digest = publish.sha256(data)
                archive = record['base'] + '.src.tar.gz'
                source = output / f'sources/sha256/{digest}/{archive}'
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(data)
                record.update(archive=archive, sha256=digest, size=len(data))
            manifest = output / 'ARCH-SOURCES.json'
            manifest.write_text(json.dumps({'schema': 1, 'closure_sha256': module.digest(closure),
                                            'packages': records}))
        return manifest

    def machine(self, channel='testing', siglevel='Required'):
        return Machine(self.base, self.keys, f'{self.url}/{channel}/x86_64', siglevel)

    def pointer(self, channel):
        path = self.bucket / 'pointers' / channel
        return path.read_text().strip() if path.exists() else None

    def requests(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.keys.close()
        shutil.rmtree(self.base, ignore_errors=True)


def versions(listing):
    return {name: version for name, version in listing}


class LiveMetadata(unittest.TestCase):
    def test_only_legacy_images_may_omit_live_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / 'image.iso'
            for version in ('0.1.0', '0.2.0'):
                self.assertIsNone(publish.check_live_metadata(image, '', version, root))
            for version in ('0.2.1', '0.3.0', '1.0.0'):
                with self.subTest(version=version), self.assertRaisesRegex(publish.Refused, 'live-closure'):
                    publish.check_live_metadata(image, '', version, root)
            with self.assertRaisesRegex(publish.Refused, 'live-closure'):
                publish.check_live_metadata(image, 'emaki/live-packages.json\n', '0.2.0', root)


class BucketDomain(pointer_server.Handler):
    """dl.emaki.sh without the image Worker, as measured on the 0.3.0 image (2026-10-07): no
    X-Emaki-Image; If-Range is ignored (even a stale validator gets 206); and the first ranged
    GET after a HEAD that the edge answered with cf-cache-status MISS gets the whole image (200),
    the next one 206 again. A browser that gets 200 for its resume starts again from zero."""
    image_worker = False
    missed = False

    def do_HEAD(self):
        type(self).missed = True
        super().do_HEAD()

    def ranged(self, path):
        if type(self).missed:
            type(self).missed = False
            return False
        return super().ranged(path)


class ResumedDownloadReadBack(unittest.TestCase):
    """The read-back of an image asks the way a browser resumes: Range with If-Range = ETag."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data = os.urandom(3 << 20)
        image = self.root / 'iso/9.9.9/emaki-9.9.9-x86_64.iso'
        image.parent.mkdir(parents=True)
        image.write_bytes(self.data)
        (self.root / 'iso/9.9.9/closure.txt').write_text('closure\n')

    def serve(self, base=None):
        server = pointer_server.serve(self.root, base=base)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_address[1]}/iso/9.9.9/'

    def test_resume_with_the_published_etag_answers_the_bytes(self):
        url = self.serve() + 'emaki-9.9.9-x86_64.iso'
        marker = publish.IMAGE_MARKER
        etag = publish.http_etag(url, marker=marker)
        self.assertRegex(etag, r'^"[^"]+"$')
        middle = len(self.data) // 2
        self.assertEqual(publish.http_range(url, middle, middle + 99, if_range=etag, marker=marker),
                         self.data[middle:middle + 100])
        with self.assertRaisesRegex(publish.Refused, r'If-Range: "stale" answered 200, not 206'):
            publish.http_range(url, middle, middle + 99, if_range='"stale"', marker=marker)
        with self.assertRaisesRegex(publish.Refused, 'HTTP 416'):
            publish.http_range(url, len(self.data), len(self.data) + 9, marker=marker)

    def test_the_bucket_domain_is_refused_although_it_answers_206(self):
        url = self.serve(BucketDomain) + 'emaki-9.9.9-x86_64.iso'
        marker = publish.IMAGE_MARKER
        # A 206 proves nothing: the bucket's domain ignores even a stale If-Range...
        self.assertEqual(publish.http_range(url, 0, 9, if_range='"stale"'), self.data[:10])
        # ...and right after a HEAD (a cache miss) a plain range gets the whole image.
        publish.http_etag(url)
        with self.assertRaisesRegex(publish.Refused, 'with Range answered 200, not 206'):
            publish.http_range(url, 0, 9)
        self.assertEqual(publish.http_range(url, 0, 9), self.data[:10])
        for read in (lambda: publish.http_range(url, 0, 9, marker=marker),
                     lambda: publish.http_etag(url, marker=marker),
                     lambda: publish.check_image_route(url)):
            with self.assertRaisesRegex(publish.Refused, 'not answered by the download Worker '
                                        r'\(no X-Emaki-Image: 1, HTTP 20[06]\)'):
                read()
        with self.assertRaisesRegex(publish.Refused, r'no X-Emaki-Image: 1, HTTP 404'):
            publish.check_image_route(url.replace('9.9.9', '9.9.8'))

    def test_the_image_route_check_accepts_the_worker_before_and_after_the_upload(self):
        base = self.serve()
        with mock.patch.object(publish.urllib.request, 'urlopen', wraps=publish.urllib.request.urlopen) as opened:
            publish.check_image_route(base + 'emaki-9.9.9-x86_64.iso')
            publish.check_image_route(base.replace('9.9.9', '9.9.8') + 'emaki-9.9.8-x86_64.iso')
        for call in opened.call_args_list:
            request = call.args[0]
            # Never the plain address of an image that may not exist yet (its 404 is cached).
            self.assertEqual((request.get_method(), request.full_url.rsplit('?', 1)[1]), ('HEAD', 'publish-check'))
        with self.assertRaisesRegex(publish.Refused, 'not answered by the download Worker'):
            publish.check_image_route(base + 'emaki-9.9.8-x86_64.iso')  # not an image address
        with self.assertRaisesRegex(publish.Refused, 'HEAD http://127.0.0.1:1/'):
            publish.check_image_route('http://127.0.0.1:1/iso/9.9.9/emaki-9.9.9-x86_64.iso')
        # The Worker answering 503 cannot read its bucket: refused before anything is uploaded.
        headers = email.message.Message()
        headers['X-Emaki-Image'] = '1'
        unreadable = urllib.error.HTTPError(base + 'emaki-9.9.9-x86_64.iso', 503, 'Service Unavailable',
                                            headers, io.BytesIO(b''))
        with mock.patch.object(publish.urllib.request, 'urlopen', side_effect=unreadable):
            with self.assertRaisesRegex(publish.Refused, 'answered 503 from the download Worker'):
                publish.check_image_route(base + 'emaki-9.9.9-x86_64.iso')

    def test_an_answer_without_a_strong_etag_is_refused(self):
        base = self.serve()
        with self.assertRaisesRegex(publish.Refused, 'no strong ETag'):
            publish.http_etag(base + 'closure.txt')
        with self.assertRaisesRegex(publish.Refused, 'HTTP 404'):
            publish.http_etag(base + 'emaki-9.9.8-x86_64.iso')
        response = mock.MagicMock()
        response.__enter__.return_value.headers = {'ETag': 'W/"weak"'}
        with mock.patch.object(publish.urllib.request, 'urlopen', return_value=response), \
                self.assertRaisesRegex(publish.Refused, 'no strong ETag'):
            publish.http_etag(base + 'emaki-9.9.9-x86_64.iso')


class PublishedAcceptance(unittest.TestCase):
    def test_publication_preserves_020_stamp_but_next_candidate_requires_020(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backend = publish.LocalBackend(root / 'bucket')
            publisher = object.__new__(publish.Publisher)
            publisher.backend = backend
            publisher.args = SimpleNamespace(stamp=root / 'stamp.json')
            current, following = 'a' * 64, 'b' * 64

            def results(manifest, starts=('0.1.0', '0.1.1')):
                paths = []
                for run_name in ('T1', 'T2'):
                    for start in starts:
                        for size in publish.REQUIRED_SIZES:
                            path = root / f'{run_name}-{start}-{size}.json'
                            path.write_text(json.dumps({'manifest_sha256': manifest,
                                'run': run_name, 'start': start, 'size': size, 'passed': True,
                                'finished': publish.utcnow().isoformat()}))
                            paths.append(path)
                return paths

            # A stamp prepared before a publication must be rechecked at promotion time.
            publish.write_stamp(results(following), publisher.args.stamp, backend)
            stale_stamp = publisher.args.stamp.read_bytes()
            publish.write_stamp(results(current), publisher.args.stamp, backend)
            original_stamp = publisher.args.stamp.read_bytes()
            publisher.check_stamp(current)
            backend.put_new('released/iso/0.2.0', json.dumps({'manifest': current}).encode())
            publisher.check_stamp(current)
            self.assertEqual(publisher.args.stamp.read_bytes(), original_stamp)
            publisher.args.stamp.write_bytes(stale_stamp)
            with self.assertRaisesRegex(publish.Refused, 'T1 from 0.2.0'):
                publisher.check_stamp(following)
            with self.assertRaisesRegex(publish.Refused, 'T1 from 0.2.0'):
                publish.write_stamp(results(following), publisher.args.stamp, backend)
            publish.write_stamp(results(following, ('0.1.0', '0.1.1', '0.2.0')),
                                publisher.args.stamp, backend)
            publisher.check_stamp(following)


class SourceProvenance(unittest.TestCase):
    """Source/binary binding without signing daemons or a network server."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='emaki-source-publish-')
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.publisher = object.__new__(publish.Publisher)
        for name in publish.PATCHED_PACKAGES:
            path = make_package(self.directory, name, '1.2-3')
            Path(str(path) + '.sig').write_bytes(b'signature')
        patched_sources(self.directory)

    def test_sources_are_bound_to_binary_versions_and_build_commit(self):
        entries = self.publisher.collect(self.directory)
        self.assertEqual(len(entries), 3)
        records = {e['name']: e['source'] for e in entries.values()}
        files = {name: b'database' for name in publish.DB_FILES}
        files.update(SOURCES=publish.sources_text(records))
        files['SOURCES.json'] = json.dumps({'format': 1, 'packages': records}).encode()
        manifest = publish.parse_manifest(publish.manifest_text(files, {}, records))
        for name, record in records.items():
            self.assertEqual(record['commit'], 'a' * 40)
            self.assertEqual(manifest['sources/' + record['archive']], record['sha256'])
            self.assertIn(publish.source_key(record).encode(), files['SOURCES'])
            self.assertNotIn(record['commit'].encode(), files['SOURCES'])
        for name in publish.SOURCE_FILES:
            self.assertEqual(manifest[name], publish.sha256(files[name]))

    def test_snapshot_checks_directions_and_downloaded_archive_hashes(self):
        publisher = self.publisher
        entries = publisher.collect(self.directory)
        records = {e['name']: e['source'] for e in entries.values()}
        files = {name: b'database' for name in publish.DB_FILES}
        files['SOURCES'] = publish.sources_text(records)
        files['SOURCES.json'] = json.dumps({'format': 1, 'packages': records}).encode()
        files['MANIFEST'] = publish.manifest_text(files, {}, records)
        publisher.backend = publish.LocalBackend(self.directory / 'bucket')
        publisher.public = 'https://packages.invalid'
        for name, data in files.items():
            publisher.backend.put_new(f'snap/testing/20260101T000000Z/{name}', data)
        self.assertEqual(publisher.snapshot('testing', '20260101T000000Z'), files)
        for record in records.values():
            with mock.patch.object(publish, 'http_get', return_value=(self.directory / record['archive']).read_bytes()):
                publisher.put_source(record, self.directory / record['archive'])
        with mock.patch.object(publish, 'http_head', return_value=200), mock.patch.object(publish, 'http_get', side_effect=AssertionError('archive downloaded again')):
            publisher.verify_sources('testing', files)
            for record in records.values():
                publisher.put_source(record, self.directory / record['archive'])
        publisher.backend.overwrite(publish.source_key(next(iter(records.values()))), b'wrong archive')
        with self.assertRaisesRegex(publish.Refused, 'verified upload'):
            publisher.verify_sources('testing', files)
        publisher.backend.overwrite('snap/testing/20260101T000000Z/SOURCES', b'changed directions')
        with self.assertRaisesRegex(publish.Refused, 'SOURCES does not match its MANIFEST'):
            publisher.snapshot('testing', '20260101T000000Z')

    def test_release_notes_preserve_placeholders_when_rendered(self):
        import markdown
        text = 'https://archive.archlinux.org/packages/<initial>/<pkgname>/.\n'
        notes = publish.source_notes(text)
        rendered = markdown.markdown(notes, extensions=['fenced_code'])
        self.assertIn('&lt;initial&gt;/&lt;pkgname&gt;', rendered)
        self.assertIn('<pre><code', rendered)
        target = publish.GithubTarget()
        saved = []
        def command(args, **kwargs):
            if 'edit' in args:
                saved.append(Path(args[-1]).read_text())
            return SimpleNamespace(stdout='Release description\n')
        with mock.patch.object(publish.subprocess, 'run', side_effect=command):
            target.notes('testing', text)
        rendered = markdown.markdown(saved[0], extensions=['fenced_code'])
        self.assertIn('&lt;initial&gt;/&lt;pkgname&gt;', rendered)

    def test_withdraw_record_keeps_its_kind_with_source_records(self):
        publisher = self.publisher
        records = {e['name']: e['source'] for e in publisher.collect(self.directory).values()}
        files = {'SOURCES.json': json.dumps({'packages': records}).encode(), 'MANIFEST': b''}
        publisher.args = SimpleNamespace(closure='emaki')
        publisher.snapshot = lambda *args: files
        publisher.done = lambda *args: None
        for method in ('verify_sources', 'verify_packages_anonymously', 'upload_snapshot',
                       'verify_snapshot', 'flip', 'confirm_channel', 'finish'):
            setattr(publisher, method, mock.Mock())
        journal = {'channel': 'testing', 'id': 'new', 'steps': []}
        publisher.copy_snapshot(journal, 'testing', 'old', {'kind': 'withdraw'}, lambda: None)
        self.assertEqual(publisher.finish.call_args.args[1]['kind'], 'withdraw')

    def test_channel_confirmation_refuses_missing_sources_route(self):
        publisher = self.publisher
        publisher.args = SimpleNamespace(layout='pointer')
        publisher.public = 'https://packages.invalid'
        with mock.patch.object(publish, 'http_get', return_value=b'') as fetch:
            with self.assertRaisesRegex(publish.Refused, 'source directions'):
                publisher.confirm_channel('testing', {'SOURCES': b'new directions'})
        fetch.assert_called_once_with('https://packages.invalid/testing/x86_64/SOURCES')

    def test_unpatched_packages_also_require_archives(self):
        path = make_package(self.directory, 'emaki-config', '1.2-3')
        Path(str(path) + '.sig').write_bytes(b'signature')
        self.publisher.collect(self.directory)
        (self.directory / 'emaki-config-1.2-3.sources.tar.gz').unlink()
        with self.assertRaisesRegex(publish.Refused, 'missing or differs'):
            self.publisher.collect(self.directory)

    def test_refuses_missing_or_changed_provenance(self):
        provenance = self.directory / 'BUILDINFO'
        original = provenance.read_bytes()
        provenance.unlink()
        with self.assertRaisesRegex(publish.Refused, 'no BUILDINFO'):
            self.publisher.collect(self.directory)
        provenance.write_bytes(original)
        archive = next(self.directory.glob('*.sources.tar.gz'))
        archive.write_bytes(b'other sources')
        with self.assertRaisesRegex(publish.Refused, 'differs from BUILDINFO'):
            self.publisher.collect(self.directory)

    def test_refuses_mismatched_source_version_and_commit(self):
        metadata = self.directory / 'SOURCES.json'
        data = json.loads(metadata.read_bytes())
        data['commit'] = 'b' * 40
        metadata.write_text(json.dumps(data))
        buildinfo(self.directory)
        with self.assertRaisesRegex(publish.Refused, 'wrong format or commit'):
            self.publisher.collect(self.directory)
        data['commit'] = 'a' * 40
        data['packages']['niri-emaki']['version'] = '9-9'
        metadata.write_text(json.dumps(data))
        buildinfo(self.directory)
        with self.assertRaisesRegex(publish.Refused, 'mismatched source archive'):
            self.publisher.collect(self.directory)

    def test_refuses_changed_binary_even_with_unchanged_sources(self):
        next(self.directory.glob('*.pkg.tar.zst')).touch()
        provenance = self.directory / 'BUILDINFO'
        provenance.write_text(provenance.read_text().replace(publish.sha256_file(next(self.directory.glob('*.pkg.tar.zst'))), '0' * 64))
        with self.assertRaisesRegex(publish.Refused, 'differs from BUILDINFO'):
            self.publisher.collect(self.directory)

    def test_iso_publishes_exact_closure_and_adjacent_sources_before_checksum(self):
        self.check_iso_publication(False)

    def test_iso_partial_sources_require_exact_explicit_missing_file(self):
        self.check_iso_publication(True)

    def test_020_publication_keeps_legacy_inputs_and_uploads(self):
        self.check_iso_publication(True, legacy=True)

    def test_iso_complete_sources_with_empty_missing_file_has_no_missing_heading(self):
        self.check_iso_publication(False, empty_missing=True)

    def check_iso_publication(self, partial, legacy=False, empty_missing=False):
        root = self.directory
        image_version = "0.2.0" if legacy else "9.9.9"
        installer = make_package(root, 'emaki-installer', '1.2-3')
        Path(str(installer) + '.sig').write_bytes(b'signature')
        entries = self.publisher.collect(root)
        records = {e['name']: e['source'] for e in entries.values()}
        files = {name: b'database' for name in publish.DB_FILES}
        files.update(SOURCES=publish.sources_text(records))
        files['SOURCES.json'] = json.dumps({'format': 1, 'packages': records}).encode()
        files['MANIFEST'] = publish.manifest_text(files, {f: (e['sha256'], e['sig']) for f, e in entries.items()}, records)
        stage = root / 'stage/emaki/repo'
        stage.mkdir(parents=True)
        for path in root.glob('*.pkg.tar.zst'):
            shutil.copyfile(path, stage / path.name)
        arch_name = 'arch-demo-1-1-x86_64.pkg.tar.zst'
        arch_build = root / 'arch-build'
        arch_build.mkdir()
        (arch_build / '.PKGINFO').write_text('pkgname = arch-demo\npkgbase = arch-demo\npkgver = 1-1\nlicense = GPL-2.0-only\n')
        arch_recipe = b'pkgname=arch-demo\npkgver=1\npkgrel=1\n'
        recipe_sha = publish.sha256(arch_recipe)
        (arch_build / '.BUILDINFO').write_text('pkgbuild_sha256sum = ' + recipe_sha + '\n')
        run(['bsdtar', '-cf', stage / arch_name, '-C', arch_build, '.PKGINFO', '.BUILDINFO'], check=True)
        closure = ''.join(f + '\n' for f in sorted([*entries, arch_name])).encode()
        (stage / 'closure.txt').write_bytes(closure)
        module = load('iso_source_fixture', ROOT / 'packaging/mirror/iso_sources.py')
        (stage.parent / 'live-closure.txt').write_bytes(closure)
        (stage.parent / 'pkglist.x86_64.txt').write_text(''.join(
            ' '.join(publish.package_version(name)) + '\n' for name in sorted([*entries, arch_name])))
        module.write_image_inventory(stage.parent / 'live-closure.txt', stage,
                                     stage.parent / 'live-packages.json', publish.EMAKI_PACKAGES)
        # The live-only Arch package and installer have source coverage without offline archives.
        target_names = (list(entries) + [arch_name] if legacy else
                        [name for name in entries if not name.startswith('emaki-installer-')])
        if legacy:
            for name in ('live-closure.txt', 'live-packages.json', 'pkglist.x86_64.txt'):
                (stage.parent / name).unlink()
        target_closure = ''.join(name + '\n' for name in sorted(target_names)).encode()
        (stage / 'closure.txt').write_bytes(target_closure)
        arch_binary_sha = publish.sha256_file(stage / arch_name)
        if not legacy:
            (stage / arch_name).unlink()
        for name in entries:
            if name not in target_names:
                (stage / name).unlink()
        run(['repo-add', stage / 'emaki-offline.db.tar.gz',
             *sorted(stage.glob('*.pkg.tar.zst'))], check=True)
        image = root / f'emaki-{image_version}-x86_64.iso'
        run(['bsdtar', '--format=iso9660', '-cf', image, '-C', root / 'stage', '.'], check=True)
        Path(str(image) + '.sig').write_bytes(b'signature')
        Path(str(image) + '.sha256').write_text(f'{publish.sha256_file(image)}  {image.name}\n')
        dl = root / 'dl'
        publisher = self.publisher
        publisher.public = 'https://pkgs.emaki.sh'
        arch_manifest = root / 'ARCH-SOURCES.json'
        arch_source = root / 'arch-source/arch-demo'
        arch_source.mkdir(parents=True)
        (arch_source / 'PKGBUILD').write_bytes(arch_recipe)
        (arch_source / '.SRCINFO').write_text(
            'pkgbase = arch-demo\npkgver = 1\npkgrel = 1\npkgname = arch-demo\n')
        arch_bundle = root / 'arch-fixture.src.tar.gz'
        run(['bsdtar', '-czf', arch_bundle, '-C', arch_source.parent, 'arch-demo'], check=True)
        arch_data = arch_bundle.read_bytes()
        arch_sha = publish.sha256(arch_data)
        arch_key = f'sources/sha256/{arch_sha}/arch-demo.src.tar.gz'
        arch_path = root / arch_key
        arch_path.parent.mkdir(parents=True)
        arch_path.write_bytes(arch_data)
        arch_record = {'name': 'arch-demo', 'base': 'arch-demo', 'version': '1-1',
                       'recipe_sha256': recipe_sha, 'binary_sha256': arch_binary_sha,
                       'archive': 'arch-demo.src.tar.gz', 'sha256': arch_sha, 'size': len(arch_data)}
        arch_manifest.write_text(json.dumps({'schema': 1, 'closure_sha256': publish.sha256(closure),
                                             'packages': {arch_name: arch_record}}))
        publisher.args = SimpleNamespace(dl_backend=f'local:{dl}', dl_public_url='https://download.invalid',
                                         arch_sources=arch_manifest)
        publisher.dry_run = False
        publisher.backend = publish.LocalBackend(root / 'bucket')
        publisher.keyring = root / 'keyring'
        publisher.trusted = root / 'trusted'
        publisher._verifier = SimpleNamespace(verify=lambda *args: True)
        publisher.pointer = lambda channel: ('20260101T000000Z', None)
        publisher.snapshot = lambda *args: files
        publisher.check_stamp = lambda digest: None
        for record in records.values():
            key = publish.source_key(record)
            publisher.backend.put_new(key, root / record['archive'])
            publisher.backend.put_new(key + '.verified', json.dumps(
                {'sha256': record['sha256'], 'etag': publisher.backend.head(key)}).encode())
        original_run = subprocess.run
        def command(args, **kwargs):
            if args[0] == 'bsdtar' and '-xOf' in args and str(image) in args:
                self.assertNotEqual(args[-1], 'emaki/repo')
                if kwargs.get('stdout') is not None:
                    self.assertEqual(len(list(Path(kwargs['stdout'].name).parent.glob('*.pkg.tar.zst'))), 1)
            if args[0] == 'gpg':
                return subprocess.CompletedProcess(args, 0, stdout=b'public key', stderr=b'')
            return original_run(args, **kwargs)
        def fetch(url):
            path = '/' + url.removeprefix('https://download.invalid/')
            routed = pointer_server.route(dl, path)
            if routed:
                self.assertEqual(routed[0], 302)
                path = routed[1]
            return (dl / path.lstrip('/')).read_bytes()
        etag = '"fixture-etag"'
        resumed = []
        def ranged(url, start, end, if_range=None, marker=None):
            self.assertIn(if_range, (None, etag))
            self.assertEqual(marker, publish.IMAGE_MARKER)
            if if_range is not None:
                resumed.append(url)
            return fetch(url)[start:end + 1]
        writes = []
        put_new = publish.LocalBackend.put_new
        def put(backend, key, data):
            writes.append(key)
            return put_new(backend, key, data)
        with mock.patch.object(publish.subprocess, 'run', side_effect=command), \
                mock.patch.object(publish, 'stop_gnupg'), \
                mock.patch.object(publish, 'http_get', side_effect=fetch), \
                mock.patch.object(publish, 'http_head', return_value=200), \
                mock.patch.object(publish, 'http_sha256', side_effect=lambda url: publish.sha256(fetch(url))), \
                mock.patch.object(publish, 'http_etag', return_value=etag), \
                mock.patch.object(publish, 'check_image_route') as route, \
                mock.patch.object(publish, 'http_range', side_effect=ranged), \
                mock.patch.object(publish.LocalBackend, 'put_new', put), \
                mock.patch.object(publish.LocalBackend, 'put_large', put):
            publisher.args.arch_sources = None
            with self.assertRaisesRegex(publish.Refused, 'requires --arch-sources'):
                publish.iso_publish(publisher, image, False)
            self.assertEqual(writes, [])
            publisher.args.arch_sources = arch_manifest
            saved_manifest = arch_manifest.read_text()
            target_manifest = json.loads(saved_manifest)
            target_manifest['closure_sha256'] = ('0' * 64 if legacy else publish.sha256(target_closure))
            target_manifest['packages'] = {}
            arch_manifest.write_text(json.dumps(target_manifest))
            with self.assertRaisesRegex(publish.Refused, 'does not describe this image package list'):
                publish.iso_publish(publisher, image, False)
            self.assertEqual(writes, [])
            missing = json.loads(saved_manifest)
            missing['packages'] = {}
            arch_manifest.write_text(json.dumps(missing))
            with self.assertRaisesRegex(publish.Refused, 'missing required packages'):
                publish.iso_publish(publisher, image, False)
            self.assertEqual(writes, [])
            arch_manifest.write_text(saved_manifest)
            if partial:
                missing_path = root / 'MISSING-SOURCES.json'
                binary_record = {key: arch_record[key] for key in
                                 ('name', 'base', 'version', 'binary_sha256', 'recipe_sha256')}
                missing_file = {'schema': 1, 'closure_sha256': publish.sha256(closure), 'bases': [{
                    'base': 'arch-demo', 'version': '1-1', 'recipe_sha256': recipe_sha,
                    'packages': {arch_name: binary_record}, 'reason': 'upstream unavailable',
                    'upstream_url': 'https://upstream.invalid/arch-demo', 'revision': 'b' * 40}]}
                missing_path.write_text(json.dumps(missing_file))
                publisher.args.missing_sources = missing_path
                # A package cannot simultaneously be collected and missing.
                with self.assertRaises(publish.Refused):
                    publish.iso_publish(publisher, image, False)
                arch_manifest.write_text(json.dumps(missing))
                for mutation in ('omitted', 'version', 'closure'):
                    invalid = json.loads(json.dumps(missing_file))
                    if mutation == 'omitted':
                        invalid['bases'] = []
                    elif mutation == 'version':
                        invalid['bases'][0]['version'] = '2-1'
                        invalid['bases'][0]['packages'][arch_name]['version'] = '2-1'
                    else:
                        invalid['closure_sha256'] = '0' * 64
                    missing_path.write_text(json.dumps(invalid))
                    with self.subTest(missing=mutation), self.assertRaises(publish.Refused):
                        publish.iso_publish(publisher, image, False)
                self.assertEqual(writes, [])
                missing_path.write_text(json.dumps(missing_file))
                if legacy:
                    publisher.args = publish.parser().parse_args([
                        '--dl-backend', f'local:{dl}', '--dl-public-url', 'https://download.invalid',
                        'iso', str(image), '--arch-sources', str(arch_manifest),
                        '--missing-sources', str(missing_path)])
                    self.assertEqual(publisher.args.command, 'iso')
                    self.assertFalse(publisher.args.full_check)
            if empty_missing:
                missing_path = root / 'MISSING-SOURCES.json'
                missing_path.write_text(json.dumps({'schema': 1,
                    'closure_sha256': publish.sha256(closure), 'bases': []}))
                publisher.args.missing_sources = missing_path
            publisher.dry_run = True
            backend = publisher.backend
            publisher.backend = publish.DryRunBackend(backend)
            with mock.patch.object(publish, 'say') as messages:
                publish.iso_publish(publisher, image, False)
            if partial:
                self.assertTrue(any('1 missing package' in str(call) for call in messages.call_args_list))
            self.assertEqual(list(dl.iterdir()), [])
            self.assertEqual(writes, [])
            publisher.dry_run = False
            publisher.backend = backend
            publish.iso_publish(publisher, image, False)
            self.assertEqual(resumed, [f'https://download.invalid/iso/{image_version}/{image.name}'])
            route.assert_called_with(f'https://download.invalid/iso/{image_version}/{image.name}')
            if legacy:
                non_sources = [key for key in writes if not key.startswith('sources/')]
                key = f'iso/{image_version}/{image.name}'
                self.assertEqual(non_sources, [f'released/iso/{image_version}',
                    f'iso/{image_version}/emaki-signing-key.asc', key, key + '.sig',
                    f'iso/{image_version}/closure.txt', f'iso/{image_version}/SOURCES-ISO.txt',
                    f'iso/{image_version}/ARCH-SOURCES.json', f'iso/{image_version}/MISSING-SOURCES.json',
                    key + '.sha256'])
                self.assertEqual((dl / f'iso/{image_version}/ARCH-SOURCES.json').read_bytes(),
                                 arch_manifest.read_bytes())
            with mock.patch.object(publish, 'http_get', side_effect=lambda url: b'changed' if url.endswith('/SOURCES-ISO.txt') else fetch(url)):
                with self.assertRaisesRegex(publish.Refused, 'published SOURCES-ISO.txt differs'):
                    publish.iso_publish(publisher, image, False)
            if empty_missing:
                self.assertNotIn(b'Sources not yet copied by Emaki',
                                 fetch(f'https://download.invalid/iso/{image_version}/SOURCES-ISO.txt'))
            if partial and not legacy:
                original_arch = arch_manifest.read_bytes()
                original_missing = missing_path.read_bytes()
                originals = {str(path.relative_to(dl)): path.read_bytes()
                             for path in dl.rglob('*') if path.is_file()}
                arch_manifest.write_text(saved_manifest)
                missing_path.write_text(json.dumps(dict(missing_file, bases=[])))
                response = mock.MagicMock()
                response.headers = {'X-Emaki-Source-Pointer': '1'}
                opener = SimpleNamespace(open=lambda request, **kwargs: response)
                with mock.patch.object(publish.urllib.request, 'build_opener', return_value=opener):
                    publish.iso_add_sources(publisher, image_version)
                arch_manifest.write_bytes(original_arch)
                missing_path.write_bytes(original_missing)
                self.assertNotIn(b'Sources not yet copied by Emaki',
                                 fetch(f'https://download.invalid/iso/{image_version}/SOURCES-ISO.txt'))
                publish.iso_publish(publisher, image, True)
                for name, data in originals.items():
                    self.assertEqual((dl / name).read_bytes(), data)
                snapshot = (dl / f'iso/{image_version}/source-pointer').read_text().strip()
                snapshot_text = dl / f'iso/{image_version}/source-snapshots/{snapshot}/SOURCES-ISO.txt'
                snapshot_text.write_bytes(b'changed')
                with self.assertRaisesRegex(publish.Refused, 'snapshot differs from its digest'):
                    publish.iso_publish(publisher, image, True)
        destination = dl / f'iso/{image_version}'
        self.assertEqual((destination / 'closure.txt').read_bytes(), closure)
        self.assertTrue((destination / 'SOURCES-ISO.txt').read_bytes().startswith(
            publish.sources_text(records, 'https://download.invalid')))
        self.assertNotIn('https://pkgs.emaki.sh/', (destination / 'SOURCES-ISO.txt').read_text())
        for record in records.values():
            self.assertFalse((destination / record['archive']).exists())
            self.assertEqual((dl / publish.source_key(record)).read_bytes(),
                             (root / record['archive']).read_bytes())
            self.assertLess(writes.index(publish.source_key(record)),
                            writes.index(f'iso/{image_version}/{image.name}'))
            self.assertIn(publish.source_key(record), (destination / 'SOURCES-ISO.txt').read_text())
        if partial:
            self.assertEqual((dl / arch_key).exists(), not legacy)
            self.assertEqual((destination / 'MISSING-SOURCES.json').read_bytes(), missing_path.read_bytes())
            text = (destination / 'SOURCES-ISO.txt').read_text()
            self.assertIn('arch-demo 1-1', text)
            self.assertIn('https://upstream.invalid/arch-demo', text)
            self.assertIn('https://gitlab.archlinux.org/archlinux/packaging/packages/arch-demo/-/tree/' + 'b' * 40, text)
            self.assertIn('will be added', text)
            self.assertLess(writes.index(f'iso/{image_version}/MISSING-SOURCES.json'),
                            writes.index(f'iso/{image_version}/{image.name}.sha256'))
        else:
            self.assertEqual((dl / arch_key).read_bytes(), arch_data)
            self.assertLess(writes.index(arch_key), writes.index(f'iso/{image_version}/{image.name}'))
        checksum_index = writes.index(f'iso/{image_version}/{image.name}.sha256')
        for name in ('closure.txt', 'SOURCES-ISO.txt', 'ARCH-SOURCES.json'):
            self.assertLess(writes.index(f'iso/{image_version}/{name}'), checksum_index)

    def test_github_restore_requires_matching_available_sources_before_uploads(self):
        root, publisher = self.directory, self.publisher
        entries = publisher.collect(root)
        records = {entry['name']: entry['source'] for entry in entries.values()}
        publisher.args = SimpleNamespace(github=f'local:{root / "github"}')
        publisher.state = root / 'state'
        publisher.public = 'https://pkgs.emaki.sh'
        publisher.dry_run = False
        publisher.pointer = lambda channel: (None, None)
        github = root / 'github/testing'
        github.mkdir(parents=True)
        (github / 'emaki.db').write_bytes(b'current')
        saved = publisher.state / 'github/testing/20260101T000000Z'
        saved.mkdir(parents=True)
        (saved / 'emaki.db').write_bytes(b'previous')
        (saved / 'SOURCES').write_bytes(publish.sources_text(records))
        (saved / 'SOURCES.json').write_text(json.dumps({'format': 1, 'packages': records}))
        db = {name: {'name': entry['name'], 'version': entry['version']}
              for name, entry in entries.items()}
        hashes = {publisher.public + '/' + publish.source_key(record): record['sha256']
                  for record in records.values()}
        with mock.patch.object(publish, 'db_entries', return_value=db), \
                mock.patch.object(publish, 'http_sha256', side_effect=lambda url: hashes[url]):
            damaged = json.loads((saved / 'SOURCES.json').read_text())
            damaged['packages'][next(iter(records))]['version'] = '0-1'
            (saved / 'SOURCES.json').write_text(json.dumps(damaged))
            with self.assertRaisesRegex(publish.Refused, 'no matching source record'):
                publisher.github('testing', True)
            self.assertEqual((github / 'emaki.db').read_bytes(), b'current')
            self.assertFalse((github / '.upload-log').exists())
            (saved / 'SOURCES.json').write_text(json.dumps({'format': 1, 'packages': records}))
            with mock.patch.object(publish, 'http_sha256', return_value='0' * 64):
                with self.assertRaisesRegex(publish.Refused, 'source archive missing or changed'):
                    publisher.github('testing', True)
            self.assertFalse((github / '.upload-log').exists())
            publisher.github('testing', True)
        self.assertEqual((github / 'emaki.db').read_bytes(), b'previous')
        self.assertEqual((github / 'SOURCES').read_bytes(), publish.sources_text(records))


    def test_github_copies_sources_and_restores_directions_with_database(self):
        root, publisher = self.directory, self.publisher
        entries = publisher.collect(root)
        records = {e['name']: e['source'] for e in entries.values()}
        files = {name: b'database' for name in publish.DB_FILES}
        files.update(SOURCES=publish.sources_text(records))
        files['SOURCES.json'] = json.dumps({'format': 1, 'packages': records}).encode()
        files['MANIFEST'] = publish.manifest_text(files, {f: (e['sha256'], e['sig']) for f, e in entries.items()}, records)
        github = root / 'github/testing'
        github.mkdir(parents=True)
        for name in ('emaki.db', 'emaki.files'):
            (github / name).write_bytes(b'previous database')
        (github / 'SOURCES').write_text('Previous source directions\n')
        (github / 'SOURCES.json').write_text('{"format":1,"packages":{}}')
        publisher.args = SimpleNamespace(github=f'local:{root / "github"}', closure='emaki')
        publisher.confirm_channel = mock.Mock()
        publisher.check_transactions = mock.Mock()
        publisher.dry_run = True
        publisher.state = root / 'state'
        publisher.backend = publish.LocalBackend(root / 'bucket')
        publisher.pointer = lambda channel: ('20260101T000000Z', None)
        publisher.snapshot = lambda *args: files
        publisher.verify_sources = mock.Mock()
        for name, entry in entries.items():
            for suffix in ('', '.sig'):
                publisher.backend.put_new('testing/x86_64/' + name + suffix, root / (name + suffix))
            archive = entry['source']['archive']
            publisher.backend.put_new(publish.source_key(entry['source']), root / archive)
        stale = 'niri-emaki-0.9-1-any.pkg.tar.zst'
        (github / stale).write_bytes(b'old binary')
        (github / (stale + '.sig')).write_bytes(b'old signature')
        before = {p.name: p.read_bytes() for p in github.iterdir()}
        publisher.github('testing', False)
        self.assertEqual({p.name: p.read_bytes() for p in github.iterdir()}, before)
        publisher.dry_run = False
        upload = publish.LocalGithubTarget.upload
        def interrupted(target, tag, path, name):
            if name in publish.SOURCE_FILES:
                for filename, entry in entries.items():
                    self.assertEqual(publish.sha256_file(github / filename), entry['sha256'])
                    self.assertEqual(publish.sha256_file(github / (filename + '.sig')), entry['sig'])
            if name == 'emaki.db':
                for source_name in publish.SOURCE_FILES:
                    self.assertEqual((github / source_name).read_bytes(), files[source_name])
                self.assertEqual((github / '.description').read_text(), publish.source_notes(files['SOURCES'].decode()))
                raise publish.Refused('interrupted before database')
            upload(target, tag, path, name)
        with mock.patch.object(publish.LocalGithubTarget, 'upload', interrupted):
            with self.assertRaisesRegex(publish.Refused, 'interrupted before database'):
                publisher.github('testing', False)
        self.assertEqual((github / 'SOURCES').read_bytes(), files['SOURCES'])
        self.assertEqual((github / 'emaki.db').read_bytes(), before['emaki.db'])
        self.assertTrue((github / stale).exists())
        publisher.github('testing', False)
        self.assertEqual((github / '.description').read_text(), publish.source_notes(files['SOURCES'].decode()))
        for record in records.values():
            self.assertFalse((github / record['archive']).exists())
            self.assertIn(publish.source_key(record), (github / 'SOURCES').read_text())
        self.assertEqual((github / '.upload-log').read_text().splitlines()[-4:], ['SOURCES', 'SOURCES.json', 'emaki.files', 'emaki.db'])
        self.assertFalse((github / stale).exists())
        self.assertTrue((github / next(iter(entries))).exists())
        before_restore = {p.name: p.read_bytes() for p in github.iterdir()}
        with self.assertRaisesRegex(publish.Refused, 'saved binaries lack verified corresponding sources'):
            publisher.github('testing', True)
        self.assertEqual({p.name: p.read_bytes() for p in github.iterdir()}, before_restore)
        publisher.args.allow_sourceless_restore = True
        publisher.github('testing', True)
        self.assertEqual((github / '.description').read_text(), publish.source_notes('Previous source directions\n'))
        self.assertEqual((github / 'SOURCES').read_bytes(), before['SOURCES'])
        self.assertEqual((github / 'emaki.db').read_bytes(), before['emaki.db'])
        self.assertEqual((github / stale).read_bytes(), b'old binary')
        self.assertEqual((github / '.upload-log').read_text().splitlines()[-4:],
                         ['SOURCES', 'SOURCES.json', 'emaki.files', 'emaki.db'])
        # The first bridge had no directions to save; restore must remove the new ones.
        for saved in (publisher.state / 'github/testing').glob('*'):
            for name in publish.SOURCE_FILES:
                (saved / name).unlink(missing_ok=True)
        publisher.github('testing', False)
        for saved in (publisher.state / 'github/testing').glob('*'):
            for name in publish.SOURCE_FILES:
                (saved / name).unlink(missing_ok=True)
        publisher.github('testing', True)
        self.assertFalse((github / 'SOURCES').exists())
        self.assertEqual((github / '.description').read_text(), '')
        self.assertEqual((github / '.upload-log').read_text().splitlines()[-2:], ['emaki.files', 'emaki.db'])



class PublishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        missing = [tool for tool in TOOLS if not shutil.which(tool)]
        if missing:
            raise unittest.SkipTest('missing tools: ' + ', '.join(missing))

    def setUp(self):
        self.addCleanup(no_gnupg_daemons_left, self)  # runs after tearDown has closed the world
        self.world = World()

    def tearDown(self):
        self.world.close()

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def refused(self, result, text):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(text, result.stderr)

    def candidate(self, version, **kwargs):
        directory = self.world.base / f'pkgs-{version}'
        release(directory, self.world.keys, version, **kwargs)
        return directory

    def served_db(self, channel='testing'):
        return self.world.bucket / 'snap' / channel / self.world.pointer(channel) / 'emaki.db'

    def assert_whole(self, machine, *allowed, extra=()):
        """A sync exits 0 and the machine sees exactly one complete release, never a mix."""
        result = machine.sync()
        self.assertEqual(result.returncode, 0, result.stderr)
        code, listing = machine.listed()
        self.assertEqual(code, 0)
        seen = versions(listing)
        self.assertIn(set(seen.values()), [{v} for v in allowed], seen)
        self.assertEqual(set(seen), {'emaki', 'emaki-config', 'emaki-mirrorlist', 'emaki-apps'} | set(extra))
        return next(iter(seen.values()))

    def test_route_table_matches_the_worker(self):
        cases = json.loads((HERE / 'fixtures/pointer-routes.json').read_text())
        for case in cases:
            with self.subTest(case=case):
                root = Path(tempfile.mkdtemp(dir=self.world.base))
                for channel, value in case['pointer'].items():
                    (root / 'pointers').mkdir(exist_ok=True)
                    (root / 'pointers' / channel).write_text(value)
                answer = pointer_server.route(root, case['path'])
                if case['status'] == 'fallthrough':
                    self.assertIsNone(answer)
                else:
                    self.assertEqual(answer[0], case['status'])
                    if case['status'] == 302:
                        self.assertEqual(answer[1], case['location'])

    def test_sources_survive_partial_publish_promotion_and_github(self):
        world = self.world
        directory = self.candidate('1-1', extra=publish.PATCHED_PACKAGES)
        patched_sources(directory)
        self.ok(world.publish('publish', 'testing', directory))
        first = world.bucket / 'snap/testing' / world.pointer('testing')
        records = json.loads((first / 'SOURCES.json').read_bytes())['packages']
        partial = world.base / 'partial'
        world.keys.sign(make_package(partial, 'emaki-apps', '2-1'))
        self.ok(world.publish('publish', 'testing', partial))
        second = world.bucket / 'snap/testing' / world.pointer('testing')
        later = json.loads((second / 'SOURCES.json').read_bytes())['packages']
        for name in publish.PATCHED_PACKAGES:
            self.assertEqual(records[name], later[name])
        self.ok(world.publish('promote', '--first'))
        self.ok(world.publish('github', '--tag', 'testing'))
        github = world.github / 'testing'
        self.assertEqual((github / '.description').read_text(), publish.source_notes((second / 'SOURCES').read_text()))
        for record in later.values():
            self.assertTrue((world.bucket / publish.source_key(record)).is_file())
            self.assertFalse((world.bucket / 'stable/x86_64' / record['archive']).exists())
            self.assertFalse((github / record['archive']).exists())

    def test_publish_serves_a_signed_pair_and_pacman_takes_the_sig_from_the_snapshot(self):
        self.ok(self.world.publish('publish', 'testing', self.candidate('1-1')))
        snap = self.world.pointer('testing')
        files = sorted(p.name for p in (self.world.bucket / 'snap/testing' / snap).iterdir())
        self.assertEqual(files, ['MANIFEST', 'SOURCES', 'SOURCES.json', 'emaki.db', 'emaki.db.sig', 'emaki.files', 'emaki.files.sig'])
        self.assertFalse((self.world.bucket / 'locks/publish').exists())
        machine = self.world.machine()
        self.assertEqual(self.assert_whole(machine, '1-1'), '1-1')
        paths = [r['path'] for r in self.world.requests()]
        # P8: the signature is requested from the redirected address, not from the channel path.
        self.assertIn(f'/snap/testing/{snap}/emaki.db.sig', paths)
        self.assertIn('/testing/x86_64/SOURCES', paths)
        self.assertNotIn('/testing/x86_64/emaki.db.sig', paths)
        status = self.ok(self.world.publish('status')).stdout
        self.assertIn(f'testing: {snap}', status)
        self.ok(self.world.publish('verify', 'testing'))

    def test_a_b_interrupted_after_each_step_never_breaks_a_machine(self):
        world = self.world
        self.ok(world.publish('publish', 'testing', self.candidate('1-1', extra=publish.PATCHED_PACKAGES)))
        held = world.machine()
        self.assert_whole(held, '1-1', extra=publish.PATCHED_PACKAGES)
        previous = '1-1'
        last_second = int(self.served_db().stat().st_mtime)
        for step in range(0, 8):
            version = f'{step + 2}-1'
            candidate = self.candidate(version, extra=publish.PATCHED_PACKAGES)
            with self.subTest(step=step):
                killed = world.publish('publish', 'testing', candidate, kill=step)
                self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stdout + killed.stderr)
                # (a) after the kill: the machine that held the old pair, and a fresh one.
                self.assert_whole(held, previous, version, extra=publish.PATCHED_PACKAGES)
                self.assert_whole(world.machine(), previous, version, extra=publish.PATCHED_PACKAGES)
                # (b) the same command again finishes the publish.
                self.ok(world.publish('publish', 'testing', candidate))
                self.assertEqual(self.assert_whole(held, version, extra=publish.PATCHED_PACKAGES), version)
                self.assertFalse((world.bucket / 'locks/publish').exists())
                # Each served database is newer by at least one second (pacman's If-Modified-Since).
                second = int(self.served_db().stat().st_mtime)
                self.assertGreater(second, last_second)
                last_second = second
            previous = version

    def test_c_a_second_publisher_stops_on_the_lock(self):
        world = self.world
        first = self.candidate('1-1')
        self.assertEqual(world.publish('publish', 'testing', first, kill=1).returncode, -signal.SIGKILL)
        other = world.publish('publish', 'testing', self.candidate('2-1'), state=world.base / 'other-state')
        self.refused(other, 'locks/publish is held')
        self.assertIn('lock: {', self.ok(world.publish('status')).stdout)
        self.refused(world.publish('unlock'), 'unlock --yes')
        self.ok(world.publish('publish', 'testing', first))
        self.assertEqual(self.assert_whole(world.machine(), '1-1'), '1-1')

    def test_d_a_burnt_name_is_refused(self):
        world = self.world
        self.ok(world.publish('publish', 'testing', self.candidate('1-1')))
        rebuilt = self.candidate('1-1b')
        for path in rebuilt.glob('*'):
            path.unlink()
        release(rebuilt, world.keys, '1-1', config_payload='other bytes\n')
        self.refused(world.publish('publish', 'testing', rebuilt), 'already released with other content')
        # A name an ISO's offline repo already carried with other bytes.
        iso_built = self.candidate('2-1')
        world.registry.write_text('0' * 64 + '  emaki-config-2-1-any.pkg.tar.zst  iso:0.1.2-release\n')
        self.refused(world.publish('publish', 'testing', iso_built), 'emaki-config-2-1-any.pkg.tar.zst')
        world.registry.write_text('')
        self.ok(world.publish('publish', 'testing', iso_built))

    def served_versions(self, channel='testing'):
        publish = load('publish', PUBLISH)
        return {e['name']: e['version'] for e in publish.db_entries(self.served_db(channel).read_bytes()).values()}

    def test_identical_package_with_new_signature_keeps_published_signature(self):
        world = self.world
        candidate = self.candidate('1-1')
        patched_sources(candidate)
        self.ok(world.publish('publish', 'testing', candidate))
        package = candidate / 'emaki-1-1-any.pkg.tar.zst'
        signature = Path(str(package) + '.sig')
        original = signature.read_bytes()
        package_digest = publish.sha256_file(package)
        # A notation makes a distinct valid signature even within the same second.
        run(['gpg', '--batch', '--yes', '--local-user', world.keys.fpr,
             '--sig-notation', 'publication@emaki.invalid=second-signature',
             '--detach-sign', '--no-armor', '--output', signature, package],
            env=world.keys.env, check=True)
        self.assertNotEqual(signature.read_bytes(), original)
        verifier = publish.Verifier(world.keys.keyring, world.keys.trusted)
        try:
            self.assertTrue(verifier.verify(package, signature))
        finally:
            verifier.close()
        self.ok(world.publish('publish', 'testing', candidate))
        served = world.bucket / 'testing/x86_64' / package.name
        self.assertEqual(publish.sha256_file(served), package_digest)
        self.assertEqual(Path(str(served) + '.sig').read_bytes(), original)
        manifest = publish.parse_manifest((self.served_db().parent / 'MANIFEST').read_bytes())
        self.assertEqual(manifest[f'packages/{package.name}.sig'], publish.sha256(original))
        # repo-add does not embed package signatures in the database (no --include-sigs):
        # the published detached .sig and the manifest are what clients and verify use.
        self.ok(world.publish('verify', 'testing'))

    def test_a_lower_version_than_the_channel_serves_is_refused(self):
        world = self.world
        self.ok(world.publish('publish', 'testing', self.candidate('2-1')))
        lower = world.base / 'lower'
        world.keys.sign(make_package(lower, 'emaki-mirrorlist', '1-1'))
        self.refused(world.publish('publish', 'testing', lower), 'emaki-mirrorlist 1-1 is lower than 2-1')
        # vercmp, not text: 2-10 is higher than 2-9, 2.10 than 2.9.
        newer = world.base / 'newer'
        world.keys.sign(make_package(newer, 'emaki-mirrorlist', '2-10'))
        self.ok(world.publish('publish', 'testing', newer))
        older = world.base / 'older'
        world.keys.sign(make_package(older, 'emaki-mirrorlist', '2-9'))
        self.refused(world.publish('publish', 'testing', older), 'emaki-mirrorlist 2-9 is lower than 2-10')
        self.assertEqual(self.served_versions()['emaki-mirrorlist'], '2-10')

    def test_same_version_other_bytes_is_refused_under_any_file_name(self):
        world = self.world
        self.ok(world.publish('publish', 'testing', self.candidate('2-1')))
        rebuilt = world.base / 'rebuilt'
        world.keys.sign(make_package(rebuilt, 'emaki-apps', '2-1', arch='x86_64', payload='different bytes\n'))
        self.refused(world.publish('publish', 'testing', rebuilt), 'emaki-apps 2-1 was already released')
        # A version an ISO's offline repository carried under the other arch suffix.
        iso_built = self.candidate('3-1')
        world.registry.write_text('0' * 64 + '  emaki-config-3-1-x86_64.pkg.tar.zst  iso:0.1.2-release\n')
        self.refused(world.publish('publish', 'testing', iso_built), 'emaki-config 3-1 was already released')
        self.assertEqual(self.served_versions()['emaki-apps'], '2-1')

    def test_two_versions_of_one_package_in_a_directory_are_refused(self):
        world = self.world
        directory = world.base / 'pkgout'
        release(directory, world.keys, '1-1')
        for version in ('1-9', '1-10'):
            world.keys.sign(make_package(directory, 'emaki-apps', version))
        self.refused(world.publish('publish', 'testing', directory), 'emaki-apps')
        self.assertIsNone(world.pointer('testing'))

    def test_repo_add_never_downgrades_a_database(self):
        publish = load('publish', PUBLISH)
        work = self.world.base / 'repo'
        work.mkdir()
        high = make_package(work / 'pool', 'emaki-apps', '2-1')
        low = make_package(work / 'pool', 'emaki-apps', '1-1')
        signer = publish.Signer(self.world.keys.fpr, self.world.keys.home)
        signer.repo_add(work / 'emaki.db.tar.gz', [high], sign=False)
        signer.repo_add(work / 'emaki.db.tar.gz', [low], sign=False)
        entries = publish.db_entries((work / 'emaki.db.tar.gz').read_bytes())
        self.assertEqual({e['version'] for e in entries.values()}, {'2-1'})

    def test_build_refuses_an_output_directory_that_is_not_empty(self):
        out = self.world.base / 'build-out'
        out.mkdir()
        empty = run(['bash', ROOT / 'packaging/build.sh', '--out', out, '--dry-run'])
        self.assertEqual(empty.returncode, 0, empty.stderr)
        (out / 'emaki-apps-1-9-any.pkg.tar.zst').write_bytes(b'an earlier build')
        full = run(['bash', ROOT / 'packaging/build.sh', '--out', out, '--dry-run'])
        self.assertEqual(full.returncode, 1, full.stdout + full.stderr)
        self.assertIn('is not empty', full.stderr)

    def test_a_database_left_unsigned_stops_cleanly_every_time(self):
        """repo-add -s only warns and exits 0 when gpg cannot sign (pinentry cancelled or timed
        out). Here gpg may never ask for the passphrase (pinentry-mode error, so no prompt can
        appear on this desktop); every run must stop with the lock free."""
        world = self.world
        world.keys.close()
        world.keys = Keys(world.base / 'protected', passphrase='pw')
        candidate = self.candidate('1-1')  # signed by the test with the passphrase typed in
        (world.keys.home / 'gpg.conf').write_text('pinentry-mode error\n')
        run(['gpgconf', '--homedir', world.keys.home, '--kill', 'all'])  # no cached passphrase
        for attempt in (1, 2):
            with self.subTest(attempt=attempt):
                self.refused(world.publish('publish', 'testing', candidate), 'emaki.db.tar.gz was not signed')
                self.assertFalse((world.bucket / 'locks/publish').exists())
                self.assertNotIn('open journal', self.ok(world.publish('status')).stdout)
                self.assertIsNone(world.pointer('testing'))

    def test_after_an_unsigned_database_the_same_command_publishes(self):
        world = self.world
        candidate = self.candidate('1-1')
        stranger = Keys(world.base / 'stranger', 'Stranger <s@example.invalid>')
        stranger.close()
        world.extra_args = ['--key', stranger.fpr]  # not in the signing home: gpg fails to sign
        self.refused(world.publish('publish', 'testing', candidate), 'was not signed')
        world.extra_args = []
        self.ok(world.publish('publish', 'testing', candidate))
        self.assertEqual(self.assert_whole(world.machine(), '1-1'), '1-1')

    def test_unsigned_dirty_and_passphrase_file_are_refused(self):
        world = self.world
        candidate = self.candidate('1-1')
        (world.tree / 'stray').write_text('x')
        self.refused(world.publish('publish', 'testing', candidate), 'is not clean')
        (world.tree / 'stray').unlink()
        passphrase = world.home / '.config/emaki-signing/passphrase'
        passphrase.parent.mkdir(parents=True)
        passphrase.write_text('secret')
        self.refused(world.publish('publish', 'testing', candidate), 'passphrase must not lie next to the key')
        passphrase.unlink()
        stranger = Keys(world.base / 'stranger', 'Stranger <s@example.invalid>')
        try:
            stranger.sign(next(candidate.glob('emaki-config-*.zst')))
        finally:
            stranger.close()
        self.refused(world.publish('publish', 'testing', candidate), 'not a valid signature by a trusted key')

    def test_closure_must_resolve(self):
        directory = self.world.base / 'broken'
        make_package(directory, 'emaki', '1-1', depends=('emaki-config=9-9',))
        make_package(directory, 'emaki-apps', '1-1')
        for path in directory.glob('*.zst'):
            self.world.keys.sign(path)
        self.refused(self.world.publish('publish', 'testing', directory), 'cannot resolve')

    def stamp_results(self, manifest, hours_ago=0, skip=(), t2_via='old-address', red=(),
                      sizes=('1920x1080', '2560x1600')):
        """skip and red name (run, start) or (run, start, size)."""
        finished = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_ago))
        paths = []
        for run_name in ('T1', 'T2'):
            for start in ('0.1.0', '0.1.1'):
                for size in sizes:
                    if (run_name, start) in skip or (run_name, start, size) in skip:
                        continue
                    path = self.world.base / f'result-{run_name}-{start}-{size}.json'
                    path.write_text(json.dumps({
                        'run': run_name, 'start': start, 'size': size, 'manifest_sha256': manifest,
                        'passed': (run_name, start) not in red and (run_name, start, size) not in red,
                        'finished': finished.isoformat(), 'via': t2_via if run_name == 'T2' else 'github-testing',
                        'arch': {'qt6-base': '6.11.2-3'}}))
                    paths.append(path)
        return paths

    def test_one_red_result_or_a_missing_size_refuses_the_stamp(self):
        world, manifest = self.world, 'a' * 64
        self.refused(world.publish('stamp', *self.stamp_results(manifest, red={('T1', '0.1.1', '2560x1600')})),
                     'T1 from 0.1.1 at 2560x1600 is red')
        self.assertFalse((world.base / 'stamp.json').exists())
        self.refused(world.publish('stamp', *self.stamp_results(manifest, sizes=('1920x1080',))),
                     'at 2560x1600')
        self.refused(world.publish('stamp', *self.stamp_results(manifest, skip={('T2', '0.1.0', '1920x1080')})),
                     'T2 from 0.1.0 at 1920x1080')
        self.ok(world.publish('stamp', *self.stamp_results(manifest)))

    def manifest_of_testing(self):
        snap = self.world.pointer('testing')
        return __import__('hashlib').sha256((self.world.bucket / 'snap/testing' / snap / 'MANIFEST')
                                            .read_bytes()).hexdigest()

    def test_e_promote_has_one_gate(self):
        world = self.world
        self.ok(world.publish('publish', 'testing', self.candidate('1-1')))
        manifest = self.manifest_of_testing()
        self.refused(world.publish('promote'), 'no acceptance stamp')
        self.refused(world.publish('stamp', *self.stamp_results(manifest, skip={('T2', '0.1.0')})),
                     'T2 from 0.1.0')
        self.refused(world.publish('stamp', *self.stamp_results(manifest, hours_ago=30)), 'more than 24 hours')
        self.ok(world.publish('stamp', *self.stamp_results('f' * 64)))
        self.refused(world.publish('promote'), 'not for ' + manifest)
        self.ok(world.publish('stamp', *self.stamp_results(manifest)))
        self.age_stamp(25)
        self.refused(world.publish('promote'), 'valid for 24 hours')
        self.ok(world.publish('stamp', *self.stamp_results(manifest)))
        self.ok(world.publish('promote'))
        testing = world.bucket / 'snap/testing' / world.pointer('testing')
        stable = world.bucket / 'snap/stable' / world.pointer('stable')
        self.assertNotEqual(testing.name, stable.name)
        for name in publish.SNAPSHOT_FILES:
            self.assertEqual((testing / name).read_bytes(), (stable / name).read_bytes(), name)
        self.assertEqual(self.assert_whole(world.machine('stable'), '1-1'), '1-1')

    def age_stamp(self, hours):
        """As if the stamp had been written `hours` earlier: every time in it moves back."""
        path = self.world.base / 'stamp.json'
        stamp = json.loads(path.read_text())
        for key, value in stamp.items():
            if isinstance(value, str):
                try:
                    moment = datetime.datetime.fromisoformat(value)
                except ValueError:
                    continue
                stamp[key] = (moment - datetime.timedelta(hours=hours)).isoformat()
        path.write_text(json.dumps(stamp))

    def test_the_public_lock_names_no_host(self):
        """locks/publish is an object in a public bucket: it must not tell who publishes from where."""
        world = self.world
        candidate = self.candidate('1-1')
        self.assertEqual(world.publish('publish', 'testing', candidate, kill=1).returncode, -signal.SIGKILL)
        lock = (world.bucket / 'locks/publish').read_text()
        self.assertNotIn(socket.gethostname(), lock)
        holder = json.loads(lock)['holder']
        self.assertIn(f'this machine: {holder}', self.ok(world.publish('status')).stdout)
        self.ok(world.publish('publish', 'testing', candidate))

    def test_finishing_never_removes_another_publishers_lock(self):
        """`unlock --yes` on a run believed dead, and another publish took the lock: when the
        first run finishes after all, the lock it removes must be its own only."""
        publish = load('publish', PUBLISH)
        world = self.world
        args = publish.parser().parse_args(['--backend', f'local:{world.bucket}', '--state-dir',
                                            str(world.state), '--public-url', world.url, 'status'])
        publisher = publish.Publisher(args)
        lock = world.bucket / 'locks/publish'
        lock.parent.mkdir(parents=True)
        lock.write_text(json.dumps({'token': 'the other publisher'}))
        journal = {'kind': 'publish', 'channel': 'testing', 'id': '20261005T000000Z', 'token': 'this run',
                   'steps': [0, 1, 2, 3, 4, 5, 6], 'closed': False}
        publisher.finish(journal, {'id': journal['id'], 'kind': 'publish', 'manifest': '0' * 64})
        self.assertEqual(json.loads(lock.read_text())['token'], 'the other publisher')
        lock.write_text(json.dumps({'token': 'this run'}))
        publisher.finish(journal, {'id': journal['id'], 'kind': 'publish', 'manifest': '0' * 64})
        self.assertFalse(lock.exists())

    def test_the_stamp_is_valid_for_24_hours_from_the_runs_not_from_its_writing(self):
        world = self.world
        self.ok(world.publish('publish', 'testing', self.candidate('1-1')))
        self.ok(world.publish('stamp', *self.stamp_results(self.manifest_of_testing(), hours_ago=23)))
        self.age_stamp(2)  # written two hours ago from runs then 21 hours old: they are 25 hours old now
        self.refused(world.publish('promote'), 'valid for 24 hours')
        self.assertIsNone(world.pointer('stable'))

    def test_promote_refused_at_the_lock_checks_the_stamp_again(self):
        world = self.world
        self.ok(world.publish('publish', 'testing', self.candidate('1-1')))
        self.ok(world.publish('stamp', *self.stamp_results(self.manifest_of_testing())))
        lock = world.bucket / 'locks/publish'
        lock.write_text(json.dumps({'holder': 'another machine', 'token': 'someone-else'}))
        self.refused(world.publish('promote'), 'locks/publish is held')
        lock.unlink()
        (world.base / 'stamp.json').unlink()
        self.refused(world.publish('promote'), 'no acceptance stamp')
        self.assertIsNone(world.pointer('stable'))

    def test_a_resumed_promote_checks_the_stamp_before_the_flip(self):
        world = self.world
        self.ok(world.publish('publish', 'testing', self.candidate('1-1')))
        self.ok(world.publish('stamp', *self.stamp_results(self.manifest_of_testing())))
        self.assertEqual(world.publish('promote', kill=5).returncode, -signal.SIGKILL)
        self.age_stamp(25)
        self.refused(world.publish('promote'), 'valid for 24 hours')
        self.assertIsNone(world.pointer('stable'))
        # Refused before the flip: nothing machines read changed, so the run is closed and the lock freed.
        self.assertFalse((world.bucket / 'locks/publish').exists())
        self.assertNotIn('open journal', self.ok(world.publish('status')).stdout)
        self.ok(world.publish('stamp', *self.stamp_results(self.manifest_of_testing())))
        self.ok(world.publish('promote'))
        self.assertEqual(self.assert_whole(world.machine('stable'), '1-1'), '1-1')

    def arch_dbs(self, name, packages):
        """A directory with core.db and extra.db as Arch would serve them on one day."""
        directory = self.world.base / name
        for repo, names in (('core', packages), ('extra', ['arch-filler'])):
            pool = directory / f'{repo}-pool'
            paths = [make_package(pool, package, '1-1') for package in names]
            run(['repo-add', '-q', directory / f'{repo}.db.tar.gz', *paths], check=True)
            (directory / f'{repo}.db').unlink()  # repo-add's symlink
            shutil.copyfile(directory / f'{repo}.db.tar.gz', directory / f'{repo}.db')
        return directory

    def test_a_resumed_publish_checks_the_closure_before_the_flip(self):
        world = self.world
        directory = self.candidate('1-1', extra=('emaki-desktop',))
        for path in directory.glob('emaki-desktop-*'):
            path.unlink()
        make_package(directory, 'emaki-desktop', '1-1', depends=('arch-library',))
        world.keys.sign(next(directory.glob('emaki-desktop-*.zst')))
        # emaki-apps needs emaki-desktop, which needs a library from Arch.
        for path in directory.glob('emaki-apps-*'):
            path.unlink()
        world.keys.sign(make_package(directory, 'emaki-apps', '1-1', depends=('emaki-config', 'emaki-desktop')))
        today = self.arch_dbs('arch-today', ['arch-library'])
        world.extra_args = ['--arch-dbs', today]
        self.assertEqual(world.publish('publish', 'testing', directory, kill=5).returncode, -signal.SIGKILL)
        world.extra_args = ['--arch-dbs', self.arch_dbs('arch-tomorrow', ['another-library'])]
        self.refused(world.publish('publish', 'testing', directory), 'cannot resolve')
        self.assertIsNone(world.pointer('testing'))
        self.assertFalse((world.bucket / 'locks/publish').exists())
        world.extra_args = ['--arch-dbs', today]
        self.ok(world.publish('publish', 'testing', directory))
        self.assertIsNotNone(world.pointer('testing'))

    def test_promote_first_is_allowed_once(self):
        world = self.world
        (world.github / 'stable').mkdir(parents=True)
        self.ok(world.publish('publish', 'testing', self.candidate('1-1')))
        self.ok(world.publish('promote', '--first'))
        self.assertEqual(self.assert_whole(world.machine('stable'), '1-1'), '1-1')
        # The old address now serves this candidate: a released mirrorlist names the mirror.
        self.ok(world.publish('stamp', *self.stamp_results(self.manifest_of_testing())))
        self.ok(world.publish('github'))
        self.ok(world.publish('publish', 'testing', self.candidate('2-1')))
        self.refused(world.publish('promote', '--first'), 'promote --first is spent')
        (world.bucket / 'released/github-stable').rename(world.base / 'hidden-released')
        self.refused(world.publish('promote', '--first'), 'GitHub stable already serves emaki-mirrorlist-1-1')

    def test_the_bridge_needs_t2_by_the_old_address_and_later_releases_do_not(self):
        world = self.world
        (world.github / 'stable').mkdir(parents=True)
        self.ok(world.publish('publish', 'testing', self.candidate('1-1')))
        self.ok(world.publish('promote', '--first'))
        self.ok(world.publish('stamp', *self.stamp_results(self.manifest_of_testing(), t2_via='pkgs-testing')))
        self.refused(world.publish('github'), 'its T2 must be the unedited old address')
        self.ok(world.publish('stamp', *self.stamp_results(self.manifest_of_testing())))
        self.ok(world.publish('github'))
        # Release 2: stable still serves 1-1, so T2 went through pkgs.emaki.sh/testing.
        self.ok(world.publish('publish', 'testing', self.candidate('2-1')))
        self.ok(world.publish('stamp', *self.stamp_results(self.manifest_of_testing(), t2_via='pkgs-testing')))
        self.ok(world.publish('promote'))
        self.ok(world.publish('github'))
        self.assertEqual(self.assert_whole(world.machine('stable'), '2-1'), '2-1')

    def test_the_documented_manual_switch_works_from_a_newer_database(self):
        """docs/updates.md's one line for a machine that missed the bridge or edited its
        mirrorlist, replayed on a machine whose database is newer than stable's (it followed
        testing): its pacman part must not meet the stale signature of P10."""
        world = self.world
        (world.github / 'stable').mkdir(parents=True)
        self.ok(world.publish('publish', 'testing', self.candidate('1-1')))
        self.ok(world.publish('promote', '--first'))
        self.ok(world.publish('publish', 'testing', self.candidate('2-1')))
        text = (ROOT / 'docs/updates.md').read_text()
        # Every documented one-line switch (missed bridge, broken channel selector) must work.
        lines = [l for l in text.splitlines() if l.startswith("echo 'Server = ") and 'sudo pacman' in l]
        self.assertGreaterEqual(len(lines), 1)
        for line in lines:
            with self.subTest(line=line):
                machine = world.machine('testing')
                self.assertEqual(machine.sync().returncode, 0)
                server = re.match(r"echo 'Server = (\S+)'", line).group(1)
                self.assertEqual(server, 'https://pkgs.emaki.sh/stable/$arch')
                machine.set_server(f'{world.url}/stable/x86_64')
                arguments = line.split('sudo pacman', 1)[1].split()
                result = machine.pacman(*arguments)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('error', result.stderr, f'pacman {" ".join(arguments)}: {result.stderr}')
                self.assertEqual(versions(machine.listed()[1])['emaki'], '1-1')

    def test_publish_writes_only_testing(self):
        """stable gets a release only through the stamped promote: `publish stable` would skip the gate."""
        world = self.world
        (world.github / 'stable').mkdir(parents=True)
        self.ok(world.publish('publish', 'testing', self.candidate('1-1')))
        self.ok(world.publish('promote', '--first'))
        self.ok(world.publish('stamp', *self.stamp_results(self.manifest_of_testing())))
        self.ok(world.publish('github'))  # the bridge is out; promote --first is spent
        (world.base / 'stamp.json').unlink()
        before = world.pointer('stable')
        result = world.publish('publish', 'stable', self.candidate('2-1'))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('stable', result.stderr)
        self.assertEqual(world.pointer('stable'), before)
        self.assertFalse(list((world.bucket / 'stable/x86_64').glob('*-2-1-*')))
        publish = load('publish', PUBLISH)
        sub = next(a for a in publish.parser()._actions if a.dest == 'command')
        self.assertEqual(sub.choices['publish']._actions[1].choices, ('testing',))

    def test_f_withdraw_heals_at_the_first_sync_but_does_not_downgrade(self):
        world = self.world
        self.ok(world.publish('publish', 'testing', self.candidate('1-1')))
        self.ok(world.publish('publish', 'testing', self.candidate('2-1')))
        machine = world.machine()
        self.assert_whole(machine, '2-1')
        installed = machine.pacman('-S', 'emaki-config')
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.ok(world.publish('withdraw', 'testing'))
        self.assertEqual(self.assert_whole(machine, '1-1'), '1-1')  # first try, exit 0 (P9)
        upgrade = machine.pacman('-Su', '--print', '--print-format', '%n %v')
        self.assertEqual(upgrade.returncode, 0, upgrade.stderr)
        self.assertEqual(upgrade.stdout.strip(), '')  # P10: the withdrawn version stays installed
        plain = machine.pacman('-Su')
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertIn('there is nothing to do', plain.stdout)
        self.assertIn('emaki-config: local (2-1) is newer than emaki (1-1)', plain.stdout + plain.stderr)
        records = sorted((world.bucket / 'served/testing').iterdir())
        self.assertEqual(json.loads(records[-1].read_text())['kind'], 'withdraw')
        # A second withdraw has no earlier release left that was not withdrawn.
        self.refused(world.publish('withdraw', 'testing'), 'no earlier release')

    def test_g_github_gets_the_mirror_bytes_last_and_never_a_db_signature(self):
        world = self.world
        old = world.github / 'testing'
        old.mkdir(parents=True)
        previous = world.base / 'previous-release'
        run(['repo-add', '-q', previous / 'emaki.db.tar.gz',
             *release(previous, world.keys, '0-1')], check=True)
        old_database = (previous / 'emaki.db.tar.gz').read_bytes()
        (old / 'emaki.db').write_bytes(old_database)
        (old / 'emaki.files').write_bytes((previous / 'emaki.files.tar.gz').read_bytes())
        self.ok(world.publish('publish', 'testing', self.candidate('1-1')))
        self.ok(world.publish('github', '--tag', 'testing'))
        uploads = (old / '.upload-log').read_text().split()
        self.assertEqual(uploads[-2:], ['emaki.files', 'emaki.db'])
        self.assertFalse([name for name in uploads if name.endswith('.db.sig') or name.endswith('.files.sig')])
        self.assertFalse((old / 'emaki.db.sig').exists())
        snap = world.bucket / 'snap/testing' / world.pointer('testing')
        self.assertEqual((old / 'emaki.db').read_bytes(), (snap / 'emaki.db').read_bytes())
        self.refused(world.publish("github"), "no stable snapshot")
        self.refused(world.publish('github', '--tag', 'testing', '--restore'), 'saved binaries lack verified corresponding sources')
        self.ok(world.publish('github', '--tag', 'testing', '--restore', '--allow-sourceless-restore'))
        self.assertEqual((old / 'emaki.db').read_bytes(), old_database)
        # A machine on the old address (no signature) accepts the copied database.
        self.ok(world.publish('github', '--tag', 'testing'))

    def test_github_restore_after_two_runs_restores_what_was_there_before(self):
        world = self.world
        github = world.github / 'stable'
        github.mkdir(parents=True)
        old = world.base / 'old-release'
        run(['repo-add', '-q', old / 'emaki.db.tar.gz', *release(old, world.keys, '0-1')], check=True)
        before = (old / 'emaki.db.tar.gz').read_bytes(), (old / 'emaki.files.tar.gz').read_bytes()
        (github / 'emaki.db').write_bytes(before[0])
        (github / 'emaki.files').write_bytes(before[1])
        self.ok(world.publish('publish', 'testing', self.candidate('1-1')))
        self.ok(world.publish('promote', '--first'))
        self.ok(world.publish('stamp', *self.stamp_results(self.manifest_of_testing())))
        self.ok(world.publish('github'))
        second = self.ok(world.publish('github'))  # a re-run, e.g. after a network error late in the first
        # A backup of the mirror's own bytes, as a run before this check would have left it.
        planted = world.state / 'github/stable/29991231T235959Z'
        planted.mkdir(parents=True)
        shutil.copyfile(github / 'emaki.db', planted / 'emaki.db')
        self.refused(world.publish('github', '--restore'), 'saved binaries lack verified corresponding sources')
        self.ok(world.publish('github', '--restore', '--allow-sourceless-restore'))
        self.assertEqual((github / 'emaki.db').read_bytes(), before[0])
        self.assertEqual((github / 'emaki.files').read_bytes(), before[1])
        self.refused(world.publish('github', '--restore'), 'differs from what GitHub stable serves')
        self.assertIn('already holds', second.stdout)  # the re-run made no backup of the mirror's bytes

    def test_github_stable_needs_the_stamp(self):
        world = self.world
        self.ok(world.publish('publish', 'testing', self.candidate('1-1')))
        self.ok(world.publish('promote', '--first'))
        self.refused(world.publish('github'), 'no acceptance stamp')

    def test_dry_run_writes_nothing(self):
        world = self.world
        self.ok(world.publish('publish', 'testing', self.candidate('1-1')))
        before = sorted(p.relative_to(world.bucket) for p in world.bucket.rglob('*'))
        result = self.ok(world.publish('--dry-run', 'publish', 'testing', self.candidate('2-1')))
        self.assertIn('DRY-RUN: would PUT (If-None-Match: *) testing/x86_64/emaki-config-2-1-any.pkg.tar.zst',
                      result.stdout)
        self.assertIn('DRY-RUN: would PUT (If-Match:', result.stdout)
        for command in (('--dry-run', 'promote', '--first'), ('--dry-run', 'withdraw', 'testing', '--to',
                                                               world.pointer('testing')),
                        ('--dry-run', 'github', '--tag', 'testing'), ('--dry-run', 'unlock', '--yes')):
            self.ok(world.publish(*command))
        after = sorted(p.relative_to(world.bucket) for p in world.bucket.rglob('*'))
        self.assertEqual(before, after)
        self.assertFalse((world.github / 'testing').exists())
        self.assertEqual(len(list(world.state.glob('*/journal.json'))), 1, 'a dry run opened no journal')

    def test_sign_subcommand_signs_only_unsigned_packages(self):
        directory = self.world.base / 'unsigned'
        make_package(directory, 'emaki', '1-1')
        env = dict(os.environ, HOME=str(self.world.home))
        result = run([sys.executable, PUBLISH, '--key', self.world.keys.fpr, '--signing-home', self.world.keys.home,
                      '--keyring', self.world.keys.keyring, '--trusted', self.world.keys.trusted,
                      'sign', directory], env=env)
        self.ok(result)
        self.assertTrue((directory / 'emaki-1-1-any.pkg.tar.zst.sig').is_file())

    def fake_iso(self, version, packages, extra=(), label='a', payload=None):
        """An ISO 9660 image with an offline repository like the real one: Emaki packages plus
        an Arch package the check must ignore, and more than 1 MiB of other payload."""
        stage = self.world.base / f'iso-stage-{label}'
        repo = stage / 'emaki/repo'
        repo.mkdir(parents=True)
        for path in list(Path(packages).glob('*.pkg.tar.zst')) + list(extra):
            shutil.copy(path, repo)
        make_package(repo, 'mesa', '26.2.4-1')
        (repo / 'closure.txt').write_text(''.join(p.name + '\n' for p in sorted(repo.glob('*.pkg.tar.zst'))))
        module = load('iso_source_fixture', ROOT / 'packaging/mirror/iso_sources.py')
        shutil.copyfile(repo / 'closure.txt', repo.parent / 'live-closure.txt')
        (repo.parent / 'pkglist.x86_64.txt').write_text(''.join(
            ' '.join(publish.package_version(path.name)) + '\n'
            for path in sorted(repo.glob('*.pkg.tar.zst'))))
        module.write_image_inventory(repo.parent / 'live-closure.txt', repo,
                                     repo.parent / 'live-packages.json', publish.EMAKI_PACKAGES)
        run(['repo-add', repo / 'emaki-offline.db.tar.gz',
             *sorted(repo.glob('*.pkg.tar.zst'))], check=True)
        (stage / 'payload').write_bytes(payload or os.urandom(3 * 1024 * 1024))
        image = self.world.base / f'iso-{label}' / f'emaki-{version}-x86_64.iso'
        image.parent.mkdir()
        run(['bsdtar', '--format=iso9660', '-cf', image, '-C', stage, '.'], check=True)
        return image

    def test_iso_after_the_yearly_key_extension(self):
        """The yearly expiry extension gives the key a new self-signature, so its export changes
        bytes; the next ISO must still be publishable, next to the new key."""
        world = self.world
        dl = world.base / 'dl-bucket'
        dl.mkdir()
        server = pointer_server.serve(dl)
        try:
            world.extra_args = ['--dl-backend', f'local:{dl}',
                                '--dl-public-url', f'http://127.0.0.1:{server.server_address[1]}']
            packages = self.candidate('1-1')
            self.ok(world.publish('publish', 'testing', packages))
            self.ok(world.publish('promote', '--first'))
            self.ok(world.publish('stamp', *self.stamp_results(self.manifest_of_testing())))
            self.ok(world.publish('iso', self.fake_iso('9.9.8', packages, label='a')))
            first_key = next(dl.rglob('emaki-signing-key.asc')).read_bytes()
            run(['gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '', '--quick-set-expire',
                 world.keys.fpr, '3d'], env=world.keys.env, check=True)
            world.keys.keyring.write_bytes(run(['gpg', '--export', world.keys.fpr], env=world.keys.env,
                                               check=True, text=False).stdout)
            result = self.ok(world.publish('iso', self.fake_iso('9.9.9', packages, label='b')))
            self.assertNotIn('pkgrel', result.stdout + result.stderr)
            keys = {p.parent.name: p.read_bytes() for p in dl.rglob('emaki-signing-key.asc')}
            self.assertEqual(keys['9.9.8'], first_key)
            self.assertNotEqual(keys['9.9.9'], first_key)
            listing = run(['gpg', '--show-keys', '--with-colons', dl / 'iso/9.9.9/emaki-signing-key.asc'],
                          env=world.keys.env).stdout  # the test's home, never the person's ~/.gnupg
            self.assertIn(world.keys.fpr, listing)
            # An interrupted run had uploaded the key as it was before the extension: it still
            # verifies the image, so it stays.
            (dl / 'iso/9.9.7').mkdir(parents=True)
            (dl / 'iso/9.9.7/emaki-signing-key.asc').write_bytes(first_key)
            kept = self.ok(world.publish('iso', self.fake_iso('9.9.7', packages, label='c')))
            self.assertIn('kept the copy an interrupted run uploaded', kept.stdout)
            # Another key next to the image is never accepted.
            stranger = Keys(world.base / 'stranger', 'Stranger <s@example.invalid>')
            stranger.close()
            (dl / 'iso/9.9.6').mkdir(parents=True)
            (dl / 'iso/9.9.6/emaki-signing-key.asc').write_bytes(stranger.keyring.read_bytes())
            self.refused(world.publish('iso', self.fake_iso('9.9.6', packages, label='d')),
                         'iso/9.9.6/emaki-signing-key.asc already exists with other bytes')
            self.assertFalse((dl / 'iso/9.9.6/emaki-9.9.6-x86_64.iso').exists())
        finally:
            server.shutdown()
            server.server_close()

    def test_an_iso_spends_promote_first(self):
        """An image installs machines whose mirrorlist names pkgs.emaki.sh/stable: once one is
        out, stable may move only through the stamped promote, even if GitHub never got a release."""
        world = self.world
        dl = world.base / 'dl-bucket'
        dl.mkdir()
        server = pointer_server.serve(dl)
        try:
            world.extra_args = ['--dl-backend', f'local:{dl}',
                                '--dl-public-url', f'http://127.0.0.1:{server.server_address[1]}']
            packages = self.candidate('1-1')
            self.ok(world.publish('publish', 'testing', packages))
            self.ok(world.publish('promote', '--first'))
            self.ok(world.publish('stamp', *self.stamp_results(self.manifest_of_testing())))
            self.ok(world.publish('iso', self.fake_iso('9.9.9', packages)))
            self.ok(world.publish('publish', 'testing', self.candidate('2-1')))
            before = world.pointer('stable')
            self.refused(world.publish('promote', '--first'), 'promote --first is spent')
            self.assertEqual(world.pointer('stable'), before)
        finally:
            server.shutdown()
            server.server_close()

    def test_iso_publication_proves_that_a_broken_browser_download_resumes(self):
        world = self.world
        dl = world.base / 'dl-bucket'
        dl.mkdir()
        packages = self.candidate('1-1')
        self.ok(world.publish('publish', 'testing', packages))
        self.ok(world.publish('promote', '--first'))
        self.ok(world.publish('stamp', *self.stamp_results(self.manifest_of_testing())))
        image = self.fake_iso('9.9.9', packages)
        port = 0
        # The same address both times: the source directions name it. Without the Worker in front
        # of the image nothing is uploaded; with it the same command publishes.
        for base, works in ((BucketDomain, False), (None, True)):
            server = pointer_server.serve(dl, port=port, base=base)
            port = server.server_address[1]
            try:
                world.extra_args = ['--dl-backend', f'local:{dl}',
                                    '--dl-public-url', f'http://127.0.0.1:{server.server_address[1]}']
                result = world.publish('iso', image)
                if works:
                    self.assertIn('a resumed download (If-Range) answers 206', self.ok(result).stdout)
                else:
                    self.refused(result, 'was not answered by the download Worker (no X-Emaki-Image: 1')
                    self.assertEqual(list(dl.iterdir()), [])
                    self.assertFalse((world.bucket / 'released/iso/9.9.9').exists())
            finally:
                server.shutdown()
                server.server_close()

    def test_an_image_left_by_an_interrupted_run_is_compared_whole(self):
        world = self.world
        dl = world.base / 'dl-bucket'
        dl.mkdir()
        server = pointer_server.serve(dl)
        try:
            world.extra_args = ['--dl-backend', f'local:{dl}',
                                '--dl-public-url', f'http://127.0.0.1:{server.server_address[1]}']
            packages = self.candidate('1-1')
            self.ok(world.publish('publish', 'testing', packages))
            self.ok(world.publish('promote', '--first'))
            self.ok(world.publish('stamp', *self.stamp_results(self.manifest_of_testing())))
            marker = os.urandom(64)
            first = self.fake_iso('9.9.9', packages, label='a',
                                  payload=os.urandom(2 << 20) + marker + os.urandom(3 << 20))
            data = bytearray(first.read_bytes())
            at = data.find(marker)
            self.assertTrue((1 << 20) < at < len(data) - (1 << 20), at)
            data[at:at + 64] = os.urandom(64)  # another build: same first and last MiB
            second = world.base / 'iso-b' / first.name
            second.parent.mkdir()
            second.write_bytes(bytes(data))
            published = dl / 'iso/9.9.9'
            published.mkdir(parents=True)
            shutil.copy(first, published / first.name)  # the earlier run died right after the image
            self.refused(world.publish('iso', second), 'exists and is another image')
            self.assertEqual(sorted(p.name for p in published.iterdir()), [first.name, 'emaki-signing-key.asc'])
            result = self.ok(world.publish('iso', first))
            self.assertIn('already uploaded by an interrupted run', result.stdout)
            self.ok(run(['sha256sum', '-c', first.name + '.sha256'], cwd=published))
        finally:
            server.shutdown()
            server.server_close()

    def test_h_iso_needs_its_packages_in_stable_and_uploads_the_checksum_last(self):
        world = self.world
        dl = world.base / 'dl-bucket'
        dl.mkdir()
        server = pointer_server.serve(dl)
        try:
            world.extra_args = ['--dl-backend', f'local:{dl}',
                                '--dl-public-url', f'http://127.0.0.1:{server.server_address[1]}']
            packages = self.candidate('1-1')
            self.ok(world.publish('publish', 'testing', packages))
            self.ok(world.publish('promote', '--first'))
            image = self.fake_iso('9.9.9', packages)
            self.refused(world.publish('iso', image), 'no acceptance stamp')
            self.ok(world.publish('stamp', *self.stamp_results(self.manifest_of_testing())))
            dry = self.ok(world.publish('--dry-run', 'iso', image)).stdout
            writes = re.findall(r'^DRY-RUN: would PUT \(If-None-Match: \*\) (\S+)', dry, re.M)
            key = 'iso/9.9.9/emaki-9.9.9-x86_64.iso'
            self.assertRegex(writes[0], r'^sources/sha256/[0-9a-f]{64}/mesa\.src\.tar\.gz$')
            self.assertEqual(writes[1], writes[0] + '.verified')
            source_writes = [name for name in writes if name.startswith('sources/sha256/')]
            self.assertGreater(len(source_writes), 2)
            self.assertEqual(writes[len(source_writes):], ['released/iso/9.9.9', 'iso/9.9.9/emaki-signing-key.asc', key, key + '.sig',
                                      'iso/9.9.9/closure.txt', 'iso/9.9.9/SOURCES-ISO.txt',
                                      'iso/9.9.9/ARCH-SOURCES.json', key + '.sha256'])
            self.assertEqual(list(dl.iterdir()), [])
            self.ok(world.publish('iso', image))
            published = dl / 'iso/9.9.9'
            self.assertEqual(sorted(p.name for p in published.iterdir()),
                             ['ARCH-SOURCES.json', 'SOURCES-ISO.txt', 'closure.txt', image.name,
                              image.name + '.sha256', image.name + '.sig', 'emaki-signing-key.asc'])
            line = (published / (image.name + '.sha256')).read_text()
            self.assertRegex(line, r'^[0-9a-f]{64}  emaki-9\.9\.9-x86_64\.iso\n$')
            self.ok(run(['sha256sum', '-c', image.name + '.sha256'], cwd=published))
            home = world.base / 'verify-home'
            home.mkdir(mode=0o700)
            try:
                run(['gpg', '--homedir', home, '--batch', '--import', published / 'emaki-signing-key.asc'],
                    check=True)
                verified = run(['gpg', '--homedir', home, '--batch', '--status-fd', '1', '--verify',
                                published / (image.name + '.sig'), published / image.name])
            finally:
                run(['gpgconf', '--homedir', home, '--kill', 'all'])
            self.assertIn(f'VALIDSIG', verified.stdout)
            self.assertIn(world.keys.fpr, verified.stdout)
            self.assertIn('already published with these bytes', self.ok(world.publish('iso', image)).stdout)
            other = self.fake_iso('9.9.9', packages, label='b')
            self.refused(world.publish('iso', other), 'never replaced')
            # PU-03: an image whose Emaki packages stable does not serve.
            # An image carrying an Emaki package version stable does not serve is refused before
            # any upload; the image transaction gate sees the marker's exact pin fail first.
            newer = world.base / 'newer'
            make_package(newer, 'emaki-config', '9-9')
            mixed = self.world.base / 'mixed'
            mixed.mkdir()
            for path in packages.glob('*.pkg.tar.zst'):
                if not path.name.startswith('emaki-config-'):
                    shutil.copy(path, mixed)
            self.refused(world.publish('iso', self.fake_iso('9.9.8', mixed, newer.glob('*.zst'), 'c')),
                         "unable to satisfy dependency 'emaki-config=1-1' required by emaki")
            changed = world.base / 'changed'
            make_package(changed, 'emaki-config', '1-1', payload='other bytes\n')
            self.refused(world.publish('iso', self.fake_iso('9.9.7', mixed, changed.glob('*.zst'), 'd')),
                         'emaki-config-1-1-any.pkg.tar.zst (other bytes)')
        finally:
            server.shutdown()
            server.server_close()

    def test_iso_sums_without_a_key_writes_only_the_checksum(self):
        image = self.world.base / 'emaki-1.2.3-x86_64.iso'
        image.write_bytes(b'image')
        result = run([ROOT / 'packaging/mirror/iso-sums.sh', image], env=dict(os.environ, EMAKI_ISO_SIGN_KEY=''))
        self.ok(result)
        self.assertEqual(Path(str(image) + '.sha256').read_text(),
                         __import__('hashlib').sha256(b'image').hexdigest() + '  emaki-1.2.3-x86_64.iso\n')
        self.assertFalse(Path(str(image) + '.sig').exists())
        self.assertIn('iso-sums.sh', (ROOT / 'iso/build.sh').read_text())

    def test_docs_name_only_commands_that_exist(self):
        """Every document in docs/ that this tree has and that gives publish.sh commands."""
        documents = [path for path in sorted((ROOT / 'docs').glob('*.md')) if 'publish.sh ' in path.read_text()]
        self.assertIn(ROOT / 'docs/mirror.md', documents)
        for path in documents:
            with self.subTest(document=path.name):
                self.names_only_commands_that_exist(path.read_text())

    def names_only_commands_that_exist(self, text):
        publish = load('publish', PUBLISH)
        sub = next(a for a in publish.parser()._actions if a.dest == 'command')
        used = set(re.findall(r'publish\.sh (?:--[\w-]+(?: (?!publish|status|sign|promote|github|stamp|iso|verify'
                              r'|withdraw|unlock)\S+)? )*([a-z-]+)', text))
        self.assertTrue(used)
        self.assertLessEqual(used, set(sub.choices), used - set(sub.choices))
        options = set(re.findall(r'upgrade-check\.sh[^`]*?((?:--[a-z-]+ ?)+)', text))
        check_source = (HERE / 'vm/upgrade-check.py').read_text()
        for option in set(re.findall(r'--[a-z-]+', ' '.join(options))):
            self.assertIn(f"'{option}'", check_source, option)
        for fixture in re.findall(r'fixture `([a-z0-9.-]+)`', text) + ['upgrade']:
            self.assertTrue((HERE / f'vm/fixtures/plan-{fixture}.json').is_file(), fixture)
        for option in re.findall(r'publish-drill\.sh ((?:--[a-z]+ \S+ ?)+)', text):
            for name in re.findall(r'--[a-z]+', option):
                self.assertIn(f'{name})', (HERE / 'publish-drill.sh').read_text(), name)

    def test_registry_in_git_burns_every_name_built_twice(self):
        publish = load('publish', PUBLISH)
        registry = publish.load_registry(ROOT / 'packaging/mirror/released-packages.sha256')
        # The two 0.1.2 ISOs carry two different emaki-config-0.1.2-1.
        self.assertEqual(registry['emaki-config-0.1.2-1-x86_64.pkg.tar.zst'],
                         {'e054b30d0b464eb42308722306a8f2ae5e345b67f767374819678ed13bad4ccb',
                          '431f32408cd5be2df861875ed3b7119805ab5ce7c2011a4a460cd355e2cc50b4'})
        burnt = {name for name, digests in registry.items() if len(digests) > 1}
        for name in ('emaki-0.1.2-1-any.pkg.tar.zst', 'emaki-apps-0.1.2-1-any.pkg.tar.zst',
                     'xdg-desktop-portal-gnome-emaki-50.0-1-x86_64.pkg.tar.zst',
                     'emaki-mirrorlist-0.1.2-1-any.pkg.tar.zst', 'emaki-keyring-0.1.2-1-any.pkg.tar.zst'):
            self.assertIn(name, burnt)
        # The forks were never rebuilt: one build each, publishable again as they are.
        self.assertEqual(len(registry['niri-emaki-26.04-7-x86_64.pkg.tar.zst']), 1)
        self.assertEqual(len(registry['quickshell-emaki-0.3.1-1-x86_64.pkg.tar.zst']), 1)
        # What GitHub stable serves today is in the registry with the bytes it serves.
        self.assertIn('3cf492690d5e5512e43c2495f8ac265f668b947a48481e51750501e453a69b5a',
                      registry['emaki-config-0.1.1-1-x86_64.pkg.tar.zst'])

    def test_no_passphrase_in_any_publishing_script(self):
        offenders = []
        for path in list((ROOT / 'packaging').glob('*.sh')) + list((ROOT / 'packaging/mirror').glob('*')):
            if path.is_file():
                text = path.read_text(errors='replace')
                for needle in ('--passphrase', 'passphrase-file', '--pinentry-mode loopback', "'loopback'"):
                    if needle in text:
                        offenders.append(f'{path.name}: {needle}')
        self.assertEqual(offenders, [])


class DrillSyncWait(unittest.TestCase):
    def test_socket_root_ignores_a_long_tmpdir(self):
        source = (HERE / 'publish-drill.sh').read_text()
        setup = source[source.index('work=$(mktemp'):source.index('# The client:')]
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / ('long-evidence-' * 12)
            evidence.mkdir()
            script = "log=''\nfakeroot() { :; }\n" + setup + '\nprintf "%s\\n" "$work" "$TMPDIR"\n'
            result = run(['bash', '-eu', '-c', script], env=dict(os.environ, TMPDIR=str(evidence)))
            self.assertEqual(result.returncode, 0, result.stderr)
            work, runtime = map(Path, result.stdout.splitlines())
            self.assertEqual(work.parent, evidence)
            self.assertEqual(runtime.parent.parent, Path('/tmp'))
            self.assertLess(len(os.fsencode(runtime)) + 60, 108)
            self.assertFalse(runtime.exists())

    def test_waits_for_completed_syncs_from_a_slow_client(self):
        source = (HERE / 'publish-drill.sh').read_text()
        helpers = source[source.index('fail() {'):source.index('while (($#)); do')]
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'drill.log'
            script = helpers + r"""
log=$1
: >"$log"
( sleep 1.2; printf '00:00:00 sync 1\n' >>"$log";
  sleep 0.2; printf '00:00:01 sync 1\n' >>"$log" ) &
client_pid=$!
trap 'kill "$client_pid" 2>/dev/null || true; wait "$client_pid" 2>/dev/null || true' EXIT
wait_syncs 2
[[ $(grep -c ' sync ' "$log") == 2 ]]
"""
            result = run(['bash', '-eu', '-c', script, 'drill-wait', log], timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class DrillScript(unittest.TestCase):
    """tests/publish-drill.sh (W3b) against the local server: it passes on the snapshot layout
    and fails on the old flat layout, so its PASS on the real bucket means something."""

    @classmethod
    def setUpClass(cls):
        missing = [tool for tool in TOOLS + ('curl',) if not shutil.which(tool)]
        if missing:
            raise unittest.SkipTest('missing tools: ' + ', '.join(missing))

    def setUp(self):
        self.addCleanup(no_gnupg_daemons_left, self)

    def drill(self, flat):
        world = World(flat=flat)
        try:
            packages = world.base / 'candidate'
            release(packages, world.keys, '1-1')
            log = world.base / 'drill.log'
            env = dict(os.environ, HOME=str(world.home), GNUPGHOME=str(world.keys.home))
            result = run([HERE / 'publish-drill.sh', '--packages', packages, '--url', world.url,
                          '--keyring', world.keys.keyring, '--trusted', world.keys.trusted, '--interval', '0.3',
                          '--log', log, '--', *world.args()], env=env)
            return result, log.read_text() if log.exists() else ''
        finally:
            world.close()

    def test_drill_passes_on_snapshots(self):
        result, log = self.drill(flat=False)
        failures = [i for i, line in enumerate(log.splitlines()) if re.search(r' sync [1-9]', line)]
        first = '\n'.join(log.splitlines()[max(0, failures[0] - 12):failures[0] + 8]) if failures else ''
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr + first)
        self.assertIn('failed=0 second_publisher_refused=1 client_on_served_db=yes', log)
        self.assertGreater(int(log.split('RESULT syncs=')[1].split()[0]), 20)

    def test_drill_fails_on_the_flat_layout(self):
        result, log = self.drill(flat=True)
        self.assertNotEqual(result.returncode, 0, log[-3000:])
        self.assertRegex(log, r' sync [1-9]')
        self.assertIn('signature', log)


class FlatLayoutSelfCheck(unittest.TestCase):
    """The old procedure (db and signature written in place) must break a syncing machine when
    it is interrupted between the two writes. If this ever passes, case (a) proves nothing."""

    @classmethod
    def setUpClass(cls):
        missing = [tool for tool in TOOLS if not shutil.which(tool)]
        if missing:
            raise unittest.SkipTest('missing tools: ' + ', '.join(missing))

    def setUp(self):
        self.addCleanup(no_gnupg_daemons_left, self)

    def test_flat_layout_goes_red(self):
        world = World(flat=True)
        try:
            first = world.base / 'one'
            release(first, world.keys, '1-1')
            result = world.publish('publish', 'testing', first)
            self.assertEqual(result.returncode, 0, result.stderr)
            machine = world.machine()
            self.assertEqual(machine.sync().returncode, 0)
            # Past the second of the database the machine holds; otherwise its If-Modified-Since
            # gets a 304 and the old pair stays consistent (observed), hiding what this checks.
            time.sleep(1.1)
            second = world.base / 'two'
            release(second, world.keys, '2-1')
            killed = world.publish('publish', 'testing', second, kill=4)
            self.assertEqual(killed.returncode, -signal.SIGKILL)
            broken = machine.sync()
            self.assertNotEqual(broken.returncode, 0, 'the in-place layout survived an interrupted publish: '
                                + json.dumps(world.requests()[-6:]))
            self.assertIn('signature', broken.stderr)
        finally:
            world.close()


# Stand-ins for the tools packaging/build.sh runs in the build VM. pacman -Q: no Emaki package is
# installed; pacman -T: the dependencies named in "missing" are not installed. makepkg reads
# ./srcinfo; with --syncdeps it installs what pacman -T reports, except a package of this build,
# which is in no repository.
BUILD_STUBS = {
    'systemd-detect-virt': 'echo kvm\n',
    'gpg': 'exit 0\n',
    'pacman': '''case $1 in
    -Q) exit 1 ;;
    -T) shift
        status=0
        for dep; do
            if grep -qxF -- "${dep%%[<>=]*}" "$BUILD_TEST_STATE/missing"; then echo "$dep"; status=127; fi
        done
        exit "$status" ;;
esac
exit 2
''',
    'makepkg': r'''set -eu
field() { sed -n "s/^\t\{0,1\}$1 = //p" srcinfo | head -n 1; }
[[ " $* " != *' --printsrcinfo '* ]] || { cat srcinfo; exit 0; }
file=$PKGDEST/$(field pkgname)-$(field pkgver)-$(field pkgrel)-x86_64.pkg.tar.zst
[[ " $* " != *' --packagelist '* ]] || { echo "$file"; exit 0; }
echo "$(field pkgname) $*" >>"$BUILD_TEST_STATE/makepkg.calls"
if [[ " $* " == *' --syncdeps '* ]]; then
    mapfile -t deps < <(sed -n 's/^\t\(make\|check\)\{0,1\}depends = //p' srcinfo)
    for dep in $(pacman -T "${deps[@]}"); do
        if grep -qxF -- "${dep%%[<>=]*}" "$BUILD_TEST_STATE/no-repository"; then
            echo "error: target not found: ${dep%%[<>=]*}" >&2
            exit 1
        fi
    done
fi
echo package >"$file"
''',
}


def srcinfo(name, version, depends):
    pkgver, pkgrel = version.split('-')
    lines = [f'pkgbase = {name}', f'\tpkgver = {pkgver}', f'\tpkgrel = {pkgrel}', '\tmakedepends = git']
    return '\n'.join(lines + [f'\tdepends = {dep}' for dep in depends] + ['', f'pkgname = {name}', ''])


class BuildScript(unittest.TestCase):
    """packaging/build.sh chooses --syncdeps or --nodeps per package. Runs a copy of it in a
    throwaway checkout with the stand-ins above; the real build runs only in the build VM."""

    @classmethod
    def setUpClass(cls):
        missing = [tool for tool in ('git', 'vercmp', 'sha256sum') if not shutil.which(tool)]
        if missing:
            raise unittest.SkipTest('missing tools: ' + ', '.join(missing))
        if os.geteuid() == 0:
            raise unittest.SkipTest('packaging/build.sh refuses root, as makepkg does')

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='emaki-build-test-')
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.tree, self.state, self.bin = self.base / 'tree', self.base / 'state', self.base / 'bin'
        for folder in (self.tree / 'packaging', self.state, self.bin):
            folder.mkdir(parents=True)
        shutil.copy2(ROOT / 'packaging/build.sh', self.tree / 'packaging/build.sh')
        (self.tree / 'packaging/source_archive.py').write_text("""import json, sys
from pathlib import Path
if sys.argv[1] == 'vendor':
    raise SystemExit(0)
args = dict(zip(sys.argv[2::2], sys.argv[3::2]))
output = Path(args['--output'])
name = Path(args['--recipe']).name
metadata_path = output / 'SOURCES.json'
metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {
    'format': 1, 'commit': args['--commit'], 'packages': {}}
archive = f"{name}-{args['--version']}.sources.tar.gz"
(output / archive).write_bytes(b'source fixture')
metadata['packages'][name] = {'version': args['--version'], 'archive': archive}
metadata_path.write_text(json.dumps(metadata))
""")
        for name, body in BUILD_STUBS.items():
            (self.bin / name).write_text('#!/bin/bash\n' + body)
            (self.bin / name).chmod(0o755)
        recipes = sorted(path.parent.name for path in (ROOT / 'packaging').glob('*/PKGBUILD'))
        (self.state / 'no-repository').write_text(''.join(f'{name}\n' for name in recipes))

    def recipes(self, installer_depends=('python', 'emaki-config', 'archinstall=4.5-1')):
        for name, depends in (('emaki-config', ('python', 'niri-emaki>=26.04-6')),
                              ('emaki-installer', installer_depends)):
            (self.tree / 'packaging' / name).mkdir()
            (self.tree / 'packaging' / name / 'srcinfo').write_text(srcinfo(name, '0.1.2-1', depends))
        git = ['git', '-C', self.tree, '-c', 'user.name=t', '-c', 'user.email=t@example.org',
               '-c', 'commit.gpgsign=false']
        run([*git, 'init', '-q'], check=True)
        run([*git, 'add', '-A'], check=True)
        run([*git, 'commit', '-q', '-m', 'recipes'], check=True)

    def build(self, missing, *only):
        """Run build.sh for ONLY with MISSING not installed; the result and makepkg's builds."""
        (self.state / 'missing').write_text(''.join(f'{name}\n' for name in missing))
        env = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}', BUILD_TEST_STATE=str(self.state),
                   TMPDIR=str(self.base))
        result = run(['bash', self.tree / 'packaging/build.sh', '--out', self.base / 'out',
                      *[arg for name in only for arg in ('--only', name)]], env=env)
        calls = self.state / 'makepkg.calls'
        lines = calls.read_text().splitlines() if calls.exists() else []
        for log in sorted((self.base / 'out/logs').glob('*.log')):
            result.stderr += f'--- {log.name}\n{log.read_text()}'
        return result, {line.split()[0]: line.split()[1:] for line in lines}

    def test_the_installer_builds_after_emaki_config_without_syncdeps(self):
        """emaki-installer depends on emaki-config, which no repository has: with --syncdeps
        makepkg stopped at "target not found: emaki-config" (P2-B3)."""
        self.recipes()
        result, calls = self.build(['emaki-config'], 'emaki-config', 'emaki-installer')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(calls), ['emaki-config', 'emaki-installer'])
        self.assertIn('--nodeps', calls['emaki-installer'])
        self.assertNotIn('--syncdeps', calls['emaki-installer'])
        self.assertIn('emaki-installer: --nodeps: emaki-config 0.1.2-1 built in this run', result.stdout)

    def test_a_missing_arch_package_still_goes_through_syncdeps(self):
        self.recipes()
        result, calls = self.build(['archinstall'], 'emaki-config', 'emaki-installer')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('--syncdeps', calls['emaki-installer'])

    def test_a_dependency_this_run_did_not_build_stops_before_makepkg(self):
        self.recipes()
        result, calls = self.build(['emaki-config'], 'emaki-installer')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('emaki-installer needs emaki-config, which this run did not build', result.stderr)
        self.assertEqual(calls, {})

    def test_a_built_version_that_does_not_satisfy_stops(self):
        self.recipes(installer_depends=('python', 'emaki-config>=0.2.0'))
        result, calls = self.build(['emaki-config'], 'emaki-config', 'emaki-installer')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('emaki-installer needs emaki-config>=0.2.0, which this run did not build', result.stderr)
        self.assertEqual(list(calls), ['emaki-config'])

    def test_missing_arch_and_emaki_packages_together_stop_with_both_named(self):
        self.recipes()
        result, calls = self.build(['emaki-config', 'archinstall'], 'emaki-config', 'emaki-installer')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('archinstall=4.5-1', result.stderr)
        self.assertIn('emaki-config', result.stderr)
        self.assertEqual(list(calls), ['emaki-config'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
