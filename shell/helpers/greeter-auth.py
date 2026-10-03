#!/usr/bin/env python3
"""A private, one-attempt greetd client, preserving outstanding reply sockets.

The QML-owned supervisor holds no socket. Its conversation child retains the
anonymous pipes, drains any pending reply after abandonment, and never launches
without the accepted parent's explicit command. Parent loss retires the client:
only the replacement greeter may cancel the shared greetd slot in that case.
"""
import ctypes
import json
import os
import select
import signal
import socket
import struct
import sys

MAX_FRAME = 65536
MAX_COMMAND = 16384
ATTEMPT = -1
ABANDONED = False
RETIRED = False
CONTROLS = None


def retire(*_args):
    global ABANDONED, RETIRED
    ABANDONED = True
    RETIRED = True


def bind_parent_lifetime(expected_parent):
    parent = os.getppid()
    if parent <= 1 or parent != expected_parent:
        raise OSError('orphaned worker')
    libc = ctypes.CDLL(None, use_errno=True)
    prctl = libc.prctl
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    if prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG, deliberately catchable.
        raise OSError('parent lifetime binding failed')
    if os.getppid() != parent:
        raise OSError('parent exited')


class Controls:
    def __init__(self):
        self.buffer = bytearray()
        self.queue = []
        self.closed = False

    def poll(self):
        global ABANDONED
        if self.closed:
            return
        while select.select([0], [], [], 0)[0]:
            part = os.read(0, MAX_COMMAND + 1)
            if not part:
                self.closed = True
                retire()
                self.queue.clear()
                self.buffer.clear()
                return
            self.buffer.extend(part)
            while b'\n' in self.buffer:
                line, _, remaining = self.buffer.partition(b'\n')
                self.buffer = bytearray(remaining)
                try:
                    if len(line) > MAX_COMMAND:
                        raise ValueError('oversize control')
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError('invalid control')
                except (ValueError, TypeError, RecursionError):
                    retire()
                    self.queue.clear()
                    self.buffer.clear()
                    return
                if value.get('op') == 'retire':
                    retire()
                elif value.get('op') == 'abandon':
                    ABANDONED = True
                elif not ABANDONED:
                    self.queue.append(value)
                if ABANDONED:
                    self.queue.clear()
            if len(self.buffer) > MAX_COMMAND:
                retire()
                self.queue.clear()
                self.buffer.clear()
                return

    def next(self):
        while True:
            self.poll()
            if ABANDONED:
                self.queue.clear()
                return None
            if self.queue:
                return self.queue.pop(0)
            select.select([0], [], [], .05)


def poll_control():
    if CONTROLS is not None:
        CONTROLS.poll()


def command():
    return CONTROLS.next()


def emit(event, **fields):
    try:
        print(json.dumps(dict(event=event, attempt=ATTEMPT, **fields), separators=(',', ':')), flush=True)
    except OSError:
        retire()  # Broken stdout is parent loss, never a reason to drop a pending socket.


def send(connection, value):
    data = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()
    if len(data) > MAX_COMMAND:
        raise ValueError('oversize request')
    # Once any bytes are sent, finish the small frame; never truncate an in-flight
    # request because an input event or a UI timeout happened concurrently.
    connection.settimeout(None)
    connection.sendall(struct.pack('=i', len(data)) + data)
    connection.settimeout(.05)


def receive(connection):
    def exact(count, discard=False):
        data = bytearray()
        remaining = count
        while remaining:
            poll_control()
            try:
                part = connection.recv(min(remaining, MAX_FRAME))
            except socket.timeout:
                continue  # Service control; never turn a pending reply into a dead client.
            if not part:
                raise OSError('connection closed')
            remaining -= len(part)
            if not discard:
                data.extend(part)
        return data
    size, = struct.unpack('=i', exact(4))
    if not 0 < size <= MAX_FRAME:
        emit('transport')
        retire()
        if size > 0:
            exact(size, discard=True)  # Bounded memory even for an invalid advertised size.
        else:
            # Unknown framing has no safe request boundary. Retain the descriptor
            # until the peer closes, while the UI is free to fall back.
            while True:
                try:
                    if not connection.recv(MAX_FRAME):
                        break
                except socket.timeout:
                    continue
        raise ValueError('invalid reply length')
    value = json.loads(exact(size))
    if not isinstance(value, dict):
        raise ValueError('invalid response')
    return value


def exchange(connection, value):
    poll_control()
    if RETIRED or (ABANDONED and value['type'] != 'cancel_session'):
        return None
    send(connection, value)
    return receive(connection)


def cleanup(connection, reason):
    poll_control()
    if RETIRED:
        return 0  # A replacement greeter owns global cancellation now.
    response = exchange(connection, dict(type='cancel_session'))
    if response is None:
        return 0
    # Context.cancel takes the slot before contacting PAM. A normal error reply
    # therefore also proves clearance, but technical recovery treats it as fatal.
    ok = response == {'type': 'success'}
    cleared = ok or (response.get('type') == 'error' and response.get('error_type') == 'error')
    emit('cleaned', reason=reason, ok=ok, cleared=cleared)
    return 0 if cleared else 1


def strings(value):
    return isinstance(value, list) and all(isinstance(word, str) and '\0' not in word for word in value)


def run():
    global ATTEMPT
    request = command()
    if request is None:
        return 0
    attempt = request.get('attempt')
    if type(attempt) is not int or not 0 <= attempt <= 2147483647:
        raise ValueError('invalid attempt')
    ATTEMPT = attempt
    operation = request.get('op')
    user = request.get('user')
    if operation != 'cleanup' and (operation != 'begin' or not isinstance(user, str) or not user or len(user) > 1024 or '\0' in user):
        raise ValueError('invalid begin')
    emit('hello')
    poll_control()
    if RETIRED:
        return 0
    path = os.environ.get('GREETD_SOCK', '')
    if not path:
        raise OSError('missing socket')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(1)
        connection.connect(path)
        if operation == 'cleanup':
            return cleanup(connection, 'cancel')
        reply = exchange(connection, dict(type='create_session', username=user))
        answered = False
        while True:
            poll_control()
            if ABANDONED or reply is None:
                return cleanup(connection, 'cancel')
            kind = reply.get('type')
            if kind == 'auth_message':
                mode = reply.get('auth_message_type')
                message = reply.get('auth_message')
                if not isinstance(message, str):
                    raise ValueError('invalid prompt')
                if mode in ('info', 'error'):
                    emit('message', message=message, error=mode == 'error')
                    reply = exchange(connection, dict(type='post_auth_message_response', response=None))
                elif mode == 'secret' and not answered:
                    emit('prompt')
                    request = command()
                    poll_control()
                    if ABANDONED or request is None:
                        return cleanup(connection, 'cancel')
                    password = request.pop('password', None)
                    if request != {'op': 'respond'} or not isinstance(password, str) or not 0 < len(password) <= 1024 or '\0' in password:
                        raise ValueError('invalid response')
                    answered = True
                    # Drop all secret references before waiting for PAM's verdict.
                    send(connection, dict(type='post_auth_message_response', response=password))
                    password = None
                    reply = receive(connection)
                else:
                    emit('unsupported')
                    return cleanup(connection, 'unsupported')
            elif kind == 'error':
                reason = 'rejected' if reply.get('error_type') == 'auth_error' else 'technical'
                emit(reason)
                return cleanup(connection, reason)
            elif kind == 'success' and answered:
                emit('ready')
                break
            else:
                raise ValueError('unexpected authentication response')
        request = command()
        poll_control()
        if ABANDONED or request is None:
            return cleanup(connection, 'cancel')
        cmd, env = request.get('cmd'), request.get('env')
        if request.get('op') != 'launch' or not strings(cmd) or not cmd or not strings(env):
            raise ValueError('invalid launch')
        reply = exchange(connection, dict(type='start_session', cmd=cmd, env=env))
        if RETIRED:
            return 0  # Never cancel a scheduled session or another greeter's slot.
        if reply is None:
            return cleanup(connection, 'cancel')
        if reply != {'type': 'success'}:
            emit('launchError')
            return 1
        emit('launched')
        return 0


def main():
    try:
        return run()
    except (OSError, ValueError, TypeError, RecursionError):
        # All normal exceptions occur at a consumed reply or peer-EOF boundary.
        # receive() retains malformed/incomplete frames until safe to close.
        emit('transport')
        return 1


def entrypoint():
    global CONTROLS
    # QProcess::~Process SIGKILLs its direct child. Keep the descriptor owner one
    # generation below it, in a separate process group. The wrapper has no socket.
    signal.signal(signal.SIGTERM, lambda *_args: os._exit(1))
    try:
        bind_parent_lifetime(int(sys.argv[1]))
        supervisor = os.getpid()
        owner = os.fork()
    except (OSError, ValueError, IndexError):
        return 1
    if owner:
        for descriptor in (0, 1, 2):
            os.close(descriptor)
        _, status = os.waitpid(owner, 0)
        return os.waitstatus_to_exitcode(status) if os.WIFEXITED(status) else 1
    signal.signal(signal.SIGTERM, retire)
    try:
        os.setsid()
        bind_parent_lifetime(supervisor)
    except OSError:
        return 1
    CONTROLS = Controls()
    return main()


if __name__ == '__main__':
    # Every event is flushed explicitly; suppress shutdown's broken-pipe retries.
    os._exit(entrypoint())
