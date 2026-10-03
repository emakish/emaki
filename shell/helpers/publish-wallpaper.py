#!/usr/bin/env python3
"""Publish a static wallpaper without granting the greeter access to the user's home.

This is always an unprivileged user operation. Input and output traversal uses directory
descriptors and O_NOFOLLOW; the privileged provisioner never reads these inputs. Images
have content-derived names, so replacing config.toml is the single publication commit.
The user watcher observes the real image and its parent (atomic replacements included).
"""
import argparse
import ctypes
import errno
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import pwd
import re
import secrets
import select
import stat
import sys
import time
import tomllib
import warnings

from wallpaper import MAX_CONFIG, MAX_IMAGE, MAX_PIXELS, PUBLISHED_FORMATS, bounded_descriptor, bounded_no_symlinks, open_no_symlinks

PUBLISH_ROOT = Path('/var/lib/emaki-greeter/users')
MAX_SECTIONS = 32
MAX_TOTAL = 64 * 1024 * 1024
USER_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}\$?\Z')
IMAGE_NAME = re.compile(r'wallpaper-[0-9a-f]{64}\.image\Z')
REFUSAL_REASONS = frozenset(('root_refused', 'invalid_user', 'image_path_invalid',
                           'config_limit', 'output_selection_unsupported', 'mode_unsupported',
                           'config_invalid', 'image_limit', 'input_limit', 'output_permissions',
                           'unsafe_output', 'unsafe_path', 'unsafe_root'))


def user_only():
    if os.getuid() == 0 or os.geteuid() == 0:
        raise ValueError('root_refused')


def paths():
    user = pwd.getpwuid(os.getuid()).pw_name
    if not USER_NAME.fullmatch(user):
        raise ValueError('invalid_user')
    config_root = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
    return config_root / 'wpaperd/config.toml', PUBLISH_ROOT / user


def image_path(value):
    if not isinstance(value, str) or not value:
        raise ValueError('image_path_invalid')
    path = Path(value).expanduser()
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('image_path_invalid')
    return path


def read_config(config_path):
    data, _ = bounded_no_symlinks(config_path, MAX_CONFIG)
    config = tomllib.loads(data.decode('utf-8'))
    if not config or len(config) > MAX_SECTIONS:
        raise ValueError('config_limit')
    # Only static exact output selectors can be reproduced. Wpaperd's description,
    # glob and random/slideshow selectors do not identify one published picture.
    for name, options in config.items():
        if (not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', name)
                or not isinstance(options, dict)):
            raise ValueError('output_selection_unsupported')
        if 'path' in options:
            image_path(options['path'])
        if options.get('mode', 'center') not in ('center', 'fit', 'stretch'):
            raise ValueError('mode_unsupported')
        offset = options.get('offset', .5)
        if isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(offset) or not 0 <= offset <= 1:
            raise ValueError('config_invalid')
    if not any('path' in section for section in config.values()):
        raise ValueError('config_invalid')
    return config


def image_bytes(path):
    from PIL import Image
    data, _ = bounded_no_symlinks(path, MAX_IMAGE)
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data), formats=PUBLISHED_FORMATS) as image:
            if image.width * image.height > MAX_PIXELS or getattr(image, 'n_frames', 1) != 1:
                raise ValueError('image_limit')
            image.load()
    return data


def output_directory(path):
    descriptor = open_no_symlinks(path, directory=True)
    info = os.fstat(descriptor)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o2750:
        os.close(descriptor)
        raise ValueError('output_permissions')
    return descriptor


def child_directory(parent, name):
    # mkdir inherits the parent's greeter gid and setgid bit. chmod(2750) as an
    # owner outside the greeter group would silently clear that bit on Linux.
    # Set the creation umask instead; this helper is deliberately single-threaded.
    previous_umask = os.umask(0o027)
    try:
        os.mkdir(name, 0o750, dir_fd=parent)
    except FileExistsError:
        pass
    finally:
        os.umask(previous_umask)
    descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=parent)
    info = os.fstat(descriptor)
    if (info.st_uid != os.getuid() or info.st_gid != os.fstat(parent).st_gid
            or stat.S_IMODE(info.st_mode) != 0o2750):
        os.close(descriptor)
        raise ValueError('output_permissions')
    return descriptor


def reject_link(parent, name):
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('unsafe_output')
    except FileNotFoundError:
        pass


def atomic_write(parent, name, data):
    reject_link(parent, name)
    # Reusing identical immutable images retains the greeter's stat-keyed cache
    # across logins. Recheck bytes and private metadata before trusting the name.
    try:
        existing = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                           dir_fd=parent)
        try:
            old, info = bounded_descriptor(existing, len(data))
            if (old == data and info.st_uid == os.getuid() and info.st_gid == os.fstat(parent).st_gid
                    and stat.S_IMODE(info.st_mode) == 0o640):
                return
        finally:
            os.close(existing)
    except (OSError, ValueError):
        pass
    temporary = '.publish-' + secrets.token_hex(16)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o640, dir_fd=parent)
    try:
        os.fchmod(descriptor, 0o640)
        with os.fdopen(descriptor, 'wb', closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(descriptor)
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass


def prepare_publication(config_path, destination):
    config = read_config(config_path)
    images, sections, total = {}, [], 0
    for name, options in config.items():
        lines = ['[' + json.dumps(name) + ']']
        if 'path' in options:
            source = image_path(options['path'])
            data = image_bytes(source)
            filename = 'wallpaper-' + hashlib.sha256(data).hexdigest() + '.image'
            if filename not in images:
                total += len(data)
                if total > MAX_TOTAL:
                    raise ValueError('image_limit')
                images[filename] = data
            lines.append('path = ' + json.dumps(str(destination / filename)))
        # Only supported renderer fields cross the publishing boundary.
        for key in ('mode', 'offset'):
            if key in options:
                lines.append(key + ' = ' + json.dumps(options[key]))
        sections.append('\n'.join(lines))
    config_data = ('\n\n'.join(sections) + '\n').encode()
    if len(config_data) > MAX_CONFIG:
        raise ValueError('config_limit')
    return images, config_data


def unpublish(parent):
    """Removal commits at config unlink; stale personal pictures stop displaying."""
    config_directory = child_directory(parent, 'wpaperd')
    try:
        reject_link(config_directory, 'config.toml')
        try:
            os.unlink('config.toml', dir_fd=config_directory)
            os.fsync(config_directory)
        except FileNotFoundError:
            pass
    finally:
        os.close(config_directory)
    for name in os.listdir(parent):
        if IMAGE_NAME.fullmatch(name):
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid():
                os.unlink(name, dir_fd=parent)
    os.fsync(parent)
    return {'state': 'unpublished'}


def publish(config_path, destination):
    user_only()
    destination = Path(destination)
    parent = output_directory(destination)
    lock = None
    try:
        lock = os.open('.publish.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                       0o600, dir_fd=parent)
        info = os.fstat(lock)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError('unsafe_output')
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            images, config_data = prepare_publication(config_path, destination)
        except FileNotFoundError:
            # Recheck after editors' brief delete/replace gap. Persistently missing
            # config or a selected image means revoke, not preserve a private photo.
            time.sleep(.15)
            try:
                images, config_data = prepare_publication(config_path, destination)
            except FileNotFoundError:
                return unpublish(parent)
        config_directory = child_directory(parent, 'wpaperd')
        previous_images = set()
        try:
            reject_link(config_directory, 'config.toml')
            try:
                old_fd = os.open('config.toml', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                                 dir_fd=config_directory)
                try:
                    old_data, _ = bounded_descriptor(old_fd, MAX_CONFIG)
                    old_config = tomllib.loads(old_data.decode())
                    for options in old_config.values():
                        if isinstance(options, dict) and isinstance(options.get('path'), str):
                            name = Path(options['path']).name
                            if IMAGE_NAME.fullmatch(name):
                                previous_images.add(name)
                finally:
                    os.close(old_fd)
            except (OSError, ValueError, UnicodeError):
                pass
            for name, data in images.items():
                atomic_write(parent, name, data)
            atomic_write(config_directory, 'config.toml', config_data)
        finally:
            os.close(config_directory)
        # Keep one previous generation for readers that already hold its config;
        # two generations bound disk use during frequent image updates.
        # Never inspect or remove nonregular/symlink entries from the user's folder.
        for name in os.listdir(parent):
            if IMAGE_NAME.fullmatch(name) and name not in images and name not in previous_images:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid():
                    os.unlink(name, dir_fd=parent)
        return {'state': 'published', 'images': len(images)}
    finally:
        if lock is not None:
            os.close(lock)
        os.close(parent)


def refusal(error):
    """Fixed reason codes only: exception text can contain private source paths."""
    if isinstance(error, ValueError) and error.args and isinstance(error.args[0], str) and error.args[0] in REFUSAL_REASONS:
        reason = error.args[0]
    elif isinstance(error, (tomllib.TOMLDecodeError, UnicodeError)):
        reason = 'config_invalid'
    elif isinstance(error, OSError):
        reason = {errno.ELOOP: 'symlink', errno.ENOTDIR: 'unsafe_path',
                  errno.EACCES: 'permission_denied', errno.EPERM: 'permission_denied',
                  errno.ENOSPC: 'storage_full', errno.EDQUOT: 'storage_full'}.get(error.errno, 'input_or_io_error')
    elif type(error).__name__ in ('DecompressionBombWarning', 'DecompressionBombError'):
        reason = 'image_limit'
    else:
        reason = 'invalid_input'
    return {'state': 'refused', 'reason': reason}


def attempt(config_path, destination):
    try:
        return publish(config_path, destination)
    except FileNotFoundError:
        return {'state': 'missing'}
    except Exception as error:
        return refusal(error)


def watch_paths(config_path):
    candidates = {Path(config_path)}
    try:
        config = read_config(config_path)
        candidates.update(image_path(options['path']) for options in config.values() if 'path' in options)
    except Exception:
        pass
    # Parent watches cover editors' atomic rename and recreation after deletion.
    for path in tuple(candidates):
        candidates.add(path.parent)
    return candidates


def watch_identity(candidates):
    identity = []
    for path in sorted(candidates):
        descriptor = None
        try:
            descriptor = open_no_symlinks(path)
            info = os.fstat(descriptor)
            # Directory events may be unrelated. Only source/config identities
            # should force another decode/copy of a large wallpaper.
            if stat.S_ISREG(info.st_mode):
                identity.append((str(path), info.st_dev, info.st_ino, info.st_size,
                                 info.st_mtime_ns, info.st_ctime_ns))
        except (OSError, ValueError):
            identity.append((str(path), 'missing'))
        finally:
            if descriptor is not None:
                os.close(descriptor)
    return identity


class Inotify:
    """An inotify watch binds to already validated open descriptors via procfs."""
    def __init__(self):
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.inotify_init1.argtypes = [ctypes.c_int]
        self.libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        self.libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
        self.descriptor = self.libc.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
        if self.descriptor < 0:
            raise OSError(ctypes.get_errno(), 'inotify_unavailable')
        self.watches = set()

    def close(self):
        os.close(self.descriptor)

    def update(self, candidates):
        next_watches = set()
        for path in candidates:
            descriptor = None
            try:
                descriptor = open_no_symlinks(path)
                if not (stat.S_ISREG(os.fstat(descriptor).st_mode) or stat.S_ISDIR(os.fstat(descriptor).st_mode)):
                    continue
                # CLOSE_WRITE | ATTRIB | MOVED_FROM/TO | CREATE | DELETE |
                # DELETE_SELF | MOVE_SELF. Reading never triggers these watches.
                mask = 0x00000008 | 0x00000004 | 0x00000040 | 0x00000080 | 0x00000100 | 0x00000200 | 0x00000400 | 0x00000800
                watch = self.libc.inotify_add_watch(self.descriptor, f'/proc/self/fd/{descriptor}'.encode(), mask)
                if watch >= 0:
                    next_watches.add(watch)
            except (OSError, ValueError):
                pass
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        for watch in self.watches - next_watches:
            self.libc.inotify_rm_watch(self.descriptor, watch)
        self.watches = next_watches

    def changed(self, timeout):
        if not select.select([self.descriptor], [], [], timeout)[0]:
            return False
        while True:
            try:
                os.read(self.descriptor, 65536)
            except BlockingIOError:
                return True


def watch(config_path, destination):
    user_only()
    notifier = Inotify()
    previous = None
    last_refusal = None
    try:
        while True:
            # Subscribe before reading/copying, so a concurrent replacement queues
            # another attempt. The bounded retry also heals missing parent folders.
            candidates = watch_paths(config_path)
            notifier.update(candidates)
            identity = watch_identity(candidates)
            if identity != previous:
                result = attempt(config_path, destination)
                if result['state'] == 'refused':
                    if result != last_refusal:
                        print(json.dumps(result, separators=(',', ':')), flush=True)
                    last_refusal = result
                else:
                    last_refusal = None
                # Retry transient failures (including a not-yet-provisioned
                # directory) without rewriting a healthy copy every 30 seconds.
                previous = identity if result['state'] in ('published', 'unpublished') else None
            notifier.changed(30)
            time.sleep(.15)
    finally:
        notifier.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch', action='store_true')
    options = parser.parse_args()
    try:
        user_only()
        config_path, destination = paths()
        if options.watch:
            watch(config_path, destination)
        else:
            print(json.dumps(attempt(config_path, destination), separators=(',', ':')))
    except Exception as error:
        print(json.dumps(refusal(error), separators=(',', ':')))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
