#!/usr/bin/env python3
"""VM ONLY, root: raw greetd login for session-isolation tests, not UI acceptance.

  printf arch | sudo -n python3 guest-login.py arch niri-session
The password comes from stdin. C9 UI coverage uses check-greeter.py/uinput instead.
"""
import json
import os
from pathlib import Path
import pwd
import socket
import struct
import subprocess
import sys

MAX_FRAME = 1024 * 1024


def receive_exact(connection, count):
    data = bytearray()
    while len(data) < count:
        part = connection.recv(count - len(data))
        if not part:
            raise RuntimeError('greetd disconnected')
        data.extend(part)
    return bytes(data)


def rpc(connection, message):
    payload = json.dumps(message, separators=(',', ':')).encode()
    connection.sendall(struct.pack('=i', len(payload)) + payload)
    size = struct.unpack('=i', receive_exact(connection, 4))[0]
    if not 0 < size <= MAX_FRAME:
        raise RuntimeError('invalid greetd frame')
    return json.loads(receive_exact(connection, size))


def greetd_socket():
    uid = pwd.getpwnam('greeter').pw_uid
    paths = set()
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != uid:
                continue
            for item in (process / 'environ').read_bytes().split(b'\0'):
                if item.startswith(b'GREETD_SOCK='):
                    paths.add(item.split(b'=', 1)[1].decode())
        except (OSError, UnicodeError):
            continue
    if len(paths) != 1:
        raise RuntimeError('expected one greetd socket in greeter-owned processes')
    return paths.pop()


def main():
    if os.getuid() != 0:
        raise RuntimeError('fixture requires root in the disposable VM')
    virtualization = subprocess.run(['systemd-detect-virt', '--vm'], text=True,
                                    capture_output=True, timeout=5)
    if virtualization.returncode != 0 or virtualization.stdout.strip() not in ('qemu', 'kvm'):
        raise RuntimeError('fixture requires disposable QEMU/KVM')
    user = sys.argv[1]
    command = sys.argv[2:] or ['niri-session']
    password = sys.stdin.read(4097)
    if len(password) > 4096:
        raise RuntimeError('fixture password too long')
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(20)
        connection.connect(greetd_socket())
        reply = rpc(connection, {'type': 'create_session', 'username': user})
        # QS normally stays idle until Enter. A previous fixture may have left
        # a configuring session; consume its cancellation acknowledgement before
        # recreating, unlike the QS 0.3.1 asynchronous cancellation bug.
        if reply == {'type': 'error', 'error_type': 'error', 'description': 'a session is already being configured'}:
            cancelled = rpc(connection, {'type': 'cancel_session'})
            if cancelled.get('type') != 'success':
                raise RuntimeError('cannot cancel previous configuring session')
            reply = rpc(connection, {'type': 'create_session', 'username': user})
        answered = False
        while reply.get('type') == 'auth_message':
            kind = reply.get('auth_message_type')
            if kind == 'secret' and not answered:
                answer = password
                password = ''
                answered = True
            elif kind in ('info', 'error'):
                answer = None
            else:
                rpc(connection, {'type': 'cancel_session'})
                raise RuntimeError('unexpected prompt in password-only VM fixture')
            reply = rpc(connection, {'type': 'post_auth_message_response', 'response': answer})
            answer = None
        password = ''
        if reply.get('type') != 'success' or not answered:
            rpc(connection, {'type': 'cancel_session'})
            raise RuntimeError('greetd authentication failed')
        reply = rpc(connection, {'type': 'start_session', 'cmd': command, 'env': ['XDG_SESSION_TYPE=wayland']})
        if reply.get('type') != 'success':
            raise RuntimeError('greetd session launch failed')
    print('Raw greetd session launch succeeded (UI was not tested).')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, IndexError, subprocess.TimeoutExpired):
        raise SystemExit('Raw VM greetd login failed; see greetd journal (no secret diagnostics).')
