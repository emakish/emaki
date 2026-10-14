#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Isolated portal callbacks, local URI validation, catalog and mutation checks."""
import importlib.util
import json
import subprocess
import os
import signal
from pathlib import Path
import sys
import tempfile
import types
import unittest
import uuid
from unittest.mock import patch

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / 'shell/helpers/wallpaper_chooser.py'


def load(source=None):
    module = types.ModuleType('wallpaper_chooser_test')
    exec(compile(source or SOURCE.read_text(), str(SOURCE), 'exec'), module.__dict__)
    return module


class Portal:
    """Real GLib variants and event loop; no session bus or host service calls."""
    def __init__(self, module, picture, mode='ready', title='Choose a wallpaper', mime_types=('image/*',)):
        self.module, self.picture, self.mode, self.title = module, picture, mode, title
        self.mime_types = mime_types
        self.callback = None
        self.closed = False
        self.unsubscribed = False
        self.path = module.ROOT + '/request/fixture/legacy_handle'

    def get_unique_name(self):
        return ':1.42'

    def signal_subscribe(self, sender, interface, member, path, arg, flags, callback):
        assert sender == self.module.PORTAL and interface == self.module.REQUEST
        assert member == 'Response' and path is None
        self.callback = callback
        return 1

    def signal_unsubscribe(self, number):
        assert number == 1
        self.unsubscribed = True

    def emit(self, path=None):
        code = 1 if self.mode == 'cancel' else 2 if self.mode == 'failure' else 0
        uris = [self.picture.as_uri()]
        if self.mode == 'multiple':
            uris *= 2
        if self.mode == 'remote':
            uris = [self.picture.as_uri().replace('file:///', 'file://other-host/')]
        parameters = GLib.Variant('(ua{sv})', (code, {'uris': GLib.Variant('as', uris)}))
        self.callback(self, self.module.PORTAL, path or self.path, self.module.REQUEST, 'Response', parameters)

    def call(self, destination, path, interface, method, parameters, reply, flags, timeout, cancel, callback):
        assert self.callback is not None, 'Response subscription must precede OpenFile'
        assert destination == self.module.PORTAL and path == self.module.ROOT
        assert interface == 'org.freedesktop.portal.FileChooser' and method == 'OpenFile'
        parent, title, options = parameters.unpack()
        assert parent == '' and title == self.title, title
        assert options['multiple'] is False and options['directory'] is False
        assert options['filters'] == [('Pictures', [(1, mime) for mime in self.mime_types])]
        assert options['handle_token'].startswith('emaki_wallpaper_')
        assert timeout == 5000

        def dispatch():
            if self.mode == 'early':
                self.emit()
            self.emit(self.module.ROOT + '/request/unrelated/token')
            callback(self, None)
            if self.mode == 'terminate':
                os.kill(os.getpid(), signal.SIGTERM)
            if self.mode not in ('early', 'timeout', 'call-error', 'open-timeout', 'terminate'):
                self.emit()
            return False
        GLib.idle_add(dispatch)

    def call_finish(self, result):
        if self.mode == 'open-timeout':
            raise GLib.Error.new_literal(Gio.io_error_quark(), 'fixture timeout', Gio.IOErrorEnum.TIMED_OUT)
        if self.mode == 'call-error':
            raise RuntimeError('portal failed')
        return GLib.Variant('(o)', (self.path,))

    def call_sync(self, destination, path, interface, method, *args):
        assert destination == self.module.PORTAL
        assert path == self.path or (self.mode == 'open-timeout' and path.startswith(self.module.ROOT + '/request/1_42/emaki_wallpaper_'))
        assert interface == self.module.REQUEST and method == 'Close'
        self.closed = True


def exercise(module, picture, mode):
    portal = Portal(module, picture, mode)
    result = module.choose_on_bus(portal, Gio, GLib, 20)
    assert portal.unsubscribed
    states = {'early': 'ready', 'ready': 'ready', 'cancel': 'cancelled',
              'failure': 'portal_failed', 'call-error': 'portal_failed',
              'multiple': 'invalid_wallpaper', 'remote': 'invalid_wallpaper', 'timeout': 'portal_timeout',
              'open-timeout': 'portal_timeout', 'terminate': 'cancelled'}
    assert result['state'] == states[mode], result
    if result['state'] == 'ready':
        assert result['path'] == str(picture)
    assert portal.closed == (mode in ('timeout', 'open-timeout', 'terminate'))


def serve_portal(address, mode, picture, record):
    """A portal process on an isolated daemon, with real request objects/signals."""
    module = load()
    flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
    bus = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
    bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
                  'RequestName', GLib.Variant('(su)', (module.PORTAL, 0)), None,
                  Gio.DBusCallFlags.NONE, 1000, None)
    info = Gio.DBusNodeInfo.new_for_xml("""<node>
      <interface name="org.freedesktop.portal.FileChooser"><method name="OpenFile">
        <arg type="s" direction="in"/><arg type="s" direction="in"/>
        <arg type="a{sv}" direction="in"/><arg type="o" direction="out"/>
      </method></interface>
      <interface name="org.freedesktop.portal.Request"><method name="Close"/>
        <signal name="Response"><arg type="u"/><arg type="a{sv}"/></signal>
      </interface></node>""")
    registrations = []

    def call(connection, sender, object_path, interface, method, parameters, invocation):
        if method == 'Close':
            with open(record, 'a') as stream:
                stream.write(json.dumps({'closed': object_path}) + '\n')
            invocation.return_value(None)
            return
        parent, title, options = parameters.unpack()
        path = module.ROOT + '/request/' + sender[1:].replace('.', '_') + '/' + options['handle_token']
        if mode == 'legacy':
            path = module.ROOT + '/request/legacy/handle'
        registrations.append(bus.register_object(path, info.interfaces[1], call, None, None))
        with open(record, 'a') as stream:
            stream.write(json.dumps({'parent': parent, 'title': title, 'options': options}) + '\n')

        def emit():
            bus.emit_signal(sender, path, module.REQUEST, 'Response',
                            GLib.Variant('(ua{sv})', (1 if mode == 'cancel' else 0,
                                         {'uris': GLib.Variant('as', [Path(picture).as_uri()])})))
            return False

        if mode == 'early':
            emit()
        invocation.return_value(GLib.Variant('(o)', (path,)))
        if mode not in ('early', 'timeout'):
            GLib.timeout_add(10, emit)

    registrations.append(bus.register_object(module.ROOT, info.interfaces[0], call, None, None))
    print('ready', flush=True)
    GLib.MainLoop().run()


class Chooser(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='wallpaper-')
        self.addCleanup(self.folder.cleanup)
        self.picture = Path(self.folder.name) / 'picture space % café.png'
        self.picture.write_bytes(b'fixture')
        self.module = load()

    def test_portal_callbacks(self):
        for mode in ('ready', 'early', 'cancel', 'failure', 'call-error', 'multiple', 'remote', 'timeout', 'open-timeout', 'terminate'):
            with self.subTest(mode=mode):
                exercise(self.module, self.picture, mode)

    def test_real_bus_request_lifecycle(self):
        daemon = subprocess.Popen(['dbus-daemon', '--session', '--nofork',
                                   '--address=unix:abstract=emaki-chooser-' + uuid.uuid4().hex, '--print-address=1'],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(daemon.communicate, timeout=5)
        self.addCleanup(daemon.terminate)
        address = daemon.stdout.readline().strip()
        if not address:
            _, error = daemon.communicate(timeout=5)
            self.fail('Private D-Bus failed: ' + error.strip())
        flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        bus = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
        self.addCleanup(bus.close_sync, None)
        accepted = ('image/png', 'image/jpeg', 'image/webp')
        for mode in ('ready', 'early', 'legacy', 'cancel', 'timeout'):
            with self.subTest(mode=mode):
                record = Path(self.folder.name) / (mode + '.jsonl')
                server = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--portal-fixture',
                                           address, mode, str(self.picture), str(record)],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    self.assertEqual(server.stdout.readline().strip(), 'ready')
                    result = self.module.choose_on_bus(bus, Gio, GLib, 200,
                                                       'Choose your picture', accepted)
                    expected = {'cancel': 'cancelled', 'timeout': 'portal_timeout'}.get(mode, 'ready')
                    self.assertEqual(result['state'], expected)
                    if expected == 'ready':
                        self.assertEqual(result['path'], str(self.picture))
                    calls = [json.loads(line) for line in record.read_text().splitlines()]
                    self.assertEqual(calls[0]['options']['filters'],
                                     [['Pictures', [[1, mime] for mime in accepted]]])
                    self.assertEqual(calls[0]['parent'], '')
                    self.assertEqual(calls[0]['title'], 'Choose your picture')
                    self.assertEqual(len(calls), 2 if mode == 'timeout' else 1)
                    if mode == 'timeout':
                        self.assertTrue(calls[1]['closed'].endswith(calls[0]['options']['handle_token']))
                finally:
                    server.terminate()
                    server.communicate(timeout=5)

    def test_account_picture_title(self):
        mime_types = ('image/png', 'image/jpeg', 'image/webp')
        portal = Portal(self.module, self.picture, title='Choose your picture', mime_types=mime_types)
        result = self.module.choose_on_bus(portal, Gio, GLib, 20, 'Choose your picture', mime_types)
        self.assertEqual(result, {'state': 'ready', 'path': str(self.picture)})
        # The wallpaper keeps its title when none is given (the fixture refuses another title).
        result = self.module.choose_on_bus(Portal(self.module, self.picture, title='Choose your picture'), Gio, GLib, 20)
        self.assertEqual(result, {'state': 'portal_failed'})
        with patch.object(Gio, 'bus_get_sync', return_value='bus'), \
                patch.object(self.module, 'choose_on_bus', return_value={'state': 'cancelled'}) as chooser:
            self.module.choose()
            self.module.choose(title='Choose your picture')
        self.assertEqual([call.args[4] for call in chooser.call_args_list], ['Choose a wallpaper', 'Choose your picture'])

    def test_private_operations_name_their_chooser(self):
        paths = types.ModuleType('emaki_paths')
        chooser = types.ModuleType('wallpaper_chooser')
        chooser.choose = lambda **options: dict(state='ready', options=options)
        modules = {'emaki_paths': paths, 'app_scope': types.ModuleType('app_scope'),
                   'clipboard_store': types.ModuleType('clipboard_store'), 'wallpaper_chooser': chooser}
        spec = importlib.util.spec_from_file_location('chooser_operations', ROOT / 'shell/helpers/launcher-tools.py')
        tools = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(tools)
            self.assertEqual(tools.handle({'op': 'wallpaper-choose'}), {'state': 'ready', 'options': {}})
            self.assertEqual(tools.handle({'op': 'account-picture-choose'}),
                             {'state': 'ready', 'options': {'title': 'Choose your picture',
                                                       'mime_types': ('image/png', 'image/jpeg', 'image/webp')}})

    def test_missing_session_bus(self):
        with patch.object(Gio, 'bus_get_sync', side_effect=RuntimeError('fixture unavailable')):
            self.assertEqual(self.module.choose(), {'state': 'portal_unavailable'})

    def test_uris(self):
        self.assertEqual(self.module.local_picture(self.picture.as_uri()), str(self.picture))
        self.assertEqual(self.module.local_picture(self.picture.as_uri().replace('file:///', 'file://localhost/')), str(self.picture))
        for uri in ('https://host/p.png', 'file://remote/tmp/p.png', 'file://user@localhost/tmp/p.png',
                    'file://localhost:123/tmp/p.png', 'file:relative.png', 'file:////server/p.png',
                    'file:///tmp/%00.png', 'file:///tmp/%GG.png', 'file:///tmp/%ff.png',
                    'file:///tmp/new%0aline.png', 'file:///tmp/%22.png', 'file:///tmp/%5c.png',
                    'file:///missing/picture.png', self.picture.as_uri() + '?download=1',
                    self.picture.as_uri() + '#fragment', 'file:///tmp/raw space.png'):
            with self.subTest(uri=uri), self.assertRaises((ValueError, UnicodeError)):
                self.module.local_picture(uri)
        with self.assertRaises(ValueError):
            self.module.local_picture(Path(self.folder.name).as_uri())

    def test_mutants(self):
        mutations = [
            ('early-response', 'if handle in early:', 'if False:', 'early'),
            ('cancel', "finish(dict(state='cancelled'))", "finish(dict(state='portal_failed'))", 'cancel'),
            ('single-file', "or len(uris) != 1", "or len(uris) < 1", 'multiple'),
            ('remote-host', "value.netloc not in ('', 'localhost')", 'False', 'remote'),
            ('close-timeout', "bus.call_sync(PORTAL, handle or predicted, REQUEST, 'Close', None, None,", "None if True else bus.call_sync(PORTAL, handle or predicted, REQUEST, 'Close', None, None,", 'timeout'),
            ('request-filter', "[(1, mime) for mime in mime_types]", "[(0, '*')]", 'ready'),
        ]
        original = SOURCE.read_text()
        for name, before, after, mode in mutations:
            with self.subTest(mutant=name):
                self.assertIn(before, original)
                mutant = load(original.replace(before, after, 1))
                with self.assertRaises(AssertionError):
                    exercise(mutant, self.picture, mode)

    def test_catalog_installed_wallpaper_and_user_pictures(self):
        root = Path(self.folder.name)
        shipped = root / 'install/wallpaper'
        shipped.mkdir(parents=True)
        for name in ('fallback.png', 'ring.png', 'ring-front.png', 'train-frames.png'):
            (shipped / name).write_bytes(b'fixture')
        extra = root / 'data/emaki/wallpapers'
        extra.mkdir(parents=True)
        (extra / 'personal.jpg').write_bytes(b'fixture')
        (extra / 'notes.txt').write_text('fixture')
        (extra / 'fallback.png').write_bytes(b'duplicate')
        paths = types.ModuleType('emaki_paths')
        paths.DATADIR = str(root / 'install')
        paths.XDG_DATA_DIRS_DEFAULT = str(root / 'empty')
        modules = {'emaki_paths': paths, 'app_scope': types.ModuleType('app_scope'),
                   'clipboard_store': types.ModuleType('clipboard_store')}
        spec = importlib.util.spec_from_file_location('wallpaper_catalog', ROOT / 'shell/helpers/launcher-tools.py')
        catalog = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, modules), patch.dict(os.environ, {'XDG_DATA_HOME': str(root / 'data'), 'XDG_DATA_DIRS': str(root / 'empty'), 'XDG_CONFIG_HOME': str(root / 'config')}):
            spec.loader.exec_module(catalog)
            self.assertEqual(catalog.handle({'op': 'wallpapers'}), {'state': 'ready', 'entries': [
                {'path': str(shipped / 'fallback.png'), 'name': 'Emaki landscape'},
                {'path': str(extra / 'personal.jpg'), 'name': 'personal'}], 'inheritedPath': ''})
            config = root / 'config/wpaperd/config.toml'
            config.parent.mkdir(parents=True)
            config.write_text('[default]\npath = ' + repr(str(shipped / 'fallback.png')) + '\n')
            self.assertEqual(catalog.handle({'op': 'wallpapers'})['inheritedPath'], str(shipped / 'fallback.png'))
            config.write_text('[default]\npath = ' + repr(str(extra / 'personal.jpg')) + '\n')
            self.assertEqual(catalog.handle({'op': 'wallpapers'})['inheritedPath'], str(extra / 'personal.jpg'))
            with config.open('a') as stream:
                stream.write('[DP-1]\npath = ' + repr(str(shipped / 'fallback.png')) + '\n')
            self.assertEqual(catalog.handle({'op': 'wallpapers'})['inheritedPath'], '')
            for contents in ('[default]\npath = "relative.png"\n', '[default]\npath = "' + str(extra) + '"\n', 'invalid ['):
                config.write_text(contents)
                self.assertEqual(catalog.handle({'op': 'wallpapers'})['inheritedPath'], '')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--portal-fixture':
        serve_portal(*sys.argv[2:])
    else:
        unittest.main()
