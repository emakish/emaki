#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply the selected login appearance through a private compositor wrapper."""
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys


def private_runtime(path):
    if not path or not Path(path).is_absolute() or '..' in Path(path).parts:
        raise ValueError('unsafe_runtime')
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in Path(path).parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('unsafe_runtime')
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def apply(theme, base):
    if theme not in ('Emaki-White', 'Emaki-Dark'):
        raise ValueError('invalid_cursor')
    base = Path(base)
    if not base.is_absolute() or not base.is_file() or any(ord(c) < 32 for c in str(base)):
        raise ValueError('invalid_base')
    runtime = os.environ.get('XDG_RUNTIME_DIR', '')
    if not os.environ.get('NIRI_SOCKET'):
        raise ValueError('compositor_unavailable')
    # The compositor executable is fixed; the wrapper chooses the installed variant.
    executable = os.environ.get('EMAKI_GREETER_COMPOSITOR', 'niri')
    if executable not in ('niri', 'niri-emaki'):
        raise ValueError('invalid_compositor')
    descriptor = private_runtime(runtime)
    target = 'emaki-greeter-cursor.kdl'
    temporary = '.emaki-greeter-cursor-' + secrets.token_hex(12)
    try:
        try:
            old = os.stat(target, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISREG(old.st_mode) or old.st_uid != os.geteuid() or old.st_nlink != 1:
                raise ValueError('unsafe_wrapper')
        except FileNotFoundError:
            pass
        contents = ('// Managed login cursor.\ninclude ' + json.dumps(str(base))
                    + '\ncursor { xcursor-theme ' + json.dumps(theme) + '; }\n')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=descriptor)
        with os.fdopen(fd, 'w') as stream:
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.rename(temporary, target, src_dir_fd=descriptor, dst_dir_fd=descriptor)
        os.fsync(descriptor)
        subprocess.run([executable, 'msg', 'action', 'load-config-file', '--path',
                        str(Path(runtime) / target)], check=True, timeout=3,
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        try:
            os.unlink(temporary, dir_fd=descriptor)
        except FileNotFoundError:
            pass
        os.close(descriptor)


def main():
    try:
        if len(sys.argv) != 3:
            raise ValueError('invalid_arguments')
        apply(sys.argv[1], sys.argv[2])
    except (OSError, ValueError, subprocess.SubprocessError):
        print(json.dumps({'ok': False, 'reason': 'greeter_cursor_apply_failed'}))
        return 1
    print(json.dumps({'ok': True}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
