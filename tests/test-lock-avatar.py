#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""The account picture on the lock and login screens.

The helper (shell/helpers/account-picture.py) is checked directly: what it accepts, what it
refuses and which private copies it keeps. Then the production AccountPicture and LockSurface
run offscreen against a fake AccountsService on a private bus, while this runner sets, replaces,
breaks and removes the picture: present, absent, changed and unreadable, as the person sees it.
"""
from runtime_fixture import runtime_path
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import zlib
import reaper
reaper.guard()  # nothing this test starts outlives it

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / '.cache'
CACHE.mkdir(exist_ok=True)
spec = importlib.util.spec_from_file_location('account_picture', ROOT / 'shell/helpers/account-picture.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def picture(colour, size=(64, 64), fmt='PNG'):
    output = io.BytesIO()
    Image.new('RGB', size, colour).save(output, fmt)
    return output.getvalue()


def png_header(width, height):
    """A PNG that only claims its size: Pillow reads the header without decoding pixels."""
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\0' * 7)) + chunk(b'IEND', b''))


def decoded(data):
    with Image.open(io.BytesIO(data)) as image:
        return image.format, image.size, image.convert('RGBA').getpixel((image.width // 2, image.height // 2))


class LookupRecovery(unittest.TestCase):
    def bus(self, icon, uid, failure):
        from gi.repository import GLib
        bus = Mock()
        bus.is_closed.return_value = False
        failed = False

        def call(service, path, interface, method, args, *rest):
            nonlocal failed
            name = args.unpack()[1] if method == 'Get' else method
            if name == failure and not failed:
                failed = True
                raise RuntimeError('temporary lookup failure')
            if method.startswith('FindUserBy'):
                return GLib.Variant('(o)', ('/org/freedesktop/Accounts/User42',))
            value = GLib.Variant('t', uid) if name == 'Uid' else GLib.Variant('s', icon)
            return GLib.Variant('(v)', (value,))

        bus.call_sync.side_effect = call
        return bus

    def test_uid_failure_keeps_named_account_reads_restricted(self):
        for kind in ('link', 'foreign'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=CACHE) as folder:
                work = Path(folder)
                source = work / 'face.png'
                source.write_bytes(picture((255, 0, 0)))
                if kind == 'foreign' and os.getuid() == 0:
                    continue  # root-owned pictures are explicitly allowed
                uid = os.getuid() if kind == 'link' else os.getuid() + 1
                icon = source
                if kind == 'link':
                    icon = work / 'link.png'
                    icon.symlink_to(source)
                copies = work / 'copies'
                copies.mkdir()
                bus = self.bus(str(icon), uid, 'Uid')
                with patch('gi.repository.Gio.DBusConnection.new_for_address_sync', return_value=bus):
                    lookup = helper.accounts_lookup('ada')
                    watcher = helper.Watcher(lookup, helper.Pictures(copies, os.getpid()))
                    self.assertEqual(watcher.poll(), '')
                    self.assertIsNone(watcher.poll(), 'failed UID lookup must not permit an unrestricted read')
                    self.assertEqual(list(copies.iterdir()), [])
                    self.assertEqual(lookup(), (str(icon), frozenset((0, uid))))

    def test_discovery_reconnects_after_real_bus_restart(self):
        with tempfile.TemporaryDirectory(prefix='bus-reset-', dir=CACHE) as folder:
            work = Path(folder)
            uid = os.getuid()
            state = work / 'state.json'
            state.write_text(json.dumps({'users': {str(uid): {
                'UserName': 'ada', 'HomeDirectory': str(work), 'IconFile': '/picture.png'}}}))
            ready = work / 'ready'
            daemon, address = private_bus(work)
            service = None

            def start_service():
                ready.unlink(missing_ok=True)
                process = subprocess.Popen([
                    sys.executable, '-B', '-W', 'ignore::DeprecationWarning',
                    str(ROOT / 'tests/fixtures/fake-accounts-service.py'), str(state),
                    str(work / 'passwd'), str(work / 'calls.log'), str(ready)],
                    env=dict(os.environ, DBUS_SESSION_BUS_ADDRESS=address),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + 10
                while not ready.exists():
                    if time.monotonic() >= deadline or process.poll() is not None:
                        stop(process)
                        self.fail('private AccountsService did not start')
                    time.sleep(0.05)
                return process

            try:
                service = start_service()
                with patch.dict(os.environ, DBUS_SYSTEM_BUS_ADDRESS=address):
                    lookups = [helper.accounts_lookup(''), helper.accounts_lookup('ada')]
                    expected = ['/picture.png', ('/picture.png', frozenset((0, uid)))]
                    self.assertEqual([lookup() for lookup in lookups], expected)
                    stop(service)
                    stop(daemon)
                    for lookup in lookups:
                        with self.assertRaises(Exception):
                            lookup()
                    daemon, restarted_address = private_bus(work, address.split('=', 1)[1])
                    self.assertEqual(restarted_address, address)
                    service = start_service()
                    # Keep both original closures: neither helper nor main loop is restarted.
                    self.assertEqual([lookup() for lookup in lookups], expected)
                    self.assertEqual([lookup() for lookup in lookups], expected)
            finally:
                if service:
                    stop(service)
                stop(daemon)

    def test_tiny_high_resolution_png_is_refused_before_decode(self):
        from PIL import PngImagePlugin
        output = io.BytesIO()
        Image.new('1', (6000, 6000)).save(output, 'PNG')
        data = output.getvalue()
        self.assertLess(len(data), 5000)
        with patch.object(PngImagePlugin.PngImageFile, 'load',
                          side_effect=AssertionError('pixel decoding started')) as decode:
            with self.assertRaisesRegex(ValueError, 'pixel_limit'):
                helper.square(data)
            decode.assert_not_called()

    def test_a_large_jpeg_photo_decodes_at_a_reduced_scale(self):
        # A 24-megapixel phone photo is accepted; PNG and WebP keep the 4096x4096 limit.
        from PIL import JpegImagePlugin
        photo = io.BytesIO()
        Image.new('RGB', (6000, 4000), (40, 120, 200)).save(photo, 'JPEG', quality=90)
        self.assertLess(len(photo.getvalue()), helper.MAX_SOURCE)
        scales = []
        load = JpegImagePlugin.JpegImageFile.load

        def record(image):
            scales.append(image.size)
            return load(image)
        with patch.object(JpegImagePlugin.JpegImageFile, 'load', record):
            fmt, size, _ = decoded(helper.square(photo.getvalue()))
        self.assertEqual((fmt, size), ('PNG', (helper.SIDE, helper.SIDE)))
        self.assertEqual(set(scales), {(750, 500)})  # One eighth: the smallest scale that still covers 384.
        over = io.BytesIO()
        Image.new('L', (8000, 5001)).save(over, 'JPEG')
        for data in (over.getvalue(), png_header(6000, 4000)):
            with self.assertRaisesRegex(ValueError, 'pixel_limit'):
                helper.square(data)

    def test_icon_failure_retries_the_complete_lookup(self):
        bus = self.bus('/picture.png', 42, 'IconFile')
        with patch('gi.repository.Gio.DBusConnection.new_for_address_sync', return_value=bus) as connect:
            lookup = helper.accounts_lookup('ada')
            with self.assertRaises(RuntimeError):
                lookup()
            self.assertEqual(lookup(), ('/picture.png', frozenset((0, 42))))
            self.assertEqual(connect.call_count, 2)
            discoveries = [call for call in bus.call_sync.call_args_list if call.args[3] == 'FindUserByName']
            self.assertEqual(len(discoveries), 2)

    def test_avatar_files_have_license_headers(self):
        reference = (ROOT / 'scripts/emaki-settings-users').read_text().splitlines()[1:3]
        for name in ('shell/AccountPicture.qml', 'shell/helpers/account-picture.py',
                     'tests/fixtures/lock/LockAvatarTest.qml', 'tests/test-lock-avatar.py'):
            with self.subTest(path=name):
                lines = (ROOT / name).read_text().splitlines()
                expected = [line.replace('# ', '// ', 1) for line in reference] if name.endswith('.qml') else reference
                start = 1 if lines[0].startswith('#!') else 0
                self.assertEqual(lines[start:start + 2], expected)


def helper_rules(work):
    # Centre square, at most SIDE wide, re-encoded as PNG whatever came in.
    wide = Image.new('RGB', (300, 100), (255, 0, 0))
    wide.paste((0, 0, 255), (100, 0, 200, 100))
    source = io.BytesIO()
    wide.save(source, 'JPEG', quality=95)
    fmt, size, centre = decoded(helper.square(source.getvalue()))
    assert fmt == 'PNG' and size == (100, 100) and centre[2] > 200 and centre[0] < 60, (fmt, size, centre)
    fmt, size, _ = decoded(helper.square(picture((0, 255, 0), (2000, 1000), 'WEBP')))
    assert size == (helper.SIDE, helper.SIDE), size
    print('PASS: the centre square, at most 384 px, as a PNG copy (PNG, JPEG and WebP sources)')
    for fmt in ('GIF', 'BMP', 'TIFF'):
        try:
            helper.square(picture((255, 0, 0), fmt=fmt))
        except Exception:
            continue
        raise AssertionError(fmt + ' was decoded')
    for data in (png_header(10000, 10000), b'not a picture', b''):
        try:
            helper.square(data)
        except Exception:
            continue
        raise AssertionError('accepted %r' % data[:16])
    print('PASS: other formats, 100-megapixel headers and broken files are refused before decoding')

    fifo = work / 'fifo'
    os.mkfifo(fifo)
    huge = work / 'huge.png'
    with huge.open('wb') as stream:
        stream.truncate(helper.MAX_SOURCE + 1)
    empty = work / 'empty.png'
    empty.write_bytes(b'')
    for path in (fifo, huge, empty, work):
        started = time.monotonic()
        try:
            helper.read_source(str(path))
        except (OSError, ValueError):
            assert time.monotonic() - started < 1, path
            continue
        raise AssertionError('read ' + str(path))
    print('PASS: a FIFO, a directory, an empty file and one over 8 MiB are refused without blocking')

    # The login screen reads another account's picture: no link anywhere, root's or theirs only.
    face = work / 'face.png'
    face.write_bytes(picture((255, 0, 0)))
    mine = frozenset((0, os.getuid()))
    assert helper.read_source(str(face), mine) == face.read_bytes()
    (work / 'link.png').symlink_to(face)
    (work / 'linked-dir').symlink_to(work)
    refused = [(str(work / 'link.png'), mine), (str(work / 'linked-dir/face.png'), mine),
               (str(face), frozenset((0, os.getuid() + 1))), ('relative.png', mine),
               (str(work / '..' / work.name / 'face.png'), mine)]
    if os.getuid() == 0:
        refused.pop(2)  # root owns the file itself
    for path, owners in refused:
        try:
            helper.read_source(path, owners)
        except (OSError, ValueError):
            continue
        raise AssertionError('read %s for %s' % (path, sorted(owners)))
    assert helper.read_source(str(work / 'link.png')) == face.read_bytes(), 'the own account may use a link'
    print('PASS: another account\'s picture is read without links and only from root\'s or its own file')

    runtime = work / 'runtime'
    runtime.mkdir(mode=0o700)
    directory = helper.private_directory(str(runtime))
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    os.chmod(directory, 0o750)
    try:
        helper.private_directory(str(runtime))
        raise AssertionError('a group-readable directory was used')
    except ValueError:
        pass
    os.chmod(directory, 0o700)
    for value in ('', 'relative'):
        try:
            helper.private_directory(value)
            raise AssertionError('runtime %r was used' % value)
        except ValueError:
            pass
    other = work / 'other-runtime'
    other.mkdir(mode=0o700)
    (other / helper.DIRECTORY).symlink_to(directory)
    try:
        helper.private_directory(str(other))
        raise AssertionError('a symlinked directory was used')
    except ValueError:
        pass
    print('PASS: copies live only in a private 0700 directory of the runtime directory, never through a link')

    # A dead helper's copy is pruned; a live one's (another lock or Settings) is kept.
    dead = subprocess.Popen(['true'])
    dead.wait()
    stale = directory / f'{dead.pid}-{"0" * 32}.png'
    stale.write_bytes(b'x')
    live = directory / f'{os.getppid()}-{"1" * 32}.png'
    live.write_bytes(b'x')
    pictures = helper.Pictures(directory, pid=os.getpid())
    assert not stale.exists() and live.exists()
    live.unlink()

    source = work / 'face'
    named = {'value': str(source)}

    def lookup():
        if named['value'] is Exception:
            raise RuntimeError('service restarting')
        return named['value']

    watcher = helper.Watcher(lookup, pictures)
    assert watcher.poll() == '' and watcher.poll() is None
    source.write_bytes(picture((255, 0, 0)))
    first = watcher.poll()
    assert first and Path(first).parent == directory and decoded(Path(first).read_bytes())[2][:3] == (255, 0, 0)
    assert watcher.poll() is None, 'an unchanged picture is not read again'
    os.utime(source)
    assert watcher.poll() is None, 'the same content keeps the same copy'
    source.write_bytes(picture((0, 0, 255)))
    second = watcher.poll()
    assert second and second != first and not Path(first).exists()
    assert decoded(Path(second).read_bytes())[2][:3] == (0, 0, 255)
    source.chmod(0)
    if not os.access(source, os.R_OK):  # root reads anything
        assert watcher.poll() == '' and not Path(second).exists()
    source.chmod(0o644)
    assert watcher.poll() and len(os.listdir(directory)) == 1
    named['value'] = Exception
    assert watcher.poll() == '' and os.listdir(directory) == []
    named['value'] = None
    assert watcher.poll() is None
    print('PASS: a change is read once; replaced, unreadable and unnamed pictures leave no copy behind')

    out = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'shell/helpers/account-picture.py'),
                          '--user', 'bad name'], capture_output=True, text=True, timeout=10)
    assert out.returncode == 2 and json.loads(out.stdout) == {'version': 1, 'user': 'bad name', 'path': ''}, out
    print('PASS: an invalid account name gives no picture')

    # No system bus yet: no picture now, and the bus is tried again at the next poll.
    saved = os.environ.get('DBUS_SYSTEM_BUS_ADDRESS')
    os.environ['DBUS_SYSTEM_BUS_ADDRESS'] = 'unix:path=' + str(work / 'no-bus-yet')
    try:
        lookup = helper.accounts_lookup('')
        for _ in range(2):
            try:
                lookup()
                raise AssertionError('a missing bus answered')
            except AssertionError:
                raise
            except Exception:
                pass
    finally:
        if saved is None:
            os.environ.pop('DBUS_SYSTEM_BUS_ADDRESS')
        else:
            os.environ['DBUS_SYSTEM_BUS_ADDRESS'] = saved
    print('PASS: a system bus that is not up yet is retried, not given up')


def private_bus(work, name=None):
    name = name or 'emaki-avatar-test-' + os.urandom(8).hex()
    config = work / 'bus.conf'
    config.write_text(f'''<busconfig><type>session</type><listen>unix:abstract={name}</listen>
<auth>EXTERNAL</auth><policy context="default"><allow send_destination="*" eavesdrop="true"/>
<allow eavesdrop="true"/><allow own="*"/></policy></busconfig>''')
    daemon = subprocess.Popen(['dbus-daemon', '--nofork', '--config-file', str(config)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 10
    while True:
        with socket.socket(socket.AF_UNIX) as client:
            try:
                client.connect('\0' + name)
                return daemon, f'unix:abstract={name}'
            except OSError:
                assert time.monotonic() < deadline and daemon.poll() is None, 'private bus did not start'
                time.sleep(0.05)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def set_icon(address, uid, path):
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
    bus = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
    bus.call_sync('org.freedesktop.Accounts', f'/org/freedesktop/Accounts/User{uid}',
                  'org.freedesktop.Accounts.User', 'SetIconFile', GLib.Variant('(s)', (str(path),)),
                  None, Gio.DBusCallFlags.NONE, 5000, None)
    bus.close_sync(None)


def live_surface(work):
    uid, other = os.getuid(), os.getuid() + 1
    home, icons, pictures = work / 'home/ada', work / 'icons', work / 'pictures'
    for path in (home, icons, pictures):
        path.mkdir(parents=True)
    for name, colour in (('blue', (0, 0, 255)), ('green', (0, 160, 0)), ('yellow', (255, 220, 0))):
        (pictures / f'{name}.png').write_bytes(picture(colour, (120, 80)))
    state = work / 'state.json'
    state.write_text(json.dumps({'icons': str(icons), 'users': {
        str(uid): {'UserName': 'ada', 'RealName': 'Ada', 'AccountType': 1, 'Locked': False,
                   'SystemAccount': False, 'HomeDirectory': str(home)},
        str(other): {'UserName': 'bo', 'RealName': '', 'AccountType': 0, 'Locked': False,
                     'SystemAccount': False, 'HomeDirectory': str(work / 'home/bo'),
                     'IconFile': str(pictures / 'yellow.png')}}}))
    daemon, address = private_bus(work)
    service = None
    try:
        ready = work / 'ready'
        service = subprocess.Popen([sys.executable, '-B', '-W', 'ignore::DeprecationWarning',
                                    str(ROOT / 'tests/fixtures/fake-accounts-service.py'), str(state),
                                    str(work / 'passwd'), str(work / 'calls.log'), str(ready)],
                                   env=dict(os.environ, DBUS_SESSION_BUS_ADDRESS=address))
        deadline = time.monotonic() + 10
        while not ready.exists():
            assert time.monotonic() < deadline and service.poll() is None, 'fake service did not start'
            time.sleep(0.05)
        profile = work / 'profile'
        for name in ('cache', 'config', 'state', 'data', 'tmp'):
            (profile / name).mkdir(parents=True, mode=0o700)
        shutil.copytree(ROOT / 'shell', profile / 'qml')
        shutil.copy(ROOT / 'tests/fixtures/lock/LockPam.qml', profile / 'qml/LockPam.qml')
        shutil.copy(ROOT / 'tests/fixtures/lock/LockAvatarTest.qml', profile / 'qml/check.qml')
        for visual in ('LockVisual.qml', 'LockWordmark.qml'):
            (profile / 'qml' / visual).write_text('This is a deliberately broken optional visual component\n')
        runtime = runtime_path(profile)
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software',
                   QML_DISABLE_DISK_CACHE='1', XDG_RUNTIME_DIR=str(runtime),
                   XDG_CACHE_HOME=str(profile / 'cache'), XDG_CONFIG_HOME=str(profile / 'config'),
                   XDG_STATE_HOME=str(profile / 'state'), XDG_DATA_HOME=str(profile / 'data'),
                   TMPDIR=str(profile / 'tmp'), PYTHONDONTWRITEBYTECODE='1', NIRI_SOCKET='',
                   DBUS_SESSION_BUS_ADDRESS='unix:path=' + str(profile / 'none'),
                   DBUS_SYSTEM_BUS_ADDRESS=address)
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'QT_LOGGING_RULES'):
            env.pop(name, None)
        face, copy = home / '.face', icons / 'ada'
        actions = {
            'absent': lambda: None,
            'present': lambda: face.write_bytes(picture((255, 0, 0), (80, 120))),
            'changed': lambda: set_icon(address, uid, pictures / 'blue.png'),
            'late-output': lambda: None,
            'changed-in-place': lambda: copy.write_bytes((pictures / 'green.png').read_bytes()),
            'unreadable': lambda: copy.chmod(0),
            'corrupt': lambda: (copy.chmod(0o644), copy.write_bytes(b'not a picture')),
            'restored': lambda: copy.write_bytes((pictures / 'green.png').read_bytes()),
            'removed': lambda: (face.unlink(), set_icon(address, uid, '')),
        }
        qs = subprocess.Popen(['qs', '-p', str(profile / 'qml/check.qml'), '--no-color'], env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        lines = []
        try:
            started = time.monotonic()
            for line in qs.stdout:
                lines.append(line.rstrip())
                assert time.monotonic() - started < 120, '\n'.join(lines)
                if 'LOCK_AVATAR_WAITING ' in line:
                    phase = line.split('LOCK_AVATAR_WAITING ', 1)[1].strip()
                    if phase == 'unreadable' and os.getuid() == 0:
                        phase = 'corrupt'  # root reads a 0000 file; the corrupt phase still covers it
                    actions[phase]()
                elif 'LOCK_AVATAR_PASS ' in line:
                    print(line[line.index('LOCK_AVATAR_PASS '):].rstrip())
            qs.wait(10)
        finally:
            stop(qs)
        output = '\n'.join(lines)
        assert qs.returncode == 0 and 'LOCK_AVATAR_COMPLETE' in output, output
        assert 'ReferenceError' not in output and 'TypeError' not in output, output
        # Quickshell ends its helpers with SIGKILL: the next helper of the account prunes their copies.
        copies = runtime / helper.DIRECTORY
        assert len(list(copies.iterdir())) <= 2, list(copies.iterdir())
        helper.Pictures(copies)
        assert not any(copies.iterdir()), list(copies.iterdir())
        print('PASS: copies left by killed helpers are removed by the next one')
    finally:
        if service:
            stop(service)
        stop(daemon)


def production_wiring():
    # The fixture drives LockSurface itself; the entry points must hand it the same picture.
    lock = (ROOT / 'shell/lock.qml').read_text()
    greeter = (ROOT / 'shell/greeter.qml').read_text()
    assert 'AccountPicture {\n        id: accountPicture\n    }' in lock, 'the lock reads its own account'
    assert lock.count('avatarUrl: accountPicture.url') == 1
    for line in ('user: memory.user', 'active: memory.user !== ""', 'isolateHelper: true'):
        assert line in greeter.split('AccountPicture {', 1)[1].split('}', 1)[0], line
    assert greeter.count('avatarUrl: accountPicture.url') == 1
    print('PASS: the lock shows its own account; the login screen only the chosen one, through an isolated helper')


def main():
    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(LookupRecovery))
    if not result.wasSuccessful():
        raise SystemExit(1)
    production_wiring()
    with tempfile.TemporaryDirectory(prefix='la-', dir=CACHE) as work:
        work = Path(work)
        (work / 'unit').mkdir()
        helper_rules(work / 'unit')
        if not shutil.which('dbus-daemon') or not shutil.which('qs'):
            raise SystemExit('dbus-daemon and qs are required')
        live_surface(work / 'live')
    print('PASS: the account picture on the lock and login screens')


if __name__ == '__main__':
    main()
