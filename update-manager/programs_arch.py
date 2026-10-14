# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read repository metadata and preview full repository installation transactions."""
from contextlib import contextmanager
import fcntl
import hashlib
from importlib.util import module_from_spec, spec_from_file_location
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile

try:
    from emaki_paths import BINDIR
except ModuleNotFoundError:
    BINDIR = str(Path(os.defpath.split(os.pathsep)[-1]))
ENV = {'PATH': str(BINDIR) + os.pathsep + os.defpath, 'LC_ALL': 'C', 'SYSTEMD_COLORS': '0'}
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9@._+\-]*\Z')


class MetadataUnavailable(RuntimeError):
    def __init__(self, repositories=()):
        detail = ('Repository metadata is unavailable: ' + ', '.join(repositories) + '.'
                  if repositories else 'Repository metadata is incomplete.')
        super().__init__(detail + ' Refresh the catalog and try again.')


def cache_directory():
    # Privileged helpers never read a caller-controlled home or cache directory.
    if os.geteuid() == 0:
        return Path('/var/cache/emaki-programs')
    return Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'emaki-update-manager'


def repository_names():
    names = run(['pacman-conf', '--repo-list']).splitlines()
    if not names or any(not NAME.fullmatch(name) for name in names):
        raise ValueError('Invalid repository name')
    return names


def database_arguments(database):
    return ['--dbpath', str(database), '--logfile', '/dev/null', '--color', 'never']


def sync_database(database):
    command = ['pacman', '-Sy', '--disable-sandbox-filesystem', *database_arguments(database)]
    if os.geteuid() != 0:
        command = ['fakeroot', '--', *command]
    def interrupted(signum, _frame):
        raise SystemExit(128 + signum)
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        with subprocess.Popen(command, cwd='/', env=ENV, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, start_new_session=True) as process:
            try:
                process.communicate(timeout=180)
            except BaseException:
                # No downloader may outlive the lock or the private database.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                raise
            return process.returncode == 0
    finally:
        signal.signal(signal.SIGTERM, previous)


@contextmanager
def private_database(cache=None, refresh=False, strict=True, warnings=None):
    """Keep repository copies in the cache; queries see a locked local snapshot.

    Catalog and both planners share this cache. Helpers build their own trusted
    copy and compare the repository rows in the plan token before any mutation.
    """
    cache = cache_directory() if cache is None else Path(cache)
    cache.mkdir(parents=True, exist_ok=True, mode=0o755 if os.geteuid() == 0 else 0o700)
    if os.geteuid() == 0:
        # Helpers use umask 077; the download user still needs directory traversal.
        cache.chmod(0o755)
    descriptor = os.open(cache / 'repositories.lock',
                         os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(descriptor, 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Expanded configuration includes mirror servers, channel and signatures.
        configuration = run(['pacman-conf'])
        identity = hashlib.sha256(configuration.encode()).hexdigest()
        saved = cache / ('repositories-' + identity)
        # With the lock held no query is live. Discard crashed queries and old
        # channels, including a previous visit to a channel later selected again.
        for path in cache.iterdir():
            if (path.name.startswith('repository-query-') or
                    (re.fullmatch(r'repositories-[0-9a-f]{64}', path.name) and path != saved)):
                if path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    shutil.rmtree(path)
        saved.mkdir(exist_ok=True, mode=0o755)
        saved.chmod(0o755)
        repositories = repository_names()
        system = Path(run(['pacman-conf', 'DBPath']).strip())
        if not system.is_absolute():
            raise ValueError('Invalid package database path')
        if (system / 'db.lck').exists():
            raise RuntimeError('Another package transaction is running. Wait for it to finish and try again.')
        before = parse_versions(run(['pacman', '-Q']))
        with tempfile.TemporaryDirectory(prefix='repository-query-', dir=cache) as temporary:
            database = Path(temporary)
            # Root downloads use pacman's DownloadUser, which must traverse this.
            database.chmod(0o755)
            shutil.copytree(system / 'local', database / 'local')
            (database / 'sync').mkdir()
            (database / 'sync').chmod(0o755)
            for repository in repositories:
                for suffix in ('.db', '.db.sig'):
                    source = saved / (repository + suffix)
                    if source.is_file():
                        shutil.copy2(source, database / 'sync' / source.name)
            missing = any(not (database / 'sync' / (name + '.db')).is_file() for name in repositories)
            if refresh or missing:
                try:
                    refreshed = sync_database(database)
                except (OSError, subprocess.SubprocessError):
                    refreshed = False
                if not refreshed and warnings is not None:
                    warnings.append('Repository metadata could not be refreshed. Cached programs are shown where available.')
                # Keep successful repositories even if a different remote failed.
                for repository in repositories:
                    for suffix in ('.db', '.db.sig'):
                        source = database / 'sync' / (repository + suffix)
                        if source.is_file():
                            shutil.copy2(source, saved / (source.name + '.new'))
                            os.replace(saved / (source.name + '.new'), saved / source.name)
                        else:
                            (saved / source.name).unlink(missing_ok=True)
            if ((system / 'db.lck').exists() or before != parse_versions(run(['pacman', '-Q']))):
                raise RuntimeError('Installed packages changed. Refresh the catalog and try again.')
            unavailable = [name for name in repositories if not (database / 'sync' / (name + '.db')).is_file()]
            if strict and unavailable:
                raise MetadataUnavailable(unavailable)
            yield database
            if (system / 'db.lck').exists() or before != parse_versions(run(['pacman', '-Q'])):
                raise RuntimeError('Installed packages changed. Refresh the catalog and try again.')


def validate_target(target):
    if not isinstance(target, str) or len(target) > 512:
        raise ValueError('Select one repository program.')
    pieces = target.split('/')
    if len(pieces) != 2 or any(not NAME.fullmatch(piece) for piece in pieces):
        raise ValueError('Select one repository program.')
    return pieces


def run(arguments, timeout=60):
    result = subprocess.run(arguments, capture_output=True, text=True, cwd='/', env=ENV, timeout=timeout)
    if result.returncode:
        raise RuntimeError('Repository package information could not be read.')
    return result.stdout


def parse_versions(text):
    result = {}
    for line in text.splitlines():
        name, version = line.split(maxsplit=1)
        if not NAME.fullmatch(name) or not version:
            raise ValueError('Invalid installed package record')
        result[name] = version
    return result


def parse_desc(text, repository):
    fields = {}
    key = None
    for line in text.splitlines():
        if line.startswith('%') and line.endswith('%'):
            key = line[1:-1]
            fields[key] = []
        elif line and key:
            fields[key].append(line)
    def field(name):
        return ' '.join(fields.get(name, []))
    def size(name):
        value = field(name)
        return int(value) if value.isascii() and value.isdigit() else None
    def package_names(name):
        return [dependency for value in fields.get(name, [])
                if NAME.fullmatch(dependency := re.split(r'[<>=:]', value, maxsplit=1)[0].strip())]
    name = field('NAME')
    validate_target(repository + '/' + name)
    if not field('VERSION'):
        raise ValueError('Missing package version')
    return {'repository': repository, 'package': name, 'version': field('VERSION'),
            'summary': field('DESC'), 'licence': field('LICENSE'),
            'optionalPackages': package_names('OPTDEPENDS'),
            'requiredPackages': package_names('DEPENDS'),
            'providedPackages': package_names('PROVIDES'),
            'downloadSize': size('CSIZE'), 'installedSize': size('ISIZE')}


def inventory(database=None, warnings=None, cache=None, refresh=False):
    """Read each available archive independently, preserving installed packages."""
    if cache is not None:
        with private_database(cache, refresh=refresh, strict=False, warnings=warnings) as private:
            return inventory(private, warnings)
    installed = parse_versions(run(['pacman', '-Q']))
    database = Path(run(['pacman-conf', 'DBPath']).strip()) if database is None else Path(database)
    if not database.is_absolute():
        raise ValueError('Invalid package database path')
    packages = {}
    for repository in repository_names():
        records = {}
        try:
            with tarfile.open(database / 'sync' / (repository + '.db'), 'r:*') as archive:
                for member in archive:
                    if not member.isfile() or not member.name.endswith('/desc'):
                        continue
                    if member.size > 1024 * 1024:
                        raise ValueError('Oversized package metadata')
                    with archive.extractfile(member) as stream:
                        package = parse_desc(stream.read().decode('utf-8'), repository)
                    records[repository + '/' + package['package']] = package
        except (OSError, ValueError, tarfile.TarError):
            if warnings is not None:
                warnings.append('Repository metadata is unavailable: ' + repository + '.')
            continue
        packages.update(records)
    return packages, installed


def parse_plan(text):
    packages = []
    for line in text.splitlines():
        # Informational replacement messages are not package records.
        if '|' not in line:
            continue
        fields = line.split('|')
        if len(fields) != 4:
            raise ValueError('Invalid transaction record')
        repository, name, version, size = fields
        validate_target(repository + '/' + name)
        if not version:
            raise ValueError('Missing transaction version')
        packages.append({'repository': repository, 'package': name, 'version': version,
                         'downloadSize': int(size) if size.isascii() and size.isdigit() else None})
    return sorted(packages, key=lambda item: (item['repository'], item['package']))


def graphics_policy():
    """Load the identical driver policy shipped with the installer."""
    path = Path(__file__).with_name('programs_graphics.py')
    if not path.exists():
        path = Path(__file__).resolve().parents[1] / 'installer/emaki_installer/graphics.py'
    spec = spec_from_file_location('programs_graphics', path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def graphics_devices():
    return graphics_policy().detect()


def install_targets(target, packages, devices=None):
    """Expand only the fixed NVIDIA support target using trusted hardware and metadata."""
    if target.split('/', 1)[1] != 'emaki-nvidia':
        return [target]
    by_name = {}
    for key in packages:
        by_name.setdefault(key.split('/', 1)[1], key)
    names = graphics_policy().package_selection(graphics_devices() if devices is None else devices, by_name)
    if not names:
        raise ValueError('This hardware uses the built-in graphics driver. NVIDIA support is not needed.')
    if any(name not in by_name for name in names):
        raise ValueError('The NVIDIA support packages are not available. Refresh repository updates and try again.')
    return [target if name == 'emaki-nvidia' else by_name[name] for name in names]


def install_plan(target, cache=None, refresh=False, database=None):
    validate_target(target)
    if database is None:
        with private_database(cache, refresh=refresh) as private:
            return install_plan(target, database=private)
    packages, installed = inventory(database)
    if target not in packages:
        return {'ok': False, 'code': 'unavailable', 'action': 'install', 'kind': 'arch', 'target': target,
                'message': 'This repository program is no longer available.'}
    try:
        targets = install_targets(target, packages)
    except ValueError as error:
        return {'ok': False, 'code': 'unavailable', 'action': 'install', 'kind': 'arch',
                'target': target, 'message': str(error)}
    planned = parse_plan(run(['pacman', '-Sup', '--needed', '--noconfirm',
                              '--print-format', '%r|%n|%v|%s', *database_arguments(database), *targets]))
    state = {'target': target, 'targets': targets, 'candidates': [packages[key] for key in targets], 'packages': planned, 'installed': installed}
    token = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'ok': True, 'code': 'ready', 'action': 'install', 'kind': 'arch', 'target': target,
            'targets': targets, 'packages': planned, 'token': token, 'fullUpgrade': True,
            'message': 'Installing also updates all repository packages. The upgrade checks fresh metadata; the final package list and sizes may change.'}
