#!/usr/bin/env python3
"""GUEST ONLY, as root: one JSON snapshot for tests/vm/check-graphics-fallback.py.

Read-only. Reports the active VT; the mode and keyboard mode of tty1 (KDGETMODE and KDGKBMODE
only read them); the text and cursor of tty1 (/dev/vcsa1 header, /dev/vcsu1 cells); greetd's
unit state; this boot's journal lines of the greeter wrapper and the session script that the
check looks for; the niri-emaki, qs and agreety processes; CLOCK_MONOTONIC when it ran.
Refuses to run outside a QEMU/KVM guest or without root.
"""
import fcntl
import json
import os
import pwd
import struct
import subprocess
import sys
import time

KDGETMODE = 0x4B3B
KDGKBMODE = 0x4B44
JOURNAL_TAGS = ('emaki-greeter-compositor', 'niri-emaki-session')
JOURNAL_TEXT = ('no primary GPU renderer', 'showing graphics-failed message')
PROCESSES = ('niri-emaki', 'qs', 'agreety')


def parse_screen(header, cells, width):
    """vcsa header (rows, cols, cursor x, cursor y) and vcsu (width 4) or vcs (width 1) cells."""
    rows, cols, x, y = header[:4]
    if width == 4:
        codes = struct.unpack(f'={rows * cols}I', cells[:rows * cols * 4])
    else:
        codes = cells[:rows * cols]
    text = ''.join(chr(code) if 0 < code < 0x110000 else ' ' for code in codes)
    lines = [text[row * cols:(row + 1) * cols].rstrip(' ') for row in range(rows)]
    return {'rows': rows, 'cols': cols, 'x': x, 'y': y, 'lines': lines}


def screen(tty=1):
    with open(f'/dev/vcsa{tty}', 'rb') as stream:
        header = stream.read(4)
    try:
        with open(f'/dev/vcsu{tty}', 'rb') as stream:
            return parse_screen(header, stream.read(), 4)
    except OSError:
        with open(f'/dev/vcs{tty}', 'rb') as stream:
            return parse_screen(header, stream.read(), 1)


def console_modes(tty=1):
    fd = os.open(f'/dev/tty{tty}', os.O_RDONLY | os.O_NOCTTY)
    try:
        kd = struct.unpack('i', fcntl.ioctl(fd, KDGETMODE, b'\0' * 4))[0]
        kb = struct.unpack('i', fcntl.ioctl(fd, KDGKBMODE, b'\0' * 4))[0]
    finally:
        os.close(fd)
    return kd, kb


def journal_entries(lines):
    """journalctl -o json lines -> the entries the check looks for (MESSAGE may be a byte list)."""
    found = []
    for line in lines:
        if not line.strip():
            continue
        entry = json.loads(line)
        message = entry.get('MESSAGE', '')
        if isinstance(message, list):
            message = bytes(message).decode('utf-8', 'replace')
        if any(text in message for text in JOURNAL_TEXT):
            found.append({'tag': entry.get('SYSLOG_IDENTIFIER', ''),
                          'mono': int(entry['__MONOTONIC_TIMESTAMP']) / 1e6, 'message': message})
    return found


def processes():
    uptime = float(open('/proc/uptime').read().split()[0])
    ticks = os.sysconf('SC_CLK_TCK')
    found = []
    for name in os.listdir('/proc'):
        if not name.isdigit():
            continue
        try:
            comm = open(f'/proc/{name}/comm').read().strip()
            if comm not in PROCESSES:
                continue
            stat = open(f'/proc/{name}/stat').read()
            started = int(stat.rsplit(')', 1)[1].split()[19]) / ticks
            uid = os.stat(f'/proc/{name}').st_uid
        except (OSError, IndexError, ValueError):
            continue
        try:
            user = pwd.getpwuid(uid).pw_name
        except KeyError:
            user = str(uid)
        found.append({'name': comm, 'pid': int(name), 'user': user, 'age': round(uptime - started, 1)})
    return sorted(found, key=lambda entry: entry['pid'])


def main():
    if os.geteuid() != 0:
        sys.exit('guest-graphics-state: run as root')
    virt = subprocess.run(['systemd-detect-virt', '--vm'], capture_output=True, text=True).stdout.strip()
    if virt not in ('qemu', 'kvm'):
        sys.exit(f'guest-graphics-state: not a QEMU/KVM guest ({virt or "none"})')
    snapshot = {'monotonic': time.monotonic(), 'virt': virt}
    with open('/sys/class/tty/tty0/active') as stream:
        snapshot['active_vt'] = stream.read().strip()
    snapshot['kd_mode'], snapshot['kb_mode'] = console_modes()
    snapshot['screen'] = screen()
    shown = subprocess.run(['systemctl', 'show', 'greetd.service', '-p', 'ActiveState', '-p', 'SubState',
                            '-p', 'NRestarts', '-p', 'InvocationID'], capture_output=True, text=True).stdout
    snapshot['greetd'] = dict(line.split('=', 1) for line in shown.splitlines() if '=' in line)
    journal = subprocess.run(['journalctl', '-b', '-o', 'json', '--no-pager',
                              *(arg for tag in JOURNAL_TAGS for arg in ('-t', tag))],
                             capture_output=True, text=True).stdout
    snapshot['journal'] = journal_entries(journal.splitlines())
    snapshot['processes'] = processes()
    json.dump(snapshot, sys.stdout)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
