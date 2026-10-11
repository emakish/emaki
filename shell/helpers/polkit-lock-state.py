#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stream the supervisor's authentication gate; errors always block requests."""
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import select
import stat
import struct
import sys


def blocked(environ=None):
    environ = os.environ if environ is None else environ
    try:
        base = Path(environ['XDG_RUNTIME_DIR'])
        info = base.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return True
        display = Path(environ.get('WAYLAND_DISPLAY', 'wayland-0'))
        display = display if display.is_absolute() else base / display
        namespace = hashlib.sha256(os.fsencode(str(display.resolve()))).hexdigest()[:16]
        directory = base / ('emaki-lock-' + namespace)
        try:
            info = directory.lstat()
        except FileNotFoundError:
            return False  # This compositor session has not requested a lock.
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            return True
        marker = directory / 'authentication-blocked'
        try:
            info = marker.lstat()
        except FileNotFoundError:
            return False
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            return True
        identity = json.loads(marker.read_text())
        info = display.stat()
        if (stat.S_ISSOCK(info.st_mode) and isinstance(identity, dict)
                and type(identity.get('device')) is int and type(identity.get('inode')) is int):
            return identity == dict(device=info.st_dev, inode=info.st_ino)
        return True
    except (KeyError, OSError, ValueError):
        return True


# Directory watches cover atomic marker replacement as well as in-place writes.
IN_ATTRIB = 0x00000004
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
WATCH_MASK = (IN_ATTRIB | IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO
              | IN_CREATE | IN_DELETE | IN_DELETE_SELF | IN_MOVE_SELF)


def main():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.inotify_init1.argtypes = [ctypes.c_int]
    libc.inotify_init1.restype = ctypes.c_int
    libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    libc.inotify_add_watch.restype = ctypes.c_int
    descriptor = libc.inotify_init1(os.O_CLOEXEC)
    if descriptor < 0:
        raise OSError(ctypes.get_errno(), 'cannot observe authentication gate')
    try:
        base = Path(os.environ['XDG_RUNTIME_DIR'])
        display = Path(os.environ.get('WAYLAND_DISPLAY', 'wayland-0'))
        display = display if display.is_absolute() else base / display
        namespace = hashlib.sha256(os.fsencode(str(display.resolve()))).hexdigest()[:16]
        directory = base / ('emaki-lock-' + namespace)
        generation = 0
        requests = bytearray()
        watches = {}
        interests = {}
        for path, name in ((base.parent, base.name), (base, directory.name),
                           (display.parent, display.name), (directory, "authentication-blocked")):
            interests.setdefault(path, set()).add(os.fsencode(name))
        while True:
            # Arm watches before sampling, including when the gate directory has
            # just appeared. Events arriving during sampling remain queued.
            for path in interests:
                result = libc.inotify_add_watch(descriptor, os.fsencode(path), WATCH_MASK)
                if result < 0 and ctypes.get_errno() != errno.ENOENT:
                    raise OSError(ctypes.get_errno(), 'cannot observe authentication gate')
                if result >= 0:
                    watches[result] = interests[path]
            print(json.dumps({'locked': blocked(), 'generation': generation},
                             separators=(',', ':')), flush=True)
            changed = False
            while not changed:
                ready, _, _ = select.select([descriptor, sys.stdin.fileno()], [], [])
                if descriptor in ready:
                    events = os.read(descriptor, 65536)
                    offset = 0
                    while offset < len(events):
                        watch, mask, _, length = struct.unpack_from('iIII', events, offset)
                        name = events[offset + 16:offset + 16 + length].rstrip(b'\0')
                        offset += 16 + length
                        # Queue overflow and watch deletion have no filename;
                        # resample and rearm rather than trusting a missed event.
                        if not name or name in watches.get(watch, set()):
                            changed = True
                if sys.stdin.fileno() in ready:
                    chunk = os.read(sys.stdin.fileno(), 65536)
                    if not chunk:
                        return
                    requests.extend(chunk)
                    while b'\n' in requests:
                        line, _, remaining = requests.partition(b'\n')
                        requests[:] = remaining
                        generation = int(line)
                        changed = True

    finally:
        os.close(descriptor)


if __name__ == '__main__':
    try:
        main()
    except BrokenPipeError:
        pass
