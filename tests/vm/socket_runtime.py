#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Short private socket paths with discoverable links beside VM evidence."""
import argparse
import os
from pathlib import Path
import tempfile

NAMES = ('mon.sock', 'vnc.sock', 'qmp.sock')


def cleanup(directory):
    directory = Path(directory).resolve()
    link = directory / '.socket-runtime'
    if not link.is_symlink():
        return
    runtime = link.resolve()
    if not runtime.exists():
        for name in NAMES:
            socket_link = directory / name
            if socket_link.is_symlink() and socket_link.resolve() == runtime / name:
                socket_link.unlink()
        link.unlink()
        return
    # Only remove a directory created for this exact evidence directory.
    if (runtime.parent != Path('/tmp') or not runtime.name.startswith('em-vm-')
            or runtime.stat().st_uid != os.getuid()
            or (runtime / 'owner').read_text() != str(directory)):
        raise ValueError('invalid VM socket runtime')
    for name in NAMES:
        (runtime / name).unlink(missing_ok=True)
        socket_link = directory / name
        if socket_link.is_symlink() and socket_link.resolve() == runtime / name:
            socket_link.unlink()
    (runtime / 'owner').unlink()
    runtime.rmdir()
    link.unlink()


def prepare(directory):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    for name in NAMES:
        socket_path = directory / name
        if socket_path.exists() and not socket_path.is_symlink():
            raise FileExistsError(socket_path)
    pidfile = directory / 'qemu.pid'
    if pidfile.is_file():
        pid = int(pidfile.read_text().strip())
        if pid > 0 and Path(f'/proc/{pid}').exists():
            raise FileExistsError(pidfile)
    cleanup(directory)
    runtime = Path(tempfile.mkdtemp(prefix='em-vm-', dir='/tmp'))
    (runtime / 'owner').write_text(str(directory))
    (directory / '.socket-runtime').symlink_to(runtime, target_is_directory=True)
    for name in NAMES:
        (directory / name).unlink(missing_ok=True)
        (directory / name).symlink_to(runtime / name)
    return runtime


def runtime(directory):
    link = Path(directory) / '.socket-runtime'
    return link.resolve() if link.is_dir() else prepare(directory)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'cleanup'))
    parser.add_argument('directory')
    args = parser.parse_args()
    if args.action == 'prepare':
        print(prepare(args.directory))
    else:
        cleanup(args.directory)
