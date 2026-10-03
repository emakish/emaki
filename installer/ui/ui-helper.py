#!/usr/bin/env python3
"""Unprivileged catalog, NetworkManager and live-keyboard operations.

One JSON request on stdin; credentials never enter argv, files or diagnostics.
There is no installation or block-device mutation in this helper.
"""
import json
import os
from pathlib import Path
import pwd
import re
import stat
import subprocess
import sys


def run(argv, **kwargs):
    return subprocess.run(argv, capture_output=True, timeout=35,
                          env=dict(os.environ, LC_ALL='C'), **kwargs)


def layouts(path=Path('/usr/share/X11/xkb/rules/evdev.lst')):
    rows, section = [], ''
    for line in path.read_text().splitlines():
        if line.startswith('!'):
            section = line[1:].strip()
        elif line.strip() and section == 'layout':
            key, label = line.split(None, 1)
            rows.append(dict(layout=key, variant='', label=label.strip()))
        elif line.strip() and section == 'variant':
            match = re.fullmatch(r'\s*(\S+)\s+(\S+):\s*(.+)', line)
            if match:
                variant, key, label = match.groups()
                rows.append(dict(layout=key, variant=variant, label=label))
    return rows


def zones(path=Path('/usr/share/zoneinfo/tzdata.zi')):
    if path.exists():
        names = {line.split()[1] for line in path.read_text().splitlines() if line.startswith('Z ')}
        names.update(line.split()[2] for line in path.read_text().splitlines() if line.startswith('L '))
        return sorted(names | {'UTC'})
    result = run(['timedatectl', 'list-timezones'])
    return sorted(set(result.stdout.decode().splitlines()) | {'UTC'})


def trial_available():
    user = pwd.getpwuid(os.getuid())
    return (user.pw_name == 'live' and Path('/run/archiso/bootmnt').is_dir()
            and bool(os.environ.get('NIRI_SOCKET')))


def trial(chosen):
    if not trial_available():
        return dict(ok=False, message='Keyboard trial is available only in the live Emaki session.')
    known = {x['layout'] for x in layouts() if not x['variant']}
    if not isinstance(chosen, list) or not 1 <= len(chosen) <= 4 or len(set(chosen)) != len(chosen) or any(x not in known for x in chosen):
        raise ValueError()
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    parent = home / '.config/emaki'
    if parent.resolve() != parent or not parent.is_dir():
        raise ValueError()
    target = parent / 'installer-input.kdl'
    data = ('// Live installer keyboard trial.\ninput {\n    keyboard {\n        xkb {\n'
            f'            layout "{",".join(chosen)}"\n'
            '            variant ""\n            options ""\n        }\n    }\n}\n').encode()
    # One small write, only to the dedicated target; no user's niri config is edited.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
            raise ValueError()
        os.write(fd, data)
        os.ftruncate(fd, len(data))
        os.fsync(fd)
    finally:
        os.close(fd)
    return dict(ok=True, message='Layouts saved for the live session; allow a moment for niri to reload. Mod+Space switches layouts.')


def terse(line):
    fields, buf, escaped = [], '', False
    for char in line:
        if escaped:
            buf += char
            escaped = False
        elif char == '\\':
            escaped = True
        elif char == ':':
            fields.append(buf)
            buf = ''
        else:
            buf += char
    fields.append(buf)
    return fields


def network():
    devices = run(['nmcli', '-t', '-f', 'TYPE,STATE', 'device', 'status'])
    if devices.returncode:
        return dict(ok=False, message='NetworkManager is unavailable. You can install offline.', networks=[], wired=False)
    states = [terse(line) for line in devices.stdout.decode().splitlines()]
    wired = any(len(row) == 2 and row[0] == 'ethernet' and row[1].startswith('connected') for row in states)
    result = run(['nmcli', '-t', '-f', 'IN-USE,SSID,BSSID,SIGNAL,SECURITY,DEVICE', 'device', 'wifi', 'list', '--rescan', 'auto'])
    rows = []
    for line in result.stdout.decode('utf-8', 'replace').splitlines():
        fields = terse(line)
        if len(fields) != 6:
            continue
        active, ssid, bssid, strength, security, device = fields
        if security == '--': security = ''
        if ssid and re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', bssid):
            rows.append(dict(ssid=ssid, bssid=bssid, strength=int(strength), security=security,
                             device=device, connected=active == '*', enterprise='802.1X' in security))
    return dict(ok=True, wired=wired, networks=sorted(rows, key=lambda x: (-x['connected'], -x['strength'])))


def join(request):
    bssid, device, secret = (request.get(k, '') for k in ('bssid', 'device', 'password'))
    if not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', bssid) or not re.fullmatch(r'[A-Za-z0-9_.:-]+', device):
        raise ValueError()
    if not isinstance(secret, str) or any(c in secret for c in '\n\r\0'):
        raise ValueError()
    # Like the shell's helper: bounded nmcli, fixed locale, no stderr/secret echo.
    # --ask reads the PSK from stdin; it must never be a `password ...` argv.
    result = run(['nmcli', '--ask', '--wait', '25', 'device', 'wifi', 'connect', bssid, 'ifname', device], input=(secret + '\n').encode())
    return dict(ok=result.returncode == 0, message='Connected' if result.returncode == 0 else
                {3: 'Connection timed out.', 4: 'Could not connect. Check the password.',
                 8: 'NetworkManager is not running.', 10: 'Network is no longer available.'}.get(result.returncode, 'Could not connect to this network.'))


def media():
    result = run(['findmnt', '-J', '-o', 'TARGET,SOURCE'])
    mounts = json.loads(result.stdout).get('filesystems', []) if result.returncode == 0 else []
    candidates = []
    def walk(nodes):
        for node in nodes:
            target, source = node.get('target', ''), node.get('source', '').split('[', 1)[0]
            if target.startswith('/run/media/live/') and source.startswith('/dev/') and Path(target).resolve() == Path(target):
                info = run(['lsblk', '--tree', '-s', '-J', '-o', 'RM,TRAN', source])
                def removable(rows):
                    return any(row.get('rm') in (True, 1, '1') or row.get('tran') == 'usb' or removable(row.get('children', [])) for row in rows)
                if info.returncode == 0 and removable(json.loads(info.stdout).get('blockdevices', [])):
                    candidates.append(target)
            walk(node.get('children', []))
    walk(mounts)
    return dict(ok=True, media=sorted(set(candidates)))


def main():
    try:
        request = json.loads(sys.stdin.buffer.readline(8193))
        op = request.get('op')
        if op == 'catalog':
            result = dict(ok=True, layouts=layouts(), zones=zones(), trial=trial_available())
        elif op == 'network': result = network()
        elif op == 'join': result = join(request)
        elif op == 'trial': result = trial(request.get('layouts'))
        elif op == 'media': result = media()
        else: raise ValueError()
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        result = dict(ok=False, message='This operation is unavailable. Please try again.')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
