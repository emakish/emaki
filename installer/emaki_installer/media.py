# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Find live packages and freeze online recovery before any disk writes."""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlsplit

from .constants import OFFLINE_CONF, WORK
from .errors import Code, InstallError, require

MISSING_NOTICE = ('The installation files were not found on the live medium. '
                  'Packages will be downloaded and checked before your disk is changed. '
                  'Keep the internet connection active (Ventoy or copytoram).')


def mounted_roots(root=Path('/')):
    """Read mount targets without mounting or probing any additional filesystem."""
    root = Path(root)
    try:
        lines = (root / 'proc/self/mountinfo').read_text().splitlines()
    except OSError:
        return []
    result = []
    for line in lines:
        fields = line.split()
        if len(fields) >= 6 and ' - ' in line:
            result.append(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), fields[4]))
    return result


def discover_repo(root=Path('/'), mount_roots=None):
    """Prefer the ordinary medium; retain incomplete repos for the strict preflight."""
    root = Path(root).resolve()
    mounts = mounted_roots(root) if mount_roots is None else mount_roots
    candidates = ['/run/archiso/bootmnt', '/run/archiso/copytoram', *mounts]
    for mount in dict.fromkeys(map(str, candidates)):
        if not mount.startswith('/'):
            continue
        candidate = root / mount.lstrip('/') / 'emaki/repo'
        try:
            candidate.resolve().relative_to(root)
            if candidate.exists():
                return candidate
        except (OSError, ValueError):
            continue
    return None


@dataclass
class PackageSource:
    config: Path
    repo: Path
    archives: list | None = None

    def validate(self):
        from .arch_backend import offline_config
        return offline_config(self.config, repo=self.repo.as_uri())


def _config(path, repo):
    path.write_text('[options]\nArchitecture = auto\n'
                    'SigLevel = Required DatabaseOptional TrustedOnly\n'
                    'LocalFileSigLevel = Required TrustedOnly\n\n'
                    '[emaki-offline]\nSigLevel = Required DatabaseOptional TrustedOnly\n'
                    f'Server = {repo.as_uri()}\n')
    return path


def _download(runner, work, packages, root):
    """Resolve against an empty database, fetch every archive and its detached signature."""
    db = work / 'db'
    cache = work / 'repo'
    db.mkdir()
    cache.mkdir()
    conf = work / 'online.conf'
    conf.write_text('[options]\nArchitecture = auto\n'
                    'SigLevel = Required DatabaseOptional TrustedOnly\n'
                    'LocalFileSigLevel = Required TrustedOnly\n\n'
                    f'[emaki]\nInclude = {root / "etc/pacman.d/emaki-mirrorlist"}\n\n'
                    f'[core]\nInclude = {root / "etc/pacman.d/mirrorlist"}\n\n'
                    f'[extra]\nInclude = {root / "etc/pacman.d/mirrorlist"}\n')
    options = ['--config', str(conf), '--dbpath', str(db), '--cachedir', str(cache),
               '--logfile', str(work / 'pacman.log')]
    runner.run(['pacman', '-Sy', '--noconfirm', *options])
    output = runner.run(['pacman', '-Sp', '--print-format', '%l', *options, *packages])
    urls = [line.strip() for line in output.splitlines() if '://' in line]
    require(urls, Code.OFFLINE_REPO_INCOMPLETE,
            'No installation packages could be found online; your disk has not been changed.')
    archives, names = [], set()
    for url in urls:
        parsed = urlsplit(url)
        name = unquote(parsed.path.rsplit('/', 1)[-1])
        require(parsed.scheme == 'https' and parsed.hostname and not parsed.username
                and not parsed.query and not parsed.fragment and '/' not in name
                and re.fullmatch(r'[A-Za-z0-9@_+.:~-]+\.pkg\.tar\.(?:zst|xz|gz)', name)
                and name not in names, Code.OFFLINE_REPO_INCOMPLETE,
                'The package download address is unsafe; your disk has not been changed.')
        names.add(name)
        archive = cache / name
        for suffix in ('', '.sig'):
            destination = Path(str(archive) + suffix)
            runner.run(['curl', '--fail', '--location', '--proto', '=https',
                        '--proto-redir', '=https', '--connect-timeout', '15',
                        '--speed-limit', '1024', '--speed-time', '60',
                        '--output', str(destination), url + suffix])
            require(destination.is_file() and destination.stat().st_size > 0,
                    Code.OFFLINE_REPO_INCOMPLETE,
                    'A package download is incomplete; your disk has not been changed.')
        # Verify before repo-add reads any downloaded archive metadata.
        runner.run(['pacman-key', '--verify', str(archive) + '.sig', str(archive)])
        archives.append(archive)
    runner.run(['repo-add', str(cache / 'emaki-offline.db.tar.gz'), *map(str, archives)])
    require((cache / 'emaki-offline.db').is_file(), Code.OFFLINE_REPO_INCOMPLETE,
            'The downloaded package repository could not be prepared; your disk has not been changed.')
    return cache, archives


@contextmanager
def package_source(runner, packages, *, online, root=Path('/'), mount_roots=None,
                   notice=lambda message: None, work_parent=WORK):
    """Keep the selected repository alive until pacstrap and all package phases finish."""
    root = Path(root).resolve()
    repo = discover_repo(root, mount_roots)
    if repo == root / 'run/archiso/bootmnt/emaki/repo':
        source = PackageSource(root / str(OFFLINE_CONF).lstrip('/'), repo)
        source.validate()
        yield source
        return
    if repo is None:
        notice(MISSING_NOTICE)
        require(online, Code.OFFLINE_REPO_INCOMPLETE,
                'The installation files are missing. Connect to the internet and try again '
                '(Ventoy or copytoram). Your disk has not been changed.')
    Path(work_parent).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='packages-', dir=work_parent) as temporary:
        work = Path(temporary)
        archives = None
        if repo is None:
            try:
                repo, archives = _download(runner, work, packages, root)
            except (OSError, InstallError) as exc:
                raise InstallError(Code.OFFLINE_REPO_INCOMPLETE,
                                   'The installation files could not be downloaded and checked. '
                                   'Check the internet connection and free space in the live session, then try again; your disk has not been changed. '
                                   + str(exc)) from exc
        source = PackageSource(_config(work / 'offline.conf', repo), repo, archives)
        source.validate()
        yield source
