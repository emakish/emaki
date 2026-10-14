#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The account picture for the lock and login screens, as a private PNG copy.

AccountsService names the picture (IconFile: the copy Settings and other desktops store,
or ~/.face by default). Qt never decodes that file: Pillow, limited to PNG, JPEG and WebP,
crops its centre square into a small PNG in this account's runtime directory, and one JSON
line per change carries the copy's path, or "" when there is no usable picture.

  account-picture.py [--user NAME]    without --user: the account running this helper
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
import warnings

ACCOUNTS = 'org.freedesktop.Accounts'
USER_INTERFACE = 'org.freedesktop.Accounts.User'
NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_.-]{0,62}\$?\Z')
FORMATS = ('PNG', 'JPEG', 'WEBP')
MAX_SOURCE = 8 * 1024 * 1024
MAX_PIXELS = 4096 * 4096  # PNG and WebP decode at full size.
MAX_JPEG_PIXELS = 40_000_000  # JPEG decodes at a reduced scale (draft()).
SIDE = 384  # The 96 px drop at a scale of 4.
INTERVAL = 1.5
DIRECTORY = 'emaki-account-picture'
COPY = re.compile(r'\.?(\d+)-[0-9a-f]{32}\.png\Z')  # a leading dot: a write in progress


def source_key(path):
    """What identifies the named file's current content without reading it."""
    if not path:
        return None
    try:
        info = os.stat(path)
    except OSError:
        return (path, None)
    return (path, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def open_strict(path):
    """Open every component without following a link: another account's ~/.face must not
    point the login screen at a file that account cannot read itself."""
    parts = Path(path).parts
    if not os.path.isabs(path) or '..' in parts:
        raise ValueError('unsafe_path')
    flags = os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open('/', os.O_PATH | os.O_DIRECTORY | flags)
    try:
        for index, part in enumerate(parts[1:]):
            final = index == len(parts) - 2
            child = os.open(part, (os.O_RDONLY if final else os.O_PATH | os.O_DIRECTORY) | flags,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_source(path, owners=None):
    """The named file's bytes. With `owners` (the login screen reading another account's
    picture) no link is followed and the file must belong to root or that account."""
    # O_NONBLOCK: a FIFO named as the picture must not stall before the type check.
    if owners is None:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    else:
        descriptor = open_strict(path)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_SOURCE:
            raise ValueError('source_limit')
        if owners is not None and info.st_uid not in owners:
            raise ValueError('foreign_owner')
        chunks, remaining = [], MAX_SOURCE + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if not remaining:
            raise ValueError('source_limit')
        return b''.join(chunks)
    finally:
        os.close(descriptor)


def square(data):
    """Decode with a fixed plugin list; the centre square, at most SIDE pixels wide."""
    from PIL import Image, ImageOps
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data), formats=FORMATS) as image:
            limit = MAX_JPEG_PIXELS if image.format == 'JPEG' else MAX_PIXELS
            if image.width < 1 or image.height < 1 or image.width * image.height > limit:
                raise ValueError('pixel_limit')
            # JPEG decodes at a reduced scale before the centre crop.
            image.draft('RGB', (SIDE, SIDE))
            image = ImageOps.exif_transpose(image).convert('RGBA')
    side = min(image.width, image.height)
    left, top = (image.width - side) // 2, (image.height - side) // 2
    image = image.crop((left, top, left + side, top + side))
    if side > SIDE:
        image = image.resize((SIDE, SIDE), Image.LANCZOS)
    output = io.BytesIO()
    image.save(output, 'PNG')
    return output.getvalue()


def private_directory(runtime):
    if not runtime or not os.path.isabs(runtime):
        raise ValueError('no_runtime_directory')
    path = Path(runtime) / DIRECTORY
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        pass
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('unsafe_directory')
    return path


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Pictures:
    """This process's copies: one current file, removed when replaced or on exit."""

    def __init__(self, directory, pid=None):
        self.directory, self.pid, self.current = directory, pid or os.getpid(), None
        for entry in os.listdir(directory):
            match = COPY.match(entry)
            if match and int(match.group(1)) != self.pid and not alive(int(match.group(1))):
                self.remove(directory / entry)

    @staticmethod
    def remove(path):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    def publish(self, png):
        name = f'{self.pid}-{hashlib.sha256(png).hexdigest()[:32]}.png'
        target = self.directory / name
        if self.current != target:
            temporary = self.directory / f'.{name}'
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(png)
            os.replace(temporary, target)
            self.clear()
            self.current = target
        return str(target)

    def clear(self):
        if self.current:
            self.remove(self.current)
            self.current = None


class Watcher:
    """Re-reads the named picture only when its name or file changes."""

    def __init__(self, lookup, pictures):
        self.lookup, self.pictures = lookup, pictures
        self.key, self.path = object(), None

    def poll(self):
        """The copy's path ("" for none) when it changed since the last poll, else None."""
        try:
            name = self.lookup()
        except Exception:
            name = None  # The service is down or restarting: no picture rather than a stale one.
        name, owners = name if isinstance(name, tuple) else (name, None)
        key = source_key(name)
        key = key and key + (owners,)
        if key == self.key:
            return None
        self.key = key
        path = ''
        if name and key[1] is not None:
            try:
                path = self.pictures.publish(square(read_source(name, owners)))
            except Exception:
                path = ''
        if not path:
            self.pictures.clear()
        if path == self.path:
            return None
        self.path = path
        return path


def accounts_lookup(user):
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    state = {'object': None, 'owners': None, 'bus': None}

    def call(path, interface, method, args, reply):
        if state['bus'] is None:
            # Connected on demand: a bus that is not up yet is tried again at the next poll.
            address = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SYSTEM, None)
            flags = (Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                     | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION)
            # The shared connection can remain closed without a GLib main loop.
            state['bus'] = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
        return state['bus'].call_sync(ACCOUNTS, path, interface, method, args, GLib.VariantType(reply),
                                      Gio.DBusCallFlags.NONE, 3000, None).unpack()

    def get(path, name):
        return call(path, 'org.freedesktop.DBus.Properties', 'Get',
                    GLib.Variant('(ss)', (USER_INTERFACE, name)), '(v)')[0]

    def lookup():
        """IconFile; for another account (--user) with the owners its file may have."""
        try:
            path, owners = state['object'], state['owners']
            if path is None:
                if user:
                    found = call('/org/freedesktop/Accounts', ACCOUNTS, 'FindUserByName',
                                 GLib.Variant('(s)', (user,)), '(o)')
                else:
                    found = call('/org/freedesktop/Accounts', ACCOUNTS, 'FindUserById',
                                 GLib.Variant('(x)', (os.getuid(),)), '(o)')
                path = found[0]
                if user:
                    uid = get(path, 'Uid')
                    owners = frozenset((0, uid)) if isinstance(uid, int) else frozenset((0,))
            value = get(path, 'IconFile')
        except Exception:
            state['object'] = None  # A removed and re-added account gets a new object.
            state['owners'] = None
            bus, state['bus'] = state['bus'], None
            if bus is not None and not bus.is_closed():
                try:
                    bus.close_sync(None)
                except Exception:
                    pass
            raise
        # Publish only a complete lookup, including the named account's read restrictions.
        state['object'], state['owners'] = path, owners
        value = value if isinstance(value, str) and value.startswith('/') else None
        return (value, state['owners']) if user else value

    return lookup


def emit(path, user=''):
    # The account travels with each line: a line from the previous account's helper, read
    # after the login screen switched accounts, is recognisably stale.
    print(json.dumps(dict(version=1, user=user, path=path), separators=(',', ':')), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--user', default='')
    args = parser.parse_args(argv)
    if args.user and not NAME.match(args.user):
        emit('', args.user)
        return 2  # Permanent: AccountPicture does not restart the helper for exit codes 1 and 2.
    try:
        pictures = Pictures(private_directory(os.environ.get('XDG_RUNTIME_DIR', '')))
    except (OSError, ValueError):
        emit('', args.user)
        return 1

    def stop(_signum, _frame):
        pictures.clear()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    try:
        lookup = accounts_lookup(args.user)
    except Exception:
        lookup = lambda: None  # noqa: E731 - no system bus: the silhouette stays.
    watcher = Watcher(lookup, pictures)
    try:
        first = True
        while True:
            path = watcher.poll()
            if path is not None or first:
                emit(watcher.path or '', args.user)
            first = False
            time.sleep(INTERVAL)
    except (BrokenPipeError, KeyboardInterrupt):
        return 0
    finally:
        pictures.clear()


if __name__ == '__main__':
    sys.exit(main())
