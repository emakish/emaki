#!/usr/bin/env python3
"""Private greeter selection memory. No passwords, user home reads or shell commands."""
import configparser
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import math
import os
from pathlib import Path
import re
import secrets
import shlex
import stat
import sys
import time
import tomllib

MAX_FILE = 65536
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,62}\$?\Z")
SESSION = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}\.desktop\Z")


@dataclass(frozen=True)
class Paths:
    state: Path = Path('/var/lib/emaki-greeter/state')
    users: Path = Path('/var/lib/emaki-greeter/users')
    passwd: Path = Path('/etc/passwd')
    login_defs: Path = Path('/etc/login.defs')
    sessions: Path = Path('/usr/share/wayland-sessions')
    regreet: Path = Path('/var/lib/regreet/state.toml')
    boot_id: Path = Path('/proc/sys/kernel/random/boot_id')


@contextmanager
def directory(path):
    """Open every component without following symlinks; callers keep the fd pinned."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('absolute path required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def read_at(fd, name, private=False):
    file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                      dir_fd=fd)
    with os.fdopen(file_fd, 'rb') as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_FILE:
            raise ValueError('not a bounded regular file')
        if private and (metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600
                        or metadata.st_nlink != 1):
            raise ValueError('state file is not private')
        data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise ValueError('file too large')
        return data.decode('utf-8')


def read_file(path):
    with directory(path.parent) as fd:
        return read_at(fd, path.name)


def repair_private_file(fd, name):
    """Retire an unsafe directory entry, never open its contents or link target."""
    try:
        metadata = os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if (stat.S_ISREG(metadata.st_mode) and metadata.st_uid == os.geteuid()
            and stat.S_IMODE(metadata.st_mode) == 0o600 and metadata.st_nlink == 1
            and metadata.st_size <= MAX_FILE):
        return
    # Even an unexpected directory is moved without traversing it. Retain the
    # rejected entry privately for administrator inspection, not as active state.
    os.rename(name, '.invalid-' + name.lstrip('.') + '-' + secrets.token_hex(12),
              src_dir_fd=fd, dst_dir_fd=fd)
    os.fsync(fd)


@contextmanager
def locked_state(path):
    with directory(path) as fd:
        metadata = os.fstat(fd)
        if metadata.st_uid != os.geteuid():
            raise ValueError('state directory has a foreign owner')
        # Lock the stable directory inode before repairing .lock. Otherwise two
        # helpers could replace that file and hold unrelated advisory locks.
        fcntl.flock(fd, fcntl.LOCK_EX)
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            os.fchmod(fd, 0o700)
        repair_private_file(fd, '.lock')
        lock = os.open('.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                       0o600, dir_fd=fd)
        try:
            metadata = os.fstat(lock)
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1):
                raise ValueError('state lock changed during repair')
            fcntl.flock(lock, fcntl.LOCK_EX)
            repair_private_file(fd, 'state.json')
            yield fd
        finally:
            os.close(lock)


def empty_state():
    return dict(version=1, last_user='', users={}, pending_launch=None, seeded=False)


def valid_name(value):
    return isinstance(value, str) and NAME.fullmatch(value) is not None


def valid_session(value):
    return isinstance(value, str) and SESSION.fullmatch(value) is not None


def decode_state(raw):
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('users'), dict) \
            or 'pending_launch' not in data:
        raise ValueError('state schema')
    if data.get('last_user') != '' and not valid_name(data.get('last_user')):
        raise ValueError('last user')
    if not isinstance(data.get('seeded'), bool) or len(data['users']) > 256:
        raise ValueError('state schema')
    for user, value in data['users'].items():
        if not valid_name(user) or not isinstance(value, dict):
            raise ValueError('user entry')
        if not valid_session(value.get('last_session')):
            raise ValueError('last session')
        if value.get('failed_session') != '' and not valid_session(value.get('failed_session')):
            raise ValueError('failed session')
    pending = data.get('pending_launch')
    if pending is not None:
        if not isinstance(pending, dict) or not valid_name(pending.get('user')) \
                or not valid_session(pending.get('session')):
            raise ValueError('pending launch')
        timestamp = pending.get('launched_at')
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError('launch time')
        if not isinstance(pending.get('boot_id'), str) or len(pending['boot_id']) > 64:
            raise ValueError('boot id')
    return data


def write_state(fd, data):
    encoded = (json.dumps(data, ensure_ascii=True, separators=(',', ':')) + '\n').encode()
    if len(encoded) > MAX_FILE:
        raise ValueError('state too large')
    name = '.state-' + secrets.token_hex(12)
    output = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=fd)
    try:
        with os.fdopen(output, 'wb') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, 'state.json', src_dir_fd=fd, dst_dir_fd=fd)
        os.fsync(fd)
    finally:
        try:
            os.unlink(name, dir_fd=fd)
        except FileNotFoundError:
            pass


def accounts(paths):
    low, high = 1000, 60000
    try:
        for line in read_file(paths.login_defs).splitlines():
            words = line.split('#', 1)[0].split()
            if len(words) == 2 and words[0] in ('UID_MIN', 'UID_MAX'):
                if words[0] == 'UID_MIN':
                    low = int(words[1])
                else:
                    high = int(words[1])
    except FileNotFoundError:
        pass
    if not 1 <= low <= high <= 2**32 - 2:
        raise ValueError('UID range')
    users = set()
    for line in read_file(paths.passwd).splitlines():
        fields = line.split(':')
        if len(fields) != 7 or not valid_name(fields[0]):
            continue
        try:
            uid = int(fields[2])
        except ValueError:
            continue
        if low <= uid <= high and fields[6].rsplit('/', 1)[-1] not in ('nologin', 'false'):
            users.add(fields[0])
    return users


def exec_words(value):
    """Desktop Exec quoting, not shell parsing. Reject unsupported field codes below."""
    # Desktop Entry string escapes are applied before Exec argument quoting.
    escapes = {'s': ' ', 'n': '\n', 't': '\t', 'r': '\r', '\\': '\\'}
    decoded = ''
    at = 0
    while at < len(value):
        char = value[at]
        if char == '\\' and at + 1 < len(value) and value[at + 1] in escapes:
            at += 1
            char = escapes[value[at]]
        decoded += char
        at += 1
    words, current, quoted, present = [], '', False, False
    at = 0
    while at < len(decoded):
        char = decoded[at]
        if char == '"':
            quoted = not quoted
            present = True
        elif char == '\\':
            at += 1
            if at >= len(decoded):
                raise ValueError('trailing Exec escape')
            current += decoded[at]
            present = True
        elif char.isspace() and not quoted:
            if present:
                words.append(current)
                current, present = '', False
        elif not quoted and char in "'`$><~|&;*?#()":
            raise ValueError('unquoted reserved Exec character')
        else:
            current += char
            present = True
        at += 1
    if quoted:
        raise ValueError('unclosed Exec quote')
    if present:
        words.append(current)
    if not words or len(words) > 128 or not words[0]:
        raise ValueError('empty Exec')
    return words


def parse_session(raw, path):
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    parser.read_string(raw)
    section = parser['Desktop Entry']
    if section.get('Type') != 'Application' or section.get('Hidden', 'false').lower() == 'true':
        raise ValueError('not an available application')
    name = section.get('Name', path.stem)
    command = []
    for word in exec_words(section['Exec']):
        if word in ('%f', '%F', '%u', '%U'):
            continue
        if word == '%i':
            if section.get('Icon'):
                command.extend(['--icon', section['Icon']])
            continue
        if word == '%c':
            command.append(name)
            continue
        if word == '%k':
            command.append(str(path))
            continue
        # Only %% can occur inside an ordinary argument; no file substitutions.
        if '%' in word.replace('%%', ''):
            raise ValueError('unsupported Exec field code')
        command.append(word.replace('%%', '%'))
    if not command or not command[0] or any('\0' in word for word in command):
        raise ValueError('invalid Exec')
    return dict(name=name, command=command)


def session_catalog(paths):
    result = {}
    with directory(paths.sessions) as fd:
        for filename in sorted(os.listdir(fd)):
            if not valid_session(filename):
                continue
            try:
                result[filename] = parse_session(read_at(fd, filename), paths.sessions / filename)
            except (OSError, ValueError, KeyError, configparser.Error):
                # A broken optional desktop file must not hide a valid Niri fallback.
                continue
    return result


def seed_state(state, paths, users, catalog):
    if state['seeded']:
        return
    state['seeded'] = True
    try:
        seed = tomllib.loads(read_file(paths.regreet))
    except (OSError, ValueError, RecursionError):
        return
    user = seed.get('last_user')
    if isinstance(user, str) and user in users:
        state['last_user'] = user
    remembered = seed.get('user_to_last_sess', {})
    if not isinstance(remembered, dict):
        return
    for user, name in remembered.items():
        matches = [key for key, value in catalog.items() if value['name'] == name]
        if user in users and len(matches) == 1:
            state['users'][user] = dict(last_session=matches[0], failed_session='')


def reconcile(state, now, boot_id):
    pending = state['pending_launch']
    if pending is None:
        return
    state['pending_launch'] = None
    if pending['boot_id'] != boot_id or not boot_id:
        return
    elapsed = now - pending['launched_at']
    if pending['session'] == 'niri-emaki.desktop':
        entry = state['users'].setdefault(pending['user'], dict(last_session=pending['session'], failed_session=''))
        if 0 <= elapsed < 30:
            entry['failed_session'] = pending['session']
        elif elapsed >= 30:
            entry['failed_session'] = ''


def selection(state, user, users, catalog, paths, message=''):
    chosen = ''
    if user:
        entry = state['users'].get(user, {})
        remembered = entry.get('last_session', '')
        if remembered in catalog:
            chosen = remembered
        elif 'niri-emaki.desktop' in catalog:
            chosen = 'niri-emaki.desktop'
        elif 'niri.desktop' in catalog:
            chosen = 'niri.desktop'
        if chosen == 'niri-emaki.desktop' and entry.get('failed_session') == chosen:
            chosen = 'niri.desktop' if 'niri.desktop' in catalog else ''
        if not chosen:
            raise ValueError('no safe session available')
    return dict(version=1, ok=True, user=user, sessionId=chosen,
                # greetd 0.10 joins cmd with spaces for /bin/sh -c; retain each argv boundary.
                command=[shlex.quote(word) for word in catalog[chosen]['command']] if chosen else [],
                wallpaperRoot=str(paths.users / user) if user else '',
                multipleUsers=len(users) != 1, message=message)


def operate(request, paths=Paths(), now=None):
    if not isinstance(request, dict) or request.get('op') not in ('load', 'select', 'launch'):
        raise ValueError('operation')
    now = time.time() if now is None else now
    users, catalog = accounts(paths), session_catalog(paths)
    if not users:
        # ReGreet can ask PAM/NSS for homed/LDAP accounts that local enumeration
        # cannot resolve. Never strand those users at our local-only name step.
        raise ValueError('no locally resolvable account')
    # procfs exposes regular files with reported size zero; read_file still bounds reads.
    boot_id = read_file(paths.boot_id).strip()
    with locked_state(paths.state) as fd:
        try:
            raw = read_at(fd, 'state.json', private=True)
        except (FileNotFoundError, UnicodeError):
            # Invalid UTF-8 is corrupt payload, distinct from unsafe file metadata.
            state = empty_state()
        else:
            try:
                state = decode_state(raw)
            except (ValueError, TypeError, RecursionError):
                state = empty_state()
        seed_state(state, paths, users, catalog)
        op = request['op']
        if op == 'load':
            reconcile(state, now, boot_id)
            user = state['last_user'] if state['last_user'] in users else (next(iter(users)) if len(users) == 1 else '')
            result = selection(state, user, users, catalog, paths)
            write_state(fd, state)
            return result
        user = request.get('user', '')
        if user == '' and op == 'select':
            return selection(state, '', users, catalog, paths)
        if not valid_name(user) or user not in users:
            if op == 'launch':
                raise ValueError('launch user unavailable')
            return selection(state, '', users, catalog, paths, 'Unknown user')
        result = selection(state, user, users, catalog, paths)
        if op == 'launch':
            if request.get('session') != result['sessionId']:
                raise ValueError('session changed before launch')
            entry = state['users'].setdefault(user, dict(last_session=result['sessionId'], failed_session=''))
            if result['sessionId'] == 'niri.desktop' and entry['failed_session'] == 'niri-emaki.desktop':
                # Consume exactly one authenticated fallback login without
                # replacing the user's remembered Emaki session.
                entry['failed_session'] = ''
            else:
                entry['last_session'] = result['sessionId']
            state['last_user'] = user
            state['pending_launch'] = dict(user=user, session=result['sessionId'], launched_at=now, boot_id=boot_id)
            write_state(fd, state)
        return result


def main():
    try:
        line = sys.stdin.buffer.readline(4097)
        if len(line) > 4096:
            raise ValueError('request too large')
        result = operate(json.loads(line))
    except (OSError, ValueError, KeyError, TypeError, RecursionError, configparser.Error):
        # No request content or local path appears in logs or failure responses.
        result = dict(version=1, ok=False, error='state_unavailable')
    print(json.dumps(result, separators=(',', ':')), flush=True)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
