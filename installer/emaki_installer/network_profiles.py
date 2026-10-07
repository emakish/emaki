# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Carry only the Wi-Fi connection selected by a successful installer join."""
import configparser
import os
from pathlib import Path
import re
import stat
import subprocess

from .errors import Code, require


PROFILE_DIRS = (Path('/etc/NetworkManager/system-connections'),
                Path('/run/NetworkManager/system-connections'))
UNAVAILABLE = ('Could not save the Wi-Fi connection [NetworkManager]. '
               'Connect to that network again before installing.')


def private_command(argv, *, input=None):
    try:
        result = subprocess.run(argv, input=input, capture_output=True, timeout=35,
                                env=dict(os.environ, LC_ALL="C"))
        require(result.returncode == 0, Code.BAD_CONFIG, UNAVAILABLE)
        return result.stdout.decode("utf-8")
    except (OSError, subprocess.TimeoutExpired, UnicodeError):
        require(False, Code.BAD_CONFIG, UNAVAILABLE)


def active_wifi(*, run=private_command):
    """Select the sole active Wi-Fi profile, including joins from the desktop."""
    active = run(['nmcli', '-t', '-f', 'UUID,TYPE', 'connection', 'show', '--active'])
    uuids = [line.rsplit(':', 1)[0] for line in active.splitlines()
             if line.endswith(':802-11-wireless')]
    require(len(uuids) <= 1, Code.BAD_CONFIG, UNAVAILABLE)
    return uuids[0] if uuids else None


def capture_wifi(uuid, *, run=private_command, directories=PROFILE_DIRS, owner=0):
    """Read one root-owned keyfile, never log its contents or saved secrets."""
    require(isinstance(uuid, str) and re.fullmatch(
        r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', uuid), Code.BAD_CONFIG, UNAVAILABLE)
    active = run(['nmcli', '-t', '-f', 'UUID,TYPE', 'connection', 'show', '--active'])
    require(any(line == uuid + ':802-11-wireless' for line in active.splitlines()),
            Code.BAD_CONFIG, UNAVAILABLE)
    listing = run(['nmcli', '-t', '--escape', 'no', '-f', 'UUID,TYPE,FILENAME',
                          'connection', 'show'])
    paths = [line.split(':', 2)[2] for line in listing.splitlines()
             if line.startswith(uuid + ':802-11-wireless:')]
    require(len(paths) == 1, Code.BAD_CONFIG, UNAVAILABLE)
    path = Path(paths[0])
    require(path.parent in directories and path.resolve() == path, Code.BAD_CONFIG, UNAVAILABLE)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == owner
                and info.st_nlink == 1 and not info.st_mode & 0o077
                and info.st_size <= 1024 * 1024, Code.BAD_CONFIG, UNAVAILABLE)
        data = stream.read(1024 * 1024 + 1)
    try:
        profile = configparser.ConfigParser(interpolation=None, strict=True)
        profile.read_string(data.decode('utf-8'))
        require(profile.get('connection', 'uuid') == uuid
                and profile.get('connection', 'type') in ('wifi', '802-11-wireless'),
                Code.BAD_CONFIG, UNAVAILABLE)
        # The join page supports personal and open networks. Session-only secrets
        # or external enterprise certificates cannot safely survive this copy.
        require(not profile.has_section('802-1x'), Code.BAD_CONFIG, UNAVAILABLE)
        if profile.has_section('wifi-security'):
            security = profile['wifi-security']
            method = security.get('key-mgmt')
            if method == 'none':
                index = security.get('wep-tx-keyidx', '0')
                saved = index in ('0', '1', '2', '3') and bool(security.get('wep-key' + index)) \
                    and security.get('wep-key-flags', '0') == '0'
            else:
                saved = method == 'owe' or (method in ('wpa-psk', 'sae')
                    and bool(security.get('psk')) and security.get('psk-flags', '0') == '0')
            require(saved, Code.BAD_CONFIG, UNAVAILABLE)
    except (configparser.Error, UnicodeError, KeyError, ValueError):
        require(False, Code.BAD_CONFIG, UNAVAILABLE)
    # libnm validates and serializes the keyfile offline. Remove the live user's
    # permissions so the installed account can activate this same connection.
    prepared = run(['nmcli', '--offline', 'connection', 'modify',
                           'connection.permissions', '', 'connection.autoconnect', 'yes'],
                          input=data)
    require(bool(prepared.strip()), Code.BAD_CONFIG, UNAVAILABLE)
    return prepared.encode()


def install_wifi(uuid, data, files):
    directory = files.mkdir('/etc/NetworkManager/system-connections', 0o700)
    directory.chmod(0o700)
    files.write('/etc/NetworkManager/system-connections/' + uuid + '.nmconnection', data, 0o600)
