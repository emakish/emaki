#!/usr/bin/env python3
"""Disposable QEMU/KVM ONLY, root: capture both sides of one login handoff.

No authentication, service changes or signalling of desktop processes. Every
Wayland/niri command drops to the selected guest account and uses its own socket.
Window titles, arbitrary journal text and process environments are never saved.
The optional wallpaper fixture also drops UID before home/config/publication IO.
"""
import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import pwd
import re
import selectors
import stat
import subprocess
import sys
import tarfile
import threading
import time
import tomllib

BASE = Path('/run/c9-session-start')
TOKEN = re.compile(r'[0-9a-f]{32}\Z')
TEXT = re.compile(r'[^A-Za-z0-9_.: /-]')
STATE_BOOLEANS = ('compositorClaimed', 'shellClaimed', 'shellReady', 'coverFinished', 'dockRequired')
STATE_NUMBERS = ('createdMs', 'shellReadyMs', 'coverFinishedMs', 'coverDrainStartedMs')
OUTPUT_NAME = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z')
ANSI_SGR = re.compile(r'\x1b\[[0-9;]*m')
PUBLISHER = '/usr/share/emaki/shell/helpers/publish-wallpaper.py'
STATE_WALK_LIMIT = 4096
STATE_CANDIDATE_LIMIT = 32


def user_directory(path):
    """Open/create an absolute user path without following any symlink."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise RuntimeError('fixture requires an absolute path without traversal')
    descriptor = os.open('/', os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
            except FileNotFoundError:
                os.mkdir(part, 0o700, dir_fd=descriptor)
                child = os.open(part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        if os.fstat(descriptor).st_uid != os.getuid():
            raise RuntimeError('fixture directory is not owned by the current user')
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def fixture_read(directory, name, limit=131072):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_size > limit:
            raise RuntimeError('unsafe fixture input')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise RuntimeError('fixture input exceeds its bound')
        return data, stat.S_IMODE(info.st_mode)
    finally:
        os.close(descriptor)


def fixture_write(directory, name, data, mode=0o600):
    # Every target is local to the descriptor; never truncate an existing link.
    temporary = '.c9-write-' + name
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         mode, dir_fd=directory)
    try:
        with os.fdopen(descriptor, 'wb', closefd=False) as stream:
            stream.write(data)
        os.fchmod(descriptor, mode)
        os.rename(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def wallpaper_fixture(operation, token, home, publisher=PUBLISHER, publication=None):
    """Unprivileged disposable-user fixture; caller guards VM and drops UID first."""
    if os.getuid() == 0 or os.geteuid() == 0:
        raise RuntimeError('wallpaper fixture refuses root')
    config_root = Path(home) / '.config'
    config_dir = config_root / 'wpaperd'
    directory = user_directory(config_dir)
    backup_name, image_name = '.c9-backup-' + token + '.json', '.c9-wallpaper-' + token + '.png'
    image_path = config_dir / image_name
    try:
        if operation == 'prepare':
            try:
                data, mode = fixture_read(directory, 'config.toml', 65536)
                previous = dict(existed=True, data=base64.b64encode(data).decode(), mode=mode)
            except FileNotFoundError:
                previous = dict(existed=False)
            # O_EXCL reserves this backup before any config/image mutation. On
            # later failure restore can still recover the previous config.
            backup = os.open(backup_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=directory)
            with os.fdopen(backup, 'w') as stream:
                json.dump(previous, stream)
            from PIL import Image, ImageDraw
            import io
            image = Image.new('RGB', (1280, 720))
            draw = ImageDraw.Draw(image)
            for y in range(720):
                draw.line((0, y, 1279, y), fill=(22 + y // 9, 60 + y // 8, 112 + y // 10))
            for x in range(-160, 1280, 180):
                draw.polygon(((x, 0), (x + 80, 0), (x + 480, 720), (x + 400, 720)), fill='#a9c9c0')
            draw.ellipse((790, 130, 1110, 450), fill='#d39a6a')
            data = io.BytesIO()
            image.save(data, format='PNG')
            fixture_write(directory, image_name, data.getvalue())
            fixture_write(directory, 'config.toml', ('[default]\npath = ' + json.dumps(str(image_path)) + '\nmode = "stretch"\n').encode())
        else:
            try:
                data, _ = fixture_read(directory, backup_name)
            except FileNotFoundError:
                return dict(state='not-prepared')
            previous = json.loads(data)
            if previous['existed']:
                fixture_write(directory, 'config.toml', base64.b64decode(previous['data'], validate=True), previous['mode'] & 0o777)
            else:
                try:
                    os.unlink('config.toml', dir_fd=directory)
                except FileNotFoundError:
                    pass
        result = subprocess.run(['/usr/bin/python3', '-B', publisher],
                                env=dict(PATH='/usr/bin:/bin', HOME=str(home), XDG_CONFIG_HOME=str(config_root)),
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
        reply = json.loads(result.stdout)
        if result.returncode or reply.get('state') not in ('published', 'unpublished'):
            raise RuntimeError('wallpaper fixture publication failed; backup retained')
        if operation == 'prepare':
            published = Path(publication or ('/var/lib/emaki-greeter/users/' + pwd.getpwuid(os.getuid()).pw_name))
            published_dir = user_directory(published / 'wpaperd')
            try:
                published_config, _ = fixture_read(published_dir, 'config.toml')
            finally:
                os.close(published_dir)
            published_path = Path(tomllib.loads(published_config.decode())['default']['path'])
            if published_path.parent != published or not re.fullmatch(r'wallpaper-[0-9a-f]{64}\.image', published_path.name):
                raise RuntimeError('unexpected fixture publication path')
            published_dir = user_directory(published)
            try:
                copied, _ = fixture_read(published_dir, published_path.name, 32 * 1024 * 1024)
            finally:
                os.close(published_dir)
            if copied != data.getvalue():
                raise RuntimeError('published wallpaper differs from fixture')
            return dict(state='published', sha256=hashlib.sha256(copied).hexdigest(), width=1280, height=720)
        for name in (image_name, backup_name):
            try:
                os.unlink(name, dir_fd=directory)
            except FileNotFoundError:
                pass
        return dict(state='restored', publication=reply['state'])
    finally:
        os.close(directory)


def fixture_as_user(operation, token, user='arch'):
    """Root dispatch performs no user-path IO; the child owns all fixture work."""
    account = pwd.getpwnam(user)
    result = subprocess.run(['/usr/bin/python3', '-I', '-B', str(Path(__file__).resolve()), '--token', token,
                             '--operation', 'wallpaper-user-' + operation, '--user', user],
                            env=dict(PATH='/usr/bin:/bin', HOME=account.pw_dir), user=account.pw_uid,
                            group=account.pw_gid, extra_groups=[], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=25)
    if result.returncode:
        raise RuntimeError('unprivileged wallpaper fixture failed; backup retained for restore')
    return json.loads(result.stdout)


def guard():
    if os.getuid() != 0 or os.geteuid() != 0:
        raise RuntimeError('capture requires root inside the disposable guest')
    result = subprocess.run(['systemd-detect-virt', '--vm'], capture_output=True, text=True, timeout=3)
    if result.returncode or result.stdout.strip() not in ('qemu', 'kvm'):
        raise RuntimeError('capture requires disposable QEMU/KVM')


def private_directory(path, create=False):
    if create:
        path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise RuntimeError('capture directory must be root-owned and private')


def safe_text(value):
    return TEXT.sub('?', value[:128]) if isinstance(value, str) else None


def sanitized_reply(kind, data):
    if kind == 'layers' and isinstance(data, list):
        # Niri exposes no layer IDs. Do not invent stable IDs from list ordering.
        return [{key: safe_text(item.get(key)) for key in ('namespace', 'output', 'layer', 'keyboard_interactivity')}
                for item in data[:128] if isinstance(item, dict)]
    if kind == 'windows' and isinstance(data, list):
        return [dict(id=item.get('id') if type(item.get('id')) is int else None,
                     pid=item.get('pid') if type(item.get('pid')) is int else None,
                     app_id=safe_text(item.get('app_id')),
                     workspace_id=item.get('workspace_id') if type(item.get('workspace_id')) is int else None)
                for item in data[:128] if isinstance(item, dict)]
    return None


def environment(account, wayland=None, niri=None):
    runtime = f'/run/user/{account.pw_uid}'
    result = dict(PATH='/usr/bin:/bin', XDG_RUNTIME_DIR=runtime,
                  DBUS_SESSION_BUS_ADDRESS='unix:path=' + runtime + '/bus')
    if wayland:
        result['WAYLAND_DISPLAY'] = wayland.name
    if niri:
        result['NIRI_SOCKET'] = str(niri)
    return result


def as_user(account, argv, env, **kwargs):
    return subprocess.run(argv, env=env, user=account.pw_uid, group=account.pw_gid,
                          extra_groups=[], stdin=subprocess.DEVNULL, timeout=.75, **kwargs)


def find_socket(account, kind):
    runtime = Path(f'/run/user/{account.pw_uid}')
    try:
        info = runtime.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != account.pw_uid:
            return None
        candidates = []
        pattern = r'wayland-[0-9]+' if kind == 'wayland' else r'niri\..+\.sock'
        for path in runtime.iterdir():
            if re.fullmatch(pattern, path.name):
                info = path.lstat()
                if stat.S_ISSOCK(info.st_mode) and info.st_uid == account.pw_uid:
                    candidates.append(path)
        return candidates[0] if len(candidates) == 1 else None
    except OSError:
        return None


def startup_states(account, since_ms, *, diagnostics=None):
    """Select recent owned state, independent of old tokens/directory order."""
    scan = dict(walked=0, candidates=0, selected=0, walkLimit=STATE_WALK_LIMIT,
                candidateLimit=STATE_CANDIDATE_LIMIT, walkTruncated=False, candidatesTruncated=False)
    if diagnostics is not None:
        diagnostics.update(scan)
        scan = diagnostics
    root = Path(f'/run/user/{account.pw_uid}/emaki-session-start')
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return []
    result = []
    try:
        info = os.fstat(directory)
        if info.st_uid != account.pw_uid or info.st_mode & 0o022:
            return []
        candidates = []
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                if index >= STATE_WALK_LIMIT:
                    scan['walkTruncated'] = True
                    break
                scan['walked'] += 1
                token = entry.name.removesuffix('.json')
                if not entry.name.endswith('.json') or not TOKEN.fullmatch(token):
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != account.pw_uid
                            or info.st_nlink != 1 or info.st_size > 65536
                            or info.st_mtime_ns < (since_ms - 5000) * 1e6):
                        continue
                    candidates.append((info.st_mtime_ns, entry.name, token))
                except OSError:
                    continue
        scan['candidates'] = len(candidates)
        scan['candidatesTruncated'] = len(candidates) > STATE_CANDIDATE_LIMIT
        # Atomic state replacement changes the inode normally. lstat is only
        # selection evidence: every selected file is opened no-follow and its
        # current descriptor is validated again before a bounded read.
        for _, name, token in sorted(candidates, reverse=True)[:STATE_CANDIDATE_LIMIT]:
            scan['selected'] += 1
            try:
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
                try:
                    info = os.fstat(descriptor)
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != account.pw_uid
                            or info.st_nlink != 1 or info.st_size > 65536):
                        continue
                    data = json.loads(os.read(descriptor, 65537))
                finally:
                    os.close(descriptor)
                if not isinstance(data, dict) or data.get('version') != 1 or data.get('token') != token:
                    continue
                if (type(data.get('createdMs')) not in (int, float) or not math.isfinite(data['createdMs'])
                        or data['createdMs'] < since_ms - 5000):
                    continue
                clean = dict(version=1, token=token)
                for key in STATE_BOOLEANS:
                    if type(data.get(key)) is bool:
                        clean[key] = data[key]
                for key in STATE_NUMBERS:
                    if type(data.get(key)) in (int, float) and math.isfinite(data[key]):
                        clean[key] = data[key]
                if data.get('coverDrainReason') in ('ready', 'deadline'):
                    clean['coverDrainReason'] = data['coverDrainReason']
                result.append(clean)
            except (OSError, ValueError, TypeError, RecursionError):
                continue
    finally:
        os.close(directory)
    return result


def output_map(value):
    """Only bounded connector names and the two-frame counters survive."""
    if not isinstance(value, dict) or len(value) > 32:
        return {}
    return {name: item for name, item in value.items()
            if isinstance(name, str) and OUTPUT_NAME.fullmatch(name)
            and type(item) is int and 0 <= item <= 2}


def cover_event(line):
    """Extract only the production marker schema; never retain raw journal text."""
    try:
        if len(line) > 262144:
            return None
        record = json.loads(line)
        message = record.get('MESSAGE', '')
        # QS enables ANSI colours by default. journalctl --output=json encodes
        # non-printable fields (including ESC) as uint8 arrays, not strings.
        if (isinstance(message, list) and len(message) <= 16384
                and all(type(byte) is int and 0 <= byte <= 255 for byte in message)):
            message = bytes(message).decode('utf-8')
        if isinstance(message, str):
            message = ANSI_SGR.sub('', message)
        if not isinstance(message, str) or 'EMAKI_SESSION_COVER ' not in message:
            return None
        value = json.loads(message.split('EMAKI_SESSION_COVER ', 1)[1])
        if value.get('phase') not in ('loaded', 'drain-ready', 'drain-cap', 'finished',
                                     'greeter-frozen', 'skipped-deadline', 'drain-deadline'):
            return None
        clean = dict(phase=value['phase'])
        if type(value.get('atMs')) in (int, float) and math.isfinite(value['atMs']):
            clean['atMs'] = value['atMs']
        if type(value.get('handoffAtMs')) in (int, float) and math.isfinite(value['handoffAtMs']):
            clean['handoffAtMs'] = value['handoffAtMs']
        if isinstance(value.get('frames'), dict):
            clean['frames'] = output_map(value['frames'])
        observation = value.get('observation', {})
        if isinstance(observation, dict):
            clean['observation'] = {key: observation[key] for key in
                                    ('ready', 'shellReady', 'coverMapped', 'barMapped', 'dockMapped', 'dockRequired', 'wallpaperMapped',
                                     'autostartSettled', 'observerAvailable', 'coverFinished')
                                    if type(observation.get(key)) is bool}
            if type(observation.get('windowCount')) is int and 0 <= observation['windowCount'] <= 4096:
                clean['observation']['windowCount'] = observation['windowCount']
        return clean
    except (ValueError, TypeError, AttributeError, RecursionError):
        return None


def journal_cover_events(start_ns, end_ns, creation_times):
    """Match the sampled creation timestamp; the picture token is never journaled."""
    try:
        result = subprocess.run(['/usr/bin/journalctl', '-b', '--since=@' + str(start_ns / 1e9),
                                 '--until=@' + str(end_ns / 1e9), '--output=json', '--all', '--no-pager',
                                 '--grep=EMAKI_SESSION_COVER ', '-n', '2000'],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=3)
        if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
            return [], 'unavailable'
        events = []
        for line in result.stdout.splitlines():
            event = cover_event(line)
            if event and event.get('handoffAtMs') in creation_times:
                events.append(event)
        return events, 'filtered' if events else 'filtered-empty'
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        return [], 'unavailable'


class Capture:
    def __init__(self, output, interval, seconds, user='arch'):
        self.user = user
        self.output, self.interval, self.seconds = output, interval, seconds
        self.started_ns = time.time_ns()
        self.started_mono = time.monotonic()
        self.stop = threading.Event()
        self.greeter_ready = threading.Event()
        self.lock = threading.Lock()
        self.first = {}
        self.counts = {}
        self.last_frame_end = {}
        self.startup_tokens = set()
        self.startup_creation_times = set()

    def record(self, filename, row):
        with self.lock:
            with (self.output / filename).open('a') as stream:
                stream.write(json.dumps(row, separators=(',', ':')) + '\n')

    def frames(self, side, account):
        index = 0
        last_missing = False
        while not self.stop.is_set():
            wayland = find_socket(account, 'wayland')
            if wayland is None:
                if not last_missing:
                    self.record('frames.jsonl', dict(side=side, state='no-socket', epochNs=time.time_ns()))
                last_missing = True
                self.stop.wait(.02)
                continue
            last_missing = False
            with self.lock:
                self.first.setdefault(side, dict(epochNs=time.time_ns(), monotonic=time.monotonic()))
            started = time.time_ns()
            filename = f'frames/{side}-{index:05d}.png'
            path = self.output / filename
            row = dict(side=side, index=index, startNs=started, state='unavailable')
            try:
                with path.open('xb') as stream:
                    result = as_user(account, ['/usr/bin/grim', '-t', 'png', '-l', '1', '-'],
                                     environment(account, wayland), stdout=stream, stderr=subprocess.DEVNULL)
                if result.returncode == 0 and 0 < path.stat().st_size <= 32 * 1024 * 1024:
                    row.update(state='frame', file=filename)
                    if side == 'greeter':
                        self.greeter_ready.set()
                else:
                    path.unlink(missing_ok=True)
                    row['reason'] = 'grim-failed-or-size-limit'
            except (OSError, subprocess.TimeoutExpired):
                path.unlink(missing_ok=True)
                row['reason'] = 'grim-timeout-or-io'
            row['endNs'] = time.time_ns()
            self.last_frame_end[side] = row['endNs']
            self.record('frames.jsonl', row)
            index += 1
            self.counts[side] = index
            self.stop.wait(max(0, self.interval - (time.time_ns() - started) / 1e9))

    def metadata(self, side, account):
        while not self.stop.is_set():
            row = dict(side=side, epochNs=time.time_ns(), layers=None, windows=None)
            niri = find_socket(account, 'niri')
            if niri:
                for kind in ('layers', 'windows'):
                    try:
                        result = as_user(account, ['/usr/bin/niri', 'msg', '-j', kind],
                                         environment(account, niri=niri), capture_output=True, text=True)
                        if result.returncode == 0 and len(result.stdout) <= 1024 * 1024:
                            row[kind] = sanitized_reply(kind, json.loads(result.stdout))
                    except (OSError, ValueError, UnicodeError, subprocess.TimeoutExpired):
                        pass
            if side == 'user':
                row['startupStateScan'] = {}
                row['startup'] = startup_states(account, self.started_ns / 1e6, diagnostics=row['startupStateScan'])
                with self.lock:
                    self.startup_tokens.update(state['token'] for state in row['startup'])
                    self.startup_creation_times.update(state['createdMs'] for state in row['startup'] if 'createdMs' in state)
                try:
                    result = as_user(account, ['/usr/bin/systemctl', '--user', 'show', 'emaki-shell.service',
                                              '-p', 'ActiveState', '-p', 'SubState', '-p', 'MainPID'],
                                     environment(account), capture_output=True, text=True)
                    row['service'] = {key: safe_text(value) for line in result.stdout.splitlines()
                                      if '=' in line for key, value in [line.split('=', 1)]
                                      if key in ('ActiveState', 'SubState', 'MainPID')}
                except (OSError, UnicodeError, subprocess.TimeoutExpired):
                    row['service'] = None
            row['endNs'] = time.time_ns()
            self.record('metadata.jsonl', row)
            self.stop.wait(.10)

    def config_events(self, side, account):
        while not self.stop.is_set():
            niri = find_socket(account, 'niri')
            if niri:
                break
            self.stop.wait(.02)
        else:
            return
        process = None
        try:
            process = subprocess.Popen(['/usr/bin/niri', 'msg', '-j', 'event-stream'],
                                       env=environment(account, niri=niri), user=account.pw_uid,
                                       group=account.pw_gid, extra_groups=[], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                pending = b''
                while not self.stop.is_set():
                    if not selector.select(.10):
                        continue
                    part = os.read(process.stdout.fileno(), 65536)
                    if not part:
                        break
                    pending += part
                    if len(pending) > 1024 * 1024:
                        break
                    while b'\n' in pending:
                        line, pending = pending.split(b'\n', 1)
                        try:
                            event = json.loads(line).get('ConfigLoaded')
                            if isinstance(event, dict) and type(event.get('failed')) is bool:
                                self.record('config-events.jsonl', dict(side=side, epochNs=time.time_ns(), failed=event['failed']))
                        except (ValueError, AttributeError):
                            pass
        except OSError:
            pass
        finally:
            if process:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)

    def run(self):
        threads = []
        for side, username in (('greeter', 'greeter'), ('user', self.user)):
            account = pwd.getpwnam(username)
            for operation in (self.frames, self.metadata, self.config_events):
                thread = threading.Thread(target=operation, args=(side, account), daemon=True)
                threads.append(thread)
                thread.start()
        ready = self.greeter_ready.wait(5)
        user_before_auth = 'user' in self.first
        print(json.dumps(dict(event='ready', greeterFrameReady=ready, userSocketBeforeAuth=user_before_auth,
                              guestEpochNs=time.time_ns())), flush=True)
        try:
            if ready and not user_before_auth:
                while time.monotonic() - self.started_mono < 35:
                    first = self.first.get('user')
                    if first and time.monotonic() >= first['monotonic'] + self.seconds:
                        break
                    self.stop.wait(.02)
        finally:
            capture_end_ns = time.time_ns()
            self.stop.set()
            for thread in threads:
                thread.join(timeout=3)
            capture_end_ns = max([capture_end_ns, *self.last_frame_end.values()])
            with self.lock:
                tokens = set(self.startup_tokens)
                creation_times = set(self.startup_creation_times)
            # The compositor cover is intentionally absent from grim. Export its
            # password-free source for comparison against physical host frames.
            if len(tokens) == 1:
                token = next(iter(tokens))
                source = Path('/var/lib/emaki-greeter/handoff') / (token + '.png')
                try:
                    descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                    with os.fdopen(descriptor, 'rb') as stream:
                        info = os.fstat(stream.fileno())
                        if (stat.S_ISREG(info.st_mode) and info.st_uid == pwd.getpwnam('greeter').pw_uid
                                and info.st_nlink == 1 and 24 <= info.st_size <= 32 * 1024 * 1024):
                            (self.output / 'handoff.png').write_bytes(stream.read(32 * 1024 * 1024 + 1))
                except OSError:
                    pass
            cover_events, journal_status = journal_cover_events(self.started_ns, capture_end_ns, creation_times)
            for event in cover_events:
                self.record('cover-events.jsonl', event)
            manifest = dict(version=1, startNs=self.started_ns, endNs=capture_end_ns, finishedNs=time.time_ns(), firstSockets=self.first,
                            attempts=self.counts, greeterFrameReady=ready,
                            socketPollMs=20, requestedIntervalMs=self.interval * 1000,
                            secondsAfterUser=self.seconds, frameSource='guest-grim-combined-outputs',
                            layerIdsAvailable=False, coverJournal=journal_status, coverEventCount=len(cover_events),
                            unjoinedThreads=sum(thread.is_alive() for thread in threads))
            (self.output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        print(json.dumps(dict(event='finished', firstUserSocket=bool(self.first.get('user')))), flush=True)
        return 0 if ready and not user_before_auth and 'user' in self.first else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token', required=True)
    parser.add_argument('--user', default='arch')
    parser.add_argument('--operation', choices=('capture', 'export', 'wallpaper-prepare', 'wallpaper-restore',
                                                'wallpaper-user-prepare', 'wallpaper-user-restore'), default='capture')
    parser.add_argument('--interval', type=float, default=.08)
    parser.add_argument('--seconds-after-user', type=float, default=10)
    args = parser.parse_args()
    user = args.user
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*', user):
        parser.error('invalid guest account')
    if not TOKEN.fullmatch(args.token) or not .04 <= args.interval <= .2 or not 10 <= args.seconds_after_user <= 20:
        parser.error('invalid token or capture bounds')
    if args.operation.startswith('wallpaper-user-'):
        account = pwd.getpwnam(user)
        if os.getuid() != account.pw_uid or os.geteuid() != account.pw_uid or account.pw_uid == 0:
            raise RuntimeError('wallpaper fixture requires the unprivileged target account in the disposable guest')
        vm = subprocess.run(['systemd-detect-virt', '--vm'], capture_output=True, text=True, timeout=3)
        if vm.returncode or vm.stdout.strip() not in ('qemu', 'kvm'):
            raise RuntimeError('wallpaper fixture requires disposable QEMU/KVM')
        print(json.dumps(wallpaper_fixture(args.operation.removeprefix('wallpaper-user-'), args.token, account.pw_dir)))
        return 0
    guard()  # Before directory creation, account lookup, sockets or any capture.
    if args.operation.startswith('wallpaper-'):
        print(json.dumps(fixture_as_user(args.operation.removeprefix('wallpaper-'), args.token, user)))
        return 0
    private_directory(BASE, create=True)
    output = BASE / args.token
    if args.operation == 'export':
        private_directory(output)
        with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as archive:
            for path in sorted(output.rglob('*')):
                info = path.lstat()
                if stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_size <= 32 * 1024 * 1024:
                    archive.add(path, arcname=str(path.relative_to(output)), recursive=False)
        return 0
    output.mkdir(mode=0o700)  # Existing token is refused, never overwritten.
    (output / 'frames').mkdir(mode=0o700)
    return Capture(output, args.interval, args.seconds_after_user, user).run()


if __name__ == '__main__':
    raise SystemExit(main())
