#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Which QEMU belongs to an ISO VM directory, and whether it owns the SSH port.

The ISO harness reaches its guest only through a forwarded port on 127.0.0.1. A QEMU left over
from another directory can hold that port, and then every SSH step, the install included, lands
in the wrong VM (GOTCHAS 2026-10-10).

  port-free PORT          exit 0 when 127.0.0.1:PORT can be bound (as QEMU binds it)
  find --dir D [--wait S] print the PID of D's QEMU; waits up to S seconds while D's launcher is
                          still preparing; exit 1 when nothing of D is running, 3 when the
                          launcher is still preparing after S seconds
  prove --dir D --port P [--wait S]
                          exit 0 only when D's QEMU is the process listening on 127.0.0.1:P

A process belongs to D when it holds D/run.lock open. The launchers (run-iso.sh, night/vm.sh)
lock it before anything else of the VM exists and QEMU inherits it, so every process of D is
found from the launcher's first step on, before any PID file is written. The kernel follows a
renamed directory, so a VM whose directory was renamed is found under its new name and never
under the old one. A holder whose argv names qemu-system-x86_64 is D's QEMU; any other holder
is a launcher still preparing. "Nothing of D is running" is said only when run.lock itself is
not held: a /proc scan alone can miss a launcher handing the lock to QEMU. Exit 1 is a refusal,
exit 2 a usage error.
"""
import argparse
import fcntl
import os
from pathlib import Path
import socket
import sys
import time

QEMU = 'qemu-system-x86_64'
LISTEN = '0A'


def argv(pid):
    try:
        raw = Path(f'/proc/{pid}/cmdline').read_bytes()
    except OSError:
        return []
    return [part.decode(errors='replace') for part in raw.split(b'\0')[:-1]]


def read_pid(path):
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    return int(text) if text.isdigit() and 0 < int(text) < 2**31 else None


def fd_targets(pid):
    try:
        entries = os.listdir(f'/proc/{pid}/fd')
    except OSError:
        return None
    targets = []
    for entry in entries:
        try:
            targets.append(os.readlink(f'/proc/{pid}/fd/{entry}'))
        except OSError:
            continue
    return targets


def named(args, name, first):
    return any(os.path.basename(arg) == name for arg in args[:first])


def holders(directory):
    """PIDs of the processes holding directory/run.lock open, this one excepted."""
    lock = str(directory / 'run.lock')
    return sorted(int(entry) for entry in os.listdir('/proc')
                  if entry.isdigit() and int(entry) != os.getpid() and lock in (fd_targets(entry) or ()))


def alive(pid):
    try:
        stat = Path(f'/proc/{pid}/stat').read_text()
    except OSError:
        return False
    return stat.rsplit(')', 1)[-1].split()[0] != 'Z'


def lock_held(directory):
    """True while some process holds directory/run.lock locked, or when that cannot be ruled out."""
    try:
        descriptor = os.open(directory / 'run.lock', os.O_RDONLY | os.O_CLOEXEC)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        # A shared lock is refused while a launcher or its QEMU holds the exclusive one. Taken
        # and dropped at once; a launch that starts in that instant is refused as already running.
        fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(descriptor)
    return False


def state(directory):
    """('qemu', pid), ('launcher', pid) or ('none', reason) for this directory, without waiting.

    The scan lists /proc first and reads descriptors after, so a launcher that hands its lock to
    QEMU and exits in between leaves an empty result while the lock stays held. Only the lock
    itself proves absence: held with no holder seen counts as a launcher (pid None) and is
    looked at again."""
    found = holders(directory)
    for pid in found:
        if named(argv(pid), QEMU, 2):
            return 'qemu', pid
    if found:
        return 'launcher', found[0]
    if lock_held(directory):
        return 'launcher', None
    return 'none', 'no QEMU of this directory is running'


def find(directory, wait):
    deadline = time.monotonic() + wait
    while True:
        kind, value = state(directory)
        if kind != 'launcher' or time.monotonic() >= deadline:
            return kind, value
        time.sleep(0.1)


def port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        # QEMU's user network binds its forward with SO_REUSEADDR; a TIME_WAIT leftover from an
        # earlier SSH session does not stop it, so it must not stop this check either.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(('127.0.0.1', port))
        except OSError:
            return False
    return True


def listeners(port):
    """Inodes of listening sockets that accept a connection to 127.0.0.1:port."""
    covering = {'0100007F', '00000000',  # 127.0.0.1, 0.0.0.0
                '00000000000000000000000000000000',  # ::
                '0000000000000000FFFF00000100007F'}  # ::ffff:127.0.0.1
    found = set()
    for table in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            address, hex_port = fields[1].rsplit(':', 1)
            if fields[3] == LISTEN and int(hex_port, 16) == port and address in covering:
                found.add(fields[9])
    return found


def socket_inodes(pid):
    targets = fd_targets(pid)
    if targets is None:
        return None
    return {target[8:-1] for target in targets if target.startswith('socket:[')}


def holder(inode):
    for entry in os.listdir('/proc'):
        if entry.isdigit() and inode in (socket_inodes(int(entry)) or ()):
            try:
                comm = Path(f'/proc/{entry}/comm').read_text().strip()
            except OSError:
                comm = '?'
            return f'PID {entry} ({comm})'
    return 'a process this user cannot inspect'


def prove(directory, port, wait):
    deadline = time.monotonic() + wait
    while True:
        kind, value = state(directory)
        if kind == 'none':
            # Nothing of this directory runs: a launch from here would be refused while the port
            # is held, so there is nothing to wait for.
            held = listeners(port)
            if held:
                return f'{value}, and 127.0.0.1:{port} is held by {holder(sorted(held)[0])}'
        if kind == 'launcher':
            # The launcher is still waiting for a QEMU that wrote its pidfile and then exited.
            started = read_pid(directory / 'qemu.pid')
            if started is not None and not alive(started):
                return f'QEMU {started} of this directory exited'
        if kind == 'qemu':
            pid = value
            forward = f'hostfwd=tcp:127.0.0.1:{port}-:22'
            if not any(forward in arg for arg in argv(pid)):
                return f'QEMU {pid} of this directory does not forward 127.0.0.1:{port}'
            owned = socket_inodes(pid)
            if owned is None:
                return f'cannot read the sockets of QEMU {pid}'
            held = listeners(port)
            foreign = sorted(held - owned)
            if foreign:
                return f'127.0.0.1:{port} is held by {holder(foreign[0])}, not by QEMU {pid}'
            if held:
                print(f'OK: QEMU {pid} of {directory} owns 127.0.0.1:{port}')
                return None
        if time.monotonic() >= deadline:
            if kind == 'qemu':
                return f'QEMU {value} of this directory is not listening on 127.0.0.1:{port}'
            return value if kind == 'none' else 'the launcher did not start QEMU in time'
        time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    free = commands.add_parser('port-free')
    free.add_argument('port', type=int)
    for name in ('find', 'prove'):
        command = commands.add_parser(name)
        command.add_argument('--dir', required=True, type=Path)
        command.add_argument('--wait', type=float, default=0)
        if name == 'prove':
            command.add_argument('--port', required=True, type=int)
    args = parser.parse_args()
    if args.command == 'port-free':
        return 0 if port_free(args.port) else 1
    directory = args.dir.resolve()
    if args.command == 'find':
        kind, value = find(directory, args.wait)
        if kind == 'qemu':
            print(value)
            return 0
        print('the launcher did not start QEMU in time' if kind == 'launcher' else value, file=sys.stderr)
        return 1 if kind == 'none' else 3
    problem = prove(directory, args.port, args.wait)
    if problem:
        print(f'BAD: {problem}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
