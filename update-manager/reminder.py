# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Arch restart evidence, with a dismissal scoped to the current boot.

Only actual version changes count. systemd, glibc and the current CPU's microcode
are conservative restart triggers because existing processes can retain old code.
A kernel event must belong to the running kernel, never merely another installed
kernel. Unknown custom kernels need a surviving modules/pkgbase file to identify
that relationship. Package logs are read by this backend, never by the window.
"""
from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

EVENT = re.compile(r'^\[([^\]]+)\] \[ALPM\] (?:upgraded|downgraded) ([a-zA-Z0-9@._+:-]+) \(([^ ]+) -> ([^ ]+)\)$')
PACKAGE = re.compile(r'[a-zA-Z0-9@._+:-]+')


def running_package(release, modules=Path('/lib/modules')):
    """Identify stock Arch kernels even after an update removed old modules."""
    if not release or '/' in release or release in ('.', '..'):
        return ''
    try:
        package = (Path(modules) / release / 'pkgbase').read_text().strip()
        if PACKAGE.fullmatch(package):
            return package
    except (OSError, UnicodeError):
        pass
    if re.fullmatch(r'\d+\.\d+(?:\.\d+)?-arch\d+-\d+', release):
        return 'linux'
    for suffix, build in (('lts', r'\d+'), ('zen', r'zen\d+-\d+'),
                          ('hardened', r'hardened\d+-\d+'),
                          ('rt-lts', r'(?:rt\d+-)?\d+'), ('rt', r'rt\d+-\d+')):
        if re.fullmatch(r'\d+\.\d+(?:\.\d+)?-' + build + '-' + suffix, release):
            return 'linux-' + suffix
    return ''


def changed_packages(lines, boot_time, kernel, vendor):
    """Return restart-relevant changes logged since boot, ignoring predictions."""
    relevant = {'glibc', 'systemd'}
    if kernel:
        relevant.add(kernel)
    microcode = {'GenuineIntel': 'intel-ucode', 'AuthenticAMD': 'amd-ucode'}.get(vendor)
    if microcode:
        relevant.add(microcode)
    changed = set()
    for line in lines:
        match = EVENT.fullmatch(line.rstrip('\n'))
        if not match:
            continue
        timestamp, package, old, new = match.groups()
        if package not in relevant or old == new:
            continue
        try:
            moment = datetime.fromisoformat(timestamp)
            # Old log formats without a zone are local time, as pacman wrote them.
            if moment.timestamp() < boot_time:
                continue
        except (ValueError, OverflowError, OSError):
            continue
        changed.add(package)
    return changed


def boot_context(proc=Path('/proc')):
    proc = Path(proc)
    boot_id = (proc / 'sys/kernel/random/boot_id').read_text().strip()
    if not re.fullmatch(r'[a-fA-F0-9-]{36}', boot_id):
        raise ValueError('Invalid boot identity')
    boot_time = next(int(line.split()[1]) for line in (proc / 'stat').read_text().splitlines()
                     if line.startswith('btime '))
    vendor = ''
    try:
        for line in (proc / 'cpuinfo').read_text().splitlines():
            if line.startswith('vendor_id') and ':' in line:
                vendor = line.split(':', 1)[1].strip()
                break
    except OSError:
        pass
    return boot_id, boot_time, vendor


def cache_path(cache_dir=None):
    directory = Path(cache_dir) if cache_dir is not None else Path(
        os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'emaki-update-manager'
    return directory / 'restart.json'


def read_state(path, boot_id):
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or data.get('bootId') != boot_id:
            return {}
        packages = data.get('packages', [])
        if not isinstance(packages, list) or any(not isinstance(p, str) or not PACKAGE.fullmatch(p) for p in packages):
            return {}
        return dict(bootId=boot_id, packages=packages, dismissed=data.get('dismissed') is True)
    except (OSError, ValueError, UnicodeError):
        return {}


def write_state(path, state):
    """Best effort cache; a read-only cache cannot hide actual restart evidence."""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.restart-', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream)
        temporary.replace(path)
    except OSError:
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


@contextmanager
def state_lock(cache_dir):
    # Shell probes and a window dismissal may overlap. Serialize their cache
    # read/modify/write so a late probe cannot resurrect a dismissed reminder.
    stream = None
    try:
        path = cache_path(cache_dir).with_suffix('.lock')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        stream = path.open('a')
        fcntl.flock(stream, fcntl.LOCK_EX)
    except OSError:
        if stream is not None:
            stream.close()
        stream = None
    try:
        yield
    finally:
        if stream is not None:
            stream.close()


def status(cache_dir=None, **options):
    """Expose restart action and an undismissed reminder, without acknowledging it."""
    with state_lock(cache_dir):
        return _status(cache_dir, **options)


def _status(cache_dir=None, *, proc=Path('/proc'), logs=None, release=None, modules=Path('/lib/modules')):
    """Expose restart action and an undismissed reminder, without acknowledging it."""
    empty = dict(restart=False, reminder=False, restartPackages=[])
    try:
        boot_id, boot_time, vendor = boot_context(proc)
    except (OSError, ValueError, StopIteration, IndexError):
        return empty
    path = cache_path(cache_dir)
    state = read_state(path, boot_id)
    packages = set(state.get('packages', []))
    kernel = running_package(os.uname().release if release is None else release, modules)
    for log in (Path('/var/log/pacman.log.1'), Path('/var/log/pacman.log')) if logs is None else logs:
        try:
            with Path(log).open(errors='replace') as stream:
                packages.update(changed_packages(stream, boot_time, kernel, vendor))
        except OSError:
            continue
    updated = dict(bootId=boot_id, packages=sorted(packages), dismissed=state.get('dismissed', False))
    if updated != state:
        write_state(path, updated)
    return dict(restart=bool(packages), reminder=bool(packages) and not updated['dismissed'],
                restartPackages=sorted(packages))


def acknowledge(cache_dir=None, *, proc=Path('/proc')):
    """Dismiss this boot's reminder; keep restart evidence available to the window."""
    with state_lock(cache_dir):
        return _acknowledge(cache_dir, proc=proc)


def _acknowledge(cache_dir=None, *, proc=Path('/proc')):
    """Dismiss this boot's reminder; keep restart evidence available to the window."""
    try:
        boot_id, _, _ = boot_context(proc)
    except (OSError, ValueError, StopIteration, IndexError):
        return False
    path = cache_path(cache_dir)
    state = read_state(path, boot_id)
    state.update(bootId=boot_id, dismissed=True)
    state.setdefault('packages', [])
    write_state(path, state)
    return read_state(path, boot_id).get('dismissed', False)


def notify_restart(cache_dir=None):
    """Send the existing boot's undismissed restart advice through the user bus."""
    state = status(cache_dir)
    if not state.get('restart') or not state.get('reminder'):
        return dict(ok=True, notified=False)
    return notify('Restart to finish the update',
                  'The new kernel and system services start after a restart. Save your work first.')


def notify_completed():
    """Report a successful update even when no restart is needed."""
    return notify('Updated', 'All updates are installed.')


def notify(title, body):
    arguments = ['busctl', '--user', '--timeout=2', 'call', '--',
                 'org.freedesktop.Notifications', '/org/freedesktop/Notifications',
                 'org.freedesktop.Notifications', 'Notify', 'susssasa{sv}i',
                 'Programs & Updates', '0', 'emaki-programs-updates',
                 title, body,
                 '0', '2', 'resident', 'b', 'true', 'urgency', 'y', '1', '0']
    try:
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=3, check=False)
        return dict(ok=result.returncode == 0, notified=result.returncode == 0)
    except (OSError, subprocess.SubprocessError):
        return dict(ok=False, notified=False)
