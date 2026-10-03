#!/usr/bin/env python3
"""Private fake greetd socket, using the real native-endian qint32 + JSON framing.

The production per-attempt greetd worker connects directly to this server.
Only request metadata enters the event queue; password responses are compared in
memory and immediately discarded. Nothing here writes or logs a response secret.
The configuring slot is global across connections and survives technical replies;
only cancellation removes it. Tests explicitly acknowledge each queued request.
"""
from collections import deque
import json
import queue
import socket
import struct
import threading
import time


class GreetdServer:
    def __init__(self, path, expected_password='fixture-éЖ🔒'):
        self.path = str(path)
        self.expected_password = expected_password
        self.events = queue.Queue()
        self.connected = threading.Event()
        self.stopped = threading.Event()
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.path)
        self.listener.listen(4)
        self.listener.settimeout(.1)
        self.connection = None
        self.connections = {}
        self.pending = {}
        self.readers = []
        self.lock = threading.RLock()
        self.configuring = None
        self.auth_connection = 0
        self.closed = set()
        self.panicked = False
        self.defer_cancel_error = False
        self.scheduled = False
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _receive(self, connection, count):
        result = bytearray()
        while len(result) < count and not self.stopped.is_set():
            try:
                chunk = connection.recv(count - len(result))
            except socket.timeout:
                continue
            if not chunk:
                raise EOFError
            result.extend(chunk)
        if len(result) != count:
            raise EOFError
        return result

    def _serve(self):
        while not self.stopped.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            connection.settimeout(.1)
            with self.lock:
                number = len(self.connections)
                self.connections[number] = connection
                self.pending[number] = deque()
                if number == 0:
                    self.connection = connection
                    self.connected.set()
            reader = threading.Thread(target=self._client, args=(number, connection), daemon=True)
            self.readers.append(reader)
            reader.start()

    def _client(self, number, connection):
        try:
            while not self.stopped.is_set():
                size, = struct.unpack('=i', self._receive(connection, 4))
                if not 0 < size <= 1024 * 1024:
                    raise ValueError('invalid greetd frame size')
                event = json.loads(self._receive(connection, size))
                kind = event.get('type')
                if kind == 'post_auth_message_response':
                    response = event.pop('response', None)
                    event['has_response'] = response is not None
                    event['response_length'] = len(response) if response is not None else 0
                    event['response_matches'] = response == self.expected_password
                    response = None
                event['received_at'] = time.monotonic()
                event['connection'] = number
                with self.lock:
                    error = None
                    if kind == 'create_session':
                        self.auth_connection = number
                        if self.configuring is not None:
                            error = 'a session is already being configured'
                        elif self.scheduled:
                            error = 'a session is already scheduled'
                        else:
                            self.configuring = {'user': event['username'], 'ready': False, 'alive': True}
                    elif kind == 'cancel_session':
                        # Context.cancel removes the slot before contacting PAM.
                        # A worker that already exited yields an error ACK.
                        exited = self.configuring is not None and not self.configuring['alive']
                        self.configuring = None
                        if exited and not self.defer_cancel_error:
                            error = 'unable to send message: worker exited'
                    elif kind == 'start_session':
                        if self.configuring is None or not self.configuring['ready']:
                            error = 'session is not ready'
                    elif kind == 'post_auth_message_response' and self.configuring is None:
                        error = 'no session under configuration'
                    self.pending[number].append((kind, self.configuring))
                    if error is not None:
                        event['automatic_error'] = error
                    self.events.put(event)
                    if error is not None:
                        self.error('error', error, connection=number)
        except (EOFError, OSError):
            pass
        except Exception as error:
            self.events.put({'server_error': type(error).__name__})
        finally:
            with self.lock:
                self.closed.add(number)

    def request(self, expected=None, timeout=3):
        event = self.events.get(timeout=timeout)
        assert 'server_error' not in event, event
        if expected is not None:
            assert event['type'] == expected, (expected, event)
        return event

    def send(self, value, fragmented=False, connection=None, worker_exited=False):
        assert self.connected.wait(3), 'auth worker did not connect to fake greetd'
        with self.lock:
            if connection is None:
                connection = self.auth_connection
            kind, slot = self.pending[connection].popleft()
            if value.get('type') == 'error' and slot is not None and (value.get('error_type') == 'auth_error' or worker_exited):
                slot['alive'] = False
            if connection in self.closed:
                if self.configuring is not None and not self.configuring['alive']:
                    self.panicked = True
                    raise AssertionError('greetd abort: auth error written to dead client; cancel().expect() reached exited PAM worker')
                self.configuring = None
                raise BrokenPipeError('dead client; greetd cancelled its live worker')
            if value.get('type') == 'success':
                if kind in ('create_session', 'post_auth_message_response') and self.configuring is not None and self.configuring is slot:
                    self.configuring['ready'] = True
                elif kind == 'start_session':
                    self.configuring = None
                    self.scheduled = True
            # A technical/auth error intentionally leaves the configuring slot.
            # Only a real cancel_session removes it, just as greetd Context does.
            payload = json.dumps(value, ensure_ascii=False).encode()
            packet = struct.pack('=i', len(payload)) + payload
            stream = self.connections[connection]
            try:
                if fragmented:
                    stream.sendall(packet[:2])
                    stream.sendall(packet[2:7])
                    stream.sendall(packet[7:])
                else:
                    stream.sendall(packet)
            except (BrokenPipeError, ConnectionResetError):
                if self.configuring is not None and not self.configuring['alive']:
                    self.panicked = True
                    raise AssertionError('greetd abort: reply to dead client and exited PAM worker')
                self.configuring = None  # client_ctx.cancel() succeeds for a live worker.
                raise

    def prompt(self, kind='secret', text='Password:', connection=None):
        self.send({'type': 'auth_message', 'auth_message_type': kind,
                   'auth_message': text}, fragmented=True, connection=connection)

    def success(self, connection=None):
        self.send({'type': 'success'}, connection=connection)

    def error(self, kind='auth_error', description='PAM authentication failed', connection=None, worker_exited=False):
        self.send({'type': 'error', 'error_type': kind, 'description': description}, connection=connection, worker_exited=worker_exited)

    def hangup(self):
        if self.connection is not None:
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()

    def close(self, *, expected_panic=False):
        self.stopped.set()
        self.hangup()
        self.listener.close()
        self.thread.join(timeout=2)
        for connection in list(self.connections.values()):
            try:
                connection.close()
            except OSError:
                pass
        for reader in self.readers:
            reader.join(timeout=2)
        assert self.panicked is expected_panic, 'unexpected fake greetd dead-client abort state'
