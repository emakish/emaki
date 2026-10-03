#!/usr/bin/env python3
"""Minimal NetworkManager stand-in for AddAndActivateConnection on a private bus.

Usage: nm-fake.py <root>. Reads <root>/nm-mode (ok | no_secrets | error | no_wifi),
records the received connection settings into <root>/nm-request.json and profile
deletions into <root>/nm-deleted. Never touches a real NetworkManager.
"""
import json
import sys
from pathlib import Path

import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib  # noqa: E402

root = Path(sys.argv[1])
NM = 'org.freedesktop.NetworkManager'
ROOT_PATH = '/org/freedesktop/NetworkManager'
WIFI = ROOT_PATH + '/Devices/3'
OTHER = ROOT_PATH + '/Devices/1'
ACTIVE = ROOT_PATH + '/ActiveConnection/1'
PROFILE = ROOT_PATH + '/Settings/1'
XML = f'''<node>
<interface name="{NM}">
  <method name="AddAndActivateConnection">
    <arg type="a{{sa{{sv}}}}" name="connection" direction="in"/><arg type="o" name="device" direction="in"/>
    <arg type="o" name="specific_object" direction="in"/><arg type="o" name="path" direction="out"/>
    <arg type="o" name="active_connection" direction="out"/>
  </method>
  <property name="Devices" type="ao" access="read"/>
</interface>
<interface name="{NM}.Device"><property name="DeviceType" type="u" access="read"/></interface>
<interface name="{NM}.Connection.Active">
  <property name="State" type="u" access="read"/>
  <signal name="StateChanged"><arg type="u" name="state"/><arg type="u" name="reason"/></signal>
</interface>
<interface name="{NM}.Settings.Connection"><method name="Delete"/></interface>
</node>'''
info = Gio.DBusNodeInfo.new_for_xml(XML)
state = {'active': 1}


def mode():
    return (root / 'nm-mode').read_text().strip() if (root / 'nm-mode').exists() else 'ok'


def method_call(conn, sender, path, iface, method, params, invocation):
    if method == 'AddAndActivateConnection':
        settings, device, specific = params.unpack()
        wireless = settings.get('802-11-wireless', {})
        security = settings.get('802-11-wireless-security', {})
        (root / 'nm-request.json').write_text(json.dumps(dict(
            ssid=bytes(wireless.get('ssid', b'')).decode(), hidden=wireless.get('hidden'), mode=wireless.get('mode'),
            key_mgmt=security.get('key-mgmt'), psk=security.get('psk'), device=device, specific=specific,
            id=settings.get('connection', {}).get('id'), type=settings.get('connection', {}).get('type'))))
        if mode() == 'error':
            invocation.return_dbus_error(NM + '.Device.NotAllowed', 'fixture refusal')
            return
        state['active'] = 1
        invocation.return_value(GLib.Variant('(oo)', (PROFILE, ACTIVE)))
        def finish():
            outcome = (2, 1) if mode() == 'ok' else (4, 9)
            state['active'] = outcome[0]
            conn.emit_signal(None, ACTIVE, NM + '.Connection.Active', 'StateChanged', GLib.Variant('(uu)', outcome))
            return False
        GLib.timeout_add(200, finish)
    elif method == 'Delete':
        with (root / 'nm-deleted').open('a') as f:
            f.write(path + '\n')
        invocation.return_value(None)
    else:
        invocation.return_dbus_error('org.freedesktop.DBus.Error.UnknownMethod', method)


def get_property(conn, sender, path, iface, name):
    if name == 'Devices':
        return GLib.Variant('ao', [OTHER] if mode() == 'no_wifi' else [OTHER, WIFI])
    if name == 'DeviceType':
        return GLib.Variant('u', 2 if path == WIFI else 14)
    if name == 'State':
        return GLib.Variant('u', state['active'])
    return None


def on_bus(conn, name):
    for path, ifaces in ((ROOT_PATH, [NM]), (WIFI, [NM + '.Device']), (OTHER, [NM + '.Device']), (ACTIVE, [NM + '.Connection.Active']), (PROFILE, [NM + '.Settings.Connection'])):
        for iface in ifaces:
            conn.register_object_with_closures2(path, info.lookup_interface(iface), method_call, get_property, None)
    (root / 'nm-ready').write_text('1')


Gio.bus_own_name(Gio.BusType.SESSION, NM, Gio.BusNameOwnerFlags.NONE, on_bus, None, lambda *_: sys.exit(3))
GLib.MainLoop().run()
