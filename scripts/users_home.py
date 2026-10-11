# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Root-only preservation of a removed account's home, without following links."""

import ctypes
import datetime
import errno
import os
import stat


def _check_parent(fd):
    info = os.fstat(fd)
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError("Home ancestors must be root-owned and not writable by others")


def _rename_noreplace(parent, source, target):
    # A check followed by rename() can overwrite a concurrently created archive.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                       ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(source), parent, os.fsencode(target), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), target)


def retain(home: str, uid: int) -> str | None:
    """Seal and rename a home before account removal; return None if absent.

    The caller must reject a home shared with another account and serialize account
    changes. Every ancestor must be trusted; no symlink is followed. Existing open
    file descriptors remain valid, so the caller must also reject signed-in users.
    Any failure aborts deletion. A failure after sealing deliberately leaves the
    directory protected rather than restoring its former permissions or owner.
    """
    if (not isinstance(home, str) or not home.startswith("/") or "\x00" in home
            or os.path.normpath(home) != home or home.startswith("//")
            or len(home.split("/")) < 3 or uid <= 0):
        raise ValueError("Unsafe home path or account ID")
    parts = home.split("/")[1:]
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError("Unsafe home path")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    parent = os.open("/", flags)
    directory = None
    try:
        _check_parent(parent)
        for component in parts[:-1]:
            try:
                child = os.open(component, flags, dir_fd=parent)
            except FileNotFoundError:
                return None
            os.close(parent)
            parent = child
            _check_parent(parent)
        try:
            directory = os.open(parts[-1], flags, dir_fd=parent)
        except FileNotFoundError:
            return None
        original = os.fstat(directory)
        if original.st_uid != uid:
            raise ValueError("Home does not belong to the removed account")
        os.fchown(directory, 0, 0)
        os.fchmod(directory, 0o700)
        sealed = os.fstat(directory)
        if sealed.st_uid != 0 or sealed.st_gid != 0 or stat.S_IMODE(sealed.st_mode) != 0o700:
            raise OSError("Could not protect retained home")
        current = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
            raise OSError("Home changed during retention")
        base = parts[-1] + ".removed-" + datetime.date.today().strftime("%Y%m%d")
        for suffix in range(10000):
            target = base + ("-" + str(suffix) if suffix else "")
            try:
                _rename_noreplace(parent, parts[-1], target)
            except FileExistsError:
                continue
            archived = os.stat(target, dir_fd=parent, follow_symlinks=False)
            if (archived.st_dev, archived.st_ino) != (original.st_dev, original.st_ino):
                raise OSError("Retained home changed during rename")
            return os.path.join(os.path.dirname(home), target)
        raise OSError(errno.EEXIST, "Too many retained homes", home)
    finally:
        if directory is not None:
            os.close(directory)
        os.close(parent)
