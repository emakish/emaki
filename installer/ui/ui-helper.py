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
import select
import socket
import stat
import struct
import subprocess
import sys


# The live boot medium. Without it (copytoram) the worker's unit never starts:
# installer/systemd/emaki-installerd.service has ConditionPathExists on this path.
BOOT_MOUNT = Path('/run/archiso/bootmnt')


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


def zones(root=Path('/usr/share/zoneinfo')):
    names = {'UTC'}
    for table in ('zone1970.tab', 'zone.tab'):
        path = root / table
        if path.is_file():
            names.update(line.split()[2] for line in path.read_text().splitlines()
                         if line and not line.startswith('#') and len(line.split()) >= 3)
    return sorted(name for name in names if (root / name).is_file())


def trial_available():
    user = pwd.getpwuid(os.getuid())
    return (user.pw_name == 'live' and BOOT_MOUNT.is_dir()
            and bool(os.environ.get('NIRI_SOCKET')))


def boot_removable():
    if not BOOT_MOUNT.is_dir():
        return False
    result = run(['findmnt', '-n', '-o', 'SOURCE', '--mountpoint', str(BOOT_MOUNT)])
    source = result.stdout.decode().strip().split('[', 1)[0]
    if result.returncode or not source.startswith('/dev/'):
        return False
    result = run(['lsblk', '--tree', '-s', '-J', '-o', 'RM,TRAN', source])
    def removable(rows):
        return any(row.get('rm') in (True, 1, '1') or row.get('tran') == 'usb'
                   or removable(row.get('children', [])) for row in rows)
    return result.returncode == 0 and removable(json.loads(result.stdout).get('blockdevices', []))


def catalog():
    # boot_medium: the window tells the person why the worker never answers.
    try:
        removable = boot_removable()
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        removable = False
    return dict(ok=True, layouts=layouts(), zones=zones(), trial=trial_available(),
                boot_medium=BOOT_MOUNT.is_dir(), boot_removable=removable,
                output_scales=output_scales())


def trial_reloads():
    """Whether the compositor behind NIRI_SOCKET reads the trial file.

    Only niri-emaki does: its configs (fork.kdl, fork-system.kdl) include
    ~/.config/emaki/niri-emaki.kdl, which includes installer-input.kdl. The stock niri session
    reads ~/.config/niri/config.kdl or /etc/niri/config.kdl and never sees the file. Unknown
    answers True: the window still waits for niri to report the list.
    """
    try:
        with socket.socket(socket.AF_UNIX) as stream:
            stream.settimeout(.5)
            stream.connect(os.environ['NIRI_SOCKET'])
            pid = struct.unpack('3i', stream.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))[0]
        # A binary replaced while it runs reads as "/usr/bin/niri-emaki (deleted)".
        return Path(os.readlink(f'/proc/{pid}/exe')).name.split(' ', 1)[0] == 'niri-emaki'
    except (OSError, KeyError, struct.error):
        return True


def trial(chosen):
    if not trial_available():
        return dict(ok=False, message='Keyboard trial is available only in the live Emaki session.')
    if not trial_reloads():
        return dict(ok=False, reloads=False, message='This session does not read the keyboard trial.')
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
    return dict(ok=True, message='Layouts saved for the live session; allow a moment for niri to reload. Super+Space switches layouts.')


def niri_request(kind):
    # The request the lock screen's helper sends (shell/helpers/lock-environment.py).
    with socket.socket(socket.AF_UNIX) as stream:
        stream.settimeout(.5)
        stream.connect(os.environ['NIRI_SOCKET'])
        stream.sendall((json.dumps(kind) + '\n').encode())
        data = b''
        while b'\n' not in data:
            chunk = stream.recv(16384)
            if not chunk or len(data) > 1048576:
                raise ValueError()
            data += chunk
    return json.loads(data.split(b'\n', 1)[0])['Ok']


def output_scales():
    """Keep the session's active output scales; disconnected outputs have no logical size."""
    try:
        outputs = niri_request('Outputs')['Outputs']
        return {name: item['logical']['scale'] for name, item in outputs.items()
                if item.get('logical') is not None}
    except (OSError, KeyError, TypeError, ValueError):
        return {}


LEDS = Path('/sys/class/leds')


def lock_led(name):
    """Whether a lock key is on, from the keyboards' LEDs named `*::<name>` (niri keeps them in
    step with its own state), as the lock screen reads Caps Lock
    (shell/helpers/lock-environment.py); None without such a LED."""
    values = []
    for path in LEDS.glob(f'*::{name}/brightness'):
        try:
            values.append(int(path.read_text().strip()) > 0)
        except (OSError, ValueError):
            pass
    return any(values) if values else None


def caps_lock():
    return lock_led('capslock')


def num_lock():
    return lock_led('numlock')


def layout_state():
    """The layouts niri runs now, as codes: a written trial file is not yet an active layout.

    niri names a layout by its xkeyboard-config description; the code is the first evdev.lst
    row with that description, as on the login and lock screens (lock-environment.py). caps and
    num: Caps Lock and Num Lock, for the lines under the password fields.
    """
    if not os.environ.get('NIRI_SOCKET'):
        return dict(ok=False, codes=[], current=-1)
    state = niri_request('KeyboardLayouts')['KeyboardLayouts']
    names, index = [str(name) for name in state['names']], state['current_idx']
    table = {}
    for row in layouts():
        table.setdefault(row['label'], row['layout'].upper())
    current = index if type(index) is int and 0 <= index < len(names) else -1
    return dict(ok=True, codes=[table.get(name) or name[:32] for name in names], current=current, caps=caps_lock(), num=num_lock())


def layout_events(stdin=sys.stdin, stdout=sys.stdout):
    """Follow niri's event stream until stdin closes: one {"switched": true} line per change of
    the layout list or of the active layout, after one {"ready": true} line.

    A layout_state report is read after the key it checks, so Super+Space, a key and Super+Space
    back inside one round trip look right in it; the window counts these lines instead. niri sends
    its current KeyboardLayoutsChanged first (niri-ipc EventStreamState::replicate): that one is
    the starting point, not a change. Switches inside one niri event loop iteration send no event
    (State::ipc_refresh_keyboard_layout_index compares with the last sent index).
    """
    if not os.environ.get('NIRI_SOCKET'):
        return
    with socket.socket(socket.AF_UNIX) as stream:
        stream.settimeout(.5)
        stream.connect(os.environ['NIRI_SOCKET'])
        stream.sendall(b'"EventStream"\n')
        stream.setblocking(False)
        data, state = b'', None
        while True:
            ready, _, _ = select.select([stream, stdin], [], [])
            if stdin in ready and not os.read(stdin.fileno(), 4096):
                return
            if stream not in ready:
                continue
            chunk = stream.recv(65536)
            if not chunk or len(data) > 16777216:
                return
            data += chunk
            *lines, data = data.split(b'\n')
            for line in lines:
                event = json.loads(line)
                if 'Ok' in event or 'Err' in event:
                    continue
                if 'KeyboardLayoutsChanged' in event:
                    layouts = event['KeyboardLayoutsChanged']['keyboard_layouts']
                    current = (tuple(layouts['names']), layouts['current_idx'])
                elif 'KeyboardLayoutSwitched' in event:
                    current = (state[0] if state else None, event['KeyboardLayoutSwitched']['idx'])
                else:
                    continue
                if state is None and 'KeyboardLayoutsChanged' in event:
                    print(json.dumps(dict(ready=True)), file=stdout, flush=True)
                elif current != state:
                    print(json.dumps(dict(switched=True)), file=stdout, flush=True)
                state = current


def first_layout():
    """`niri msg action switch-layout 0`: the first layout of niri's list becomes the active one."""
    if not os.environ.get('NIRI_SOCKET'):
        return dict(ok=False)
    return dict(ok=niri_request({'Action': {'SwitchLayout': {'layout': {'Index': 0}}}}) == 'Handled')


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
    # One row per network: the access point in use, else the strongest one, is kept for join.
    merged = {}
    for row in sorted(rows, key=lambda x: (-x['connected'], -x['strength'])):
        merged.setdefault((row['ssid'], row['security']), row)
    return dict(ok=True, wired=wired, networks=list(merged.values()))


def join(request):
    bssid, device, secret = (request.get(k, '') for k in ('bssid', 'device', 'password'))
    if not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', bssid) or not re.fullmatch(r'[A-Za-z0-9_.:-]+', device):
        raise ValueError()
    if not isinstance(secret, str) or any(c in secret for c in '\n\r\0'):
        raise ValueError()
    # Like the shell's helper: bounded nmcli, fixed locale, no stderr/secret echo.
    # --ask reads the PSK from stdin; it must never be a `password ...` argv.
    result = run(['nmcli', '--ask', '--wait', '25', 'device', 'wifi', 'connect', bssid, 'ifname', device], input=(secret + '\n').encode())
    reply = dict(ok=result.returncode == 0, message='Connected' if result.returncode == 0 else
                 {3: 'Connection timed out.', 4: 'Could not connect. Check the password.',
                  8: 'NetworkManager is not running.', 10: 'Network is no longer available.'}.get(result.returncode, 'Could not connect to this network.'))
    if reply['ok']:
        state = run(['nmcli', '-g', 'GENERAL.CON-UUID', 'device', 'show', device])
        uuid = state.stdout.decode().strip()
        if state.returncode == 0 and re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', uuid):
            reply['wifi_uuid'] = uuid.lower()
        else:
            reply.update(ok=False, message='Connected, but the Wi-Fi settings could not be saved for installation. Connect again.')
    return reply


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
        if op == 'catalog': result = catalog()
        elif op == 'network': result = network()
        elif op == 'join': result = join(request)
        elif op == 'trial': result = trial(request.get('layouts'))
        elif op == 'layout_state': result = layout_state()
        elif op == 'first_layout': result = first_layout()
        elif op == 'layout_events':
            layout_events()
            return
        elif op == 'media': result = media()
        else: raise ValueError()
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        result = dict(ok=False, message='This operation is unavailable. Please try again.')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
