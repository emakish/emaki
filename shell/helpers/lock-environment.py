#!/usr/bin/env python3
"""Read-only lock metadata; never request windows or change monitor power."""
import json
import os
from pathlib import Path
import select
import socket
import sys


def request(path, kind):
    with socket.socket(socket.AF_UNIX) as stream:
        stream.settimeout(.2)
        stream.connect(path)
        stream.sendall((json.dumps(kind) + '\n').encode())
        data = b''
        while b'\n' not in data:
            chunk = stream.recv(16384)
            if not chunk or len(data) > 1048576:
                raise ValueError('invalid response')
            data += chunk
        return json.loads(data.split(b'\n', 1)[0])['Ok']


def caps_state():
    values = []
    for path in Path('/sys/class/leds').glob('*::capslock/brightness'):
        try:
            values.append(int(path.read_text().strip()) > 0)
        except (OSError, ValueError):
            pass
    return any(values) if values else None


def layout_label(name):
    # Match niri's human XKB names; retain an understandable label for unknown layouts.
    for prefix, short in [('English', 'EN'), ('Russian', 'RU'), ('German', 'DE'),
                          ('French', 'FR'), ('Spanish', 'ES'), ('Ukrainian', 'UK')]:
        if name.startswith(prefix):
            return short
    return name[:32] or 'Layout unavailable'


def main():
    path = os.environ.get('NIRI_SOCKET', '')
    parent = os.getppid()
    previous = None
    state = dict(layout='Layout unavailable', outputs_active=True, caps=None)
    while os.getppid() == parent:
        try:
            if path:
                layouts = request(path, 'KeyboardLayouts')['KeyboardLayouts']
                names, index = layouts['names'], layouts['current_idx']
                state['layout'] = layout_label(str(names[index]))
                outputs = request(path, 'Outputs')['Outputs']
                state['outputs_active'] = any(out.get('current_mode') is not None for out in outputs.values())
        except (OSError, ValueError, KeyError, IndexError, TypeError):
            state['layout'] = 'Layout unavailable'
            state['outputs_active'] = True
        state['caps'] = caps_state()
        encoded = json.dumps(state, separators=(',', ':'))
        if encoded != previous:
            print(encoded, flush=True)
            previous = encoded
        ready, _, _ = select.select([sys.stdin], [], [], .5)
        if ready:
            chunk = os.read(sys.stdin.fileno(), 4096)
            if not chunk:
                return



if __name__ == '__main__':
    try:
        main()
    except BrokenPipeError:
        pass
