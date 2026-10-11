#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Disposable VM only: ordinary Wayland overlay and exclusive-keyboard probe.

No key values, pointer positions, clipboard data or credentials are recorded.
Uses the public display socket explicitly, never an inherited privileged fd.
"""
import argparse
import array
import json
import mmap
import os
from pathlib import Path
import select
import socket
import struct
import subprocess
import time


def word(*values):
    return struct.pack('<' + 'I' * len(values), *values)


def string(value):
    value = value.encode() + b'\0'
    return word(len(value)) + value + b'\0' * (-len(value) % 4)


def read_string(data, offset):
    size, = struct.unpack_from('<I', data, offset)
    return data[offset + 4:offset + 3 + size].decode(), offset + 4 + (size + 3) // 4 * 4


class Client:
    def __init__(self, namespace, output):
        self.namespace = namespace
        self.output = output
        self.ids = {1: 'display', 2: 'registry'}
        self.next_id = 3
        self.objects = {}
        self.pending = b''
        self.state = dict(namespace=namespace, configured=0, closed=0, mapped=False,
                          keyboard_enter=0, keyboard_key=0, pointer_enter=0,
                          pointer_button=0, protocol_error=False, input_injection_globals=[])
        self.socket = socket.socket(socket.AF_UNIX)
        display = os.environ['WAYLAND_DISPLAY']
        path = display if display.startswith('/') else os.path.join(os.environ['XDG_RUNTIME_DIR'], display)
        self.socket.connect(path)
        self.send(1, 1, word(2))
        self.roundtrip()
        for interface in ('wl_compositor', 'zwlr_layer_shell_v1', 'wl_shm'):
            if interface not in self.objects:
                raise RuntimeError('missing Wayland global: ' + interface)
        self.surface = self.new('surface')
        self.send(self.objects['wl_compositor'], 0, word(self.surface))
        self.layer = self.new('layer')
        self.send(self.objects['zwlr_layer_shell_v1'], 0,
                  word(self.layer, self.surface, 0, 3) + string(namespace))
        self.send(self.layer, 0, word(360, 240))
        self.send(self.layer, 1, word(1 | 4))
        self.send(self.layer, 4, word(1))
        self.send(self.surface, 6)
        self.save()

    def new(self, interface):
        ident = self.next_id
        self.next_id += 1
        self.ids[ident] = interface
        return ident

    def send(self, ident, opcode, data=b'', fd=None):
        packet = word(ident, ((len(data) + 8) << 16) | opcode) + data
        if fd is None:
            self.socket.sendall(packet)
        else:
            sent = self.socket.sendmsg([packet], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [fd]))])
            if sent < len(packet):
                self.socket.sendall(packet[sent:])

    def save(self):
        temporary = self.output.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state) + '\n')
        temporary.replace(self.output)

    def roundtrip(self):
        callback = self.new('callback')
        self.send(1, 0, word(callback))
        deadline = time.monotonic() + 5
        while callback in self.ids:
            if time.monotonic() > deadline:
                raise RuntimeError('Wayland roundtrip timeout')
            self.receive(.2)

    def receive(self, timeout):
        if not select.select([self.socket], [], [], timeout)[0]:
            return
        chunk, ancillary, flags, _ = self.socket.recvmsg(65536, socket.CMSG_SPACE(256))
        for level, kind, data in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                fds = array.array('i')
                fds.frombytes(data[:len(data) - len(data) % fds.itemsize])
                for fd in fds:
                    os.close(fd)
        if not chunk:
            raise RuntimeError('Wayland connection closed')
        self.pending += chunk
        while len(self.pending) >= 8:
            ident, header = struct.unpack_from('<II', self.pending)
            size, opcode = header >> 16, header & 65535
            if size < 8:
                raise RuntimeError('invalid Wayland message')
            if len(self.pending) < size:
                break
            data, self.pending = self.pending[8:size], self.pending[size:]
            self.event(ident, opcode, data)

    def event(self, ident, opcode, data):
        kind = self.ids.get(ident)
        if kind == 'registry' and opcode == 0:
            name, = struct.unpack_from('<I', data)
            interface, offset = read_string(data, 4)
            version, = struct.unpack_from('<I', data, offset)
            if interface in ('zwp_virtual_keyboard_manager_v1', 'zwp_input_method_manager_v2'):
                self.state['input_injection_globals'].append(interface)
            caps = {'wl_compositor': 4, 'wl_shm': 1, 'wl_seat': 5, 'zwlr_layer_shell_v1': 1}
            if interface in caps and interface not in self.objects:
                bound = self.new(interface)
                self.objects[interface] = bound
                self.send(ident, 0, word(name) + string(interface) + word(min(version, caps[interface]), bound))
        elif kind == 'callback' and opcode == 0:
            del self.ids[ident]
        elif kind == 'display' and opcode == 0:
            self.state['protocol_error'] = True
            self.save()
            raise RuntimeError('Wayland protocol error (not an accepted denial)')
        elif kind == 'wl_seat' and opcode == 0:
            capabilities, = struct.unpack_from('<I', data)
            for bit, request, name in ((1, 0, 'pointer'), (2, 1, 'keyboard')):
                if capabilities & bit and name not in self.objects:
                    bound = self.new(name)
                    self.objects[name] = bound
                    self.send(ident, request, word(bound))
        elif kind == 'layer':
            if opcode == 1:
                self.state['closed'] += 1
            elif opcode == 0:
                self.state['configured'] += 1
                serial, width, height = struct.unpack_from('<III', data)
                self.send(ident, 6, word(serial))
                if self.namespace != 'emaki-auth-prompt':
                    self.map_buffer(width or 360, height or 240)
            self.save()
        elif kind in ('keyboard', 'pointer'):
            names = {'keyboard': {1: 'keyboard_enter', 3: 'keyboard_key'},
                     'pointer': {0: 'pointer_enter', 3: 'pointer_button'}}
            counter = names[kind].get(opcode)
            if counter:
                self.state[counter] += 1
                self.save()

    def map_buffer(self, width, height):
        if width * height > 16_777_216:
            raise RuntimeError('unreasonable configured buffer size')
        size = width * height * 4
        fd = os.memfd_create('emaki-untrusted-overlay')
        try:
            os.ftruncate(fd, size)
            with mmap.mmap(fd, size) as pixels:
                pixels[:] = b'\x20\x20\xd0\xff' * (width * height)
            pool, buffer = self.new('pool'), self.new('buffer')
            self.send(self.objects['wl_shm'], 0, word(pool, size), fd)
            self.send(pool, 0, word(buffer, 0, width, height, width * 4, 0))
            self.send(pool, 1)
            self.send(self.surface, 1, word(buffer, 0, 0))
            self.send(self.surface, 2, word(0, 0, width, height))
            self.send(self.surface, 6)
            self.state['mapped'] = True
        finally:
            os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--namespace', default='emaki-auth-prompt')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=float, default=5)
    args = parser.parse_args()
    vm = subprocess.run(['systemd-detect-virt', '--vm'], capture_output=True, text=True)
    if vm.returncode or vm.stdout.strip() not in ('qemu', 'kvm'):
        parser.error('disposable QEMU guest required')
    if args.seconds <= 0:
        parser.error('--seconds must be positive')
    client = Client(args.namespace, args.output)
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        client.receive(max(0, min(.2, deadline - time.monotonic())))
    client.save()
    if client.state['input_injection_globals']:
        raise SystemExit('FAIL: ordinary client can bind input-injection globals')
    if args.namespace == 'emaki-auth-prompt':
        if client.state['closed'] != 1 or client.state['configured'] or client.state['mapped']:
            raise SystemExit('FAIL: reserved namespace was not closed before configure')
        if any(client.state[key] for key in ('keyboard_enter', 'keyboard_key', 'pointer_enter', 'pointer_button')):
            raise SystemExit('FAIL: denied client received input')
    elif not client.state['mapped']:
        raise SystemExit('FAIL: ordinary competing overlay did not map')
    print(json.dumps(client.state))


if __name__ == '__main__':
    main()
