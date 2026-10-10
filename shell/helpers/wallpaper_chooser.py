#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read a single local picture selection from the desktop file chooser portal."""
import os
from pathlib import Path
import re
import secrets
import signal as process_signal
from urllib.parse import unquote, urlsplit

PORTAL = 'org.freedesktop.portal.Desktop'
ROOT = '/org/freedesktop/portal/desktop'
REQUEST = 'org.freedesktop.portal.Request'


def local_picture(uri):
    """Decode once and match the settings core's local-path restrictions."""
    if not isinstance(uri, str) or re.search(r'[\x00-\x20\x7f]', uri):
        raise ValueError('invalid URI')
    if re.search(r'%(?![0-9a-fA-F]{2})', uri):
        raise ValueError('invalid escape')
    value = urlsplit(uri)
    if value.scheme != 'file' or value.netloc not in ('', 'localhost') or value.query or value.fragment:
        raise ValueError('not a local file')
    path = unquote(value.path, encoding='utf-8', errors='strict')
    if (not os.path.isabs(path) or path.startswith('//') or len(path.encode('utf-8')) > 4096
            or re.search(r'["\\\x00-\x1f\x7f]', path) or not Path(path).is_file()):
        raise ValueError('invalid picture path')
    return path


def choose(timeout_ms=120000):
    try:
        import gi
        gi.require_version('Gio', '2.0')
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except Exception:
        return dict(state='portal_unavailable')
    return choose_on_bus(bus, Gio, GLib, timeout_ms)


def choose_on_bus(bus, Gio, GLib, timeout_ms):
    loop = GLib.MainLoop()
    token = 'emaki_wallpaper_' + secrets.token_hex(12)
    predicted = ROOT + '/request/' + bus.get_unique_name()[1:].replace('.', '_') + '/' + token
    handle = None
    early = {}
    outcome = None
    expired = False
    timer_fired = False

    def finish(value):
        nonlocal outcome
        if outcome is None:
            outcome = value
            loop.quit()

    def response(parameters):
        try:
            code, results = parameters.unpack()
            if code == 1:
                finish(dict(state='cancelled'))
            elif code != 0:
                finish(dict(state='portal_failed'))
            else:
                uris = results.get('uris')
                if not isinstance(uris, (list, tuple)) or len(uris) != 1:
                    raise ValueError('expected one picture')
                finish(dict(state='ready', path=local_picture(uris[0])))
        except (ValueError, TypeError, UnicodeError):
            finish(dict(state='invalid_wallpaper'))

    def signal(connection, sender, path, interface, member, parameters):
        if handle is None:
            # A portal may emit Response before returning the request object path.
            if len(early) < 16:
                early[path] = parameters
        elif path == handle:
            response(parameters)

    def opened(connection, result):
        nonlocal handle, expired
        try:
            handle = connection.call_finish(result).unpack()[0]
            if not isinstance(handle, str) or not handle.startswith(ROOT + '/request/'):
                raise ValueError('invalid request handle')
            if handle in early:
                response(early[handle])
        except GLib.Error as error:
            if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT):
                expired = True
                finish(dict(state='portal_timeout'))
            else:
                finish(dict(state='portal_failed'))
        except Exception:
            finish(dict(state='portal_failed'))

    def timeout():
        nonlocal expired, timer_fired
        expired = True
        timer_fired = True
        finish(dict(state='portal_timeout'))
        return False

    def terminate():
        nonlocal expired
        expired = True
        finish(dict(state='cancelled'))
        return True

    # Subscribe before OpenFile, including older portals that choose another handle.
    subscription = bus.signal_subscribe(PORTAL, REQUEST, 'Response', None, None,
                                        Gio.DBusSignalFlags.NONE, signal)
    timer = GLib.timeout_add(timeout_ms, timeout)
    signals = [GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, number, terminate)
               for number in (process_signal.SIGTERM, process_signal.SIGINT)]
    try:
        options = {
            'handle_token': GLib.Variant('s', token),
            'multiple': GLib.Variant('b', False),
            'directory': GLib.Variant('b', False),
            'filters': GLib.Variant('a(sa(us))', [('Pictures', [(1, 'image/*')])]),
        }
        bus.call(PORTAL, ROOT, 'org.freedesktop.portal.FileChooser', 'OpenFile',
                 GLib.Variant('(ssa{sv})', ('', 'Choose a wallpaper', options)),
                 GLib.VariantType.new('(o)'), Gio.DBusCallFlags.NONE, 5000, None, opened)
        if outcome is None:
            loop.run()
    except Exception:
        finish(dict(state='portal_failed'))
    finally:
        bus.signal_unsubscribe(subscription)
        if not timer_fired:
            GLib.source_remove(timer)
        for source in signals:
            GLib.source_remove(source)
        if expired:
            try:
                bus.call_sync(PORTAL, handle or predicted, REQUEST, 'Close', None, None,
                              Gio.DBusCallFlags.NONE, 1000, None)
            except Exception:
                pass
    return outcome
