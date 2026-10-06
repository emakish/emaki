#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Publish the [emaki] package repository as immutable snapshots behind one pointer per channel.

Layout in the package bucket (docs/mirror.md):
  <channel>/x86_64/<package>.pkg.tar.zst(.sig)   written once, never replaced
  <channel>/x86_64/<package>.sources.tar.gz   written once, never replaced
  snap/<channel>/<id>/emaki.db(.sig) emaki.files(.sig) SOURCES SOURCES.json MANIFEST   written once
  pointers/<channel>                              the only object that is ever overwritten
  locks/publish                                   exists while a publish runs
  served/<channel>/<id>, released/<target>/<id>   write-once records
pacman asks for <channel>/x86_64/emaki.db; the pointer Worker answers with a redirect into the
snapshot the pointer names, and pacman takes the signature from the redirected address.
"""
import argparse
import atexit
import contextlib
import datetime
import email.utils
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import r2  # noqa: E402

CHANNELS = ('testing', 'stable')
DB_FILES = ('emaki.db', 'emaki.db.sig', 'emaki.files', 'emaki.files.sig')
SOURCE_FILES = ('SOURCES', 'SOURCES.json')
SNAPSHOT_FILES = DB_FILES + SOURCE_FILES + ('MANIFEST',)
PATCHED_PACKAGES = ('niri-emaki', 'quickshell-emaki', 'xdg-desktop-portal-gnome-emaki')
ID_RE = re.compile(r'^\d{8}T\d{6}Z$')
PACKAGE_RE = re.compile(r'^[a-z0-9@._+-]+-[^-/]+-[^-/]+-(x86_64|any)\.pkg\.tar\.zst$')
DEFAULT_KEY = '668569956D2D747847D87D01F62680BE583363AC'
STAMP_MAX_AGE = datetime.timedelta(hours=24)
REQUIRED_STARTS = ('0.1.0', '0.1.1')
REQUIRED_RUNS = ('T1', 'T2')
REQUIRED_SIZES = ('1920x1080', '2560x1600')  # tests/vm/upgrade-check.py SIZES
EMAKI_PACKAGES = ('emaki', 'emaki-apps', 'emaki-config', 'emaki-desktop', 'emaki-installer', 'emaki-keyring',
                  'emaki-mirrorlist', 'niri-emaki', 'quickshell-emaki', 'xdg-desktop-portal-gnome-emaki')
GITHUB_REPO = 'emakish/packages'
GITHUB_DOWNLOAD = 'https://github.com/emakish/packages/releases/download'


class Refused(Exception):
    """A rule of the publishing procedure says no. Nothing that machines read was changed."""


class Exists(Exception):
    pass


class Conflict(Exception):
    pass


def say(text):
    print(text, flush=True)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def new_id(existing):
    """A UTC stamp greater than every id this channel has had: ids only grow (pacman, P9)."""
    floor = max(existing, default='')
    while True:
        candidate = utcnow().strftime('%Y%m%dT%H%M%SZ')
        if candidate > floor:
            return candidate
        time.sleep(0.2)


# Storage backends ----------------------------------------------------------------------------

class LocalBackend:
    """A directory with the bucket's layout. Served by tests/pointer-server.py like the Worker."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.name = f'local:{self.root}'

    def _path(self, key):
        if not key or key.startswith('/') or '..' in key.split('/') or '\\' in key:
            raise ValueError(f'bad key: {key!r}')
        return self.root / key

    def get(self, key):
        try:
            data = self._path(key).read_bytes()
        except FileNotFoundError:
            return None, None
        return data, sha256(data)

    def head(self, key):
        return self.get(key)[1]

    def _temp(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.tmp-')
        with os.fdopen(fd, 'wb') as stream:
            if isinstance(data, Path):
                with open(data, 'rb') as source:
                    shutil.copyfileobj(source, stream)
            else:
                stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o644)
        return temp

    def put_new(self, key, data):
        path = self._path(key)
        temp = self._temp(path, data)
        try:
            os.link(temp, path)  # atomic and refuses an existing name, like If-None-Match: *
        except FileExistsError:
            raise Exists(key) from None
        finally:
            os.unlink(temp)

    put_large = put_new

    @contextlib.contextmanager
    def _pointer_lock(self):
        with open(self.root / '.pointer.lock', 'a+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def put_pointer(self, key, data, if_match):
        path = self._path(key)
        with self._pointer_lock():
            current = self.head(key)
            if (if_match is None and current is not None) or (if_match is not None and current != if_match):
                raise Conflict(key)
            os.replace(self._temp(path, data), path)

    def overwrite(self, key, data):
        """Only for the test-only flat layout: the old in-place procedure."""
        path = self._path(key)
        os.replace(self._temp(path, data), path)

    def delete(self, key):
        self._path(key).unlink(missing_ok=True)

    def copy_new(self, source, destination):
        data, _ = self.get(source)
        if data is None:
            raise FileNotFoundError(source)
        self.put_new(destination, data)

    def list(self, prefix):
        base = self.root
        found = []
        for path in base.rglob('*'):
            if path.is_file() and not path.name.startswith('.'):
                key = path.relative_to(base).as_posix()
                if key.startswith(prefix):
                    found.append(key)
        return sorted(found)


class R2Backend:
    """The real bucket through its S3 API (packaging/mirror/r2.py). Same contract as LocalBackend."""

    def __init__(self, bucket, client=None):
        self.client = client or r2.S3Client.for_r2(bucket)
        self.name = f'r2:{bucket}'

    def get(self, key):
        return self.client.get(key)

    def head(self, key):
        return self.client.head(key)

    def put_new(self, key, data):
        body = data.read_bytes() if isinstance(data, Path) else data
        status, _ = self.client.put(key, body, if_none_match=True)
        if status == 412:
            raise Exists(key)

    def put_large(self, key, path):
        if self.client.head(key) is not None:
            raise Exists(key)
        try:
            self.client.upload_large(key, path)
        except r2.S3Error as error:
            if error.status == 412:
                raise Exists(key) from None
            raise

    def put_pointer(self, key, data, if_match):
        status, _ = self.client.put(key, data, if_none_match=if_match is None, if_match=if_match,
                                    content_type='text/plain', cache_control='no-store')
        if status == 412:
            raise Conflict(key)

    def overwrite(self, key, data):
        raise Refused('the flat layout exists only for the local self-check')

    def delete(self, key):
        self.client.delete(key)

    def copy_new(self, source, destination):
        if self.client.copy(source, destination) == 412:
            raise Exists(destination)

    def list(self, prefix):
        return sorted(self.client.list(prefix))


class DryRunBackend:
    """Reads go to the real backend; every write is printed instead of performed."""

    def __init__(self, inner):
        self.inner = inner
        self.name = inner.name + ' (dry run)'
        self.writes = []

    def get(self, key):
        return self.inner.get(key)

    def head(self, key):
        return self.inner.head(key)

    def list(self, prefix):
        return self.inner.list(prefix)

    def _write(self, verb, key, detail=''):
        self.writes.append((verb, key))
        say(f'DRY-RUN: would {verb} {key}{detail}')

    def put_new(self, key, data):
        size = data.stat().st_size if isinstance(data, Path) else len(data)
        self._write('PUT (If-None-Match: *)', key, f' ({size} bytes)')

    put_large = put_new

    def put_pointer(self, key, data, if_match):
        condition = f'If-Match: {if_match}' if if_match else 'If-None-Match: *'
        self._write(f'PUT ({condition})', key, f' = {data.decode().strip()}')

    def overwrite(self, key, data):
        self._write('OVERWRITE', key)

    def delete(self, key):
        self._write('DELETE', key)

    def copy_new(self, source, destination):
        self._write('COPY (If-None-Match: *)', destination, f' from {source}')


def open_backend(spec, dry_run=False):
    kind, _, value = spec.partition(':')
    if kind == 'local' and value:
        backend = LocalBackend(value)
    elif kind == 'r2' and value:
        backend = R2Backend(value)
    else:
        raise Refused(f'unknown backend {spec!r}: use local:DIR or r2:BUCKET')
    return DryRunBackend(backend) if dry_run else backend


# GitHub (the old address during the bridge period) ------------------------------------------

class GithubTarget:
    """Release assets of emakish/packages through gh. Assets are replaced one at a time."""

    def __init__(self, repo=GITHUB_REPO, download=GITHUB_DOWNLOAD, dry_run=False):
        self.repo, self.download_base, self.dry_run = repo, download, dry_run
        self.name = f'github:{repo}'

    def assets(self, tag):
        out = subprocess.run(['gh', 'release', 'view', tag, '-R', self.repo, '--json', 'assets',
                              '--jq', '.assets[].name'], capture_output=True, text=True, check=True).stdout
        return set(out.split())

    def fetch(self, tag, name):
        return http_get(f'{self.download_base}/{tag}/{name}', missing_ok=True)

    def notes(self, tag, text):
        if self.dry_run:
            say(f'DRY-RUN: would update source directions in {self.repo} release {tag}')
            return
        current = subprocess.run(['gh', 'release', 'view', tag, '-R', self.repo, '--json', 'body',
                                  '--jq', '.body'], capture_output=True, text=True, check=True).stdout
        marker = '<!-- emaki-sources -->'
        current = current.split(marker)[0].rstrip()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'notes.txt'
            path.write_text(current + '\n\n' + marker + '\n' + source_notes(text))
            subprocess.run(['gh', 'release', 'edit', tag, '-R', self.repo, '--notes-file', str(path)], check=True)

    def delete(self, tag, name):
        if self.dry_run:
            say(f'DRY-RUN: would delete {name} from {self.repo} release {tag}')
            return
        subprocess.run(['gh', 'release', 'delete-asset', tag, name, '-R', self.repo, '--yes'], check=True)

    def upload(self, tag, path, name):
        if name.endswith('.db.sig') or name.endswith('.files.sig'):
            raise Refused('the old address never serves a database signature')
        if self.dry_run:
            say(f'DRY-RUN: would upload {name} to {self.repo} release {tag} (--clobber)')
            return
        with tempfile.TemporaryDirectory() as temp:
            staged = Path(temp) / name
            shutil.copyfile(path, staged)
            subprocess.run(['gh', 'release', 'upload', tag, str(staged), '-R', self.repo, '--clobber'],
                           check=True)


class LocalGithubTarget(GithubTarget):
    """A directory per tag; every upload is appended to <tag>/.upload-log (tests)."""

    def __init__(self, root, dry_run=False):
        self.root, self.dry_run = Path(root), dry_run
        self.name = f'local-github:{self.root}'

    def assets(self, tag):
        directory = self.root / tag
        return {p.name for p in directory.iterdir() if not p.name.startswith('.')} if directory.is_dir() else set()

    def fetch(self, tag, name):
        path = self.root / tag / name
        return path.read_bytes() if path.is_file() else None

    def notes(self, tag, text):
        if self.dry_run:
            say(f'DRY-RUN: would update source directions in {self.root / tag}')
            return
        directory = self.root / tag
        directory.mkdir(parents=True, exist_ok=True)
        (directory / '.description').write_text(source_notes(text))

    def delete(self, tag, name):
        if not self.dry_run:
            (self.root / tag / name).unlink(missing_ok=True)

    def upload(self, tag, path, name):
        if name.endswith('.db.sig') or name.endswith('.files.sig'):
            raise Refused('the old address never serves a database signature')
        if self.dry_run:
            say(f'DRY-RUN: would upload {name} to {self.root / tag}')
            return
        directory = self.root / tag
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, directory / name)
        with open(directory / '.upload-log', 'a') as log:
            log.write(name + '\n')


def open_github(spec, dry_run):
    if spec.startswith('local:'):
        return LocalGithubTarget(spec[6:], dry_run)
    return GithubTarget(spec or GITHUB_REPO, dry_run=dry_run)


# HTTP as an anonymous client ----------------------------------------------------------------

def http_get(url, missing_ok=False):
    request = urllib.request.Request(url, headers={'User-Agent': 'emaki-publish'})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if missing_ok and error.code == 404:
            return None
        raise Refused(f'GET {url}: HTTP {error.code}') from None
    except urllib.error.URLError as error:
        raise Refused(f'GET {url}: {error.reason}') from None


def http_last_modified(url):
    request = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'emaki-publish'})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            value = response.headers.get('Last-Modified')
    except (urllib.error.HTTPError, urllib.error.URLError):
        return None
    return email.utils.parsedate_to_datetime(value) if value else None


def http_range(url, start, end):
    """Bytes start..end (inclusive) of url; the server must answer 206 (resumable downloads)."""
    request = urllib.request.Request(url, headers={'User-Agent': 'emaki-publish', 'Range': f'bytes={start}-{end}'})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            if response.status != 206:
                raise Refused(f'GET {url} with Range answered {response.status}, not 206')
            return response.read()
    except urllib.error.HTTPError as error:
        raise Refused(f'GET {url} with Range: HTTP {error.code}') from None


def http_sha256(url):
    """sha256 of the whole object at url, downloaded anonymously in 1 MiB pieces."""
    request = urllib.request.Request(url, headers={'User-Agent': 'emaki-publish'})
    hasher = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            while chunk := response.read(1 << 20):
                hasher.update(chunk)
    except urllib.error.HTTPError as error:
        raise Refused(f'GET {url}: HTTP {error.code}') from None
    except urllib.error.URLError as error:
        raise Refused(f'GET {url}: {error.reason}') from None
    return hasher.hexdigest()


def http_head(url):
    request = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'emaki-publish'})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except urllib.error.URLError as error:
        raise Refused(f'HEAD {url}: {error.reason}') from None


# Packages, databases, MANIFEST --------------------------------------------------------------

def pkginfo(path):
    with tarfile.open(path, 'r:*') as archive:
        member = archive.extractfile('.PKGINFO')
        text = member.read().decode()
    info = {}
    for line in text.splitlines():
        key, sep, value = line.partition(' = ')
        if sep:
            info.setdefault(key.strip(), value.strip())
    return info


def read_zst_pkginfo(path):
    """Package archives are zstd; tarfile needs it from Python 3.14 on, bsdtar always works."""
    try:
        return pkginfo(path)
    except (tarfile.TarError, KeyError, AttributeError, ValueError):
        text = subprocess.run(['bsdtar', '-xOf', str(path), '.PKGINFO'], capture_output=True,
                              text=True, check=True).stdout
        info = {}
        for line in text.splitlines():
            key, sep, value = line.partition(' = ')
            if sep:
                info.setdefault(key.strip(), value.strip())
        return info


def db_entries(db_bytes):
    """{filename: {'name', 'version', 'sha256'}} from a repo-add database (any compression)."""
    entries = {}
    with tarfile.open(fileobj=io.BytesIO(db_bytes), mode='r:*') as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.endswith('/desc'):
                continue
            fields, key = {}, None
            for line in archive.extractfile(member).read().decode().splitlines():
                if line.startswith('%') and line.endswith('%'):
                    key = line.strip('%')
                    fields[key] = []
                elif line and key:
                    fields[key].append(line)
            filename = fields['FILENAME'][0]
            entries[filename] = {'name': fields['NAME'][0], 'version': fields['VERSION'][0],
                                 'sha256': (fields.get('SHA256SUM') or [''])[0]}
    return entries


def manifest_text(snapshot, packages, sources=None):
    """snapshot: {db file name: bytes}; packages: {filename: (sha, sig sha)}. No ids or dates,
    so a promoted or withdrawn copy has the same MANIFEST as the snapshot it was copied from."""
    lines = [f'{sha256(snapshot[name])}  {name}' for name in DB_FILES + SOURCE_FILES]
    for _, record in sorted((sources or {}).items()):
        if 'archive' in record:
            lines.append(f'{record["sha256"]}  sources/{record["archive"]}')
    for filename in sorted(packages):
        package, signature = packages[filename]
        lines.append(f'{package}  packages/{filename}')
        lines.append(f'{signature}  packages/{filename}.sig')
    return ('\n'.join(lines) + '\n').encode()


def source_key(record):
    return f'sources/sha256/{record["sha256"]}/{record["archive"]}'


def sources_text(records, base_url='https://pkgs.emaki.sh'):
    lines = ['Emaki source archives', '',
             'Download the source archive for each package below.',
             'Each archive contains its build recipe, patches and local files.',
             'Packages with upstream changes also include their prepared upstream sources.',
             'Build instructions are included in each archive.', '']
    for name, record in sorted(records.items()):
        lines += [f'{name} {record["version"]}',
                  f'Archive: {base_url.rstrip("/")}/{source_key(record)}',
                  f'SHA256: {record["sha256"]}', '']
    lines += ['Project repository: https://github.com/emakish/emaki', '']
    return '\n'.join(lines).encode()


def source_notes(text):
    fence = '`' * max(3, max((len(m.group()) + 1 for m in re.finditer(r'`+', text)), default=3))
    return f'{fence}text\n{text.rstrip()}\n{fence}\n' if text else ''


def source_records(files):
    return json.loads(files['SOURCES.json'])['packages']


def parse_manifest(text):
    result = {}
    for line in text.decode().splitlines():
        digest, _, name = line.partition('  ')
        if len(digest) == 64 and name:
            result[name] = digest
    return result


def manifest_packages(manifest):
    return {name[9:]: digest for name, digest in manifest.items()
            if name.startswith('packages/') and not name.endswith('.sig')}


def package_version(filename):
    """(pkgname, pkgver-pkgrel) of a package file name."""
    name, version, release, _ = filename[:-len('.pkg.tar.zst')].rsplit('-', 3)
    return name, f'{version}-{release}'


def vercmp(a, b):
    """pacman's own version order (2-10 > 2-9), negative, zero or positive."""
    return int(subprocess.run(['vercmp', a, b], capture_output=True, text=True, check=True).stdout)


def load_registry(path):
    """Package files that left this machine before the mirror: GitHub assets and ISO repos."""
    registry = {}
    if path and Path(path).is_file():
        for line in Path(path).read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2 and len(parts[0]) == 64 and not line.startswith('#'):
                registry.setdefault(parts[1], set()).add(parts[0])
    return registry


# Signing (through gpg-agent's pinentry; never a passphrase in a file or an argument) ---------

class Signer:
    def __init__(self, key, home, dry_run=False):
        self.key, self.home, self.dry_run = key, Path(home), dry_run
        self.prepared = False

    def env(self):
        return dict(os.environ, GNUPGHOME=str(self.home))

    def prepare(self):
        if self.prepared:
            return
        passphrase = Path.home() / '.config/emaki-signing/passphrase'
        if passphrase.exists():
            raise Refused(f'{passphrase} exists: a passphrase must not lie next to the key. Delete it '
                          'and type the passphrase when pinentry asks.')
        if not self.dry_run:
            # Drop a cached passphrase so this publish asks for it (gpg-agent caches for 600 s).
            subprocess.run(['gpg-connect-agent', '--homedir', str(self.home), 'reloadagent', '/bye'],
                           capture_output=True, env=self.env())
        self.prepared = True

    def sign(self, path):
        self.prepare()
        if self.dry_run:
            say(f'DRY-RUN: would sign {path.name} with {self.key}')
            return
        subprocess.run(['gpg', '--yes', '--quiet', '--local-user', self.key, '--detach-sign', '--no-armor',
                        '--output', str(path) + '.sig', str(path)], check=True, env=self.env())

    def repo_add(self, db, packages, sign=True):
        # --prevent-downgrade: a lower version never replaces a higher one in the database.
        command = ['repo-add', '-q', '--prevent-downgrade']
        if sign and not self.dry_run:
            self.prepare()
            command += ['-s', '-k', self.key]
        subprocess.run(command + [str(db)] + [str(p) for p in packages], check=True, env=self.env(),
                       stdout=subprocess.DEVNULL)


def stop_gnupg(home, fakeroot=False):
    """Stop what gpg started for a temporary HOME (its key daemon, scdaemon, dirmngr) before HOME
    is removed: nothing else ends them, and each holds inotify instances of this user. A home
    that pacman-key used under fakeroot keeps its sockets in the home itself (fakeroot's uid 0
    has no /run/user/0/gnupg), so it is stopped under fakeroot as well; a plain gpgconf looks in
    /run/user/<uid>/gnupg, finds nothing and leaves the daemon running."""
    try:
        subprocess.run((['fakeroot', '--'] if fakeroot else []) +
                       ['gpgconf', '--homedir', str(home), '--kill', 'all'], capture_output=True)
    except OSError:
        pass


class Verifier:
    """Checks signatures against the public keys in git (packaging/emaki-keyring)."""

    def __init__(self, keyring, trusted):
        self.keyring = Path(keyring)
        self.trusted = [line.split(':')[0] for line in Path(trusted).read_text().split()
                        if line.strip() and ':' in line]
        self.temp = Path(tempfile.mkdtemp(prefix='emaki-verify-'))
        self.home = self.temp / 'gnupg'
        self.home.mkdir(mode=0o700)
        # --no-autostart: importing public keys and verifying need no GnuPG daemon, and a run
        # killed on purpose (SIGKILL, no cleanup) must not leave one behind.
        subprocess.run(['gpg', '--homedir', str(self.home), '--no-autostart', '--batch', '--quiet',
                        '--import', str(self.keyring)], check=True, capture_output=True)

    def verify(self, data_path, sig_path):
        result = subprocess.run(['gpg', '--homedir', str(self.home), '--no-autostart', '--batch', '--status-fd',
                                 '1', '--verify', str(sig_path), str(data_path)], capture_output=True, text=True)
        valid = [line.split()[2] for line in result.stdout.splitlines() if line.startswith('[GNUPG:] VALIDSIG')]
        primaries = [line.split()[-1] for line in result.stdout.splitlines()
                     if line.startswith('[GNUPG:] VALIDSIG')]
        return result.returncode == 0 and any(fpr in self.trusted for fpr in valid + primaries)

    def close(self):
        stop_gnupg(self.home)
        shutil.rmtree(self.temp, ignore_errors=True)


# A throwaway pacman, run as an ordinary user under fakeroot -------------------------------------

class ScratchPacman:
    def __init__(self, keyring=None, trusted=None):
        self.dir = Path(tempfile.mkdtemp(prefix='emaki-pacman-'))
        for name in ('root', 'db', 'cache', 'hooks', 'gnupg'):
            (self.dir / name).mkdir()
        self.keyring_ready = False
        self.keyring, self.trusted = keyring, trusted

    def close(self):
        stop_gnupg(self.dir / 'gnupg', fakeroot=True)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _config(self, sections, siglevel):
        lines = ['[options]', f'RootDir = {self.dir}/root', f'DBPath = {self.dir}/db',
                 f'CacheDir = {self.dir}/cache', f'LogFile = {self.dir}/pacman.log',
                 f'GPGDir = {self.dir}/gnupg', f'HookDir = {self.dir}/hooks', 'Architecture = x86_64',
                 f'SigLevel = {siglevel}', 'DisableDownloadTimeout']
        for name, body in sections:
            lines += [f'[{name}]', body]
        path = self.dir / 'pacman.conf'
        path.write_text('\n'.join(lines) + '\n')
        return path

    def run(self, config, *arguments, check=False):
        env = dict(os.environ, LC_ALL='C', LANG='C')
        return subprocess.run(['fakeroot', '--', 'pacman', '--config', str(config), '--noconfirm', *arguments],
                              capture_output=True, text=True, env=env, check=check)

    _templates = {}

    def _init_keyring(self, config):
        """pacman's keyring with the keys from git, built once per process and copied."""
        if self.keyring_ready:
            return
        cache_key = (str(self.keyring), tuple(self.trusted))
        template = self._templates.get(cache_key)
        if template is None:
            template = Path(tempfile.mkdtemp(prefix='emaki-pacman-keyring-')) / 'gnupg'
            atexit.register(shutil.rmtree, template.parent, True)
            env = dict(os.environ, LC_ALL='C')
            base = ['fakeroot', '--', 'pacman-key', '--config', str(config), '--gpgdir', str(template)]
            try:
                subprocess.run(base + ['--init'], capture_output=True, check=True, env=env)
                subprocess.run(base + ['--add', str(self.keyring)], capture_output=True, check=True, env=env)
                for fingerprint in self.trusted:
                    subprocess.run(base + ['--lsign-key', fingerprint], capture_output=True, check=True, env=env)
            finally:
                # --init generates and --lsign-key uses a secret key: both start the key daemon.
                stop_gnupg(template, fakeroot=True)
            self._templates[cache_key] = template
        shutil.rmtree(self.dir / 'gnupg')
        shutil.copytree(template, self.dir / 'gnupg', symlinks=True,
                        ignore=shutil.ignore_patterns('S.*', '*.lock', '.#*'))
        self.keyring_ready = True

    def sync(self, server, siglevel='Required'):
        """Exactly what an installed machine does: -Sy against one Server line."""
        config = self._config([('emaki', f'Server = {server}')], siglevel)
        if siglevel != 'Never':
            self._init_keyring(config)
        return self.run(config, '-Sy')

    def synced_db(self):
        path = self.dir / 'db/sync/emaki.db'
        return path.read_bytes() if path.is_file() else None

    def resolve(self, repo_dir, targets, arch_dbs):
        """pacman -S --print against a local (unsigned) [emaki] plus Arch's core/extra."""
        emaki = ('emaki', f'Server = file://{repo_dir}')
        if arch_dbs == 'auto':
            config = self._config([emaki, ('core', 'Include = /etc/pacman.d/mirrorlist'),
                                   ('extra', 'Include = /etc/pacman.d/mirrorlist')], 'Never')
            synced = self.run(config, '-Sy')
        else:
            sections = [emaki]
            if arch_dbs != 'none':
                sync = self.dir / 'db/sync'
                sync.mkdir(parents=True, exist_ok=True)
                for name in ('core', 'extra'):
                    source = Path(arch_dbs) / f'{name}.db'
                    if not source.is_file():
                        raise Refused(f'--arch-dbs {arch_dbs}: missing {name}.db')
                    shutil.copyfile(source, sync / f'{name}.db')
                    sections.append((name, 'Server = file:///nonexistent'))
            # Sync [emaki] only; supplied Arch databases are used as they are.
            synced = self.run(self._config([emaki], 'Never'), '-Sy')
            config = self._config(sections, 'Never')
        if synced.returncode:
            raise Refused('closure check: pacman -Sy failed:\n' + synced.stderr.strip())
        result = self.run(config, '-S', '--print', '--print-format', '%r/%n %v', *targets)
        return result


# The publisher -------------------------------------------------------------------------------

class Publisher:
    def __init__(self, args):
        self.args = args
        self.dry_run = args.dry_run
        self.backend = open_backend(args.backend, args.dry_run)
        self.public = args.public_url.rstrip('/')
        self.state = Path(args.state_dir)
        self.state.mkdir(parents=True, exist_ok=True)
        self.signer = Signer(args.key, args.signing_home, args.dry_run)
        self.keyring, self.trusted = Path(args.keyring), Path(args.trusted)
        self.holder = self.publisher_id()
        self._verifier = None

    def publisher_id(self):
        """This machine's name in locks/publish. The bucket is public, so it is a random id kept
        in the state directory, never the host name."""
        path = self.state / 'publisher-id'
        if path.is_file():
            return path.read_text().strip()
        value = 'publisher-' + secrets.token_hex(4)
        path.write_text(value + '\n')
        return value

    @property
    def verifier(self):
        if self._verifier is None:
            self._verifier = Verifier(self.keyring, self.trusted)
        return self._verifier

    def close(self):
        if self._verifier:
            self._verifier.close()

    # pointers, snapshots, records ----------------------------------------------------------
    def pointer(self, channel):
        data, etag = self.backend.get(f'pointers/{channel}')
        if data is None:
            return None, None
        value = data.decode().strip()
        if not ID_RE.match(value):
            raise Refused(f'pointers/{channel} holds {value!r}, not a snapshot id')
        return value, etag

    def snapshot_ids(self, channel):
        ids = {key.split('/')[2] for key in self.backend.list(f'snap/{channel}/') if key.count('/') >= 3}
        return sorted(i for i in ids if ID_RE.match(i))

    def snapshot(self, channel, snap_id):
        files = {}
        for name in SNAPSHOT_FILES:
            data, _ = self.backend.get(f'snap/{channel}/{snap_id}/{name}')
            if data is None:
                raise Refused(f'snap/{channel}/{snap_id}/{name} is missing')
            files[name] = data
        manifest = parse_manifest(files['MANIFEST'])
        for name in DB_FILES + SOURCE_FILES:
            if manifest.get(name) != sha256(files[name]):
                raise Refused(f'snap/{channel}/{snap_id}/{name} does not match its MANIFEST')
        return files

    def all_manifests(self):
        result = {}
        for key in self.backend.list('snap/'):
            if key.endswith('/MANIFEST'):
                data, _ = self.backend.get(key)
                result[key] = parse_manifest(data)
        return result

    def served(self, channel):
        records = []
        for key in self.backend.list(f'served/{channel}/'):
            data, _ = self.backend.get(key)
            records.append(json.loads(data))
        return sorted(records, key=lambda record: record['id'])

    # journal and lock ----------------------------------------------------------------------
    def journal_path(self, snap_id):
        return self.state / snap_id / 'journal.json'

    def open_journals(self):
        found = []
        for path in sorted(self.state.glob('*/journal.json')):
            journal = json.loads(path.read_text())
            if not journal.get('closed'):
                found.append(journal)
        return found

    def save(self, journal):
        path = self.journal_path(journal['id'])
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(journal, indent=1, sort_keys=True))
        os.replace(temp, path)

    def done(self, journal, step):
        if step not in journal['steps']:
            journal['steps'].append(step)
        if not self.dry_run:
            self.save(journal)
        say(f'step {step} done: {STEP_NAMES[journal["kind"]][step]}')
        kill = os.environ.get('EMAKI_PUBLISH_TEST_KILL_AFTER', '')
        if kill and kill == str(step) and not self.dry_run:
            # Test hook: the publisher dies like a laptop losing power, with no cleanup at all.
            os.kill(os.getpid(), signal.SIGKILL)

    def begin(self, kind, channel, inputs):
        """Steps 0→1: find our own open journal (resume) or start a new one; take the lock."""
        mine = [j for j in self.open_journals() if j['kind'] == kind and j['channel'] == channel]
        others = [j for j in self.open_journals() if j not in mine]
        if others:
            raise Refused(f'an unfinished {others[0]["kind"]} {others[0]["channel"]} ({others[0]["id"]}) '
                          'is open on this machine; re-run it first (status shows it)')
        if len(mine) > 1:
            raise Refused('more than one open journal for this channel; see status')
        if mine:
            journal = mine[0]
            if journal['inputs'] != inputs:
                raise Refused(f'an interrupted {kind} {channel} ({journal["id"]}) was started with other '
                              'inputs; re-run it with the same inputs to finish it')
            say(f'resuming {kind} {channel} {journal["id"]} after step {max(journal["steps"], default=0)}')
            return journal
        existing = self.snapshot_ids(channel)
        current, _ = self.pointer(channel)
        snap_id = new_id(existing + ([current] if current else []))
        return {'kind': kind, 'channel': channel, 'id': snap_id, 'holder': self.holder, 'steps': [],
                'inputs': inputs, 'pointer_etag': None, 'pointer_before': None, 'closed': False,
                'token': secrets.token_hex(8),
                'started': utcnow().isoformat(timespec='seconds')}

    def acquire_lock(self, journal):
        """PUT locks/publish, or accept it when it is this journal's own (an interrupted run)."""
        lock = json.dumps({'holder': journal['holder'], 'token': journal['token'], 'kind': journal['kind'],
                           'channel': journal['channel'],
                           'id': journal['id'], 'started': journal['started']}, sort_keys=True).encode()
        try:
            self.backend.put_new('locks/publish', lock)
        except Exists:
            held, _ = self.backend.get('locks/publish')
            holder = json.loads(held) if held else {}
            # Our own lock from an interrupted run of this very journal (its random token).
            if holder.get('token') != journal['token']:
                raise Refused(f'locks/publish is held: {held.decode() if held else "?"} — another publish '
                              'is running or died; `status`, then `unlock --yes` if it is dead') from None

    def take_lock(self, journal):
        self.acquire_lock(journal)
        if 1 not in journal['steps']:
            current, etag = self.pointer(journal['channel'])
            journal['pointer_before'], journal['pointer_etag'] = current, etag
        self.done(journal, 1)

    def checked(self, journal):
        """End of step 0: the lock first, then the record. A run refused at the lock records
        nothing, so running it again repeats every check of step 0."""
        self.acquire_lock(journal)
        self.done(journal, 0)

    def release_lock(self, journal):
        held, _ = self.backend.get('locks/publish')
        if held is not None and json.loads(held).get('token') == journal['token']:
            self.backend.delete('locks/publish')

    def abandon(self, journal, error):
        """A check right before the flip said no. Nothing machines read has changed (the new
        snapshot is named by nothing), so close the run and free the lock; the next run starts
        over with a new id."""
        self.release_lock(journal)
        journal['closed'] = True
        journal['abandoned'] = str(error)
        journal['finished'] = utcnow().isoformat(timespec='seconds')
        if not self.dry_run:
            self.save(journal)
        shutil.rmtree(self.state / journal['id'] / 'work', ignore_errors=True)
        return Refused(f'{error}\nStopped before the flip: nothing that machines read has changed; the lock '
                       'is released and this run is closed.')

    def finish(self, journal, record):
        """Step 7 tail: write the served record, drop the lock, close the journal."""
        try:
            self.backend.put_new(f'served/{journal["channel"]}/{journal["id"]}',
                                 json.dumps(record, sort_keys=True).encode())
        except Exists:
            pass
        # Only our own lock: after `unlock --yes` another publisher may hold it by now.
        self.release_lock(journal)
        journal['closed'] = True
        journal['finished'] = utcnow().isoformat(timespec='seconds')
        if not self.dry_run:
            self.save(journal)

    # shared steps --------------------------------------------------------------------------
    def put_or_same(self, key, data, backend=None, hint='Build it again with a higher pkgrel.'):
        backend = backend or self.backend
        try:
            backend.put_new(key, data)
            return 'uploaded'
        except Exists:
            existing, _ = backend.get(key)
            expected = sha256_file(data) if isinstance(data, Path) else sha256(data)
            if existing is None or sha256(existing) != expected:
                raise Refused(f'{key} already exists with other bytes: a published name is never '
                              f'replaced. {hint}'.strip()) from None
            return 'already there'

    def verify_packages_anonymously(self, channel, packages):
        """GET each package and signature through the public address; compare and verify."""
        with tempfile.TemporaryDirectory() as temp:
            for filename, (digest, sig_digest) in sorted(packages.items()):
                base = f'{self.public}/{channel}/x86_64/{filename}'
                data = http_get(base)
                signature = http_get(base + '.sig')
                if sha256(data) != digest or sha256(signature) != sig_digest:
                    raise Refused(f'anonymous download of {filename} differs from what was uploaded')
                (Path(temp) / filename).write_bytes(data)
                (Path(temp) / (filename + '.sig')).write_bytes(signature)
                if not self.verifier.verify(Path(temp) / filename, Path(temp) / (filename + '.sig')):
                    raise Refused(f'{filename}.sig does not verify against {self.keyring}')
        say(f'anonymous check: {len(packages)} packages identical and signed')

    def verify_snapshot(self, channel, snap_id, files, targets):
        """Step 5: a scratch pacman with SigLevel = Required reads the snapshot directly."""
        self.verify_sources(channel, files)
        for name in SOURCE_FILES:
            if http_get(f'{self.public}/snap/{channel}/{snap_id}/{name}') != files[name]:
                raise Refused(f'anonymous download of {name} differs from snapshot')
        client = ScratchPacman(self.keyring, self.verifier.trusted)
        try:
            server = f'{self.public}/snap/{channel}/{snap_id}'
            result = client.sync(server)
            if result.returncode:
                raise Refused(f'scratch pacman cannot sync {server}:\n{result.stderr.strip()}')
            if client.synced_db() != files['emaki.db']:
                raise Refused(f'{server}/emaki.db is not the database that was built')
            listed = client.run(client.dir / 'pacman.conf', '-Sl', 'emaki')
            if listed.returncode:
                raise Refused('pacman -Sl emaki failed after sync: ' + listed.stderr.strip())
            for filename in db_entries(files['emaki.db']):
                status = http_head(f'{self.public}/{channel}/x86_64/{filename}')
                if status != 200:
                    raise Refused(f'{channel}/x86_64/{filename} answers HTTP {status}')
            missing = [t for t in targets if t not in {e['name'] for e in db_entries(files['emaki.db']).values()}]
            if missing:
                raise Refused('the snapshot lacks ' + ', '.join(missing))
        finally:
            client.close()
        say(f'snapshot {channel}/{snap_id} verified by a scratch pacman (SigLevel = Required)')

    def confirm_channel(self, channel, files):
        """Step 7: sync through the public channel address, as installed machines do."""
        if self.args.layout == 'flat':
            return
        server = f'{self.public}/{channel}/x86_64'
        for name in SOURCE_FILES:
            if http_get(f'{server}/{name}') != files[name]:
                raise Refused(f'{server}/{name} does not serve the new source directions')
        client = ScratchPacman(self.keyring, self.verifier.trusted)
        try:
            result = client.sync(server)
            if result.returncode:
                raise Refused(f'pacman -Sy through {server} failed:\n{result.stderr.strip()}')
            if client.synced_db() != files['emaki.db']:
                raise Refused(f'{server} does not serve the new database yet')
        finally:
            client.close()
        say(f'{server}: pacman -Sy serves the new database')

    def flip(self, journal, gate):
        """Step 6. `gate` repeats the checks of step 0 that time can change (the stamp, today's
        Arch) on every run, right before the only write machines see."""
        channel, snap_id = journal['channel'], journal['id']
        current, etag = self.pointer(channel)
        if current == snap_id:
            say(f'pointers/{channel} already names {snap_id}')
        else:
            if current != journal['pointer_before']:
                raise Refused(f'pointers/{channel} moved to {current} during this run; stopping')
            try:
                gate()
            except Refused as error:
                raise self.abandon(journal, error) from None
            try:
                self.backend.put_pointer(f'pointers/{channel}', (snap_id + '\n').encode(), etag)
            except Conflict:
                raise Refused(f'pointers/{channel} changed under us (If-Match failed); stopping') from None
            say(f'pointers/{channel}: {current or "(none)"} -> {snap_id}')
        self.done(journal, 6)

    def check_closure(self, repo_dir, targets):
        if self.args.arch_dbs == 'skip':
            say('closure check skipped (--arch-dbs skip)')
            return
        client = ScratchPacman()
        try:
            result = client.resolve(repo_dir, targets, self.args.arch_dbs)
        finally:
            client.close()
        if result.returncode:
            raise Refused('pacman cannot resolve ' + ' '.join(targets) + ':\n' +
                          (result.stderr.strip() or result.stdout.strip()))
        say(f'closure: {" ".join(targets)} resolve ({len(result.stdout.splitlines())} packages)')

    def check_db_closure(self, db, targets):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / 'emaki.db').write_bytes(db)
            self.check_closure(Path(temp), targets)

    def check_tree(self):
        tree = Path(self.args.source_tree)
        status = subprocess.run(['git', '-C', str(tree), 'status', '--porcelain'], capture_output=True, text=True)
        if status.returncode or status.stdout.strip():
            raise Refused(f'the working tree {tree} is not clean; commit first:\n{status.stdout.strip()}')

    # publish -------------------------------------------------------------------------------
    def collect(self, directory):
        directory = Path(directory)
        packages = sorted(p for p in directory.glob('*.pkg.tar.zst') if '-debug-' not in p.name)
        if not packages:
            raise Refused(f'no packages in {directory}')
        found = {}
        for path in packages:
            if not PACKAGE_RE.match(path.name):
                raise Refused(f'unexpected package file name: {path.name}')
            signature = Path(str(path) + '.sig')
            if not signature.is_file():
                raise Refused(f'{path.name} has no .sig: run `publish.sh sign {directory}` first')
            info = read_zst_pkginfo(path)
            expected = f'{info["pkgname"]}-{info["pkgver"]}-{info["arch"]}.pkg.tar.zst'
            if path.name != expected:
                raise Refused(f'{path.name} does not match its .PKGINFO ({expected})')
            found[path.name] = {'path': str(path), 'sha256': sha256_file(path), 'sig': sha256_file(signature),
                                'name': info['pkgname'], 'version': info['pkgver']}
        by_name = {}
        for filename, entry in found.items():
            by_name.setdefault(entry['name'], []).append(filename)
        for name, filenames in sorted(by_name.items()):
            if len(filenames) > 1:
                raise Refused(f'{directory} holds {len(filenames)} versions of {name} ({", ".join(filenames)}); '
                              'one directory is one build: build into an empty directory')
        provenance = directory / 'BUILDINFO'
        if not provenance.is_file():
            raise Refused(f'{directory} has no BUILDINFO; rebuild from a clean committed tree')
        lines = provenance.read_text().splitlines()
        if not lines or not re.fullmatch(r'commit [0-9a-f]{40}', lines[0]):
            raise Refused('BUILDINFO has no full build commit')
        commit = lines[0].split()[1]
        hashes = parse_manifest(('\n'.join(lines[1:]) + '\n').encode())
        def bound(path):
            if not path.is_file() or hashes.get(path.name) != sha256_file(path):
                raise Refused(f'{path.name} is missing or differs from BUILDINFO')
        bound(directory / 'SOURCES.json')
        try:
            metadata = json.loads((directory / 'SOURCES.json').read_bytes())
            if metadata['format'] != 1 or metadata['commit'] != commit:
                raise ValueError('wrong format or commit')
            records = metadata['packages']
            if not isinstance(records, dict) or not all(isinstance(record, dict) for record in records.values()):
                raise ValueError('packages must map names to source records')
        except (ValueError, KeyError, TypeError) as error:
            raise Refused(f'invalid SOURCES.json: {error}') from None
        for filename, entry in found.items():
            bound(directory / filename)
            record = {'version': entry['version'], 'commit': commit}
            source = records.get(entry['name'], {})
            archive = f'{entry["name"]}-{entry["version"]}.sources.tar.gz'
            if source.get('version') != entry['version'] or source.get('archive') != archive:
                raise Refused(f'{entry["name"]}: missing or mismatched source archive record')
            bound(directory / archive)
            if source.get('sha256') != hashes[archive]:
                raise Refused(f'{archive} differs from SOURCES.json')
            record.update(archive=archive, sha256=hashes[archive])
            entry['source'] = record
        return found

    def put_source(self, record, path):
        key = source_key(record)
        if self.backend.head(key) is None:
            self.put_or_same(key, path)
        receipt, _ = self.backend.get(key + '.verified')
        etag = self.backend.head(key)
        expected = {'sha256': record['sha256'], 'etag': etag}
        if receipt is None:
            if sha256(http_get(f'{self.public}/{key}')) != record['sha256']:
                raise Refused(f'{key} differs from source metadata')
            self.put_or_same(key + '.verified', json.dumps(expected, sort_keys=True).encode())
        elif json.loads(receipt) != expected:
            raise Refused(f'{key} differs from its verified upload')

    def verify_sources(self, channel, files):
        manifest = parse_manifest(files['MANIFEST'])
        for record in source_records(files).values():
            name, digest = record['archive'], record['sha256']
            if manifest.get(f'sources/{name}') != digest:
                raise Refused(f'{name} is not bound by MANIFEST')
            key = source_key(record)
            receipt, _ = self.backend.get(key + '.verified')
            etag = self.backend.head(key)
            if etag is None or receipt is None or json.loads(receipt) != {'sha256': digest, 'etag': etag}:
                raise Refused(f'{name} differs from its verified upload or is missing')
            if http_head(f'{self.public}/{key}') != 200:
                raise Refused(f'{name} is not publicly available')

    def refuse_burnt(self, packages):
        """A file name, or a pkgname with its version under any file name (another arch suffix),
        that ever left this machine with other bytes can never be published: pacman decides by
        name and version, so a machine holding the other bytes would never receive these."""
        registry = load_registry(self.args.registry)
        for key, manifest in self.all_manifests().items():
            for filename, digest in manifest_packages(manifest).items():
                registry.setdefault(filename, set()).add(digest)
        for filename, entry in packages.items():
            seen = registry.get(filename, set())
            if seen and seen != {entry['sha256']}:
                raise Refused(f'{filename} was already released with other content '
                              f'({", ".join(sorted(d[:12] for d in seen))}); it can never be published. '
                              'Raise pkgrel and rebuild.')
        versions = {}
        for filename, digests in registry.items():
            if PACKAGE_RE.match(filename):
                versions.setdefault(package_version(filename), {}).update({d: filename for d in digests})
        for filename, entry in sorted(packages.items()):
            seen = versions.get((entry['name'], entry['version']), {})
            if seen and set(seen) != {entry['sha256']}:
                raise Refused(f'{entry["name"]} {entry["version"]} was already released with other content '
                              f'(as {", ".join(sorted(set(seen.values())))}); {filename} can never be '
                              'published. Raise pkgrel and rebuild.')

    def refuse_downgrade(self, channel, packages):
        """pacman never installs a lower version over a higher one: a lower version in the
        database would leave every machine that has the higher one where it is, silently."""
        current, _ = self.pointer(channel)
        if current is None:
            return
        served = {e['name']: e['version'] for e in db_entries(self.snapshot(channel, current)['emaki.db']).values()}
        for filename, entry in sorted(packages.items()):
            old = served.get(entry['name'])
            if old and vercmp(entry['version'], old) < 0:
                raise Refused(f'{entry["name"]} {entry["version"]} is lower than {old}, which {channel} serves '
                              f'({current}); machines that have {old} would keep it. Build a higher version.')

    def previous_db(self, channel, work):
        current, _ = self.pointer(channel)
        if current is None:
            return None
        files = self.snapshot(channel, current)
        for name in ('emaki.db', 'emaki.files'):
            (work / f'{name}.tar.gz').write_bytes(files[name])
        return current

    def build_db(self, channel, work, packages, sign):
        """repo-add the new packages on top of the channel's current database."""
        work.mkdir(parents=True, exist_ok=True)
        for leftover in work.glob('emaki.*'):
            leftover.unlink()
        pool = work / 'pool'
        shutil.rmtree(pool, ignore_errors=True)
        pool.mkdir()
        for filename, entry in packages.items():
            os.symlink(entry['path'], pool / filename)
            os.symlink(entry['path'] + '.sig', pool / (filename + '.sig'))
        self.previous_db(channel, work)
        self.signer.repo_add(work / 'emaki.db.tar.gz', [pool / f for f in sorted(packages)], sign=sign)
        return pool

    def publish(self, channel, directory):
        if channel != 'testing':
            # stable changes only through `promote`, behind the acceptance stamp (one gate).
            raise Refused(f'publish writes only testing, not {channel}: stable gets a release through '
                          '`promote` with the acceptance stamp')
        targets = self.args.closure.split()
        packages = self.collect(directory)
        inputs = {f: e['sha256'] for f, e in packages.items()}
        inputs['provenance'] = sha256(json.dumps({f: e['source'] for f, e in packages.items()}, sort_keys=True).encode())
        journal = self.begin('publish', channel, inputs)
        work = self.state / journal['id'] / 'work'

        if 0 not in journal['steps']:
            self.check_tree()
            for filename, entry in packages.items():
                if not self.verifier.verify(entry['path'], entry['path'] + '.sig'):
                    raise Refused(f'{filename}.sig is not a valid signature by a trusted key in {self.trusted}')
            self.refuse_burnt(packages)
            self.refuse_downgrade(channel, packages)
            with tempfile.TemporaryDirectory() as temp:
                trial = Path(temp)
                self.build_db(channel, trial, packages, sign=False)
                (trial / 'emaki.db').unlink(missing_ok=True)
                shutil.copyfile(trial / 'emaki.db.tar.gz', trial / 'emaki.db')
                self.check_closure(trial, targets)
            if self.dry_run:
                self.dry_plan(journal, packages)
                return
            self.checked(journal)
        self.take_lock(journal)

        if 2 not in journal['steps']:
            for filename, entry in sorted(packages.items()):
                if 'archive' in entry['source']:
                    archive = entry['source']['archive']
                    self.put_source(entry['source'], Path(directory) / archive)
                for suffix in ('', '.sig'):
                    result = self.put_or_same(f'{channel}/x86_64/{filename}{suffix}', Path(entry['path'] + suffix))
                    say(f'{channel}/x86_64/{filename}{suffix}: {result}')
            self.done(journal, 2)

        if 3 not in journal['steps']:
            self.verify_packages_anonymously(channel, {f: (e['sha256'], e['sig']) for f, e in packages.items()})
            self.done(journal, 3)

        if 4 not in journal['steps']:
            if not journal.get('db_built'):
                self.build_signed_db(journal, channel, work, packages)
                journal['db_built'] = True
                self.save(journal)
            files = self.local_db_files(work)
            entries = db_entries(files['emaki.db'])
            named = {}
            for filename, entry in entries.items():
                if filename in packages:
                    named[filename] = (packages[filename]['sha256'], packages[filename]['sig'])
                else:
                    sig, _ = self.backend.get(f'{channel}/x86_64/{filename}.sig')
                    if sig is None:
                        raise Refused(f'the database names {filename}, which {channel}/x86_64 lacks')
                    named[filename] = (entry['sha256'], sha256(sig))
                if named[filename][0] != entry['sha256']:
                    raise Refused(f'database checksum of {filename} differs from the file')
            current, _ = self.pointer(channel)
            records = source_records(self.snapshot(channel, current)) if current else {}
            records.update({e['name']: e['source'] for e in packages.values()})
            records = {e['name']: records[e['name']] for e in entries.values()}
            for entry in entries.values():
                if records[entry['name']]['version'] != entry['version']:
                    raise Refused(f'{entry["name"]}: source version differs from database')
            files['SOURCES.json'] = (json.dumps({'format': 1, 'packages': records}, sort_keys=True, indent=2) + '\n').encode()
            files['SOURCES'] = sources_text(records, self.public)
            for name in SOURCE_FILES:
                (work / name).write_bytes(files[name])
            files['MANIFEST'] = manifest_text(files, named, records)
            (work / 'MANIFEST').write_bytes(files['MANIFEST'])
            if self.args.layout == 'flat':
                # The old procedure, kept only so the self-check can show what it did to clients.
                for name in ('emaki.db', 'emaki.files'):
                    self.backend.overwrite(f'{channel}/x86_64/{name}', files[name])
                self.done(journal, 4)
                for name in ('emaki.db.sig', 'emaki.files.sig'):
                    self.backend.overwrite(f'{channel}/x86_64/{name}', files[name])
                self.done(journal, 5)
                self.done(journal, 6)
            else:
                self.upload_snapshot(journal, files)
                self.done(journal, 4)
        files = self.local_db_files(work)
        for name in SOURCE_FILES:
            files[name] = (work / name).read_bytes()
        files['MANIFEST'] = (work / 'MANIFEST').read_bytes()

        if 5 not in journal['steps']:
            self.verify_snapshot(channel, journal['id'], files, targets)
            self.done(journal, 5)
        if 6 not in journal['steps']:
            self.flip(journal, lambda: self.check_db_closure(files['emaki.db'], targets))
        self.confirm_channel(channel, files)
        self.finish(journal, {'id': journal['id'], 'kind': 'publish', 'manifest': sha256(files['MANIFEST'])})
        self.done(journal, 7)
        say(f'published {channel} {journal["id"]} (MANIFEST {sha256(files["MANIFEST"])})')

    def build_signed_db(self, journal, channel, work, packages):
        """Step 4's database. repo-add -s only warns and exits 0 when gpg cannot sign (pinentry
        cancelled or timed out), so both signatures are verified here. Without them this run
        ends before anything names the new database: nothing machines read has changed."""
        try:
            self.build_db(channel, work, packages, sign=True)
        except subprocess.CalledProcessError as error:
            raise self.abandon(journal, Refused(f'emaki.db.tar.gz was not signed: repo-add failed ({error})')) \
                from None
        for name in ('emaki.db.tar.gz', 'emaki.files.tar.gz'):
            signature = work / (name + '.sig')
            if not signature.is_file() or not self.verifier.verify(work / name, signature):
                raise self.abandon(journal, Refused(f'{name} was not signed by a trusted key (pinentry cancelled '
                                                    'or timed out, or gpg failed; repo-add only warns then)'))

    def upload_snapshot(self, journal, files):
        """Step 4. pacman sends If-Modified-Since with the time of the database it holds, at one
        second resolution, and fetches the signature even after a 304 (P2). A new database
        uploaded within the same second as the one machines hold would be answered 304 while
        its new signature is fetched: the fatal mismatch of P3. So wait until the clock is past
        the current snapshot's Last-Modified before the first upload."""
        channel = journal['channel']
        current, _ = self.pointer(channel)
        if current and current != journal['id'] and not self.dry_run:
            stamp = http_last_modified(f'{self.public}/snap/{channel}/{current}/emaki.db')
            while stamp and utcnow() < stamp + datetime.timedelta(seconds=1):
                time.sleep(0.2)
        for name in SNAPSHOT_FILES:
            self.put_or_same(f'snap/{channel}/{journal["id"]}/{name}', files[name], hint='')

    def local_db_files(self, work):
        files = {}
        for name in DB_FILES:
            source = work / name.replace('emaki.db', 'emaki.db.tar.gz').replace('emaki.files', 'emaki.files.tar.gz')
            files[name] = source.read_bytes()
        return files

    def dry_plan(self, journal, packages):
        channel, snap_id = journal['channel'], journal['id']
        say(f'DRY-RUN: publish {channel} as snapshot {snap_id}; nothing below is written')
        self.backend.put_new('locks/publish', b'{}')
        for filename in sorted(packages):
            if 'archive' in packages[filename]['source']:
                name = packages[filename]['source']['archive']
                self.backend.put_new(source_key(packages[filename]['source']), Path(packages[filename]['path']).parent / name)
            self.backend.put_new(f'{channel}/x86_64/{filename}', Path(packages[filename]['path']))
            self.backend.put_new(f'{channel}/x86_64/{filename}.sig', Path(packages[filename]['path'] + '.sig'))
        for name in SNAPSHOT_FILES:
            self.backend.put_new(f'snap/{channel}/{snap_id}/{name}', b'')
        current, etag = self.pointer(channel)
        self.backend.put_pointer(f'pointers/{channel}', (snap_id + '\n').encode(), etag)
        self.backend.put_new(f'served/{channel}/{snap_id}', b'')
        self.backend.delete('locks/publish')

    # copy a snapshot to a channel as a new snapshot (promote, withdraw) ----------------------
    def copy_snapshot(self, journal, source_channel, source_id, record, gate):
        channel = journal['channel']
        targets = self.args.closure.split()
        files = self.snapshot(source_channel, source_id)
        manifest = parse_manifest(files['MANIFEST'])
        packages = {f: (d, manifest[f'packages/{f}.sig']) for f, d in manifest_packages(manifest).items()}
        if 2 not in journal['steps']:
            self.verify_sources(source_channel, files)
            for filename in sorted(packages):
                for suffix in ('', '.sig'):
                    destination = f'{channel}/x86_64/{filename}{suffix}'
                    try:
                        self.backend.copy_new(f'{source_channel}/x86_64/{filename}{suffix}', destination)
                        say(f'{destination}: copied from {source_channel}')
                    except Exists:
                        data, _ = self.backend.get(destination)
                        expected = packages[filename][1 if suffix else 0]
                        if data is None or sha256(data) != expected:
                            raise Refused(f'{destination} exists with other bytes') from None
            self.done(journal, 2)
        if 3 not in journal['steps']:
            self.verify_packages_anonymously(channel, packages)
            self.done(journal, 3)
        if 4 not in journal['steps']:
            self.upload_snapshot(journal, files)
            self.done(journal, 4)
        if 5 not in journal['steps']:
            self.verify_snapshot(channel, journal['id'], files, targets)
            self.done(journal, 5)
        if 6 not in journal['steps']:
            self.flip(journal, gate)
        self.confirm_channel(channel, files)
        record = dict(record, id=journal['id'], manifest=sha256(files['MANIFEST']))
        self.finish(journal, record)
        self.done(journal, 7)
        return files

    def promote(self, first):
        current, _ = self.pointer('testing')
        if current is None:
            raise Refused('testing has no snapshot to promote')
        files = self.snapshot('testing', current)
        manifest_sha = sha256(files['MANIFEST'])
        journal = self.begin('promote', 'stable', {'source': current, 'first': first})

        def gate():
            """Step 0, and again right before the flip of every run (a resumed one too)."""
            if first:
                self.refuse_second_first(files)
                say('promote --first: allowed once, while no released mirrorlist names this mirror')
            else:
                self.check_stamp(manifest_sha)
            self.check_db_closure(files['emaki.db'], self.args.closure.split())

        if 0 not in journal['steps']:
            gate()
            if self.dry_run:
                say(f'DRY-RUN: promote testing {current} (MANIFEST {manifest_sha}) to stable as {journal["id"]}')
                stable, etag = self.pointer('stable')
                for name in SNAPSHOT_FILES:
                    self.backend.put_new(f'snap/stable/{journal["id"]}/{name}', files[name])
                self.backend.put_pointer('pointers/stable', (journal['id'] + '\n').encode(), etag)
                return
            self.checked(journal)
        self.take_lock(journal)
        self.copy_snapshot(journal, 'testing', current, {'kind': 'promote-first' if first else 'promote',
                                                          'source': current}, gate)
        say(f'promoted testing {current} to stable {journal["id"]}')

    def refuse_second_first(self, files):
        released = self.backend.list('released/')
        if released:
            raise Refused('promote --first is spent: ' + released[0] + ' exists. Use the stamped promote.')
        github = open_github(self.args.github, False)
        old = github.fetch('stable', 'emaki.db')
        if old is None:
            return
        on_github = {e['name']: f for f, e in db_entries(old).items()}
        mirror_lists = set()
        for manifest in self.all_manifests().values():
            mirror_lists |= {f for f in manifest_packages(manifest) if f.startswith('emaki-mirrorlist-')}
        if on_github.get('emaki-mirrorlist') in mirror_lists:
            raise Refused(f'GitHub stable already serves {on_github["emaki-mirrorlist"]}, a mirrorlist '
                          'published through this mirror: released machines name it. Use the stamped promote.')

    def check_stamp(self, manifest_sha, bridge=False):
        """bridge: this act puts the first mirror release on GitHub stable, which every unedited
        0.1.x machine reads; its T2 must have been the unedited old address."""
        path = Path(self.args.stamp)
        if not path.is_file():
            raise Refused(f'no acceptance stamp at {path}: run the upgrade acceptance (tests/vm/upgrade-check.py)')
        stamp = json.loads(path.read_text())
        if stamp.get('manifest_sha256') != manifest_sha:
            raise Refused(f'the acceptance stamp is for MANIFEST {stamp.get("manifest_sha256")}, '
                          f'not for {manifest_sha}')
        if bridge:
            t2 = [entry for entry in stamp.get('via', []) if entry.startswith('T2 ')]
            if not t2 or any(not entry.endswith(' old-address') for entry in t2):
                raise Refused('this is the bridge (GitHub stable has no release from this mirror yet): its T2 '
                              'must be the unedited old address, run after promote --first; the stamp has '
                              + (', '.join(t2) or 'no T2 address'))
        if 'tested' not in stamp:
            raise Refused('the acceptance stamp does not say when its runs finished; write it again with stamp')
        # 24 hours from the oldest run, not from writing the stamp: Arch and the mirror move meanwhile.
        age = utcnow() - datetime.datetime.fromisoformat(stamp['tested'])
        if age > STAMP_MAX_AGE or age < -datetime.timedelta(minutes=5):
            raise Refused(f'the oldest run of the acceptance stamp finished {age} ago; it is valid for 24 hours. '
                          'Run the acceptance again.')
        say(f'acceptance stamp OK: MANIFEST {manifest_sha[:12]}, {age} old')

    def withdraw(self, channel, to):
        current, _ = self.pointer(channel)
        if current is None:
            raise Refused(f'{channel} has no snapshot')
        current_manifest = sha256(self.snapshot(channel, current)['MANIFEST'])
        records = self.served(channel)
        withdrawn = {r.get('withdrawn') for r in records if r.get('kind') == 'withdraw'} | {current_manifest}
        if to:
            if to not in {r['id'] for r in records}:
                raise Refused(f'{channel} never served {to}')
            target = to
        else:
            candidates = [r for r in records if r['id'] < current and r['manifest'] not in withdrawn]
            if not candidates:
                raise Refused(f'{channel} has no earlier release to return to')
            target = candidates[-1]['id']
        journal = self.begin('withdraw', channel, {'to': target, 'from': current})
        if 0 not in journal['steps']:
            if self.dry_run:
                say(f'DRY-RUN: withdraw {channel}: serve the files of {target} as a new snapshot {journal["id"]}')
                _, etag = self.pointer(channel)
                self.backend.put_pointer(f'pointers/{channel}', (journal['id'] + '\n').encode(), etag)
                return
            self.checked(journal)
        self.take_lock(journal)
        self.copy_snapshot(journal, channel, target, {'kind': 'withdraw', 'source': target,
                                                       'withdrawn': current_manifest},
                           lambda: None)  # the named exception: only to a snapshot this channel served
        say(f'withdrew {channel} {current}: now serving the files of {target} as {journal["id"]}. '
            'Machines that already updated keep the withdrawn versions (pacman -Su never downgrades).')

    # status / unlock / verify ----------------------------------------------------------------
    def status(self):
        for channel in CHANNELS:
            current, _ = self.pointer(channel)
            say(f'{channel}: {current or "(no pointer)"}')
        lock, _ = self.backend.get('locks/publish')
        say('lock: ' + (lock.decode() if lock else 'free'))
        say(f'this machine: {self.holder}')
        for journal in self.open_journals():
            say(f'open journal: {journal["kind"]} {journal["channel"]} {journal["id"]} '
                f'steps {journal["steps"]} — re-run the same command to finish it')

    def unlock(self, yes):
        lock, _ = self.backend.get('locks/publish')
        if not lock:
            say('lock: free')
            return
        if not yes:
            raise Refused(f'lock held: {lock.decode()}. Only if that publisher is dead: unlock --yes')
        self.backend.delete('locks/publish')
        say('lock removed')

    def verify(self, channel):
        current, _ = self.pointer(channel)
        if current is None:
            raise Refused(f'{channel} has no pointer')
        files = self.snapshot(channel, current)
        manifest = parse_manifest(files['MANIFEST'])
        self.confirm_channel(channel, files)
        for filename, digest in manifest_packages(manifest).items():
            status = http_head(f'{self.public}/{channel}/x86_64/{filename}')
            if status != 200:
                raise Refused(f'{channel}/x86_64/{filename}: HTTP {status}')
        say(f'{channel} {current}: database signed and served, {len(manifest_packages(manifest))} packages present')

    # GitHub ------------------------------------------------------------------------------------
    def github(self, tag, restore):
        target = open_github(self.args.github, self.dry_run)
        backups = self.state / 'github' / tag
        channel = 'testing' if tag == 'testing' else 'stable'
        if restore:
            # The newest saved database that is neither the one GitHub serves now (putting back
            # those bytes would restore nothing) nor the mirror's own current one.
            serving = target.fetch(tag, 'emaki.db')
            current, _ = self.pointer(channel)
            mirror = self.snapshot(channel, current)['emaki.db'] if current else None
            stamps = sorted(p for p in backups.glob('*') if (p / 'emaki.db').is_file()
                            and (p / 'emaki.db').read_bytes() not in (serving, mirror))
            if not stamps:
                raise Refused(f'no saved GitHub {tag} database differs from what GitHub {tag} serves now')
            for path in sorted(stamps[-1].iterdir()):
                if path.name.endswith(('.pkg.tar.zst', '.pkg.tar.zst.sig', '.sources.tar.gz')):
                    target.upload(tag, path, path.name)
            for name in SOURCE_FILES:
                if (stamps[-1] / name).is_file():
                    target.upload(tag, stamps[-1] / name, name)
                else:
                    target.delete(tag, name)
            target.notes(tag, (stamps[-1] / 'SOURCES').read_text()
                         if (stamps[-1] / 'SOURCES').is_file() else '')
            for name in ('emaki.files', 'emaki.db'):
                if (stamps[-1] / name).is_file():
                    target.upload(tag, stamps[-1] / name, name)
            say(f'GitHub {tag}: restored emaki.files and emaki.db from {stamps[-1]}')
            return
        if tag not in ('testing', 'stable'):
            raise Refused('github takes --tag testing or no tag (stable)')
        current, _ = self.pointer(channel)
        if current is None:
            raise Refused(f'the mirror has no {channel} snapshot to copy')
        files = self.snapshot(channel, current)
        if tag == 'stable':
            self.check_stamp(sha256(files['MANIFEST']), bridge=not self.backend.list('released/github-stable/'))
        self.verify_sources(channel, files)
        manifest = parse_manifest(files['MANIFEST'])
        # A retry must add pruned assets to the same backup as the pre-publish database.
        backup = backups / current
        previous = {name: target.fetch(tag, name) for name in ('emaki.db', 'emaki.files') + SOURCE_FILES}
        if previous['emaki.db'] == files['emaki.db']:
            # A re-run: an earlier run already saved what GitHub served before it.
            say(f'GitHub {tag} already holds the database of mirror {channel} {current}; no backup made')
        elif not (backup / 'emaki.db').is_file():
            for name, data in previous.items():
                if data is not None and not self.dry_run:
                    backup.mkdir(parents=True, exist_ok=True)
                    (backup / name).write_bytes(data)
        existing = target.assets(tag)
        with tempfile.TemporaryDirectory() as temp:
            for filename, digest in sorted(manifest_packages(manifest).items()):
                for suffix in ('', '.sig'):
                    name = filename + suffix
                    data, _ = self.backend.get(f'{channel}/x86_64/{name}')
                    expected = manifest[f'packages/{name}']
                    if data is None or sha256(data) != expected:
                        raise Refused(f'{channel}/x86_64/{name} is missing or differs from the MANIFEST')
                    if name in existing:
                        old = target.fetch(tag, name)
                        if old is not None and sha256(old) == expected:
                            continue
                        if not suffix:
                            raise Refused(f'GitHub {tag} holds another {name}; a published name is never replaced')
                    path = Path(temp) / name
                    path.write_bytes(data)
                    target.upload(tag, path, name)
                    say(f'GitHub {tag}: {name}')
            # Directions describe only packages already uploaded, including on an interrupted run.
            for name in SOURCE_FILES:
                path = Path(temp) / name
                path.write_bytes(files[name])
                target.upload(tag, path, name)
            target.notes(tag, files['SOURCES'].decode())
            # The database last, and never a database signature (P5: an address that once served
            # one must serve a matching one forever, and assets cannot change together).
            for name in ('emaki.files', 'emaki.db'):
                path = Path(temp) / name
                path.write_bytes(files[name])
                target.upload(tag, path, name)
                say(f'GitHub {tag}: {name} (bytes of mirror {channel} {current})')
            keep = set(manifest_packages(manifest))
            keep |= {name + '.sig' for name in keep}
            for name in sorted(existing - keep):
                if not (name.endswith('.pkg.tar.zst') or name.endswith('.pkg.tar.zst.sig')
                        or name.endswith('.sources.tar.gz')):
                    continue
                if not self.dry_run:
                    data = target.fetch(tag, name)
                    if data is not None:
                        backup.mkdir(parents=True, exist_ok=True)
                        (backup / name).write_bytes(data)
                target.delete(tag, name)
        if tag == 'stable' and not self.dry_run:
            try:
                self.backend.put_new(f'released/github-stable/{current}',
                                     json.dumps({'manifest': sha256(files['MANIFEST'])}).encode())
            except Exists:
                pass
        if backup.is_dir():
            say(f'previous GitHub {tag} database kept in {backup} (github --restore puts it back)')


def key_verifies(publisher, armored, image, signature):
    """True when the armored public key verifies the image's signature as a trusted key."""
    if not armored or not signature.is_file():
        return False
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / 'key.asc'
        path.write_bytes(armored)
        verifier = Verifier(path, publisher.trusted)
        try:
            return verifier.verify(image, signature)
        finally:
            verifier.close()


def iso_publish(publisher, path, full_check):
    """W11: dl.emaki.sh/iso/<version>/ gets the image, then .sig, then .sha256 last (the file a
    download page links first must not exist before what it describes). Refused unless stable
    already serves every Emaki package of the image's offline repository with the same bytes
    (PU-03) and the acceptance stamp names the stable snapshot."""
    import iso_sources
    args, dry_run = publisher.args, publisher.dry_run
    image = Path(path).resolve()
    match = re.fullmatch(r'emaki-(\d+\.\d+\.\d+)-x86_64\.iso', image.name)
    if not match or not image.is_file():
        raise Refused(f'{path}: expected an existing emaki-<version>-x86_64.iso')
    version = match.group(1)
    current, _ = publisher.pointer('stable')
    if current is None:
        raise Refused('stable serves nothing yet; an ISO is published only after its packages are in stable')
    files = publisher.snapshot('stable', current)
    publisher.check_stamp(sha256(files['MANIFEST']))
    stable = manifest_packages(parse_manifest(files['MANIFEST']))

    listing = subprocess.run(['bsdtar', '-tf', str(image)], capture_output=True, text=True, check=True).stdout
    ours = [entry for entry in listing.split('\n') if entry.startswith('emaki/repo/')
            and entry.endswith('.pkg.tar.zst') and entry.split('/')[-1].rsplit('-', 3)[0] in EMAKI_PACKAGES]
    if not ours:
        raise Refused(f'{image.name} has no Emaki packages in emaki/repo')
    if 'emaki/repo/closure.txt' not in listing.splitlines():
        raise Refused(f'{image.name} has no emaki/repo/closure.txt')
    closure = subprocess.run(['bsdtar', '-xOf', str(image), 'emaki/repo/closure.txt'],
                             capture_output=True, check=True).stdout
    try:
        filenames = closure.decode().splitlines()
    except UnicodeError:
        raise Refused('closure.txt is not UTF-8') from None
    actual = {entry.split('/')[-1] for entry in listing.splitlines()
              if entry.startswith('emaki/repo/') and entry.endswith('.pkg.tar.zst')}
    if not filenames or len(filenames) != len(set(filenames)) or set(filenames) != actual:
        raise Refused('closure.txt does not match the image package files')
    problems = []
    with tempfile.TemporaryDirectory() as temp:
        subprocess.run(['bsdtar', '-xf', str(image), '-C', temp, 'emaki/repo'], check=True)
        source_manifest = getattr(args, 'arch_sources', None)
        if source_manifest is None:
            raise Refused('ISO publication requires --arch-sources ARCH-SOURCES.json')
        try:
            arch_records, arch_objects = iso_sources.validate(
                source_manifest, Path(temp) / 'emaki/repo/closure.txt',
                Path(temp) / 'emaki/repo', EMAKI_PACKAGES)
        except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
            raise Refused(f'ISO source coverage failed: {error}') from None
        for entry in sorted(ours):
            filename = entry.split('/')[-1]
            digest = sha256_file(Path(temp) / entry)
            if stable.get(filename) != digest:
                problems.append(f'{filename} ({"not in stable" if filename not in stable else "other bytes"})')
    if problems:
        raise Refused('stable does not serve the packages of this image: ' + ', '.join(problems))
    records = source_records(files)
    image_sources = {}
    for entry in ours:
        name, version_ = package_version(entry.split('/')[-1])
        record = records.get(name)
        if record is None or record['version'] != version_:
            raise Refused(f'{name} {version_}: stable has no matching source record')
        image_sources[name] = record
    companions = {'closure.txt': closure,
                  'SOURCES-ISO.txt': sources_text(image_sources, publisher.public) + b'\n' +
                  iso_sources.directions(arch_records, args.dl_public_url).encode(),
                  'ARCH-SOURCES.json': Path(source_manifest).read_bytes()}
    publisher.verify_sources('stable', files)
    say(f'{len(ours)} Emaki packages of the image are served by stable {current} with the same bytes')

    sums, signature = Path(str(image) + '.sha256'), Path(str(image) + '.sig')
    if not (sums.is_file() and signature.is_file()):
        publisher.signer.prepare()
        if dry_run:
            say(f'DRY-RUN: would write {sums.name} and sign {signature.name} with {args.key}')
        else:
            env = dict(os.environ, EMAKI_ISO_SIGN_KEY=args.key, EMAKI_SIGNING_GNUPGHOME=str(args.signing_home))
            subprocess.run([str(HERE / 'iso-sums.sh'), str(image)], check=True, env=env)
    digest = sha256_file(image)
    if not dry_run:
        if sums.read_text() != f'{digest}  {image.name}\n':
            raise Refused(f'{sums.name} does not match the image')
        if not publisher.verifier.verify(image, signature):
            raise Refused(f'{signature.name} is not a valid signature by a trusted key in {publisher.trusted}')

    dl = open_backend(args.dl_backend, dry_run)
    public = args.dl_public_url.rstrip('/')
    key = f'iso/{version}/{image.name}'
    # Sources must exist before an image or its download directions can become public.
    for source_key_, source_path in arch_objects.items():
        source_digest = source_key_.split('/')[2]
        receipt, _ = dl.get(source_key_ + '.verified')
        etag = dl.head(source_key_)
        if etag is None:
            try:
                dl.put_large(source_key_, source_path)
            except Exists:
                pass
            etag = dl.head(source_key_)
        expected = {'sha256': source_digest, 'etag': etag}
        if receipt is None:
            if not dry_run and http_sha256(f'{public}/{source_key_}') != source_digest:
                raise Refused(f'{source_key_} differs from the image source manifest')
            publisher.put_or_same(source_key_ + '.verified',
                                  json.dumps(expected, sort_keys=True).encode(), dl)
        elif json.loads(receipt) != expected:
            raise Refused(f'{source_key_} differs from its verified upload')
        elif not dry_run and http_head(f'{public}/{source_key_}') != 200:
            raise Refused(f'{source_key_} is not publicly available')

    with tempfile.TemporaryDirectory() as temp:
        try:
            subprocess.run(['gpg', '--homedir', temp, '--no-autostart', '--batch', '--quiet', '--import',
                            str(publisher.keyring)], check=True, capture_output=True)
            armored = subprocess.run(['gpg', '--homedir', temp, '--no-autostart', '--batch', '--armor', '--export'],
                                     check=True, capture_output=True).stdout
        finally:
            stop_gnupg(temp)
    once = 'An ISO version is published once: build the image under a new version.'
    # The public key as of this image, next to it and written once like the image: the yearly
    # extension of the key (a new self-signature, other bytes) never meets an object that
    # cannot be replaced, and each image keeps the key that verifies it.
    key_file = f'iso/{version}/emaki-signing-key.asc'
    # Machines installed from this image read pkgs.emaki.sh/stable: from the first write that can
    # make it public, stable may move only through the stamped promote (promote --first is spent).
    try:
        publisher.backend.put_new(f'released/iso/{version}', json.dumps(
            {'stable': current, 'manifest': sha256(files['MANIFEST']), 'image_sha256': digest},
            sort_keys=True).encode())
    except Exists:
        pass
    published, _ = dl.get(key + '.sha256')
    if published is not None:
        if not sums.is_file() or published != sums.read_bytes():
            raise Refused(f'{key} is already published with other content; an ISO name is never replaced')
        say(f'{key}: already published with these bytes')
    else:
        try:
            publisher.put_or_same(key_file, armored, dl, hint=once)
        except Refused:
            # An interrupted run uploaded the key before an extension changed its bytes.
            existing, _ = dl.get(key_file)
            if not key_verifies(publisher, existing, image, signature):
                raise
            say(f'{key_file}: kept the copy an interrupted run uploaded; it verifies this image')
        try:
            dl.put_large(key, image)
        except Exists:
            # An earlier run died after the image: accept it only if it is this image, all of it
            # (two builds can share their first and last megabyte), before .sig and .sha256
            # vouch for it.
            if dry_run:
                say(f'DRY-RUN: {key} exists; a real run downloads it whole and compares its sha256')
            elif http_sha256(f'{public}/{key}') != digest:
                raise Refused(f'{key} exists and is another image (the sha256 of the whole download '
                              'differs)') from None
            else:
                say(f'{key}: already uploaded by an interrupted run (whole image, sha256 identical)')
        publisher.put_or_same(key + '.sig', signature if signature.is_file() else b'', dl, hint=once)
        for name, data in companions.items():
            publisher.put_or_same(f'iso/{version}/{name}', data, dl, hint=once)
        publisher.put_or_same(key + '.sha256', sums if sums.is_file() else b'', dl, hint=once)
    if published is not None:
        for name, data in companions.items():
            publisher.put_or_same(f'iso/{version}/{name}', data, dl, hint=once)
    if dry_run:
        return
    for name, data in companions.items():
        if http_get(f'{public}/iso/{version}/{name}') != data:
            raise Refused(f'the published {name} differs from the image source records')
    if http_get(f'{public}/{key}.sha256') != sums.read_bytes() or http_get(f'{public}/{key}.sig') != signature.read_bytes():
        raise Refused('the published .sha256 or .sig differs from the local files')
    size = image.stat().st_size
    with image.open('rb') as stream:
        head = stream.read(1048576)
        stream.seek(max(0, size - 1048576))
        tail = stream.read()
    if http_range(f'{public}/{key}', 0, len(head) - 1) != head or \
            http_range(f'{public}/{key}', size - len(tail), size - 1) != tail:
        raise Refused('the first or last megabyte of the published image differs')
    if full_check:
        if http_sha256(f'{public}/{key}') != digest:
            raise Refused('the full anonymous download differs from the image')
        say('full anonymous download: sha256 identical')
    say(f'published {public}/{key} (+ .sig, .sha256); first and last megabyte read back identical')


STEP_NAMES = {
    'publish': {0: 'checks', 1: 'lock', 2: 'packages uploaded', 3: 'packages verified anonymously',
                4: 'snapshot uploaded', 5: 'snapshot verified by pacman', 6: 'pointer flipped',
                7: 'channel confirmed, lock released'},
}
STEP_NAMES['promote'] = STEP_NAMES['publish']
STEP_NAMES['withdraw'] = STEP_NAMES['publish']


# acceptance stamp ----------------------------------------------------------------------------

def write_stamp(results, path):
    """The gate's input: T1 and T2 green for 0.1.0 and 0.1.1 at both screen sizes, all on one
    candidate MANIFEST. A single red result for that candidate refuses: a green run of the same
    kind does not cancel it."""
    loaded = [json.loads(Path(p).read_text()) for p in results]
    manifests = {r.get('manifest_sha256') for r in loaded}
    if len(manifests) != 1 or None in manifests:
        raise Refused(f'results name different candidates: {sorted(map(str, manifests))}')
    now = utcnow()
    green = set()
    for result in loaded:
        if result.get('rehearsal'):
            raise Refused(f'{result["run"]} from {result["start"]} was a rehearsal against a local mirror, '
                          'not acceptance')
        finished = datetime.datetime.fromisoformat(result['finished'])
        if now - finished > STAMP_MAX_AGE:
            raise Refused(f'{result["run"]} from {result["start"]} finished {finished}, more than 24 hours ago')
        if result.get('passed') is not True:
            raise Refused(f'{result["run"]} from {result["start"]} at {result.get("size")} is red '
                          f'({result.get("evidence", "no evidence directory")}); one red result for the '
                          'candidate refuses the stamp')
        green.add((result['run'], result['start'], result.get('size')))
    missing = [f'{run} from {start} at {size}' for run in REQUIRED_RUNS for start in REQUIRED_STARTS
               for size in REQUIRED_SIZES if (run, start, size) not in green]
    if missing:
        raise Refused('the acceptance is not green for: ' + ', '.join(missing))
    stamp = {'manifest_sha256': manifests.pop(), 'created': now.isoformat(timespec='seconds'),
             'tested': min(datetime.datetime.fromisoformat(r['finished']) for r in loaded).isoformat(),
             'runs': sorted(f'{r} {s} {z}' for r, s, z in green),
             # How each run reached the candidate (upgrade-check.py): the bridge needs T2 by the old address.
             'via': sorted({f'{r["run"]} {r["start"]} {r.get("via")}' for r in loaded}),
             'arch': loaded[-1].get('arch', {})}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(stamp, indent=1, sort_keys=True) + '\n')
    say(f'stamp written: {path} (MANIFEST {stamp["manifest_sha256"]})')


def sign_directory(args):
    signer = Signer(args.key, args.signing_home, args.dry_run)
    verifier = Verifier(args.keyring, args.trusted)
    try:
        for path in sorted(Path(args.directory).glob('*.pkg.tar.zst')):
            signature = Path(str(path) + '.sig')
            if not signature.exists():
                signer.sign(path)
                if args.dry_run:
                    continue
            if not verifier.verify(path, signature):
                raise Refused(f'{signature.name} does not verify against {args.keyring}')
            say(f'signed: {path.name}')
    finally:
        verifier.close()


def parser():
    state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'emaki-publish'
    main = argparse.ArgumentParser(prog='publish.sh', description=__doc__.split('\n')[0])
    main.add_argument('--backend', default=os.environ.get('EMAKI_PUBLISH_BACKEND', 'r2:emaki-pkgs'),
                      help='local:DIR or r2:BUCKET (default r2:emaki-pkgs)')
    main.add_argument('--public-url', default=os.environ.get('EMAKI_PUBLISH_PUBLIC_URL', 'https://pkgs.emaki.sh'))
    main.add_argument('--github', default=os.environ.get('EMAKI_PUBLISH_GITHUB', GITHUB_REPO),
                      help='owner/repo, or local:DIR for tests')
    main.add_argument('--state-dir', default=str(state))
    main.add_argument('--stamp', default=str(state / 'acceptance-stamp.json'))
    main.add_argument('--key', default=os.environ.get('EMAKI_SIGN_KEY', DEFAULT_KEY))
    main.add_argument('--signing-home', default=os.environ.get(
        'EMAKI_SIGNING_GNUPGHOME', str(Path.home() / '.config/emaki-signing/gnupg')))
    main.add_argument('--keyring', default=str(ROOT / 'packaging/emaki-keyring/emaki.gpg'))
    main.add_argument('--trusted', default=str(ROOT / 'packaging/emaki-keyring/emaki-trusted'))
    main.add_argument('--registry', default=str(HERE / 'released-packages.sha256'))
    main.add_argument('--dl-backend', default=os.environ.get('EMAKI_PUBLISH_DL_BACKEND', 'r2:emaki-dl'),
                      help='where ISO images go: local:DIR or r2:BUCKET (default r2:emaki-dl)')
    main.add_argument('--dl-public-url', default=os.environ.get('EMAKI_PUBLISH_DL_URL', 'https://dl.emaki.sh'))
    main.add_argument('--source-tree', default=str(ROOT))
    main.add_argument('--closure', default='emaki emaki-apps', help='packages that must resolve')
    main.add_argument('--arch-dbs', default='auto',
                      help="'auto' (fresh core/extra via /etc/pacman.d/mirrorlist), 'none', 'skip' or a DIR "
                           'holding core.db and extra.db')
    main.add_argument('--dry-run', action='store_true', help='read everything, write nothing, print the writes')
    main.add_argument('--layout', choices=('snapshot', 'flat'), default='snapshot', help=argparse.SUPPRESS)
    sub = main.add_subparsers(dest='command', required=True)
    p = sub.add_parser('publish', help='upload packages and serve them in testing')
    p.add_argument('channel', choices=('testing',), help='testing; stable changes only through promote')
    p.add_argument('directory', help='built packages with their .sig files')
    sub.add_parser('status')
    u = sub.add_parser('unlock')
    u.add_argument('--yes', action='store_true')
    v = sub.add_parser('verify')
    v.add_argument('channel', choices=CHANNELS)
    pr = sub.add_parser('promote', help='testing -> stable; needs the acceptance stamp')
    pr.add_argument('--first', action='store_true', help='once, for the bridge release')
    w = sub.add_parser('withdraw')
    w.add_argument('channel', choices=CHANNELS)
    w.add_argument('--to', help='a snapshot id this channel served before')
    g = sub.add_parser('github', help='copy the mirror channel to the old GitHub address')
    g.add_argument('--tag', default='stable', choices=('stable', 'testing'))
    g.add_argument('--restore', action='store_true')
    s = sub.add_parser('sign', help='sign packages that have no .sig yet')
    s.add_argument('directory')
    i = sub.add_parser('iso', help='publish an ISO image whose packages stable already serves')
    i.add_argument('image')
    i.add_argument('--arch-sources', type=Path, help='verified ARCH-SOURCES.json from iso_sources.py')
    i.add_argument('--full-check', action='store_true', help='download the whole image back and compare')
    st = sub.add_parser('stamp', help='write the acceptance stamp from upgrade-check results')
    st.add_argument('results', nargs='+')
    return main


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command not in ('status', 'verify', 'stamp'):
            # Checked as a file, at the start: a missing pinentry prompt would prove nothing,
            # because gpg-agent caches a typed passphrase.
            passphrase = Path.home() / '.config/emaki-signing/passphrase'
            if passphrase.exists():
                raise Refused(f'{passphrase} exists: a passphrase must not lie next to the key. Delete it '
                              'and type the passphrase when pinentry asks.')
        if args.command == 'sign':
            sign_directory(args)
            return 0
        if args.command == 'stamp':
            write_stamp(args.results, args.stamp)
            return 0
        publisher = Publisher(args)
        try:
            if args.command == 'publish':
                if args.layout == 'flat' and not args.backend.startswith('local:'):
                    raise Refused('the flat layout exists only for the local self-check')
                publisher.publish(args.channel, args.directory)
            elif args.command == 'status':
                publisher.status()
            elif args.command == 'unlock':
                publisher.unlock(args.yes)
            elif args.command == 'verify':
                publisher.verify(args.channel)
            elif args.command == 'promote':
                publisher.promote(args.first)
            elif args.command == 'withdraw':
                publisher.withdraw(args.channel, args.to)
            elif args.command == 'iso':
                iso_publish(publisher, args.image, args.full_check)
            elif args.command == 'github':
                publisher.github(args.tag, args.restore)
        finally:
            publisher.close()
    except Refused as error:
        print(f'REFUSED: {error}', file=sys.stderr, flush=True)
        return 2
    except (subprocess.CalledProcessError, KeyError, PermissionError, r2.S3Error) as error:
        print(f'ERROR: {error}', file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == '__main__':
    # SIGTERM unwinds like an error: the finally blocks and atexit stop the GnuPG daemons of the
    # temporary homes and remove them. The journal makes the run resumable either way.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    sys.exit(main())
