#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Repair and atomically save the shell's private notification history."""
import json
import os
from pathlib import Path
import secrets
import stat
import sys

from clipboard_store import directory


def repair_file(folder):
    try:
        fd = os.open('notifications.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=folder)
    except FileNotFoundError:
        return
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise PermissionError('unsafe history file')
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)


def save(text):
    root = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'emaki'
    try:
        folder = directory(root, create=text is not None)
    except FileNotFoundError:
        if text is None:
            return
        raise
    try:
        os.fchmod(folder, 0o700)
        repair_file(folder)
        if text is None:
            return
        temporary = '.notifications-' + secrets.token_hex(16)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=folder)
        try:
            with os.fdopen(fd, 'wb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(text.encode('utf-8'))
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(temporary, 'notifications.json', src_dir_fd=folder, dst_dir_fd=folder)
            os.fsync(folder)
        finally:
            try:
                os.unlink(temporary, dir_fd=folder)
            except FileNotFoundError:
                pass
    finally:
        os.close(folder)


if __name__ == '__main__':
    os.umask(0o077)
    try:
        request = json.loads(sys.stdin.readline())
        save(request['text'])
    except (OSError, ValueError, KeyError, TypeError):
        # Never print message bodies or paths to the shell log.
        sys.exit(1)
