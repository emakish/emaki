#!/usr/bin/env python3
"""One-shot login decoration and bounded readiness observation, never authentication.

Only the session user's private runtime directory is written. A stale/missing marker,
helper failure or cover crash leaves the ordinary desktop usable. No password, window
title, home configuration or autostart command is stored or forwarded here.
"""
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time

TOKEN = re.compile(r'[0-9a-f]{32}\Z')
ACCOUNT = re.compile(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}\$?\Z')
MAX_STATE = 8192
MARKER_AGE_MS = 30000
HANDOFF_ROOT = Path("/var/lib/emaki-greeter/handoff")


def marker_from(environment, now_ms=None):
    raw = environment.get('EMAKI_LOGIN_HANDOFF', '')
    if len(raw) > 1024:
        return None
    try:
        value = json.loads(raw)
        now_ms = time.time() * 1000 if now_ms is None else now_ms
        if (not isinstance(value, dict) or type(value.get('v')) is not int or value.get('v') != 1
                or not isinstance(value.get('token'), str) or not TOKEN.fullmatch(value['token'])
                or value.get('idleMode') not in ('lake', 'letters', 'breath', 'wind')
                or type(value.get('createdMs')) not in (int, float)
                or not math.isfinite(value['createdMs'])
                or not -1000 <= now_ms - value['createdMs'] <= MARKER_AGE_MS
                or type(value.get('clock')) not in (int, float)
                or not math.isfinite(value['clock']) or value['clock'] < 0
                or type(value.get('introStart')) not in (int, float)
                or not math.isfinite(value['introStart']) or not 0 <= value['introStart'] <= value['clock']):
            return None
        return {key: value[key] for key in ('v', 'token', 'createdMs', 'idleMode', 'clock', 'introStart')}
    except (ValueError, TypeError, OverflowError, RecursionError):
        return None


@contextmanager
def runtime_directory(environment, create=False):
    path = Path(environment.get('XDG_RUNTIME_DIR', ''))
    if not path.is_absolute() or '..' in path.parts or os.geteuid() == 0:
        raise ValueError('private session runtime required')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for name in path.parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('unsafe runtime directory')
        if create:
            try:
                os.mkdir('emaki-session-start', 0o700, dir_fd=fd)
            except FileExistsError:
                pass
        child = os.open('emaki-session-start', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=fd)
        os.close(fd)
        fd = child
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('unsafe startup directory')
        yield fd
    finally:
        os.close(fd)


def read_state(fd, token):
    descriptor = os.open(token + '.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=fd)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > MAX_STATE):
            raise ValueError('unsafe startup state')
        raw = stream.read(MAX_STATE + 1)
        if len(raw) > MAX_STATE:
            raise ValueError('startup state too large')
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get('version') != 1 or value.get('token') != token:
        raise ValueError('startup state schema')
    return value


def write_state(fd, token, value):
    raw = json.dumps(value, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_STATE:
        raise ValueError('startup state too large')
    name = '.new-' + secrets.token_hex(12)
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=fd)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, token + '.json', src_dir_fd=fd, dst_dir_fd=fd)
    finally:
        try:
            os.unlink(name, dir_fd=fd)
        except FileNotFoundError:
            pass


def lock_state(fd):
    # A same-user stuck helper must not block ordinary shell startup. Normally
    # the simultaneous cover/shell claims take only one atomic replacement each.
    deadline = time.monotonic() + .2
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise TimeoutError('startup state busy')
            time.sleep(.01)


def picture(marker):
    """Only a regular, recent, greeter-owned PNG in the fixed publishing root."""
    import pwd
    root = os.open(HANDOFF_ROOT, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        greeter_uid = pwd.getpwnam('greeter').pw_uid
        info = os.fstat(root)
        if info.st_uid != greeter_uid or stat.S_IMODE(info.st_mode) != 0o711:
            raise ValueError('unsafe handoff root')
        from PIL import Image
        size = None
        for suffix in ('', '-plate'):
            name = marker['token'] + suffix + '.png'
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=root)
            with os.fdopen(descriptor, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != greeter_uid or info.st_nlink != 1
                        or stat.S_IMODE(info.st_mode) != 0o644 or not 24 <= info.st_size <= 64 * 1024 * 1024
                        or not -1 <= time.time() - info.st_mtime <= 30):
                    raise ValueError('invalid handoff picture')
                with Image.open(stream) as image:
                    if image.format != 'PNG' or image.width * image.height > 48_000_000:
                        raise ValueError('invalid handoff PNG')
                    image.load()  # Header/signature alone does not prove decodability.
                    if size is not None and image.size != size:
                        raise ValueError('handoff plate size mismatch')
                    size = image.size
        return str(HANDOFF_ROOT / (marker['token'] + '.png'))
    finally:
        os.close(root)


def prepare(environment):
    """Consume the compositor claim before exec; never replay on service restart."""
    marker = marker_from(environment)
    if marker is None:
        return ''
    path = picture(marker)
    with runtime_directory(environment, create=True) as fd:
        lock_state(fd)
        try:
            read_state(fd, marker['token'])
            return ''
        except FileNotFoundError:
            pass
        write_state(fd, marker['token'], dict(version=1, token=marker['token'],
                    createdMs=marker['createdMs'], image=path, visual=marker,
                    compositorClaimed=True, shellClaimed=False, shellReady=False, coverFinished=False))
    return path


def status(token, environment):
    if not TOKEN.fullmatch(token):
        raise ValueError('invalid token')
    with runtime_directory(environment) as fd:
        return read_state(fd, token)


def update(token, environment, **fields):
    with runtime_directory(environment) as fd:
        lock_state(fd)
        value = read_state(fd, token)
        value.update(fields)
        write_state(fd, token, value)


def claim_shell(environment):
    marker = marker_from(environment)
    if marker is None:
        return ''
    with runtime_directory(environment) as fd:
        lock_state(fd)
        value = read_state(fd, marker['token'])
        if (not value.get('compositorClaimed') or value.get('shellClaimed')
                or value.get('coverFinished') or value['createdMs'] != marker['createdMs']):
            return ''
        value['shellClaimed'] = True
        write_state(fd, marker['token'], value)
    return marker['token']


def cover_context(token, environment):
    value = status(token, environment)
    queried_mono, queried_ms = time.monotonic(), time.time()*1000
    # Bound a stuck IPC peer; charge successful reply transit against expiry.
    response = subprocess.run(['/usr/bin/niri-emaki', 'msg', '--json', 'emaki-startup-cover'],
                              env=environment, capture_output=True, check=True, timeout=2)
    state = json.loads(response.stdout)
    if not state.get('active') or state.get('remaining_ms', 0) <= 250 or value.get('coverFinished'):
        return dict(version=1, active=False)
    return dict(version=1, active=True, image=value['image'],
                remainingMs=state['remaining_ms'] - 250 - (time.monotonic()-queried_mono)*1000,
                expiresAtMs=queried_ms + state['remaining_ms'] - 250,
                outputs=state.get('outputs', []),
                visual=value['visual'])


def niri_snapshot(environment):
    """Read only the current session socket; retain no titles or app metadata."""
    if not environment.get('NIRI_SOCKET'):
        return None
    observations = []
    for request in ('layers', 'windows'):
        response = subprocess.run(['/usr/bin/niri-emaki', 'msg', '--json', request], env=environment,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=.25, check=False)
        if response.returncode or len(response.stdout) > 1024 * 1024:
            return None
        value = json.loads(response.stdout)
        if not isinstance(value, list) or len(value) > 4096:
            return None
        observations.append(value)
    layers, windows = observations
    namespaces = {(item.get('output'), item.get('namespace')) for item in layers if isinstance(item, dict)}
    cover_outputs = {output for output, name in namespaces if name == 'emaki-session-cover'}
    return dict(
        coverMapped=bool(cover_outputs),
        wallpaperMapped=bool(cover_outputs) and all((output, 'wpaperd-' + output) in namespaces for output in cover_outputs),
        barMapped=bool(cover_outputs) and all((output, 'emaki-test-bar') in namespaces for output in cover_outputs),
        dockMapped=bool(cover_outputs) and all((output, 'emaki-test-dock') in namespaces for output in cover_outputs),
        windowIds=sorted(item['id'] for item in windows if isinstance(item, dict) and type(item.get('id')) is int),
        windowSizes=sorted((item['id'], item.get('layout', {}).get('window_size'))
                           for item in windows if isinstance(item, dict) and type(item.get('id')) is int))


class Readiness:
    def __init__(self, now):
        self.started = now
        self.windows = None
        self.changed = now

    def sample(self, snapshot, shell_ready, now, dock_required=True):
        if snapshot is None:
            self.windows = None
            self.changed = now
            return dict(ready=False, shellReady=bool(shell_ready), observerAvailable=False)
        windows = (snapshot['windowIds'], snapshot.get('windowSizes', []))
        if windows != self.windows:
            self.windows = windows
            self.changed = now
        # Require a full second without observed opens, closes or resizes.
        # The cover expiry still bounds waiting for slow user autostarts.
        settled = now - self.changed >= 1.0
        return dict(ready=bool(shell_ready and snapshot['coverMapped'] and snapshot['barMapped']
                               and (not dock_required or snapshot['dockMapped'])
                               and snapshot['wallpaperMapped'] and settled),
                    shellReady=bool(shell_ready), observerAvailable=True,
                    coverMapped=snapshot['coverMapped'], barMapped=snapshot['barMapped'],
                    dockMapped=snapshot['dockMapped'], dockRequired=bool(dock_required),
                    wallpaperMapped=snapshot['wallpaperMapped'], autostartSettled=settled,
                    windowCount=len(snapshot['windowIds']))


def observe(token, environment, expires_at_ms):
    readiness = Readiness(time.monotonic())
    previous = None
    while time.time()*1000 < expires_at_ms:
        value = status(token, environment)
        try:
            snapshot = niri_snapshot(environment)
        except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
            snapshot = None
        result = readiness.sample(snapshot, value.get('shellReady') is True, time.monotonic(),
                                  value.get('dockRequired', True))
        result.update(version=1, coverFinished=value.get('coverFinished') is True)
        if result != previous:
            print(json.dumps(result, separators=(',', ':')), flush=True)
            previous = result
        if result['coverFinished']:
            return
        time.sleep(.05)


def main():
    args = sys.argv[1:]
    environment = dict(os.environ)
    try:
        if args == ['prepare']:
            print(prepare(environment))
        elif args == ['shell-token']:
            print(claim_shell(environment))
        elif len(args) >= 2 and TOKEN.fullmatch(args[1]):
            operation, token = args[:2]
            if operation == 'context':
                print(json.dumps(cover_context(token, environment)), flush=True)
            elif operation == 'observe' and len(args) == 3 and math.isfinite(float(args[2])):
                observe(token, environment, float(args[2]))
            elif operation == 'shell-ready' and len(args) == 3 and args[2] in ('dock', 'no-dock'):
                update(token, environment, shellReady=True, shellReadyMs=time.time()*1000,
                       dockRequired=args[2] == 'dock')
            elif operation == 'drain' and len(args) == 3 and args[2] in ('ready', 'deadline'):
                update(token, environment, coverDrainStartedMs=time.time()*1000, coverDrainReason=args[2])
            elif operation == 'finished':
                update(token, environment, coverFinished=True, coverFinishedMs=time.time()*1000)
            else:
                return 1
        else:
            return 1
    except (OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.SubprocessError):
        return 1  # All callers fail open; this helper is decoration only.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
