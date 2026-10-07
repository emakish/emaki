# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare real pacman transactions without downloading or installing packages."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile


class TransactionError(Exception):
    """A clean install or full upgrade cannot be prepared."""


def records(data):
    result = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
            for member in archive:
                if not member.isfile() or not member.name.endswith('/desc'):
                    continue
                raw = archive.extractfile(member).read()
                fields = {}
                key = None
                for line in raw.decode().splitlines():
                    if line.startswith('%') and line.endswith('%'):
                        key = line[1:-1]
                        fields[key] = []
                    elif line and key:
                        fields[key].append(line)
                result[fields['NAME'][0]] = (fields, raw)
    except (tarfile.TarError, UnicodeError, KeyError, IndexError, ValueError, EOFError) as error:
        raise TransactionError(f'Invalid repository database [pacman]: {error}') from None
    return result


def seed(client, selected):
    local = client.dir / 'db/local'
    shutil.rmtree(local, ignore_errors=True)
    local.mkdir()
    (local / 'ALPM_DB_VERSION').write_text('9\n')
    for name, (fields, raw) in selected.items():
        # Database names become local paths; reject malformed repository metadata.
        version = fields['VERSION'][0]
        if any('/' in item or item in ('.', '..') for item in (name, version)):
            raise TransactionError('Invalid package metadata [pacman].')
        directory = local / f'{name}-{version}'
        directory.mkdir()
        (directory / 'desc').write_bytes(raw)
        (directory / 'files').write_text('%FILES%\n\n')


def prepare(client, config, label, *arguments):
    # --print skips conflicts. Accept pacman's defaults and stop at missing package
    # downloads: the frozen server and private cache contain databases only.
    try:
        result = subprocess.run(
            ['fakeroot', '--', 'pacman', '--config', str(config), '--noconfirm', *arguments],
            capture_output=True, text=True,
            env=dict(os.environ, LC_ALL='C', LANG='C'), timeout=120)
    except subprocess.TimeoutExpired:
        raise TransactionError(f'{label} timed out [pacman].') from None
    output = result.stdout + result.stderr
    if ':: Retrieving packages...' in output or (
            result.returncode == 0 and 'there is nothing to do' in output):
        return
    raise TransactionError(f'{label} cannot resolve [pacman]:\n{output.strip()}')


def check_transactions(client, repo_dir, targets, arch_dbs, previous_db=None):
    """Use a caller-owned ScratchPacman; return the scenarios successfully prepared.

    Previous Emaki metadata is exact. The Arch baseline uses dependency packages from the
    supplied/current Arch databases because published snapshots do not retain historical Arch
    databases. No package contents, hooks, scriptlets, or system files are installed.
    """
    if arch_dbs == 'skip':
        raise TransactionError('Transaction checks cannot be skipped [pacman].')
    candidate = records((Path(repo_dir) / 'emaki.db').read_bytes())
    names = list(targets)
    result = client.resolve(repo_dir, names, arch_dbs)
    if result.returncode:
        raise TransactionError('Fresh install cannot resolve [pacman]:\n' +
                               (result.stdout + result.stderr).strip())
    config = client.dir / 'pacman.conf'
    databases = {'emaki': candidate}
    for path in (client.dir / 'db/sync').glob('*.db'):
        if path.stem != 'emaki':
            databases[path.stem] = records(path.read_bytes())
    # Freeze every database under a local server for the actual -Syu checks.
    frozen = client.dir / 'transaction-repository'
    frozen.mkdir(exist_ok=True)
    for path in (client.dir / 'db/sync').glob('*.db'):
        shutil.copyfile(path, frozen / path.name)
    config = client._config([(name, f'Server = {frozen.as_uri()}') for name in databases], 'Never')
    # This server contains only databases: no package content can be downloaded or installed.
    selected = {}
    arch_selected = {}
    for line in result.stdout.splitlines():
        repository_name, version = line.split(' ', 1)
        repository, name = repository_name.split('/', 1)
        record = databases[repository][name]
        if record[0]['VERSION'][0] != version:
            raise TransactionError('Package metadata changed during checks [pacman].')
        selected[name] = record
        if repository != 'emaki':
            arch_selected[name] = record
    seed(client, {})
    prepare(client, config, 'Fresh install', '-S', *names)
    seed(client, selected)
    prepare(client, config, 'Fresh install upgrade', '-Syu')
    checked = ['fresh install', 'fresh install upgrade']
    if previous_db is not None:
        previous = {name: record for name, record in records(previous_db).items()
                    if name != 'emaki-installer'}
        # Keep old dependency names when they can still resolve against today's Arch. Older
        # exact Arch bounds may already be obsolete; the candidate can legitimately repair them.
        sync = client.dir / 'db/sync/emaki.db'
        candidate_bytes = sync.read_bytes()
        sync.write_bytes(previous_db)
        seed(client, {})
        old = client.run(config, '-S', '--print', '--print-format', '%r/%n %v', *sorted(previous))
        sync.write_bytes(candidate_bytes)
        if old.returncode == 0:
            for line in old.stdout.splitlines():
                repository_name, _ = line.split(' ', 1)
                repository, name = repository_name.split('/', 1)
                if repository != 'emaki':
                    arch_selected[name] = databases[repository][name]
        seed(client, {**arch_selected, **previous})
        prepare(client, config, 'Previous release upgrade', '-Syu')
        checked.append('previous release upgrade')
    return checked
