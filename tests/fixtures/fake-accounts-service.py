#!/usr/bin/env python3
# Copyright (C) 2026 Artur Yakymenko
# SPDX-License-Identifier: GPL-3.0-or-later
"""Synthetic AccountsService and logind; only callable inside an isolated test bus.

  fake-accounts-service.py STATE PASSWD LOG READY

STATE is JSON: users {uid: properties}, sessions [uid], deny [method], fail [method].
Mutations update STATE and the passwd-format file PASSWD the helper lists accounts from;
every call is appended to LOG as JSON with its arguments and whether the caller allowed an
interactive authentication.
"""
import json
from pathlib import Path
import sys
import gi
gi.require_version('Gio', '2.0')
from gi.repository import Gio, GLib

state_path, passwd_path, log_path, ready_path = map(Path, sys.argv[1:5])
state = json.loads(state_path.read_text())
XML = '''<node>
<interface name="org.freedesktop.Accounts">
 <method name="FindUserById"><arg type="x" direction="in"/><arg type="o" direction="out"/></method>
 <method name="CreateUser"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="i" direction="in"/><arg type="o" direction="out"/></method>
 <method name="DeleteUser"><arg type="x" direction="in"/><arg type="b" direction="in"/></method>
</interface>
<interface name="org.freedesktop.Accounts.User">
 <method name="SetRealName"><arg type="s" direction="in"/></method>
 <method name="SetPassword"><arg type="s" direction="in"/><arg type="s" direction="in"/></method>
 <method name="SetAccountType"><arg type="i" direction="in"/></method>
 <property name="UserName" type="s" access="read"/>
 <property name="RealName" type="s" access="read"/>
 <property name="AccountType" type="i" access="read"/>
 <property name="Locked" type="b" access="read"/>
 <property name="SystemAccount" type="b" access="read"/>
 <property name="HomeDirectory" type="s" access="read"/>
 <property name="Uid" type="t" access="read"/>
</interface>
<interface name="org.freedesktop.login1.Manager">
 <method name="ListUsers"><arg type="a(uso)" direction="out"/></method>
</interface>
</node>'''
node = Gio.DBusNodeInfo.new_for_xml(XML)
accounts, user_interface, login = node.interfaces
TYPES = {'UserName': 's', 'RealName': 's', 'AccountType': 'i', 'Locked': 'b', 'SystemAccount': 'b',
         'HomeDirectory': 's', 'Uid': 't'}
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
registered = {}


def save():
    state_path.write_text(json.dumps(state))
    lines = [f"{user['UserName']}:x:{uid}:{uid}::{user['HomeDirectory']}:/bin/bash"
             for uid, user in sorted(state['users'].items(), key=lambda item: int(item[0]))]
    passwd_path.write_text('\n'.join(lines + state.get('extra_passwd', [])) + '\n')


def path(uid):
    return f'/org/freedesktop/Accounts/User{uid}'


def register(uid):
    registered[uid] = bus.register_object(path(uid), user_interface, method, get, None)


def error(invocation, name, text):
    invocation.return_dbus_error(name, text)


def method(_conn, _sender, object_path, interface, name, args, invocation):
    interactive = bool(invocation.get_message().get_flags() & Gio.DBusMessageFlags.ALLOW_INTERACTIVE_AUTHORIZATION)
    with log_path.open('a') as stream:
        stream.write(json.dumps({'method': name, 'path': object_path, 'args': args.unpack(),
                                 'interactive': interactive}) + '\n')
    if name in state.get('deny', []):
        return error(invocation, 'org.freedesktop.Accounts.Error.PermissionDenied', 'Not authorized')
    if name in state.get('fail', []):
        return error(invocation, 'org.freedesktop.Accounts.Error.Failed', 'running a tool failed: private detail')
    if name == 'ListUsers':
        return invocation.return_value(GLib.Variant('(a(uso))', ([
            (int(uid), state['users'].get(str(uid), {}).get('UserName', 'x'), f'/org/freedesktop/login1/user/_{uid}')
            for uid in state.get('sessions', [])],)))
    if name == 'FindUserById':
        uid = str(args.unpack()[0])
        if uid not in state['users']:
            return error(invocation, 'org.freedesktop.Accounts.Error.Failed', 'Failed to look up user')
        return invocation.return_value(GLib.Variant('(o)', (path(uid),)))
    if name == 'CreateUser':
        login_name, real_name, account_type = args.unpack()
        if any(user['UserName'] == login_name for user in state['users'].values()):
            return error(invocation, 'org.freedesktop.Accounts.Error.UserExists', 'exists')
        uid = str(max([999] + [int(key) for key in state['users'] if int(key) < 60000]) + 1)
        state['users'][uid] = {'UserName': login_name, 'RealName': real_name, 'AccountType': account_type,
                               'Locked': True, 'SystemAccount': False, 'HomeDirectory': '/home/' + login_name}
        register(uid)
        save()
        return invocation.return_value(GLib.Variant('(o)', (path(uid),)))
    if name == 'DeleteUser':
        uid, remove_files = args.unpack()
        uid = str(uid)
        if uid not in state['users']:
            return error(invocation, 'org.freedesktop.Accounts.Error.UserDoesNotExist', 'missing')
        state.setdefault('deleted', []).append({'uid': int(uid), 'remove_files': remove_files})
        del state['users'][uid]
        bus.unregister_object(registered.pop(uid))
        save()
        return invocation.return_value(None)
    uid = object_path.rsplit('User', 1)[1]
    user = state['users'][uid]
    if name == 'SetRealName':
        user['RealName'] = args.unpack()[0]
    elif name == 'SetPassword':
        user['Locked'] = False
        state.setdefault('passwords', {})[user['UserName']] = args.unpack()[0]
    elif name == 'SetAccountType':
        user['AccountType'] = args.unpack()[0]
    save()
    invocation.return_value(None)


def get(_conn, _sender, object_path, _interface, name):
    uid = object_path.rsplit('User', 1)[1]
    value = int(uid) if name == 'Uid' else state['users'][uid][name]
    return GLib.Variant(TYPES[name], value)


bus.register_object('/org/freedesktop/Accounts', accounts, method, None, None)
bus.register_object('/org/freedesktop/login1', login, method, None, None)
for uid in state['users']:
    register(uid)
save()
names = ['org.freedesktop.Accounts', 'org.freedesktop.login1']


def owned(_conn, name):
    names.remove(name)
    if not names:
        ready_path.write_text('ready')


for name in list(names):
    Gio.bus_own_name_on_connection(bus, name, Gio.BusNameOwnerFlags.NONE, owned, None)
GLib.MainLoop().run()
