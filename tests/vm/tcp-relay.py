#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""stdin/stdout <-> TCP HOST PORT. QEMU's guestfwd=...-cmd: runs one per guest connection, so a
guest connection to a fixed virtual address reaches a server on the host's loopback."""
import os
import selectors
import socket
import sys


def main():
    host, port = sys.argv[1], int(sys.argv[2])
    connection = socket.create_connection((host, port))
    selector = selectors.DefaultSelector()
    selector.register(sys.stdin.fileno(), selectors.EVENT_READ, 'in')
    selector.register(connection, selectors.EVENT_READ, 'net')
    out = sys.stdout.fileno()
    while True:
        for key, _ in selector.select():
            if key.data == 'in':
                data = os.read(sys.stdin.fileno(), 65536)
                if not data:
                    connection.shutdown(socket.SHUT_WR)
                    selector.unregister(sys.stdin.fileno())
                    continue
                connection.sendall(data)
            else:
                data = connection.recv(65536)
                if not data:
                    return 0
                while data:
                    data = data[os.write(out, data):]


if __name__ == '__main__':
    sys.exit(main())
