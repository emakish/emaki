#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lock metadata and a separate, supervisor-owned keyboard layout lifetime."""
import json
import os
from pathlib import Path
import re
import select
import socket
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import emaki_paths

# Mirror installer/emaki_installer/latin_layouts.py; tests/test-lock-layout.py
# keeps both facts in sync without making the desktop depend on the installer.
LATIN_LAYOUTS = frozenset((
    'al ml be dz ba cn hr cz dk nl au cm gh nz ng za gb us ee fo ph fi fr ca cd tg de at ch hu '
    'is id ie it jp kr lv lt mt md me no pl pt br ro sk si es latam ke se tw bw tr vn sn').split())
LATIN_VARIANTS = frozenset(('dvorak', 'colemak'))


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


def layout_codes(latin_only=False):
    """Layout name -> xkb code, uppercased, from evdev.lst (layout and variant rows).

    niri reports the xkeyboard-config description of a layout; the bar reads the same file
    the same way (shell/XkbCodes.js), so the login screen, the lock screen and the bar show
    one code for one layout. For switch targets, variants need their own approved
    entry; a Latin base code says nothing about the variant's alphabet.
    """
    path = Path(os.environ.get('EMAKI_XKB_RULES') or emaki_paths.XKB_RULES)
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
            if not latin_only or code in LATIN_LAYOUTS:
                table.setdefault(name.strip(), code.upper())
        elif line.strip() and section == 'variant':
            match = re.fullmatch(r'\s*(\S+)\s+(\S+):\s*(.+)', line)
            if match and (not latin_only or match[1] in LATIN_VARIANTS):
                table.setdefault(match[3].strip(), match[2].upper())
    return table


def layout_label(name, codes):
    # Retain an understandable label for a layout outside the table.
    return codes.get(name) or name[:32] or 'Layout unavailable'


class LayoutEvents:
    """Bounded event reader; unrelated event contents are never saved or logged."""
    def __init__(self, path):
        self.stream = socket.socket(socket.AF_UNIX)
        self.buffer = b''
        try:
            self.stream.settimeout(.2)
            self.stream.connect(path)
            self.stream.sendall(b'"EventStream"\n')
            self.stream.setblocking(False)
        except OSError:
            self.stream.close()
            raise

    def read(self):
        chunk = self.stream.recv(65536)
        if not chunk:
            raise OSError('event stream closed')
        self.buffer += chunk
        if len(self.buffer) > 1048576:
            raise ValueError('event too large')
        events = []
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            event = json.loads(line)
            if 'KeyboardLayoutsChanged' in event or 'KeyboardLayoutSwitched' in event:
                events.append(event)
            elif 'Err' in event:
                raise ValueError('event stream refused')
        return events


class LayoutLease:
    def __init__(self, path):
        self.path = path
        self.names = None
        self.original = self.target = None
        self.expected = False
        self.changed = False

    def begin(self, layouts):
        names, index = layouts['names'], layouts['current_idx']
        if (not isinstance(names, list) or not all(isinstance(name, str) for name in names)
                or type(index) is not int or not 0 <= index < len(names)):
            raise ValueError('invalid layouts')
        # This is the same request as niri msg -j keyboard-layouts. The stream
        # snapshot must agree, or an intervening switch/config reload wins.
        if request(self.path, 'KeyboardLayouts')['KeyboardLayouts'] != layouts:
            return
        codes = layout_codes(latin_only=True)
        latin = [i for i, name in enumerate(names) if name in codes]
        if index in latin or not latin:
            return
        self.names, self.original, self.target = list(names), index, latin[0]
        self.expected = True
        request(self.path, {'Action': {'SwitchLayout': {'layout': {'Index': self.target}}}})

    def observe(self, event):
        if 'KeyboardLayoutsChanged' in event:
            self.changed = True
        elif 'KeyboardLayoutSwitched' in event:
            index = event['KeyboardLayoutSwitched']['idx']
            if self.expected and index == self.target:
                self.expected = False
            else:
                self.changed = True

    def restore(self):
        if self.original is None or self.changed or self.expected:
            return
        layouts = request(self.path, 'KeyboardLayouts')['KeyboardLayouts']
        if layouts == dict(names=self.names, current_idx=self.target):
            request(self.path, {'Action': {'SwitchLayout': {'layout': {'Index': self.original}}}})


def switch_for_lock():
    """Private stdin belongs to the supervisor; this process cannot release a lock."""
    path = os.environ.get('NIRI_SOCKET', '')
    if not path:
        return
    events = LayoutEvents(path)
    lease = LayoutLease(path)
    initialized = False
    restoring = False
    deadline = time.monotonic() + 2
    try:
        while True:
            if not initialized and time.monotonic() >= deadline:
                return
            ready, _, _ = select.select([events.stream] + ([] if restoring else [sys.stdin]), [], [], .2)
            if sys.stdin in ready:
                # A late helper must not switch after its owner has already
                # unlocked or died. EOF always abandons the layout lifetime.
                if os.read(sys.stdin.fileno(), 4096) != b'restore\n' or not initialized:
                    return
                restoring = True
            # Drain queued layout changes before considering a restore request.
            if events.stream in ready:
                for event in events.read():
                    if not initialized and 'KeyboardLayoutsChanged' in event:
                        initialized = True
                        lease.begin(event['KeyboardLayoutsChanged']['keyboard_layouts'])
                    else:
                        lease.observe(event)
                continue
            if restoring:
                lease.restore()
                return
    finally:
        events.stream.close()


def main():
    path = os.environ.get('NIRI_SOCKET', '')
    parent = os.getppid()
    previous = None
    codes = layout_codes()
    state = dict(layout='Layout unavailable', outputs_active=True, caps=None)
    try:
        events = LayoutEvents(path) if path else None
    except OSError:
        events = None
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
        # Layout events wake the read-only metadata query immediately. Caps and
        # output metadata still refresh on the timer if there is no event stream.
        ready, _, _ = select.select([sys.stdin] + ([events.stream] if events else []), [], [], .5)
        if events and events.stream in ready:
            try:
                events.read()
            except (OSError, ValueError, TypeError):
                events.stream.close()
                events = None
        if sys.stdin in ready:
            chunk = os.read(sys.stdin.fileno(), 4096)
            if not chunk:
                return



if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['--switch-for-lock']:
            switch_for_lock()
        else:
            main()
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        pass
