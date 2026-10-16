#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Estimate package transactions against a private database and root-space budget."""
from decimal import Decimal, ROUND_CEILING
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time

# Isolated hook execution excludes the script directory from the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from emaki_package_lock import lock_message

MARGIN = 1024 ** 3
SPACE_ADVICE = 'Delete unneeded files or clear the package cache in Settings > Storage, then try again.'
FAILURE = 'The free-space check could not finish. The update was not started.'
FINAL_PROMPT = ':: Proceed with installation? [Y/n]'
WARNING = 'Warning: the update space check is unavailable; the package transaction will continue.'


class InsufficientSpace(RuntimeError):
    """Only a measured shortage may abort the package transaction."""


def failure(output):
    """Keep the first package-manager diagnostic when an estimate is refused."""
    text = output.decode('utf-8', errors='replace')
    diagnostic = re.search(r'^error:.*$', text, re.M)
    return FAILURE + ('\n' + diagnostic[0].rstrip() if diagnostic else '')


def stop(process):
    """Stop the whole probe before its private database is removed."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def command(arguments, probe=False):
    with subprocess.Popen(arguments, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, start_new_session=True,
                          env=dict(os.environ, LC_ALL='C')) as process:
        output = b''
        try:
            if not probe:
                output, _ = process.communicate(timeout=180)
            else:
                pending = ''
                declined = False
                deadline = time.monotonic() + 180
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    while True:
                        if time.monotonic() >= deadline:
                            raise RuntimeError(failure(output))
                        if not selector.select(1):
                            continue
                        chunk = os.read(process.stdout.fileno(), 4096)
                        if not chunk:
                            break
                        output += chunk
                        pending += chunk.decode('utf-8', errors='replace')
                        if len(output) > 4 * 1024 * 1024:
                            raise RuntimeError(failure(output))
                        line = pending.rstrip('\n').rsplit('\n', 1)[-1].strip()
                        if line.endswith('[Y/n]') or line.endswith('[y/N]'):
                            if line == FINAL_PROMPT:
                                answer = b'n\n'
                                declined = True
                            elif not declined and re.fullmatch(r':: Replace .+ with .+\? \[Y/n\]', line):
                                answer = b'\n'
                            elif not declined and re.fullmatch(r':: .+ are in conflict.* Remove .+\? \[y/N\]', line):
                                answer = b'y\n'
                            elif not declined and re.fullmatch(r':: .*[Ss]kip the above packages?.*\[y/N\]', line):
                                answer = b'y\n'
                            else:
                                raise RuntimeError(failure(output))
                            process.stdin.write(answer)
                            process.stdin.flush()
                            pending = ''
                        elif (re.fullmatch(r'Enter a number \(default=\d+\):', line)
                              or line == 'Enter a selection (default=all):') and not declined:
                            process.stdin.write(b'\n')
                            process.stdin.flush()
                            pending = ''
                process.wait(timeout=5)
                if not declined and 'there is nothing to do' not in output.decode('utf-8', errors='replace'):
                    raise RuntimeError(failure(output))
            text = output.decode('utf-8', errors='replace')
            if process.returncode != 0 and not (probe and process.returncode == 1 and declined):
                raise RuntimeError(failure(output))
            if probe and re.search(r'^error:', text, re.M):
                raise RuntimeError(failure(output))
            return text
        except subprocess.TimeoutExpired as error:
            stop(process)
            raise RuntimeError(failure(error.output or output)) from error
        except BaseException:
            stop(process)
            raise


def transaction_sizes(text):
    """pacman's C-locale summary accounts for replacements and cached archives."""
    if 'there is nothing to do' in text and FINAL_PROMPT not in text:
        return 0, 0, 0
    fields = {}
    for name, amount, unit in re.findall(
            r'^(Total Download Size|Total Installed Size|Total Removed Size|Net Upgrade Size):\s+'
            r'(-?\d+\.\d+)\s+(B|KiB|MiB|GiB|TiB)\s*$', text, re.M):
        if name in fields:
            raise RuntimeError(FAILURE)
        # pacman rounds its display to two decimals; round the upper bound up.
        scale = 1024 ** ('B', 'KiB', 'MiB', 'GiB', 'TiB').index(unit)
        fields[name] = int(((Decimal(amount) + Decimal('0.005')) * scale).to_integral_value(rounding=ROUND_CEILING))
    if FINAL_PROMPT not in text or 'Total Installed Size' not in fields:
        raise RuntimeError(FAILURE)
    for name in ('Total Download Size', 'Total Installed Size', 'Total Removed Size', 'Net Upgrade Size'):
        if name + ':' in text and name not in fields:
            raise RuntimeError(FAILURE)
    download = fields.get('Total Download Size', 0)
    net = fields.get('Net Upgrade Size', fields['Total Installed Size'] - fields.get('Total Removed Size', 0))
    if download < 0 or fields['Total Installed Size'] < 0:
        raise RuntimeError(FAILURE)
    return download, net, fields['Total Installed Size']


def human_size(size):
    divisor, unit = (10 ** 9, 'GB') if size >= 10 ** 9 else (10 ** 6, 'MB')
    return f'{size / divisor:.1f} {unit}'


def mount_details(path, mountinfo=None):
    """Match the visible containing mount, ignoring btrfs's per-subvolume device IDs."""
    try:
        path = Path(path).resolve()
        text = mountinfo if mountinfo is not None else Path('/proc/self/mountinfo').read_text()
        mounts = []
        for line in text.splitlines():
            before, after = line.split(' - ', 1)
            fields, filesystem = before.split(), after.split()
            unescape = lambda value: re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)
            target = Path(unescape(fields[4]))
            source = unescape(filesystem[1])
            # Pseudo filesystems and anonymous sources cannot prove separation.
            identity = os.path.realpath(source) if source.startswith('/dev/') else None
            mounts.append((fields[0], fields[1], target, identity, filesystem[0]))
        # Walk the mount tree as path lookup does: at each path prefix, a mount whose parent is
        # the mount visible so far covers it, so a later /var/cache hides an earlier, deeper
        # /var/cache/pacman/pkg that hangs off the mount it covered.
        roots = [mount for mount in mounts if mount[2] == Path('/')]
        at_root = {mount[0] for mount in roots}
        visible = next((mount for mount in reversed(roots) if mount[1] not in at_root), None)
        for prefix in (*reversed(path.parents), path):
            seen = set()  # A malformed table must not loop.
            while visible is not None and visible[0] not in seen:
                seen.add(visible[0])
                covering = [mount for mount in mounts if mount[2] == prefix and mount[1] == visible[0]]
                if not covering:
                    break
                visible = covering[-1]
        if visible is not None:
            return visible[3], visible[4]
    except (OSError, ValueError, IndexError):
        pass
    return None, None


def root_snapshots(kind):
    """Allow for retained replacement files when snapper can snapshot the root."""
    if kind != 'btrfs':
        return False
    try:
        if Path('/.snapshots').is_dir():
            return True
        for config in Path('/etc/snapper/configs').glob('*'):
            if re.search(r'^SUBVOLUME=["\']?/["\']?\s*$', config.read_text(), re.M):
                return True
    except OSError:
        # If configuration cannot be read, do not assume replacements free space.
        return True
    return False


def requirements(download, net, root, cache, pools=(None, None), installed=0):
    """Only split budgets when both backing filesystems are known to differ."""
    root_need = max(0, net, installed) + MARGIN
    if None in pools or pools[0] == pools[1]:
        return [(download + root_need, min(root.f_bavail * root.f_frsize, cache.f_bavail * cache.f_frsize))]
    return [(root_need, root.f_bavail * root.f_frsize),
            (download, cache.f_bavail * cache.f_frsize)]


def check_space(download, net, root, cache, pools=(None, None), installed=0):
    for need, free in requirements(download, net, root, cache, pools, installed):
        if free < need:
            raise InsufficientSpace('Not enough free space for this update: it needs about '
                               f'{human_size(need)}, {human_size(free)} is free. '
                               + SPACE_ADVICE)


def _preflight(targets=(), transaction=None, expected=()):
    database = Path(command(['pacman-conf', 'DBPath']).strip())
    caches = command(['pacman-conf', 'CacheDir']).splitlines()
    cache = next((Path(path) for path in caches if Path(path).is_dir()
                  and os.access(path, os.W_OK, effective_ids=True)), None)
    if not database.is_absolute() or cache is None:
        raise RuntimeError(FAILURE)
    initial = (database / 'local').stat().st_mtime_ns
    if transaction is None and (database / 'db.lck').exists():
        raise RuntimeError(lock_message(database / 'db.lck'))
    with tempfile.TemporaryDirectory(prefix='emaki-update-space-', dir='/tmp') as directory:
        private = Path(directory)
        # pacman's DownloadUser needs traversal; only root can modify this DB.
        private.chmod(0o755)
        shutil.copytree(database / 'local', private / 'local', symlinks=True)
        if (database / 'sync').is_dir():
            shutil.copytree(database / 'sync', private / 'sync')
        base = ['--dbpath', str(private), '--logfile', '/dev/null', '--color', 'never']
        if transaction is None:
            command(['pacman', '-Sy', *base])
            selection = ['-Su', *(['--needed', *targets] if targets else [])]
        else:
            # The parent owns db.lck and has already downloaded its archives. Use
            # its current databases, never refresh them or probe the live lock.
            selection = transaction
            resolved = command(['pacman', *selection, *base, '--print',
                                '--print-format', '%n', '--noconfirm']).splitlines()
            if len(resolved) != len(expected) or set(resolved) != set(expected):
                raise RuntimeError(FAILURE)
        # This subprocess can only reach the summary: the installation prompt
        # is rejected explicitly, never accepted through a default answer.
        summary = command(['pacman', selection[0], '--confirm', *base, *selection[1:]], probe=True)
        download, net, installed = transaction_sizes(summary)
    if transaction is None and (database / 'db.lck').exists():
        raise RuntimeError(lock_message(database / 'db.lck'))
    if (database / 'local').stat().st_mtime_ns != initial:
        raise RuntimeError('Installed packages changed during the check. Try again when the package operation finishes.')
    if download or net or installed:
        root_pool, root_kind = mount_details('/')
        cache_pool, _ = mount_details(cache)
        retained = installed if root_snapshots(root_kind) else 0
        check_space(download, net, os.statvfs('/'), os.statvfs(cache),
                    (root_pool, cache_pool), retained)


def preflight(targets=(), transaction=None, expected=()):
    def interrupted(signum, frame):
        raise SystemExit(128 + signum)

    previous = {signum: signal.signal(signum, interrupted) for signum in (signal.SIGHUP, signal.SIGTERM)}
    try:
        if transaction is not None:
            _preflight(transaction=transaction, expected=expected)
        elif targets:
            _preflight(targets=targets)
        else:
            _preflight()
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(FAILURE) from error
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def sync_selection(arguments):
    """Reproduce ordinary sync transactions; unknown options cannot justify refusal.

    Hook targets contain names only. A local archive or a custom root/database
    could contain different versions, so those transactions remain unmeasured.
    """
    sync = False
    upgrades = 0
    options, targets = [], []
    for argument in arguments:
        if argument == '--sync':
            sync = True
        elif argument == '--sysupgrade':
            upgrades += 1
        elif re.fullmatch(r'-[Suyq]+', argument):
            sync |= 'S' in argument
            upgrades += argument.count('u')
        elif argument == '--needed':
            options.append(argument)
        elif argument in ('--refresh', '--quiet', '--noconfirm', '--confirm',
                          '--asdeps', '--asexplicit', '--disable-download-timeout'):
            continue
        elif re.fullmatch(r'(?:[A-Za-z0-9@_+][A-Za-z0-9@_.+:-]*/)?[A-Za-z0-9@_+][A-Za-z0-9@_.+:-]*', argument):
            targets.append(argument)
        else:
            raise ValueError('unrecognized transaction')
    if not sync or (not upgrades and not targets):
        raise ValueError('unrecognized transaction')
    return ['-S' + 'u' * upgrades, *options, *targets]


def process_parent(pid):
    status = (Path('/proc') / str(pid) / 'status').read_text()
    match = re.search(r'^PPid:\s+(\d+)$', status, re.M)
    if match is None:
        raise ValueError('unknown parent process')
    return int(match[1])


def parent_selection(launcher_pid=None):
    parent_pid = os.getppid()
    if launcher_pid is not None:
        if not re.fullmatch(r'[1-9][0-9]*', launcher_pid):
            raise ValueError('invalid launcher process')
        launcher = int(launcher_pid)
        # Command substitution may retain an extra shell. The supplied PID must
        # be our actual launcher ancestor, whose immediate parent is pacman.
        for _ in range(3):
            if parent_pid == launcher:
                break
            parent_pid = process_parent(parent_pid)
        else:
            raise ValueError('unrelated launcher process')
        parent_pid = process_parent(launcher)
    parent = Path('/proc') / str(parent_pid)
    executable = shutil.which('pacman')
    if executable is None or (parent / 'exe').resolve(strict=True) != Path(executable).resolve(strict=True):
        raise ValueError('unknown package manager')
    arguments = (parent / 'cmdline').read_bytes().rstrip(b'\0').decode().split('\0')
    return sync_selection(arguments[1:])


def transaction_preflight(targets, launcher_pid=None):
    try:
        if mount_details('/')[1] != 'btrfs':
            raise ValueError('not a measured Btrfs root')
        if not targets or len(set(targets)) != len(targets) or any(
                not re.fullmatch(r'[A-Za-z0-9@_+][A-Za-z0-9@_.+:-]*', name) for name in targets):
            raise ValueError('unrecognized targets')
        preflight(transaction=parent_selection(launcher_pid), expected=targets)
    except InsufficientSpace as error:
        print(str(error), file=sys.stderr, flush=True)
        return 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        print(WARNING, file=sys.stderr, flush=True)
    return 0


if __name__ == '__main__':
    # Reserve this status for the shell launcher's measured-shortage contract;
    # interpreter/import failures normally exit 1 and must remain fail-open.
    targets = sys.stdin.read().splitlines()
    if len(sys.argv) != 2:
        print(WARNING, file=sys.stderr, flush=True)
        sys.exit(0)
    sys.exit(3 if transaction_preflight(targets, sys.argv[1]) else 0)
