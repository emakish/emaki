#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep clipboard history in the login runtime and retire its old database once."""
import os
from pathlib import Path
import stat
import sys


def runtime_db():
    value = os.environ.get('XDG_RUNTIME_DIR', '')
    if not value or not os.path.isabs(value):
        raise PermissionError('missing runtime directory')
    root = Path(value)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError('unsafe runtime directory')
    db = root / 'emaki-cliphist.db'
    try:
        info = db.lstat()
    except FileNotFoundError:
        return db
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise PermissionError('unsafe clipboard database')
    return db


def directory(path, create=False):
    """Open without following any directory symlink; callers close the returned fd."""
    path = Path(path)
    if not path.is_absolute():
        raise PermissionError('relative directory')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if part in ('.', '..'):
                raise PermissionError('unsafe directory')
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        if os.fstat(fd).st_uid != os.getuid():
            raise PermissionError('foreign directory')
        return fd
    except BaseException:
        os.close(fd)
        raise


def prepare():
    runtime_db()  # Never fall back to a persistent database if the login has no runtime.
    try:
        retire_legacy_database()
    except OSError:
        # Cleanup can retry next login; it must not disable runtime recording.
        pass


def retire_legacy_database():
    state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'emaki'
    fd = directory(state, create=True)
    try:
        # Serialize both the niri and optional shell recorder startup.
        import fcntl
        marker = os.open('clipboard-runtime-v1', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=fd)
        try:
            info = os.fstat(marker)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise PermissionError('unsafe migration marker')
            fcntl.flock(marker, fcntl.LOCK_EX)
            if os.read(marker, 1) == b'1':
                return
            cache = Path(os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')
            try:
                old = directory(cache / 'cliphist')
            except FileNotFoundError:
                old = None
            if old is not None:
                try:
                    try:
                        info = os.stat('db', dir_fd=old, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        # Unlink only the regular database Emaki's old watcher created.
                        if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1:
                            os.unlink('db', dir_fd=old)
                        else:
                            return  # Leave the marker incomplete until cleanup is safe.
                finally:
                    os.close(old)
            os.lseek(marker, 0, os.SEEK_SET)
            os.write(marker, b'1')
            os.fsync(marker)
        finally:
            os.close(marker)
    finally:
        os.close(fd)


if __name__ == '__main__':
    prepare()
    if sys.argv[1:] == ['--watch']:
        os.execvp('wl-paste', ['wl-paste', '--watch', 'cliphist',
                             '-config-path', '/dev/null', '-db-path', str(runtime_db()), 'store'])
