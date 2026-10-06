#!/usr/bin/env python3
"""Read-only lock metadata; never request windows or change monitor power."""
import json
import os
from pathlib import Path
import re
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


def layout_codes():
    """Layout name -> xkb code, uppercased, from evdev.lst (layout and variant rows).

    niri reports the xkeyboard-config description of a layout; the bar reads the same file
    the same way (shell/XkbCodes.js), so the login screen, the lock screen and the bar show
    one code for one layout.
    """
    path = Path(os.environ.get('EMAKI_XKB_RULES') or '/usr/share/X11/xkb/rules/evdev.lst')
    try:
        text = path.read_text('utf-8', 'replace')
    except OSError:
        return {}
    table, section = {}, ''
    for line in text.splitlines():
        if line.startswith('!'):
            section = line[1:].strip()
        elif line.strip() and section == 'layout':
            code, _, name = line.strip().partition(' ')
            table.setdefault(name.strip(), code.upper())
        elif line.strip() and section == 'variant':
            match = re.fullmatch(r'\s*(\S+)\s+(\S+):\s*(.+)', line)
            if match:
                table.setdefault(match[3].strip(), match[2].upper())
    return table


def layout_label(name, codes):
    # Retain an understandable label for a layout outside the table.
    return codes.get(name) or name[:32] or 'Layout unavailable'


def main():
    path = os.environ.get('NIRI_SOCKET', '')
    parent = os.getppid()
    previous = None
    codes = layout_codes()
    state = dict(layout='Layout unavailable', outputs_active=True, caps=None)
    while os.getppid() == parent:
        try:
            if path:
                layouts = request(path, 'KeyboardLayouts')['KeyboardLayouts']
                names, index = layouts['names'], layouts['current_idx']
                state['layout'] = layout_label(str(names[index]), codes)
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
