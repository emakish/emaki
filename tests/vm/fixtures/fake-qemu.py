#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""A stand-in for qemu-system-x86_64 in the harness tests: no guest, only what the harness sees.

Like QEMU it binds the `hostfwd=tcp:127.0.0.1:PORT-:22` forward (and exits 1 when the port is
taken), writes `-pidfile`, answers `system_powerdown` and `quit` on the monitor socket and exits on
SIGTERM. FAKE_QEMU_DELAY seconds pass before the pidfile and the monitor exist (the window
right after a launch); FAKE_QEMU_LOG gets one line per start and FAKE_QEMU_MONITOR_LOG records commands.
"""
import os
import re
import signal
import socket
import sys
import time

args = sys.argv[1:]


def value(flag):
    return args[args.index(flag) + 1] if flag in args else None


if os.environ.get('FAKE_QEMU_LOG'):
    with open(os.environ['FAKE_QEMU_LOG'], 'a') as log:
        log.write(' '.join(args) + '\n')
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
forward = re.search(r'hostfwd=tcp:127\.0\.0\.1:(\d+)-:22', value('-nic') or '')
listener = socket.socket()
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    listener.bind(('127.0.0.1', int(forward.group(1))))
except OSError:
    sys.exit('qemu-system-x86_64: -nic user: Could not set up host forwarding rule')
listener.listen()
time.sleep(float(os.environ.get('FAKE_QEMU_DELAY', '0')))
if value('-pidfile'):
    with open(value('-pidfile'), 'w') as pidfile:
        pidfile.write(f'{os.getpid()}\n')
monitor = re.match(r'unix:([^,]+),server=on', value('-monitor') or '')
if not monitor:
    signal.pause()
server = socket.socket(socket.AF_UNIX)
server.bind(monitor.group(1))
server.listen()
while True:
    connection, _ = server.accept()
    with connection:
        connection.sendall(b'QEMU fake monitor\r\n(qemu) ')
        command = connection.recv(1024).strip()
        if os.environ.get('FAKE_QEMU_MONITOR_LOG'):
            with open(os.environ['FAKE_QEMU_MONITOR_LOG'], 'a') as log:
                log.write(command.decode() + '\n')
        if command in (b'system_powerdown', b'quit'):
            sys.exit(0)
