# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Publish immutable shell generations and pin them for a compositor's lifetime."""
from collections import deque
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time

STORE = Path('@EMAKI_LOCALSTATEDIR@/shell-generations')
RUNTIMES = Path('/run/user')
NAME = re.compile(r'[0-9a-f]{64}')
RECORD_LIMIT = 16384
LOCK_WAIT = 30


def trusted_path(path, *, allow_missing=False):
    """Check every traversed component, including the full targets of symlinks."""
    path = Path(path)
    if not path.is_absolute():
        raise ValueError(f'trusted path is not absolute: {path}')
    pending, current, links = deque(path.parts[1:]), Path('/'), 0
    while True:
        try:
            info = current.lstat()
        except FileNotFoundError:
            if allow_missing:
                return None
            raise
        if info.st_uid != 0 or (not stat.S_ISLNK(info.st_mode) and info.st_mode & 0o022):
            raise ValueError(f'trusted path is not root-owned and protected: {current}')
        if stat.S_ISLNK(info.st_mode):
            links += 1
            if links > 40:
                raise ValueError(f'trusted path has too many links: {path}')
            target = Path(os.readlink(current))
            current = Path('/') if target.is_absolute() else current.parent
            pending.extendleft(reversed(target.parts[1:] if target.is_absolute() else target.parts))
            continue
        if not pending:
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ValueError(f'trusted path is not a file or directory: {current}')
            return current
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f'trusted path component is not a directory: {current}')
        part = pending.popleft()
        current = current.parent if part == '..' else current / part


def trusted_tree(path, links=True, seen=None, allow_dangling=False):
    """Validate files as well as directories; a safe parent does not protect a writable file."""
    path = Path(path)
    if path.is_symlink() and not links:
        raise ValueError(f'links are not allowed in a shell generation: {path}')
    resolved = trusted_path(path, allow_missing=allow_dangling and path.is_symlink())
    if resolved is None or not resolved.is_dir():
        return
    seen = set() if seen is None else seen
    if resolved in seen:
        return
    seen.add(resolved)
    for entry in resolved.iterdir():
        trusted_tree(entry, links=links, seen=seen, allow_dangling=allow_dangling)


def process_start(pid):
    try:
        return Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, ValueError, IndexError):
        return None


def live(value, uid):
    try:
        pid = value['controller']
        return (isinstance(pid, int) and pid > 1 and isinstance(value['start'], str)
                and Path(f'/proc/{pid}').stat().st_uid == uid
                and process_start(pid) == value['start'])
    except (OSError, KeyError, TypeError):
        return False


def read_json_at(directory, name):
    descriptor = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > RECORD_LIMIT:
            raise ValueError('invalid shell generation record')
        value = json.loads(stream.read(RECORD_LIMIT + 1))
        if not isinstance(value, dict):
            raise ValueError('invalid shell generation record')
        return value


def write_json(path, value, mode=0o600):
    fd, temporary = tempfile.mkstemp(prefix='.record-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def locked(store):
    """Only publishers lock; the old world-readable .lock is never consulted."""
    trusted_path(store)
    lock = store / '.publish-lock'
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW
                         | os.O_NONBLOCK, 0o600)
    try:
        trusted_path(lock)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError('the shell publication lock is not private')
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('the shell generation lock is busy') from None
                time.sleep(0.05)
        yield
    finally:
        os.close(descriptor)


def write_pointer(store, name, value):
    descriptor, temporary = tempfile.mkstemp(prefix='.pointer-', dir=store)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(value + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, store / name)
    finally:
        Path(temporary).unlink(missing_ok=True)


def valid_generation(generation):
    trusted_tree(generation, links=False)
    for required in ('shell/shell.qml', 'shell/Emaki/Sudo/libemakisudoplugin.so',
                     'emaki', 'watch.py', 'emaki_session_state.py', 'generation.json'):
        if not (generation / required).is_file():
            raise ValueError(f'incomplete shell generation: {generation / required}')
    return generation


def newest_generation(store):
    trusted_path(store)
    candidates = []
    for path in store.iterdir():
        if NAME.fullmatch(path.name):
            try:
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    candidates.append((info.st_mtime_ns, path.name, path))
            except OSError:
                continue
    for _, _, path in sorted(candidates, reverse=True):
        try:
            return valid_generation(path)
        except (OSError, ValueError):
            continue
    raise ValueError('no valid shell generation is available')


def pointer(store, name):
    path = store / name
    trusted_path(path)
    value = path.read_text().strip()
    if not NAME.fullmatch(value):
        raise ValueError(f'invalid shell generation pointer: {path}')
    return value


def select(controller, store=None):
    """The user record is advisory: it can select only a fully protected generation."""
    store = STORE if store is None else store
    uid = os.getuid()
    start = process_start(controller)
    if start is None or not live(dict(controller=controller, start=start), uid):
        raise ValueError('the compositor process is no longer live')
    runtime = RUNTIMES / str(uid)
    info = runtime.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
        raise ValueError('a private login runtime directory is required')
    directory = runtime / 'emaki-shell-generations'
    directory.mkdir(mode=0o700, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
        raise ValueError('unsafe shell generation record directory')
    record = directory / f'{controller}-{start}.json'
    generation = None
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        try:
            value = read_json_at(descriptor, record.name)
            if (value.get('controller') == controller and value.get('start') == start
                    and isinstance(value.get('generation'), str)
                    and NAME.fullmatch(value['generation'])):
                generation = valid_generation(store / value['generation'])
        except (OSError, ValueError):
            pass
    finally:
        os.close(descriptor)
    # A complete rename is publication even if updating the convenience pointer
    # later fails. Readers never wait for root's publication lock.
    for attempt in range(3):
        generation = generation or newest_generation(store)
        write_json(record, dict(controller=controller, start=start, generation=generation.name))
        try:
            return valid_generation(generation)
        except (OSError, ValueError):
            if attempt == 2:
                raise
            generation = None


def retained(runtimes=None):
    """Never follow a user record link as root; uncertainty disables pruning."""
    runtimes = RUNTIMES if runtimes is None else runtimes
    keep = set()
    if not runtimes.exists():
        return keep
    for runtime in runtimes.iterdir():
        if not runtime.name.isdecimal():
            continue
        uid = int(runtime.name)
        parent = os.open(runtime, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            try:
                directory = os.open('emaki-shell-generations', os.O_RDONLY | os.O_DIRECTORY
                                    | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
            except FileNotFoundError:
                continue
            try:
                for name in os.listdir(directory):
                    if not name.endswith('.json'):
                        continue
                    value = read_json_at(directory, name)
                    generation = value.get('generation')
                    if isinstance(generation, str) and NAME.fullmatch(generation) and live(value, uid):
                        keep.add(generation)
            finally:
                os.close(directory)
        finally:
            os.close(parent)
    return keep


def prune(store):
    try:
        keep = retained() | {pointer(store, 'current')}
        if (store / 'previous').exists():
            keep.add(pointer(store, 'previous'))
    except (OSError, ValueError):
        # Records are untrusted input. A damaged record may retain disk space,
        # but must never remove a generation still serving a live compositor.
        return
    for path in store.iterdir():
        if NAME.fullmatch(path.name) and path.name not in keep:
            trusted_tree(path, links=False)
            # Published directories are read-only even for accidental root writes.
            for directory, _, _ in os.walk(path):
                Path(directory).chmod(0o755)
            shutil.rmtree(path)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def tree(path):
    return {str(item.relative_to(path)): digest(item) for item in sorted(path.rglob('*'))
            if item.is_file() and '__pycache__' not in item.parts and item.suffix not in ('.pyc', '.pyo')}


def publish(source, binary, watcher, modules, store=None):
    """Called only at package time, after the installed payload is complete."""
    store = STORE if store is None else store
    for path in (source, binary, watcher, *modules):
        trusted_tree(path, links=False)
    store.mkdir(parents=True, exist_ok=True, mode=0o755)
    with locked(store):
        files, core = tree(source), digest(binary)
        helpers = {module.name: digest(module) for module in modules}
        version = hashlib.sha256(json.dumps([files, core, digest(watcher), helpers],
                                           sort_keys=True).encode()).hexdigest()
        target = store / version
        if not target.exists():
            stage = Path(tempfile.mkdtemp(prefix='.preparing-', dir=store))
            try:
                shutil.copytree(source, stage / 'shell',
                                ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))
                shutil.copy2(binary, stage / 'emaki')
                shutil.copy2(watcher, stage / 'watch.py')
                for module in modules:
                    shutil.copy2(module, stage / module.name)
                if (tree(stage / 'shell') != files or digest(stage / 'emaki') != core
                        or digest(stage / 'watch.py') != digest(watcher)
                        or any(digest(stage / name) != value for name, value in helpers.items())):
                    raise ValueError('installed files changed while publishing a shell generation')
                write_json(stage / 'generation.json', dict(schema=1, trusted=True, version=version,
                           source=str(source), binary=str(binary), files=files, core=core), mode=0o444)
                for item in stage.rglob('*'):
                    item.chmod(0o555 if item.is_dir() or item.stat().st_mode & 0o111 else 0o444)
                stage.chmod(0o555)
                stage.rename(target)
            finally:
                if stage.exists():
                    for directory, _, _ in os.walk(stage):
                        Path(directory).chmod(0o755)
                    shutil.rmtree(stage)
        trusted_tree(target, links=False)
        # Directory metadata records publication order, including a deliberate
        # downgrade to an existing digest; the generation's contents stay fixed.
        os.utime(target, None)
        try:
            old = pointer(store, 'current')
        except (OSError, ValueError):
            old = None
        if old != version:
            if old is not None:
                write_pointer(store, 'previous', old)
            write_pointer(store, 'current', version)
        prune(store)
    return target
