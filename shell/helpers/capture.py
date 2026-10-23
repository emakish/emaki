#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Selected compositor captures; the recording lifetime belongs to the shell's pipe."""
import argparse
import ctypes
import errno
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time

OWNER_FD = None


class CaptureError(Exception):
    pass


def emit(event, **values):
    print(json.dumps(dict(event=event, **values), separators=(',', ':')), flush=True)


def child_environment():
    env = dict(os.environ)
    # The inherited private connection belongs exclusively to the shell.
    env.pop('WAYLAND_SOCKET', None)
    return env


def command(argv, *, data=None, timeout=8):
    try:
        result = subprocess.run(argv, input=data, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=timeout,
                                env=child_environment(), close_fds=True)
    except FileNotFoundError as error:
        raise CaptureError('dependency_missing') from error
    except subprocess.TimeoutExpired as error:
        raise CaptureError('capture_timeout') from error
    if result.returncode:
        raise CaptureError('capture_failed')
    return result.stdout


def niri(*args):
    try:
        return json.loads(command(['niri', 'msg', '--json', *args]))
    except (ValueError, TypeError) as error:
        raise CaptureError('compositor_unavailable') from error


def supported():
    version = niri('version')
    # The CLI may be newer than the running compositor; only the latter protects pixels.
    running = version.get('compositor', '') if isinstance(version, dict) else ''
    match = re.fullmatch(r'26\.04 \(v26\.04\+emaki\.([1-9][0-9]*)\)', running)
    if not match or int(match[1]) < 15:
        raise CaptureError('compositor_unsupported')


def locked():
    spec = importlib.util.spec_from_file_location('capture_lock_gate',
                                                Path(__file__).with_name('polkit-lock-state.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.blocked()


def guard():
    if locked():
        raise CaptureError('session_locked')


class LockWatch:
    """Observe marker writes before sampling; retain a cutoff even after unlock.

    A missing timestamp (short-lived marker, replaced directory or queue overflow)
    cuts at the last known safe observation. Event-loop delays never move that
    boundary forward to the time the stop request happens to be processed.
    """
    MASK = 0x00000fcc  # attrib, close-write, moves, create/delete and self changes

    def __init__(self):
        self.fd = self.clock_fd = -1
        self.checkpoint_ns = time.time_ns()
        self.stopped_at = None
        base = runtime_directory()
        display = Path(os.environ.get('WAYLAND_DISPLAY', 'wayland-0'))
        display = display if display.is_absolute() else base / display
        namespace = hashlib.sha256(os.fsencode(str(display.resolve()))).hexdigest()[:16]
        self.directory = base / ('emaki-lock-' + namespace)
        self.marker = self.directory / 'authentication-blocked'
        self.interests = {}
        for path, name in ((base.parent, base.name), (base, self.directory.name),
                           (display.parent, display.name), (self.directory, self.marker.name)):
            self.interests.setdefault(path, set()).add(os.fsencode(name))
        self.watches = {}
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.inotify_init1.argtypes = [ctypes.c_int]
        self.libc.inotify_init1.restype = ctypes.c_int
        self.libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        self.libc.inotify_add_watch.restype = ctypes.c_int
        self.fd = self.libc.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
        if self.fd < 0:
            raise CaptureError('runtime_unavailable')
        try:
            self.clock_fd = os.timerfd_create(time.CLOCK_REALTIME,
                                              flags=os.TFD_CLOEXEC | os.TFD_NONBLOCK)
            os.timerfd_settime_ns(self.clock_fd,
                                 flags=os.TFD_TIMER_ABSTIME | os.TFD_TIMER_CANCEL_ON_SET,
                                 initial=time.time_ns() + 7 * 24 * 3600 * 1_000_000_000)
            self.arm()
            guard()
        except Exception:
            self.close()
            raise

    def arm(self):
        for path, names in self.interests.items():
            watch = self.libc.inotify_add_watch(self.fd, os.fsencode(path), self.MASK)
            if watch < 0:
                if ctypes.get_errno() != errno.ENOENT:
                    raise CaptureError('runtime_unavailable')
            else:
                self.watches[watch] = names

    def cutoff(self):
        # Cover clock steps before the recorder has armed its own clock guard.
        try:
            os.read(self.clock_fd, 8)
        except BlockingIOError:
            pass
        except OSError as error:
            raise CaptureError('recorder_failed') from error
        else:
            raise CaptureError('recorder_failed')
        if self.stopped_at is not None:
            return self.stopped_at
        checkpoint = time.time_ns()
        changed = False
        uncertain = False
        for _ in range(16):
            try:
                events = os.read(self.fd, 65536)
            except BlockingIOError:
                break
            offset = 0
            while offset < len(events):
                watch, mask, _, length = struct.unpack_from('iIII', events, offset)
                name = events[offset + 16:offset + 16 + length].rstrip(b'\0')
                offset += 16 + length
                if not name or name in self.watches.get(watch, set()):
                    changed = True
                    uncertain |= bool(mask & 0x00004e40)  # deletion, move away, overflow
        else:
            changed = uncertain = True
        if changed:
            self.arm()
            cutoff = self.checkpoint_ns
            try:
                info = self.marker.lstat()
                if (not uncertain and stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                        and not info.st_mode & 0o077 and info.st_mtime_ns <= checkpoint):
                    # A restarted lock supervisor can truncate the same marker.
                    # Its immutable birth time retains the first write boundary.
                    birth = self.marker_birth_ns()
                    if birth is not None:
                        cutoff = min(info.st_mtime_ns, birth)
            except OSError:
                pass
            self.stopped_at = cutoff
        else:
            self.checkpoint_ns = checkpoint
        return self.stopped_at

    def marker_birth_ns(self):
        statx = getattr(self.libc, 'statx', None)
        if statx is None:
            return None
        statx.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                          ctypes.c_uint, ctypes.c_void_p]
        statx.restype = ctypes.c_int
        result = ctypes.create_string_buffer(256)
        # Linux statx: AT_FDCWD, AT_SYMLINK_NOFOLLOW and STATX_BTIME.
        if statx(-100, os.fsencode(self.marker), 0x100, 0x800, result) != 0:
            return None
        if not struct.unpack_from('I', result, 0)[0] & 0x800:
            return None
        seconds, nanoseconds = struct.unpack_from('qI', result, 80)
        return seconds * 1_000_000_000 + nanoseconds

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
        if self.clock_fd >= 0:
            os.close(self.clock_fd)
            self.clock_fd = -1


def inventory():
    supported()
    guard()
    outputs = []
    raw = niri('outputs')
    windows = niri('windows')
    if not isinstance(raw, dict) or not isinstance(windows, list):
        raise CaptureError('compositor_unavailable')
    for name, output in raw.items():
        logical = output.get('logical')
        if not isinstance(logical, dict) or output.get('current_mode') is None:
            continue
        outputs.append(dict(name=name, **{key: logical[key] for key in
                            ('x', 'y', 'width', 'height', 'scale', 'transform')}))
    return dict(outputs=outputs, windows=[{key: window.get(key) for key in
                ('id', 'title', 'app_id', 'workspace_id', 'is_focused')} for window in windows])


def selection(request, available):
    if not isinstance(request, dict) or request.get('target') not in ('screen', 'window', 'area'):
        raise CaptureError('selection_invalid')
    target = request['target']
    if target == 'window':
        ident = request.get('window_id')
        if type(ident) is not int or not 0 <= ident < 2 ** 64:
            raise CaptureError('selection_invalid')
        if not any(item['id'] == ident for item in available['windows']):
            raise CaptureError('target_unavailable')
        return dict(target=target, window_id=ident)
    output = next((item for item in available['outputs'] if item['name'] == request.get('output')), None)
    if output is None:
        raise CaptureError('target_unavailable')
    result = dict(target=target, output=output)
    if target == 'area':
        area = request.get('area')
        if not isinstance(area, dict):
            raise CaptureError('selection_invalid')
        values = [area.get(key) for key in ('x', 'y', 'width', 'height')]
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
            raise CaptureError('selection_invalid')
        x, y, width, height = values
        if x < 0 or y < 0 or width < 1 or height < 1 or x + width > output['width'] or y + height > output['height']:
            raise CaptureError('selection_invalid')
        result['area'] = dict(zip(('x', 'y', 'width', 'height'), values))
    return result


def crop_box(selected, width, height):
    """Use the actual transformed image, including rounded fractional output sizes."""
    area, output = selected['area'], selected['output']
    sx, sy = width / output['width'], height / output['height']
    # Round inward so even fractional selection edges never expose outside pixels.
    left, top = math.ceil(area['x'] * sx), math.ceil(area['y'] * sy)
    right = math.floor((area['x'] + area['width']) * sx)
    bottom = math.floor((area['y'] + area['height']) * sy)
    if not 0 <= left < right <= width or not 0 <= top < bottom <= height:
        raise CaptureError('selection_invalid')
    return left, top, right, bottom


def runtime_directory():
    try:
        path = Path(os.environ['XDG_RUNTIME_DIR'])
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise CaptureError('runtime_unavailable')
        return path
    except (KeyError, OSError) as error:
        raise CaptureError('runtime_unavailable') from error


def save_directory(kind):
    # xdg-user-dir resolves the existing per-user configuration without evaluating it here.
    fallback, leaf = ('Pictures', 'Screenshots') if kind == 'picture' else ('Videos', 'Screencasts')
    try:
        resolved = command(['xdg-user-dir', fallback.upper()]).decode().strip()
        base = Path(resolved) if resolved else Path.home() / fallback
    except CaptureError:
        base = Path.home() / fallback
    if not base.is_absolute() or base == Path.home():
        base = Path.home() / fallback
    path = base / leaf
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def reserve(kind):
    folder = save_directory(kind)
    label, suffix = ('Screenshot', '.png') if kind == 'picture' else ('Screencast', '.mp4')
    descriptor, name = tempfile.mkstemp(prefix='.' + label + '-' + time.strftime('%Y-%m-%d-%H-%M-%S') + '-',
                                      suffix=suffix + '.part', dir=folder)
    os.close(descriptor)
    return Path(name)


def publish(partial):
    final = partial.with_name(partial.name[1:-5])
    # No replacement of a pre-existing file, even under a concurrent capture.
    os.link(partial, final)
    partial.unlink()
    return final


def png_ready(path, deadline):
    from PIL import Image
    while time.monotonic() < deadline:
        guard()
        try:
            with Image.open(path) as image:
                image.load()
                if image.format != 'PNG':
                    raise CaptureError('capture_failed')
                return image.copy()
        except (OSError, ValueError):
            time.sleep(.05)
    raise CaptureError('capture_timeout')


def picture(selected):
    __import__('PIL.Image')  # Fail before capture if the decoder is unavailable.
    guard()
    if selected['target'] == 'window':
        partial = reserve('picture')
        submitted = False
        try:
            command(['niri', 'msg', 'action', 'screenshot-window', '--id', str(selected['window_id']),
                     '--show-pointer', 'false', '--path', str(partial)])
            submitted = True
            png_ready(partial, time.monotonic() + 8)
            guard()
            path = publish(partial)
            return dict(path=str(path), clipboard=copy_picture(path))
        finally:
            # The native action acknowledges before its asynchronous writer finishes.
            # Keep a failed reservation private through a bounded late-write grace.
            if submitted and partial.exists():
                time.sleep(2)
            partial.unlink(missing_ok=True)
    partial = None
    with tempfile.TemporaryDirectory(prefix='emaki-capture-', dir=runtime_directory()) as temporary:
        source = Path(temporary) / 'capture.png'
        command(['grim', '-o', selected['output']['name'], '-s',
                 str(selected['output']['scale']), str(source)])
        image = png_ready(source, time.monotonic() + 8)
        if selected['target'] == 'area':
            image = image.crop(crop_box(selected, *image.size))
        guard()
        try:
            partial = reserve('picture')
            image.save(partial, format='PNG')
            guard()
            path = publish(partial)
            partial = None
            return dict(path=str(path), clipboard=copy_picture(path))
        finally:
            if partial is not None:
                partial.unlink(missing_ok=True)


def recognition_languages(environment=None):
    """Prefer the current message locale, with English as the shared fallback."""
    environment = os.environ if environment is None else environment
    locale = (environment.get('LC_ALL') or environment.get('LC_MESSAGES')
              or environment.get('LANG') or 'en')
    language = re.split(r'[_\-.@]', locale.lower())[0]
    preferred = {'en': 'eng', 'es': 'spa', 'pt': 'por', 'de': 'deu',
                 'fr': 'fra', 'ru': 'rus'}.get(language, 'eng')
    return preferred if preferred == 'eng' else preferred + '+eng'


def cleanup_recognition():
    """Reap abandoned private images without following runtime-directory links."""
    descriptor = None
    try:
        descriptor = os.open(runtime_directory(), os.O_RDONLY | os.O_DIRECTORY
                             | os.O_CLOEXEC | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            return
        cutoff = time.time() - 60
        for name in os.listdir(descriptor):
            if not name.startswith('emaki-recognize-'):
                continue
            try:
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if (stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                        and not info.st_mode & 0o077 and info.st_mtime < cutoff):
                    shutil.rmtree(name, dir_fd=descriptor)
            except OSError:
                # Another helper may have reaped the same abandoned directory.
                continue
    except (CaptureError, OSError):
        # Cleanup must not prevent inventory or another capture mode from starting.
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def recognize_image(path, kind):
    program = 'tesseract' if kind == 'text' else 'zbarimg'
    try:
        if kind == 'text':
            listing = subprocess.run([program, '--list-langs'], stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, timeout=8,
                                     env=child_environment(), close_fds=True)
            if listing.returncode:
                raise CaptureError('capture_failed')
            installed = set(listing.stdout.decode('utf-8', errors='replace').splitlines())
            languages = [language for language in recognition_languages().split('+')
                         if language in installed]
            if not languages:
                raise CaptureError('capture_failed')
            argv = [program, str(path), 'stdout', '-l', '+'.join(languages)]
        else:
            argv = [program, '--quiet', '--nodbus', '--raw', str(path)]
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=30, env=child_environment(), close_fds=True)
    except FileNotFoundError as error:
        raise CaptureError('tesseract_missing' if kind == 'text' else 'zbar_missing') from error
    except subprocess.TimeoutExpired as error:
        raise CaptureError('capture_timeout') from error
    # zbar uses status 4 for a readable image containing no symbols.
    if kind == 'qr' and result.returncode == 4:
        return ''
    if result.returncode:
        raise CaptureError('capture_failed')
    decoded = result.stdout.decode('utf-8', errors='replace')
    return decoded.strip() if kind == 'text' else decoded.removesuffix('\n')


def recognition(selected, kind):
    """Recognize only the selection and leave no image in the picture library."""
    if kind not in ('text', 'qr') or selected['target'] != 'area':
        raise CaptureError('selection_invalid')
    program = 'tesseract' if kind == 'text' else 'zbarimg'
    if shutil.which(program) is None:
        raise CaptureError('tesseract_missing' if kind == 'text' else 'zbar_missing')
    guard()
    old_handlers = {}
    def cancelled(*_):
        raise CaptureError('capture_cancelled')
    try:
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            old_handlers[sig] = signal.signal(sig, cancelled)
        with tempfile.TemporaryDirectory(prefix='emaki-recognize-', dir=runtime_directory()) as temporary:
            descriptor, filename = tempfile.mkstemp(suffix='.png', dir=temporary)
            os.close(descriptor)
            source = Path(filename)
            command(['grim', '-o', selected['output']['name'], '-s',
                     str(selected['output']['scale']), str(source)])
            image = png_ready(source, time.monotonic() + 8)
            image = image.crop(crop_box(selected, *image.size))
            guard()
            image.save(source, format='PNG')
            text = recognize_image(source, kind)
            guard()
            copied = False
            if text:
                try:
                    command(['wl-copy', '--type', 'text/plain;charset=utf-8'], data=text.encode('utf-8'))
                    copied = True
                except CaptureError:
                    pass
            return dict(kind=kind, text=text, clipboard=copied)
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


def copy_picture(path):
    try:
        command(['wl-copy', '--type', 'image/png'], data=path.read_bytes())
        return True
    except CaptureError:
        return False


class RecordingLease:
    """One recorder per Wayland session; the kernel releases the lease on death."""
    def __enter__(self):
        import hashlib
        key = hashlib.sha256(os.fsencode(os.environ.get('WAYLAND_DISPLAY', 'wayland-0'))).hexdigest()[:16]
        self.fd = os.open(runtime_directory() / ('emaki-capture-' + key + '.lock'),
                          os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(self.fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise CaptureError('runtime_unavailable')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(self.fd)
            raise CaptureError('recording_busy') from error
        except Exception:
            os.close(self.fd)
            raise
        return self

    def __exit__(self, *_):
        os.close(self.fd)


def native_recorder():
    root = Path(__file__).resolve().parents[2]
    if (root / 'capture/recorder.py').is_file():
        return root / 'capture/recorder.py'
    spec = importlib.util.spec_from_file_location('capture_paths', Path(__file__).with_name('emaki_paths.py'))
    paths = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(paths)
    installed = Path(paths.LIBEXECDIR) / 'emaki-capture-recorder'
    if not installed.is_file():
        raise CaptureError('dependency_missing')
    return installed


def stop_child(child, timeout=3):
    if child is None:
        return
    if child.stdin is not None and not child.stdin.closed:
        child.stdin.close()
    if child.poll() is None:
        try:
            child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)
    if child.stdout is not None:
        child.stdout.close()


class Recorder:
    SERVICE = 'org.gnome.Mutter.ScreenCast'
    SESSION = SERVICE + '.Session'
    STREAM = SERVICE + '.Stream'

    def __init__(self, selected, path):
        from gi.repository import Gio, GLib
        self.Gio, self.GLib = Gio, GLib
        self.selected, self.path = selected, path
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.session = None
        self.subscription = None
        self.native = None
        self.native_buffer = b''
        self.node = None
        self.stop_requested = False
        self.started = False
        self.native_saved = False
        self.lock_watch = None

    def call(self, path, interface, method, signature='()', values=()):
        return self.connection.call_sync(self.SERVICE, path, interface, method,
                    self.GLib.Variant(signature, values), None, self.Gio.DBusCallFlags.NONE, 3000, None).unpack()

    def connect(self):
        self.session = self.call('/org/gnome/Mutter/ScreenCast', self.SERVICE, 'CreateSession', '(a{sv})', ({},))[0]
        properties = {'cursor-mode': self.GLib.Variant('u', 1), 'is-recording': self.GLib.Variant('b', True)}
        if self.selected['target'] == 'window':
            properties['window-id'] = self.GLib.Variant('t', self.selected['window_id'])
            stream = self.call(self.session, self.SESSION, 'RecordWindow', '(a{sv})', (properties,))[0]
        else:
            stream = self.call(self.session, self.SESSION, 'RecordMonitor', '(sa{sv})',
                               (self.selected['output']['name'], properties))[0]
        self.subscription = self.connection.signal_subscribe(self.SERVICE, self.STREAM, 'PipeWireStreamAdded',
                                  stream, None, self.Gio.DBusSignalFlags.NONE, self.stream_added)
        guard()
        self.call(self.session, self.SESSION, 'Start')

    def stream_added(self, _connection, _sender, _path, _interface, _signal, parameters):
        self.node = parameters.unpack()[0]

    def input_stopped(self):
        if self.stop_requested:
            return True
        descriptors = [sys.stdin.fileno()]
        if OWNER_FD is not None:
            descriptors.append(OWNER_FD)
        ready = select.select(descriptors, [], [], 0)[0]
        if ready:
            os.read(ready[0], 4096)
            return True
        return False

    def pump(self):
        context = self.GLib.MainContext.default()
        for _ in range(64):
            if not context.pending():
                break
            context.iteration(False)

    def events(self, child, field):
        data = getattr(self, field)
        while True:
            try:
                chunk = os.read(child.stdout.fileno(), 8192)
            except BlockingIOError:
                break
            if not chunk:
                break
            data += chunk
            if len(data) > 65536:
                raise CaptureError('recorder_failed')
        events = []
        while b'\n' in data:
            line, data = data.split(b'\n', 1)
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError()
                events.append(event)
            except ValueError as error:
                raise CaptureError('recorder_failed') from error
        setattr(self, field, data)
        return events

    def start_native(self):
        argv = [sys.executable, '-I', '-B', str(native_recorder()), '--output', str(self.path),
                '--mode', self.selected['target'], '--node', str(self.node), '--cursor']
        if self.selected['target'] == 'area':
            output, area = self.selected['output'], self.selected['area']
            argv.extend(['--logical-width', str(output['width']), '--logical-height', str(output['height'])])
            for flag in ('x', 'y', 'width', 'height'):
                argv.extend(['--' + flag, str(area[flag])])
        self.native = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=child_environment(), close_fds=True)
        os.set_blocking(self.native.stdout.fileno(), False)

    def target_exists(self):
        if self.selected['target'] != 'window':
            return True
        try:
            windows = json.loads(command(['niri', 'msg', '--json', 'windows'], timeout=2))
            return any(item.get('id') == self.selected['window_id'] for item in windows)
        except (CaptureError, ValueError, TypeError, AttributeError):
            return False

    def run(self):
        if self.input_stopped():
            raise CaptureError('capture_cancelled')
        deadline = time.monotonic() + 10
        self.connect()
        while self.node is None:
            self.pump()
            guard()
            if self.input_stopped():
                raise CaptureError('capture_cancelled')
            if time.monotonic() >= deadline:
                raise CaptureError('capture_timeout')
            time.sleep(.02)
        # Before anything reads the node: the shell must know this cast is its own.
        emit('stream', node=self.node)
        self.lock_watch = LockWatch()
        guard()
        if self.input_stopped():
            raise CaptureError('capture_cancelled')
        self.start_native()
        deadline = time.monotonic() + 20
        next_target = 0
        stopping = False
        while True:
            self.pump()
            now = time.monotonic()
            status = self.native.poll()
            for event in self.events(self.native, 'native_buffer'):
                if event.get('event') == 'error':
                    reason = event.get('code')
                    raise CaptureError(reason if reason in ('target_changed', 'target_unavailable', 'capture_cancelled',
                                                           'capture_timeout', 'dependency_missing') else 'recorder_failed')
                if event.get('event') == 'recording' and not self.started:
                    self.started = True
                    emit('recording')
                elif event.get('event') == 'stopping':
                    self.stop_requested = True
                elif event.get('event') == 'saved':
                    self.native_saved = type(event.get('frames')) is int and event['frames'] > 0
            if status is not None:
                if status == 0 and self.native_saved:
                    return
                raise CaptureError('recorder_failed')
            if not stopping:
                cutoff = self.lock_watch.cutoff()
                gone = False
                if cutoff is None and now >= next_target:
                    gone = not self.target_exists()
                    next_target = now + .5
                # Read the gate again after potentially blocking source inventory.
                observed = self.lock_watch.cutoff()
                if observed is not None:
                    cutoff = observed if cutoff is None else min(cutoff, observed)
                if cutoff is not None or gone or self.input_stopped():
                    stopping = True
                    boundary = time.time_ns() if cutoff is None else cutoff
                    try:
                        self.native.stdin.write(json.dumps({'stop_at_ns': boundary}).encode() + b'\n')
                        self.native.stdin.flush()
                    except BrokenPipeError:
                        pass  # A group termination may already be draining the encoder.
                    self.native.stdin.close()
                    deadline = now + 35
                elif not self.started and now >= deadline:
                    raise CaptureError('capture_timeout')
            elif now >= deadline:
                raise CaptureError('capture_timeout')
            # inotify wakes the stop path immediately; the deadline only pumps D-Bus.
            descriptors = [self.native.stdout.fileno()]
            if not stopping:
                descriptors.append(self.lock_watch.fd)
            select.select(descriptors, [], [], .02)

    def close(self):
        try:
            stop_child(self.native, timeout=35)
        finally:
            try:
                if self.lock_watch is not None:
                    self.lock_watch.close()
                    self.lock_watch = None
                if self.session is not None:
                    session, self.session = self.session, None
                    try:
                        self.call(session, self.SESSION, 'Stop')
                    except self.GLib.Error:
                        pass  # The compositor may already have exited during logout.
            finally:
                if self.subscription is not None:
                    subscription, self.subscription = self.subscription, None
                    try:
                        self.connection.signal_unsubscribe(subscription)
                    except self.GLib.Error:
                        pass  # The session bus may already be gone as well.


def record(selected):
    with RecordingLease():
        guard()
        partial = reserve('recording')
        recorder = None
        old_handlers = {}
        try:
            recorder = Recorder(selected, partial)
            for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                old_handlers[sig] = signal.signal(sig, lambda *_: setattr(recorder, 'stop_requested', True))
            recorder.run()
            if partial.stat().st_size <= 0:
                raise CaptureError('recorder_failed')
            return dict(path=str(publish(partial)))
        finally:
            try:
                if recorder is not None:
                    recorder.close()
            finally:
                partial.unlink(missing_ok=True)
                for sig, handler in old_handlers.items():
                    signal.signal(sig, handler)


def launch_record(payload, worker_path=None):
    """Keep recording cleanup outside the shell's directly owned, killable process."""
    read, write = os.pipe2(os.O_CLOEXEC)
    child = None
    old_handlers = {}
    def release(*_):
        nonlocal write
        if write is not None:
            os.close(write)
            write = None
    try:
        environment = child_environment()
        child = subprocess.Popen([sys.executable, '-I', '-B',
                    str(worker_path or Path(__file__).resolve()), '--record-worker', payload,
                    '--owner-fd', str(read)],
                    stdin=sys.stdin, stdout=sys.stdout, stderr=subprocess.DEVNULL,
                    env=environment, close_fds=True, pass_fds=(read,), start_new_session=True)
        os.close(read)
        read = None
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            old_handlers[sig] = signal.signal(sig, release)
        return child.wait()
    finally:
        release()
        if read is not None:
            os.close(read)
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


def main():
    global OWNER_FD
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--inventory', action='store_true')
    mode.add_argument('--picture')
    mode.add_argument('--record')
    mode.add_argument('--text')
    mode.add_argument('--qr')
    mode.add_argument('--record-worker', help=argparse.SUPPRESS)
    parser.add_argument('--owner-fd', type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        cleanup_recognition()
        if args.record is not None:
            return launch_record(args.record)
        if args.record_worker is not None:
            if args.owner_fd is None or not stat.S_ISFIFO(os.fstat(args.owner_fd).st_mode):
                raise CaptureError('runtime_unavailable')
            OWNER_FD = args.owner_fd
            os.set_inheritable(OWNER_FD, False)
        elif args.owner_fd is not None:
            raise CaptureError('selection_invalid')
        available = inventory()
        if args.inventory:
            emit('inventory', **available)
            return 0
        payload = next(value for value in (args.picture, args.text, args.qr, args.record_worker)
                       if value is not None)
        request = json.loads(payload)
        selected = selection(request, available)
        emit('starting')
        if args.text is not None or args.qr is not None:
            result = recognition(selected, 'text' if args.text is not None else 'qr')
            if result['text']:
                emit('recognized', **result)
            else:
                emit('empty', kind=result['kind'])
        else:
            result = picture(selected) if args.picture is not None else record(selected)
            emit('saved', **result)
        return 0
    except CaptureError as error:
        emit('error', reason=str(error))
    except (ImportError, ModuleNotFoundError):
        emit('error', reason='dependency_missing')
    except (ValueError, TypeError, KeyError):
        emit('error', reason='selection_invalid')
    except Exception:
        # Backend diagnostics may contain paths or window titles; the UI gets only a code.
        emit('error', reason='capture_failed')
    return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except BrokenPipeError:
        # record() has already unwound and closed its compositor session.
        pass
