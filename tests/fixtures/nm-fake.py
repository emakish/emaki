#!/usr/bin/env python3
"""Minimal NetworkManager stand-in on a private bus.

Usage: nm-fake.py <root>. Two users:
- the hidden-network helper (AddAndActivateConnection): reads <root>/nm-mode (ok | no_secrets |
  error | no_wifi), records the received connection settings into <root>/nm-request.json and
  profile deletions into <root>/nm-deleted;
- Quickshell's own NetworkManager backend (SystemNative): a Wi-Fi device wlan0 with one saved
  network and two unsaved ones in range, every property QS 0.3.1 binds, and RequestScan calls
  recorded into <root>/nm-scans (one device path per line). org.emaki.FakeNM.AddWifi/RemoveWifi
  plug a second adapter wlan1 (one open network) in and out. Native joins persist profiles
  before activation; rejected secrets emit device NoSecrets then active DeviceDisconnected.
  Update replaces a saved secret, Delete removes a profile, and nm-native.json records only
  profile identities and D-Bus method names (no secrets).
Never touches a real NetworkManager.
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
WIFI2 = ROOT_PATH + '/Devices/4'
OTHER = ROOT_PATH + '/Devices/1'
ACTIVE = ROOT_PATH + '/ActiveConnection/1'
PROFILE = ROOT_PATH + '/Settings/1'
SAVED = ROOT_PATH + '/Settings/2'
TEST = 'org.emaki.FakeNM'
# NM_802_11_AP_SEC_PAIR_CCMP | GROUP_CCMP | KEY_MGMT_PSK: WPA2-PSK.
RSN_PSK = 0x8 | 0x80 | 0x100
# path: (device, SSID, strength, secured)
APS = {
    ROOT_PATH + '/AccessPoint/1': (WIFI, 'PRIVATE_SAVED', 70, True),
    ROOT_PATH + '/AccessPoint/2': (WIFI, 'PRIVATE_NEAR', 50, True),
    ROOT_PATH + '/AccessPoint/3': (WIFI, 'PRIVATE_CAFE', 30, False),
    ROOT_PATH + '/AccessPoint/4': (WIFI2, 'PRIVATE_FAR', 40, False),
}
XML = f'''<node>
<interface name="{NM}">
  <method name="AddAndActivateConnection">
    <arg type="a{{sa{{sv}}}}" name="connection" direction="in"/><arg type="o" name="device" direction="in"/>
    <arg type="o" name="specific_object" direction="in"/><arg type="o" name="path" direction="out"/>
    <arg type="o" name="active_connection" direction="out"/>
  </method>
  <method name="ActivateConnection">
    <arg type="o" direction="in"/><arg type="o" direction="in"/><arg type="o" direction="in"/>
    <arg type="o" direction="out"/>
  </method>
  <method name="GetAllDevices"><arg type="ao" name="devices" direction="out"/></method>
  <method name="CheckConnectivity"><arg type="u" name="connectivity" direction="out"/></method>
  <signal name="DeviceAdded"><arg type="o" name="device_path"/></signal>
  <signal name="DeviceRemoved"><arg type="o" name="device_path"/></signal>
  <property name="Devices" type="ao" access="read"/>
  <property name="WirelessEnabled" type="b" access="read"/>
  <property name="WirelessHardwareEnabled" type="b" access="read"/>
  <property name="Connectivity" type="u" access="read"/>
  <property name="ConnectivityCheckAvailable" type="b" access="read"/>
  <property name="ConnectivityCheckEnabled" type="b" access="read"/>
</interface>
<interface name="{NM}.Device">
  <method name="Disconnect"/>
  <signal name="StateChanged"><arg type="u" name="new_state"/><arg type="u" name="old_state"/><arg type="u" name="reason"/></signal>
  <property name="DeviceType" type="u" access="read"/>
  <property name="Interface" type="s" access="read"/>
  <property name="HwAddress" type="s" access="read"/>
  <property name="Managed" type="b" access="read"/>
  <property name="State" type="u" access="read"/>
  <property name="Autoconnect" type="b" access="read"/>
  <property name="AvailableConnections" type="ao" access="read"/>
  <property name="ActiveConnection" type="o" access="read"/>
  <property name="InterfaceFlags" type="u" access="read"/>
</interface>
<interface name="{NM}.Device.Wireless">
  <method name="GetAllAccessPoints"><arg type="ao" name="access_points" direction="out"/></method>
  <method name="RequestScan"><arg type="a{{sv}}" name="options" direction="in"/></method>
  <signal name="AccessPointAdded"><arg type="o" name="access_point"/></signal>
  <signal name="AccessPointRemoved"><arg type="o" name="access_point"/></signal>
  <property name="LastScan" type="x" access="read"/>
  <property name="WirelessCapabilities" type="u" access="read"/>
  <property name="ActiveAccessPoint" type="o" access="read"/>
  <property name="Mode" type="u" access="read"/>
</interface>
<interface name="{NM}.AccessPoint">
  <property name="Ssid" type="ay" access="read"/>
  <property name="Strength" type="y" access="read"/>
  <property name="Flags" type="u" access="read"/>
  <property name="WpaFlags" type="u" access="read"/>
  <property name="RsnFlags" type="u" access="read"/>
  <property name="Mode" type="u" access="read"/>
</interface>
<interface name="{NM}.Connection.Active">
  <property name="State" type="u" access="read"/>
  <property name="Connection" type="o" access="read"/>
  <signal name="StateChanged"><arg type="u" name="state"/><arg type="u" name="reason"/></signal>
</interface>
<interface name="{NM}.Settings.Connection">
  <method name="GetSettings"><arg type="a{{sa{{sv}}}}" name="settings" direction="out"/></method>
  <method name="Update"><arg type="a{{sa{{sv}}}}" direction="in"/></method>
  <method name="Delete"/>
  <signal name="Updated"/>
  <signal name="Removed"/>
</interface>
<interface name="{TEST}"><method name="AddWifi"/><method name="RemoveWifi"/></interface>
</node>'''
info = Gio.DBusNodeInfo.new_for_xml(XML)
state = {'active': 1, 'wifi2': False, 'profile': PROFILE, 'active_path': '/', 'ap': '/', 'device_state': 30}


def profile_settings(ssid, serial):
    return {
        'connection': {'id': ssid, 'uuid': f'22222222-3333-4444-5555-{serial:012d}',
                       'type': '802-11-wireless', 'timestamp': 1},
        '802-11-wireless': {'ssid': list(ssid.encode()), 'mode': 'infrastructure'},
        '802-11-wireless-security': {'key-mgmt': 'wpa-psk'},
    }


profiles = {SAVED: profile_settings('PRIVATE_SAVED', 2)}
secrets = {SAVED: 'outdated-password'}
calls = []
serial = 10


def record(method, path):
    calls.append([method, path])
    # Only profile identities and method names: never serialize a native join's secret.
    (root / 'nm-native.json').write_text(json.dumps({
        'profiles': {p: v['connection']['id'] for p, v in profiles.items()}, 'calls': calls}))


def changed(conn, path, iface, values):
    conn.emit_signal(None, path, 'org.freedesktop.DBus.Properties', 'PropertiesChanged',
                     GLib.Variant('(sa{sv}as)', (iface, values, [])))


def available(conn):
    changed(conn, WIFI, NM + '.Device', {'AvailableConnections': GLib.Variant('ao', list(profiles))})


def activate(conn, profile, ap):
    state.update(active=1, profile=profile, active_path=ACTIVE, ap=ap, device_state=40)
    changed(conn, ACTIVE, NM + '.Connection.Active', {'State': GLib.Variant('u', 1), 'Connection': GLib.Variant('o', profile)})
    changed(conn, WIFI, NM + '.Device', {'ActiveConnection': GLib.Variant('o', ACTIVE), 'State': GLib.Variant('u', 40)})
    changed(conn, WIFI, NM + '.Device.Wireless', {'ActiveAccessPoint': GLib.Variant('o', ap)})
    def finish():
        # Native QS needs device NoSecrets (7), then active DeviceDisconnected (3).
        # The hidden helper separately consumes active NoSecrets (9).
        reason = {'auth_timeout': 11, 'network_lost': 53, 'client_failed': 10}.get(mode(), 7)
        ok = mode() == 'ok' and secrets.get(profile) == 'fixture-password'
        state.update(active=2 if ok else 4, device_state=100 if ok else 120)
        conn.emit_signal(None, WIFI, NM + '.Device', 'StateChanged', GLib.Variant('(uuu)', (state['device_state'], 40, 0 if ok else reason)))
        changed(conn, ACTIVE, NM + '.Connection.Active', {'State': GLib.Variant('u', state['active'])})
        conn.emit_signal(None, ACTIVE, NM + '.Connection.Active', 'StateChanged', GLib.Variant('(uu)', (state['active'], 1 if ok else 3)))
        if not ok:
            # Leave the profile saved after failure, just as AddAndActivateConnection does.
            disconnect(conn)
        return False
    GLib.timeout_add(300, finish)


def disconnect(conn):
    state.update(active_path='/', ap='/', device_state=30)
    changed(conn, WIFI, NM + '.Device', {'ActiveConnection': GLib.Variant('o', '/'), 'State': GLib.Variant('u', 30)})
    changed(conn, WIFI, NM + '.Device.Wireless', {'ActiveAccessPoint': GLib.Variant('o', '/')})
    return False



def mode():
    return (root / 'nm-mode').read_text().strip() if (root / 'nm-mode').exists() else 'ok'


def devices():
    if mode() == 'no_wifi':
        return [OTHER]
    return [OTHER, WIFI] + ([WIFI2] if state['wifi2'] else [])


def method_call(conn, sender, path, iface, method, params, invocation):
    global serial
    if method == 'AddAndActivateConnection':
        settings, device, specific = params.unpack()
        if specific in APS:
            profile = ROOT_PATH + f'/Settings/{serial}'
            profiles[profile] = profile_settings(APS[specific][1], serial)
            serial += 1
            secrets[profile] = settings.get('802-11-wireless-security', {}).get('psk')
            conn.register_object_with_closures2(profile, info.lookup_interface(NM + '.Settings.Connection'), method_call, get_property, None)
            record(method, profile)
            available(conn)
            invocation.return_value(GLib.Variant('(oo)', (profile, ACTIVE)))
            activate(conn, profile, specific)
            return
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
    elif method == 'ActivateConnection':
        profile, device, specific = params.unpack()
        assert profile in profiles and device == WIFI
        ssid = profiles[profile]['connection']['id']
        ap = next(p for p, v in APS.items() if v[1] == ssid)
        record(method, profile)
        invocation.return_value(GLib.Variant('(o)', (ACTIVE,)))
        activate(conn, profile, ap)
    elif method == 'Update':
        settings = params.unpack()[0]
        assert settings['connection']['uuid'] == profiles[path]['connection']['uuid']
        secrets[path] = settings.get('802-11-wireless-security', {}).pop('psk', None)
        profiles[path] = settings
        record(method, path)
        conn.emit_signal(None, path, NM + '.Settings.Connection', 'Updated', None)
        invocation.return_value(None)
    elif method == 'Delete':
        with (root / 'nm-deleted').open('a') as f:
            f.write(path + '\n')
        if path in profiles:
            del profiles[path]
            secrets.pop(path, None)
            record(method, path)
            conn.emit_signal(None, path, NM + '.Settings.Connection', 'Removed', None)
            available(conn)
        invocation.return_value(None)
    elif method == 'GetAllDevices':
        invocation.return_value(GLib.Variant('(ao)', (devices(),)))
    elif method == 'CheckConnectivity':
        invocation.return_value(GLib.Variant('(u)', (4,)))
    elif method == 'GetAllAccessPoints':
        invocation.return_value(GLib.Variant('(ao)', ([ap for ap, (device, *_) in APS.items() if device == path],)))
    elif method == 'RequestScan':
        with (root / 'nm-scans').open('a') as f:
            f.write(path + '\n')
        invocation.return_value(None)
    elif method == 'GetSettings':
        types = {'timestamp': 't', 'ssid': 'ay'}
        settings = {group: {key: GLib.Variant(types.get(key, 's'), value) for key, value in values.items()}
                    for group, values in profiles[path].items()}
        invocation.return_value(GLib.Variant('(a{sa{sv}})', (settings,)))
    elif method == 'Disconnect':
        disconnect(conn)
        invocation.return_value(None)
    elif method in ('AddWifi', 'RemoveWifi'):
        state['wifi2'] = method == 'AddWifi'
        conn.emit_signal(None, ROOT_PATH, NM, 'DeviceAdded' if state['wifi2'] else 'DeviceRemoved', GLib.Variant('(o)', (WIFI2,)))
        invocation.return_value(None)
    else:
        invocation.return_dbus_error('org.freedesktop.DBus.Error.UnknownMethod', method)


def get_property(conn, sender, path, iface, name):
    if iface == NM:
        values = dict(Devices=GLib.Variant('ao', devices()), WirelessEnabled=GLib.Variant('b', True),
                      WirelessHardwareEnabled=GLib.Variant('b', True), Connectivity=GLib.Variant('u', 4),
                      ConnectivityCheckAvailable=GLib.Variant('b', False), ConnectivityCheckEnabled=GLib.Variant('b', False))
    elif iface == NM + '.Device':
        wifi = path in (WIFI, WIFI2)
        # Native activation publishes device state, saved profiles and the active connection.
        values = dict(DeviceType=GLib.Variant('u', 2 if wifi else 14), Interface=GLib.Variant('s', {WIFI: 'wlan0', WIFI2: 'wlan1'}.get(path, 'eth9')),
                      HwAddress=GLib.Variant('s', '02:00:00:00:00:0' + path[-1]), Managed=GLib.Variant('b', True),
                      State=GLib.Variant('u', state['device_state'] if path == WIFI else 30), Autoconnect=GLib.Variant('b', True),
                      AvailableConnections=GLib.Variant('ao', list(profiles) if path == WIFI else []),
                      ActiveConnection=GLib.Variant('o', state['active_path'] if path == WIFI else '/'), InterfaceFlags=GLib.Variant('u', 0))
    elif iface == NM + '.Device.Wireless':
        # LastScan -1: never scanned. Capabilities: WEP40|WEP104|TKIP|CCMP|WPA|RSN. Mode 2: infrastructure.
        values = dict(LastScan=GLib.Variant('x', -1), WirelessCapabilities=GLib.Variant('u', 0x3f),
                      ActiveAccessPoint=GLib.Variant('o', state['ap'] if path == WIFI else '/'), Mode=GLib.Variant('u', 2))
    elif iface == NM + '.AccessPoint':
        _device, ssid, strength, secured = APS[path]
        values = dict(Ssid=GLib.Variant('ay', ssid.encode()), Strength=GLib.Variant('y', strength),
                      Flags=GLib.Variant('u', 1 if secured else 0), WpaFlags=GLib.Variant('u', 0),
                      RsnFlags=GLib.Variant('u', RSN_PSK if secured else 0), Mode=GLib.Variant('u', 2))
    elif iface == NM + '.Connection.Active':
        values = dict(State=GLib.Variant('u', state['active']), Connection=GLib.Variant('o', state['profile']))
    else:
        values = {}
    return values.get(name)


def on_bus(conn, name):
    objects = [(ROOT_PATH, [NM, TEST]), (WIFI, [NM + '.Device', NM + '.Device.Wireless']), (WIFI2, [NM + '.Device', NM + '.Device.Wireless']),
               (OTHER, [NM + '.Device']), (ACTIVE, [NM + '.Connection.Active']), (PROFILE, [NM + '.Settings.Connection']),
               (SAVED, [NM + '.Settings.Connection'])] + [(ap, [NM + '.AccessPoint']) for ap in APS]
    for path, ifaces in objects:
        for iface in ifaces:
            conn.register_object_with_closures2(path, info.lookup_interface(iface), method_call, get_property, None)
    (root / 'nm-ready').write_text('1')


Gio.bus_own_name(Gio.BusType.SESSION, NM, Gio.BusNameOwnerFlags.NONE, on_bus, None, lambda *_: sys.exit(3))
GLib.MainLoop().run()
