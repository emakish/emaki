# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Carry the Wi-Fi network the person joined in the live session into the installed system."""
import configparser
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import subprocess

from .errors import Code, InstallError


PROFILE_DIRS = (Path('/etc/NetworkManager/system-connections'),
                Path('/run/NetworkManager/system-connections'))
WIFI_TYPES = ('wifi', '802-11-wireless')
UNAVAILABLE = ('Could not save the Wi-Fi connection [NetworkManager]. '
               'Connect to that network again before installing.')
UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'


class WifiNotCopied(InstallError):
    """A refusal whose reason goes to the install log: fixed words and UUIDs, never a secret,
    a network name or keyfile contents."""
    def __init__(self, reason):
        super().__init__(Code.BAD_CONFIG, UNAVAILABLE)
        self.reason = reason


def refuse(condition, reason):
    if not condition:
        raise WifiNotCopied(reason)


def private_command(argv, *, input=None):
    try:
        result = subprocess.run(argv, input=input, capture_output=True, timeout=35,
                                env=dict(os.environ, LC_ALL="C"))
    except subprocess.TimeoutExpired:
        raise WifiNotCopied('NetworkManager did not answer within 35 seconds.') from None
    except OSError:
        raise WifiNotCopied('nmcli could not be started.') from None
    refuse(result.returncode == 0, f'nmcli failed (exit {result.returncode}).')
    try:
        return result.stdout.decode("utf-8")
    except UnicodeError:
        raise WifiNotCopied('nmcli printed text that is not UTF-8.') from None


@dataclass(frozen=True)
class Profile:
    uuid: str
    timestamp: int
    active: bool
    filename: str


def saved_wifi(*, run=private_command):
    """Every Wi-Fi profile NetworkManager has in the live session, saved or in memory."""
    # FILENAME is last: unescaped, it may contain ':'. TIMESTAMP is the last successful
    # activation (0 = never connected); ACTIVE includes a connection still activating.
    listing = run(['nmcli', '-t', '--escape', 'no', '-f', 'UUID,TYPE,TIMESTAMP,ACTIVE,FILENAME',
                   'connection', 'show'])
    profiles = []
    for line in listing.splitlines():
        row = line.split(':', 4)
        if len(row) == 5 and row[1] in WIFI_TYPES and re.fullmatch(UUID, row[0]):
            profiles.append(Profile(row[0], int(row[2]) if row[2].isdigit() else 0,
                                    row[3] == 'yes', row[4]))
    return profiles


@dataclass(frozen=True)
class Choice:
    uuid: str | None
    reason: str
    saved: int


def choose_wifi(joined=None, *, run=private_command):
    """Which Wi-Fi profile the installed system keeps, and why (DECISIONS 2026-10-10).

    1. The one active Wi-Fi connection, wherever it was joined (panel or Network page).
    2. With several active, the one the Network page joined.
    3. With none active (the link dropped), the network joined on the Network page. A recorded
       join that is no longer saved is refused, never replaced by another saved profile.
    4. With none active and no Network page join (a panel join), the live session's only saved
       Wi-Fi profile, if it has ever connected. With several saved, none: an earlier connection
       does not say which network the person means now.
    """
    profiles = saved_wifi(run=run)
    uuids = {p.uuid for p in profiles}
    active = [p.uuid for p in profiles if p.active]
    if len(active) == 1:
        return Choice(active[0], 'the active Wi-Fi connection', len(profiles))
    if active:
        if joined in active:
            return Choice(joined, f'joined on the Network page, one of {len(active)} active Wi-Fi connections',
                          len(profiles))
        return Choice(None, f'{len(active)} Wi-Fi connections are active and the Network page '
                            'joined none of them', len(profiles))
    if joined in uuids:
        return Choice(joined, 'joined on the Network page; no Wi-Fi is active now', len(profiles))
    if joined:
        return Choice(None, f'the Network page join {joined} is no longer saved and no Wi-Fi '
                            'is active now', len(profiles))
    if len(profiles) == 1 and profiles[0].timestamp > 0:
        return Choice(profiles[0].uuid, 'the only saved Wi-Fi profile, and it has connected; '
                                        'no Wi-Fi is active now', 1)
    if len(profiles) == 1:
        # A failed join leaves a profile that never connected, often with a wrong password.
        return Choice(None, 'the only saved Wi-Fi profile has never connected', 1)
    if profiles:
        return Choice(None, f'{len(profiles)} saved Wi-Fi profiles, none is active and the Network '
                            'page joined none of them', len(profiles))
    return Choice(None, 'the live session has no Wi-Fi profile', 0)


def capture_wifi(uuid, *, run=private_command, directories=PROFILE_DIRS, owner=0, note=None):
    """Read one root-owned keyfile, active or not; never log its contents or saved secrets."""
    refuse(isinstance(uuid, str) and re.fullmatch(UUID, uuid), 'the profile ID is not a UUID.')
    paths = [p.filename for p in saved_wifi(run=run) if p.uuid == uuid]
    refuse(len(paths) == 1, f'NetworkManager lists {len(paths)} Wi-Fi profiles with this UUID.')
    refuse(bool(paths[0]), 'the profile has no keyfile (NetworkManager keeps it only in memory).')
    path = Path(paths[0])
    refuse(path.parent in directories, 'the keyfile is outside NetworkManager\'s connection '
                                       'directories (' + ', '.join(map(str, directories)) + ').')
    refuse(path.resolve() == path, 'the keyfile path goes through a symbolic link.')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise WifiNotCopied(f'the keyfile could not be opened ({error.strerror}).') from None
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        refuse(stat.S_ISREG(info.st_mode), 'the keyfile is not a regular file.')
        refuse(info.st_uid == owner, f'the keyfile belongs to UID {info.st_uid}, not {owner}.')
        refuse(info.st_nlink == 1, 'the keyfile has more than one hard link.')
        refuse(not info.st_mode & 0o077, f'the keyfile is readable by others (mode {info.st_mode & 0o777:o}).')
        refuse(info.st_size <= 1024 * 1024, 'the keyfile is larger than 1 MiB.')
        data = stream.read(1024 * 1024 + 1)
    # Normalize aliases before checking saved secrets. libnm accepts both wifi-security and
    # 802-11-wireless-security in source keyfiles. Both join paths bind the profile to the live
    # adapter's name (interface-name=wlan0); the installed system may name it differently.
    # Leave autoconnect to the source profile, including NetworkManager's default when absent.
    prepared = run(['nmcli', '--offline', 'connection', 'modify',
                    'connection.permissions', '',
                    'connection.interface-name', ''],
                   input=data)
    refuse(bool(prepared.strip()), 'NetworkManager returned an empty profile.')
    try:
        profile = configparser.ConfigParser(interpolation=None, strict=True)
        profile.read_string(prepared)
        refuse(profile.get('connection', 'uuid') == uuid, 'the keyfile holds another UUID.')
        refuse(profile.get('connection', 'type') in WIFI_TYPES, 'the keyfile is not a Wi-Fi profile.')
        # The join page supports personal and open networks. Session-only secrets
        # or external enterprise certificates cannot safely survive this copy.
        refuse(not profile.has_section('802-1x'), 'enterprise (802.1X) networks are not copied.')
        method = 'open'
        if profile.has_section('wifi-security'):
            security = profile['wifi-security']
            method = security.get('key-mgmt', '')
            if method == 'none':
                index = security.get('wep-tx-keyidx', '0')
                flags = security.get('wep-key-flags', '0')
                refuse(flags == '0', f'the WEP key is not saved in the keyfile (wep-key-flags {flags}: '
                                     'held by a desktop keyring client or asked at each connection).')
                refuse(index in ('0', '1', '2', '3') and bool(security.get('wep-key' + index)),
                       'the keyfile has no WEP key.')
            elif method in ('wpa-psk', 'sae'):
                flags = security.get('psk-flags', '0')
                refuse(flags == '0', f'the password is not saved in the keyfile (psk-flags {flags}: '
                                     'held by a desktop keyring client or asked at each connection).')
                refuse(bool(security.get('psk')), 'the keyfile has no password.')
            else:
                refuse(method == 'owe', f'key management {method or "unknown"} is not supported.')
    except (configparser.Error, UnicodeError, KeyError, ValueError):
        raise WifiNotCopied('NetworkManager\'s normalized profile could not be read.') from None
    if note:
        secret = 'no secret (open network)' if method in ('open', 'owe') else \
            'secret saved in the keyfile'
        note(f'captured {uuid} from {path.parent}: security {method}, {secret}; '
             'autoconnect preserved, any adapter, every user.')
    return prepared.encode()


def install_wifi(uuid, data, files):
    directory = files.mkdir('/etc/NetworkManager/system-connections', 0o700)
    directory.chmod(0o700)
    files.write('/etc/NetworkManager/system-connections/' + uuid + '.nmconnection', data, 0o600)
